"""Query execution: a read-only connection with code-level safety checks.

The system prompt tells the model to write one SELECT against the views. This module
enforces it, so a tricked or mistaken model still can't change data or read private
columns:

  1. The file is opened read-only (mode=ro).
  2. check_sql() accepts only a single statement that starts with SELECT or WITH.
  3. An SQLite authorizer allows only reads. It denies the private columns of
     `users` everywhere, and denies raw tables named directly in the query.
  4. Results are capped at MAX_ROWS, and a progress handler stops slow queries.
"""

import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config

VIEWS = {"venues", "sales_detail", "listing_detail", "event_summary", "users_public"}
PRIVATE_COLUMNS = {("users", c) for c in ("firstname", "lastname", "email", "phone")}
_DENIED_FUNCTIONS = {"load_extension"}


class UnsafeQueryError(Exception):
    """The SQL was rejected before running (not a single read-only query)."""


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
        # SUM() over nothing returns one row of NULLs. Treat that as empty too, so
        # "no data" is never reported as zero.
        self.is_empty = not self.rows or all(v is None for row in self.rows for v in row)


_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_STRING = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")


def check_sql(sql: str) -> str:
    """Return the SQL ready to run, or raise UnsafeQueryError.

    A cheap first filter: the authorizer is the real enforcement.
    """
    stripped = _COMMENT.sub(" ", sql).strip().rstrip(";").strip()
    if not stripped:
        raise UnsafeQueryError("The query is empty.")
    if ";" in _STRING.sub("''", stripped):
        raise UnsafeQueryError("Only one statement is allowed.")
    first_word = stripped.split(None, 1)[0].upper()
    if first_word not in ("SELECT", "WITH"):
        raise UnsafeQueryError(f"Only SELECT queries are allowed, not {first_word}.")
    return stripped


def _authorizer(action, arg1, arg2, _db, source):
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        table, column = arg1, arg2
        if (table, column) in PRIVATE_COLUMNS:
            return sqlite3.SQLITE_DENY
        # source is None when the read comes straight from the query text rather than
        # from inside a view. Column '' is a row count (COUNT(*)), which reveals nothing.
        if source is None and table not in VIEWS and column != "":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION:
        return sqlite3.SQLITE_DENY if arg2 in _DENIED_FUNCTIONS else sqlite3.SQLITE_OK
    if action == getattr(sqlite3, "SQLITE_RECURSIVE", -1):
        return sqlite3.SQLITE_OK      # recursive WITH; the timeout bounds it
    return sqlite3.SQLITE_DENY        # PRAGMA, ATTACH, writes, schema changes, ...


class Database:
    def __init__(self, path: Path = config.DB_PATH):
        if not Path(path).exists():
            raise FileNotFoundError(
                f"Database not found at {path}. Build it with: python scripts/build_db.py")
        self._conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True,
                                     check_same_thread=False)
        self._conn.set_authorizer(_authorizer)

    def run(self, sql: str, max_rows: int = config.MAX_ROWS,
            timeout_s: float = config.QUERY_TIMEOUT_S) -> QueryResult:
        """Run one read-only query. Raises UnsafeQueryError, QueryTimeoutError or
        sqlite3.Error (malformed SQL, unknown column, not authorized)."""
        sql = check_sql(sql)
        start = time.monotonic()
        deadline = start + timeout_s
        self._conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
        try:
            cursor = self._conn.execute(sql)
            rows = cursor.fetchmany(max_rows + 1)
        except sqlite3.OperationalError as e:
            if "interrupted" in str(e):
                raise QueryTimeoutError(f"The query took longer than {timeout_s:g} seconds.") from e
            raise
        finally:
            self._conn.set_progress_handler(None, 0)
        columns = [d[0] for d in cursor.description or []]
        return QueryResult(columns=columns, rows=rows[:max_rows],
                           truncated=len(rows) > max_rows,
                           elapsed_s=time.monotonic() - start)

    def close(self):
        self._conn.close()
