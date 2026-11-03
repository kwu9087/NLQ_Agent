"""Safety checks on query execution. Runs against data/tickit.db, no API calls."""

import sqlite3

import pytest

from nlq_agent.database import Database, QueryTimeoutError, UnsafeQueryError, check_sql


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
    check_sql(sql)


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
    # A CTE hides the raw table from the top-level check; the column check still applies.
    "WITH t AS (SELECT phone FROM users) SELECT * FROM t LIMIT 1",
    "SELECT u.email FROM sales_detail s JOIN users u ON u.userid = s.buyer_id LIMIT 1",
])
def test_private_columns_are_denied(db, sql):
    with pytest.raises(sqlite3.DatabaseError, match="prohibited|not authorized"):
        db.run(sql)


def test_raw_tables_denied_at_top_level(db):
    with pytest.raises(sqlite3.DatabaseError, match="prohibited|not authorized"):
        db.run("SELECT pricepaid FROM sales LIMIT 1")


def test_rows_are_capped(db):
    result = db.run("SELECT sale_id FROM sales_detail", max_rows=10)
    assert len(result.rows) == 10 and result.truncated


def test_empty_and_null_aggregate_are_empty(db):
    assert db.run("SELECT sale_id FROM sales_detail WHERE sale_year = 2028").is_empty
    assert db.run("SELECT SUM(tickets) FROM sales_detail WHERE sale_year = 2028").is_empty


def test_slow_query_times_out(db):
    slow = ("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) "
            "SELECT COUNT(*) FROM n")
    with pytest.raises(QueryTimeoutError):
        db.run(slow, timeout_s=0.2)


def test_malformed_sql_raises_sqlite_error(db):
    with pytest.raises(sqlite3.OperationalError):
        db.run("SELECT no_such_column FROM sales_detail")
