"""Query execution: a read-only connection with code-level safety checks.

The system prompt tells the model to write one SELECT against the views. This module
enforces it, so a tricked or mistaken model still can't change data, read the raw
tables or read private columns:

  1. The file is opened read-only (mode=ro), with a fresh connection for every query,
     so concurrent users never share connection state.
  2. check_sql() accepts only a single statement that starts with SELECT or WITH, and
     rejects a WITH name that would pose as one of the views.
  3. An SQLite authorizer allows only reads, and only of the views: a column of a raw
     table can be read only from inside a view's definition. The private columns of
     `users` are denied everywhere. Writes, PRAGMA and ATTACH are denied.
  4. Results are capped at MAX_ROWS rows and MAX_VALUE_BYTES per value, and a progress
     handler stops slow queries.
"""

import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from . import config

PRIVATE_COLUMNS = {("users", c) for c in ("firstname", "lastname", "email", "phone")}
_DENIED_FUNCTIONS = {"load_extension"}
MAX_VALUE_BYTES = 1_000_000


class UnsafeQueryError(Exception):
    """The SQL was rejected: not a single read-only query. Never retried."""


class QueryTimeoutError(Exception):
    """The query ran longer than the time limit."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool = False          # more rows existed than were returned
    elapsed_s: float = 0.0
    is_empty: bool = field(init=False)

    def __post_init__(self):
        # An aggregate over nothing returns one row of NULLs, e.g. SUM() -> (None,),
        # or SUM() with COUNT() -> (None, 0). Treat that as empty too, so "no data" is
        # never reported as zero.
        single_empty_row = (len(self.rows) == 1 and None in self.rows[0]
                            and all(v in (None, 0) for v in self.rows[0]))
        self.is_empty = not self.rows or single_empty_row


# Strings, quoted names and comments, matched left to right, so "--" inside a string
# is not a comment and a quote inside a comment is not a string.
_LEXEMES = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*|/\*.*?(?:\*/|$)", re.S)
# Tokens for finding WITH names. SQLite accepts a name bare or quoted in any of four
# ways ("x", [x], `x`, even 'x'), with or without spaces around AS, so a regex over
# bare words misses some. Comments are matched so they can be skipped.
_TOKENS = re.compile(r"""'(?:[^']|'')*'|"(?:[^"]|"")*"|`(?:[^`]|``)*`|\[[^\]]*\]
                         |--[^\n]*|/\*.*?(?:\*/|$)|\w+|\S""", re.S | re.X)
_QUOTES = {"'": "'", '"': '"', "`": "`", "[": "]"}


def _blank_strings_and_comments(sql: str) -> str:
    return _LEXEMES.sub(lambda m: "''" if m.group(0)[0] in "'\"" else " ", sql)


def _name(token: str) -> str:
    """The name a token stands for: venues, "venues", [venues], `venues` -> venues."""
    close = _QUOTES.get(token[0])
    if close and len(token) >= 2 and token[-1] == close:
        return token[1:-1].replace(close * 2, close)
    return token


def _with_names(sql: str) -> list[str]:
    """Names declared as WITH tables: NAME [(cols)] AS [NOT] [MATERIALIZED] (."""
    tokens = [t for t in _TOKENS.findall(sql) if not t.startswith(("--", "/*"))]
    upper = [t.upper() for t in tokens]
    names = []
    for i, token in enumerate(tokens):
        if not (token[0].isalnum() or token[0] == "_" or token[0] in _QUOTES):
            continue
        j = i + 1
        if j < len(tokens) and tokens[j] == "(":          # optional column list
            while j < len(tokens) and tokens[j] != ")":
                j += 1
            j += 1
        if j >= len(tokens) or upper[j] != "AS":
            continue
        j += 1
        for optional in ("NOT", "MATERIALIZED"):
            if j < len(tokens) and upper[j] == optional:
                j += 1
        if j < len(tokens) and tokens[j] == "(":
            names.append(_name(token))
    return names


def check_sql(sql: str, views: frozenset[str] = frozenset()) -> str:
    """Return the SQL to run (unchanged apart from surrounding whitespace), or raise
    UnsafeQueryError.

    The checks look at a copy with strings and comments blanked out, so text inside
    quotes can't hide a second statement or break the check. The original SQL is what
    runs. The authorizer is the real enforcement; this rejects the obvious cases early
    with a clear message.
    """
    code = _blank_strings_and_comments(sql).strip().rstrip(";").strip()
    if not code:
        raise UnsafeQueryError("The query is empty.")
    if ";" in code:
        raise UnsafeQueryError("Only one statement is allowed.")
    first_word = code.split(None, 1)[0].upper()
    if first_word not in ("SELECT", "WITH"):
        raise UnsafeQueryError(f"Only SELECT queries are allowed, not {first_word}.")
    # SQLite reports a WITH table's name to the authorizer as the source of its reads,
    # just as it does for a view, so a WITH named like a view would be trusted as one.
    posing = {name for name in _with_names(sql) if name.lower() in views}
    if posing:
        raise UnsafeQueryError(f"A WITH name can't reuse a view's name: {sorted(posing)}.")
    return sql.strip()


def make_authorizer(views: frozenset[str]):
    """The SQLite authorizer for agent queries. `views` are the queryable view names."""

    def authorize(action, arg1, arg2, _db, source):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            table, column = arg1, arg2
            if (table, column) in PRIVATE_COLUMNS:
                return sqlite3.SQLITE_DENY
            # Allowed: a view's own columns (from the query or a WITH), reads a view's
            # definition makes of the raw tables (source is that view), and row-only
            # reads (column '', as in COUNT(*) or a join), which reveal no values.
            # Anything else is a raw table read from the query or from inside a WITH.
            if table in views or source in views or column == "":
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_DENY if arg2 in _DENIED_FUNCTIONS else sqlite3.SQLITE_OK
        if action == getattr(sqlite3, "SQLITE_RECURSIVE", -1):
            return sqlite3.SQLITE_OK      # recursive WITH; the timeout bounds it
        return sqlite3.SQLITE_DENY        # PRAGMA, ATTACH, writes, schema changes, ...

    return authorize


def _unique(columns: list[str]) -> list[str]:
    """Make repeated column names unique (tickets, tickets_2) so every column can be
    shown; SELECT a.tickets, b.tickets would otherwise give two 'tickets' columns."""
    seen: dict[str, int] = {}
    out = []
    for name in columns:
        seen[name] = seen.get(name, 0) + 1
        out.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return out


class Database:
    """Runs agent queries against data/tickit.db. Safe to share between threads: every
    run() opens its own read-only connection (a few milliseconds in SQLite)."""

    def __init__(self, path: Path = config.DB_PATH):
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"Database not found at {path}. Build it with: python scripts/build_db.py")
        self._uri = f"file:{quote(path.resolve().as_posix())}?mode=ro"
        # The queryable views are the ones the build created from scripts/views.sql.
        try:
            with closing(self._connect()) as conn:
                self.views = frozenset(r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'view'"))
        except sqlite3.DatabaseError as e:
            raise RuntimeError(f"The database at {path} can't be read ({e}). Rebuild it "
                               "with: python scripts/build_db.py") from e
        if not self.views:
            raise RuntimeError(f"The database at {path} has no views. Rebuild it with: "
                               "python scripts/build_db.py")
        self._authorizer = make_authorizer(self.views)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._uri, uri=True)

    def run(self, sql: str, max_rows: int = config.MAX_ROWS,
            timeout_s: float = config.QUERY_TIMEOUT_S) -> QueryResult:
        """Run one read-only query.

        Raises UnsafeQueryError (not a single read-only query, including a write the
        authorizer blocked), QueryTimeoutError, or sqlite3.Error (malformed SQL, an
        unknown column, or a read the authorizer denied, which the model can repair).
        """
        sql = check_sql(sql, self.views)
        start = time.monotonic()
        deadline = start + timeout_s
        conn = self._connect()
        try:
            conn.set_authorizer(self._authorizer)
            if hasattr(conn, "setlimit"):                   # Python 3.11+
                conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES)
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
            cursor = conn.execute(sql)
            rows = cursor.fetchmany(max_rows + 1)
            columns = _unique([d[0] for d in cursor.description or []])
        except sqlite3.OperationalError as e:
            if "interrupted" in str(e):
                raise QueryTimeoutError(f"The query took longer than {timeout_s:g} seconds.") from e
            raise
        except sqlite3.DatabaseError as e:
            # "not authorized" is a write, PRAGMA or ATTACH that got past check_sql.
            # "prohibited" is a denied read: a raw table or private column, which the
            # model may have used by mistake, so it stays repairable.
            if "not authorized" in str(e):
                raise UnsafeQueryError(f"The query tried more than reading data ({e}).") from e
            raise
        finally:
            conn.close()
        return QueryResult(columns=columns, rows=rows[:max_rows],
                           truncated=len(rows) > max_rows,
                           elapsed_s=time.monotonic() - start)

    def close(self):
        """Kept for callers that close the database; connections are per query."""
