"""Safety checks on query execution. Runs against data/tickit.db, no API calls."""

import sqlite3

import pytest

import subprocess
import sys
from pathlib import Path

from nlq_agent.database import (Database, QueryTimeoutError, UnsafeQueryError, check_sql,
                                make_authorizer)

VIEWS = frozenset({"venues", "sales_detail", "listing_detail", "event_summary",
                   "users_public"})


@pytest.fixture(scope="module")
def db():
    database = Database()
    yield database
    database.close()


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "  -- comment\nSELECT 1;",
    "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
    "SELECT 'a;b' AS text_with_semicolon",
])
def test_check_sql_accepts_single_select(sql):
    assert check_sql(sql) == sql.strip()          # runs exactly as written


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM sales_detail WHERE event_name LIKE '%--%'",
    "SELECT '/*' AS a, '*/' AS b",
])
def test_comment_markers_inside_strings_are_left_alone(db, sql):
    assert check_sql(sql) == sql
    db.run(sql)                                  # runs, unchanged


def test_string_literals_reach_the_database_unchanged(db):
    assert db.run("SELECT '/*' AS a, '*/' AS b").rows == [("/*", "*/")]


@pytest.mark.parametrize("sql", [
    "WITH sales_detail AS (SELECT pricepaid FROM sales) SELECT * FROM sales_detail",
    "WITH venues(x) AS (SELECT email FROM users) SELECT * FROM venues",
])
def test_with_name_posing_as_a_view_is_rejected(sql):
    with pytest.raises(UnsafeQueryError, match="view's name"):
        check_sql(sql, VIEWS)


@pytest.mark.parametrize("sql", [
    "",
    "DELETE FROM sales",
    "SELECT 1; DROP TABLE sales",
    "PRAGMA table_info(users)",
    "ATTACH DATABASE 'x.db' AS x",
    "/* hide */ UPDATE users SET email = NULL",
])
def test_check_sql_rejects_other_statements(sql):
    with pytest.raises(UnsafeQueryError):
        check_sql(sql)


def test_view_query_runs(db):
    result = db.run("SELECT category, SUM(tickets) AS n FROM sales_detail "
                    "GROUP BY category ORDER BY n DESC LIMIT 3")
    assert result.columns == ["category", "n"]
    assert len(result.rows) == 3 and not result.is_empty


def test_users_public_and_row_count_allowed(db):
    assert db.run("SELECT COUNT(*) FROM users_public").rows[0][0] > 0
    assert db.run("SELECT username, state FROM users_public LIMIT 1").rows


@pytest.mark.parametrize("sql", [
    "SELECT email FROM users LIMIT 1",
    "SELECT firstname, lastname FROM users LIMIT 1",
    "WITH t AS (SELECT phone FROM users) SELECT * FROM t LIMIT 1",
    "SELECT u.email FROM sales_detail s JOIN users u ON u.userid = s.buyer_id LIMIT 1",
])
def test_private_columns_are_denied(db, sql):
    with pytest.raises(sqlite3.DatabaseError, match="prohibited"):
        db.run(sql)


def test_private_column_rule_holds_even_inside_a_view():
    # The queries above are also stopped by the raw-table rule. This checks the
    # private-column rule on its own: even a view that exposed users.email (say, a
    # future view written by mistake) could not read it.
    authorize = make_authorizer(VIEWS)
    assert authorize(sqlite3.SQLITE_READ, "users", "email", "main", "users_public") \
        == sqlite3.SQLITE_DENY
    assert authorize(sqlite3.SQLITE_READ, "users", "username", "main", "users_public") \
        == sqlite3.SQLITE_OK


@pytest.mark.parametrize("sql", [
    "SELECT pricepaid FROM sales LIMIT 1",
    "SELECT pricepaid FROM sales AS sales_detail LIMIT 1",      # alias named like a view
    "WITH t AS (SELECT pricepaid FROM sales) SELECT * FROM t",   # raw table inside WITH
    "WITH x AS (SELECT sql FROM sqlite_master) SELECT * FROM x", # schema inside WITH
    "SELECT sql FROM sqlite_master",
])
def test_raw_tables_are_denied_however_the_query_is_written(db, sql):
    with pytest.raises(sqlite3.DatabaseError, match="prohibited"):
        db.run(sql)


@pytest.mark.parametrize("sql", [
    "WITH x AS (SELECT tickets FROM sales_detail) SELECT SUM(tickets) FROM x",
    "WITH m AS (SELECT sale_month, SUM(sale_total) AS r FROM sales_detail GROUP BY 1) "
    "SELECT sale_month, r - LAG(r) OVER (ORDER BY sale_month) FROM m",
    "SELECT u.username FROM sales_detail s JOIN users_public u ON u.user_id = s.buyer_id",
])
def test_with_and_joins_over_views_still_run(db, sql):
    assert db.run(sql).rows


def test_write_inside_with_is_unsafe(db):
    with pytest.raises(UnsafeQueryError):
        db.run("WITH x AS (SELECT 1) DELETE FROM sales")


def test_repeated_column_names_are_made_unique(db):
    assert db.run("SELECT tickets, tickets FROM sales_detail LIMIT 1").columns \
        == ["tickets", "tickets_2"]


def test_huge_values_are_refused(db):
    with pytest.raises(sqlite3.DataError):
        db.run("SELECT printf('%.*c', 400000000, 'x')")


def test_stale_or_empty_database_gives_a_clear_error(tmp_path):
    empty = tmp_path / "empty.db"
    empty.write_bytes(b"")
    with pytest.raises(RuntimeError, match="build_db.py"):
        Database(empty)


CONCURRENT_SCRIPT = """
import threading
from nlq_agent.database import Database
db = Database()
slow = "SELECT COUNT(*) FROM sales_detail a JOIN venues v ON 1=1"
threads = [threading.Thread(target=db.run, args=(sql,))
           for sql in (slow, "SELECT COUNT(*) FROM venues")]
for t in threads: t.start()
for t in threads: t.join()
print("ok")
"""


def test_concurrent_queries_dont_hang():
    # Two threads sharing one Database, as two browser sessions share the app's agent.
    # Run in a subprocess: a deadlock here freezes the whole interpreter.
    done = subprocess.run([sys.executable, "-c", CONCURRENT_SCRIPT], capture_output=True,
                          text=True, timeout=60, cwd=Path(__file__).resolve().parent.parent)
    assert done.stdout.strip() == "ok", done.stderr


def test_rows_are_capped(db):
    result = db.run("SELECT sale_id FROM sales_detail", max_rows=10)
    assert len(result.rows) == 10 and result.truncated


def test_empty_and_null_aggregate_are_empty(db):
    assert db.run("SELECT sale_id FROM sales_detail WHERE sale_year = 2028").is_empty
    assert db.run("SELECT SUM(tickets) FROM sales_detail WHERE sale_year = 2028").is_empty
    assert db.run("SELECT SUM(tickets), COUNT(*) FROM sales_detail "
                  "WHERE sale_year = 2028").is_empty
    # A count of zero on its own is a real answer, not missing data.
    assert not db.run("SELECT COUNT(*) FROM sales_detail WHERE sale_year = 2028").is_empty


def test_slow_query_times_out(db):
    slow = ("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) "
            "SELECT COUNT(*) FROM n")
    with pytest.raises(QueryTimeoutError):
        db.run(slow, timeout_s=0.2)


def test_malformed_sql_raises_sqlite_error(db):
    with pytest.raises(sqlite3.OperationalError):
        db.run("SELECT no_such_column FROM sales_detail")
