"""The question-to-answer flow, with the model replaced by fakes. No API calls."""

from datetime import date

import pytest

from nlq_agent.agent import Agent
from nlq_agent.database import Database
from nlq_agent.llm import LLMError, ModelRefusal, QueryPlan

TODAY = date(2026, 11, 3)
GOOD_SQL = "SELECT category, SUM(tickets) AS tickets FROM sales_detail GROUP BY category LIMIT 5"


class FakeGenerator:
    def __init__(self, plans):
        self.plans = list(plans)      # QueryPlan, or an exception to raise
        self.repair_errors = []

    def _next(self):
        item = self.plans.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def plan(self, question, today):
        return self._next()

    def repair(self, previous, error):
        self.repair_errors.append(error)
        return self._next()


class FakeWriter:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def write(self, question, plan, columns, rows, truncated):
        self.calls.append(rows)
        if self.fail:
            raise LLMError("The AI returned an empty answer. The data is shown below.")
        return "Pop sold the most tickets."


@pytest.fixture(scope="module")
def db():
    database = Database()
    yield database
    database.close()


def query(sql, **kw):
    return QueryPlan("query", sql=sql, explanation="Adds up tickets.", **kw)


def make(db, plans, writer=None):
    return Agent(db, FakeGenerator(plans), writer or FakeWriter())


def test_answered(db):
    result = make(db, [query(GOOD_SQL, assumptions=["All dates."])]).ask("Top categories?", TODAY)
    assert result.status == "answered"
    assert result.answer and result.rows and result.columns == ["category", "tickets"]
    assert result.sql == GOOD_SQL and result.assumptions == ["All dates."]


def test_clarify_and_cannot_answer_skip_the_database(db):
    r1 = make(db, [QueryPlan("clarify", question="Which metric?")]).ask("How are we doing?", TODAY)
    r2 = make(db, [QueryPlan("cannot_answer", reason="No weather data.")]).ask("Weather?", TODAY)
    assert (r1.status, r1.clarifying_question) == ("clarify", "Which metric?")
    assert (r2.status, r2.reason) == ("cannot_answer", "No weather data.")


def test_blank_question_asks_for_one(db):
    assert make(db, []).ask("   ", TODAY).status == "clarify"


def test_sql_error_is_repaired_once(db):
    agent = make(db, [query("SELECT nope FROM sales_detail"), query(GOOD_SQL)])
    result = agent.ask("Top categories?", TODAY)
    assert result.status == "answered" and result.attempts == 2
    assert "nope" in agent.generator.repair_errors[0]


def test_sql_error_twice_gives_friendly_error(db):
    bad = "SELECT nope FROM sales_detail"
    result = make(db, [query(bad), query(bad)]).ask("Top categories?", TODAY)
    assert result.status == "error" and result.attempts == 2
    assert "rephras" in result.error and "nope" in result.error_detail


def test_unsafe_sql_is_not_run_or_retried(db):
    agent = make(db, [query("DELETE FROM sales")])
    result = agent.ask("Delete everything", TODAY)
    assert result.status == "error" and result.attempts == 1
    assert agent.generator.repair_errors == []


def test_private_column_attempt_is_blocked(db):
    sneaky = "WITH t AS (SELECT email FROM users) SELECT * FROM t"
    result = make(db, [query(sneaky), query(sneaky)]).ask("Emails?", TODAY)
    assert result.status == "error" and not result.rows


@pytest.mark.parametrize("sql", [
    "SELECT SUM(tickets) AS tickets FROM sales_detail WHERE sale_year = 2028",
    # A total with a count over nothing is (None, 0): still no data, not "0 tickets".
    "SELECT SUM(tickets) AS tickets, COUNT(*) AS sales FROM sales_detail WHERE sale_year = 2028",
])
def test_empty_result_is_no_data_not_zero(db, sql):
    assert make(db, [query(sql)]).ask("Tickets in 2028?", TODAY).status == "no_data"


def test_answer_failure_still_returns_rows(db):
    result = make(db, [query(GOOD_SQL)], FakeWriter(fail=True)).ask("Top categories?", TODAY)
    assert result.status == "answered" and result.rows and result.sql
    assert "summary couldn't be produced" in result.answer
    assert result.error_detail.startswith("Answer step:")


def test_answer_failure_on_empty_result_doesnt_promise_data(db):
    plan = query("SELECT SUM(tickets) AS tickets FROM sales_detail WHERE sale_year = 2028")
    result = make(db, [plan], FakeWriter(fail=True)).ask("Tickets in 2028?", TODAY)
    assert (result.status, result.answer) == ("no_data", "No matching data was found.")


def test_write_caught_only_by_the_authorizer_is_not_retried(db):
    # Starts with WITH, so it passes the statement check; the authorizer blocks it.
    agent = make(db, [query("WITH x AS (SELECT 1) DELETE FROM sales")])
    result = agent.ask("Delete everything", TODAY)
    assert result.status == "error" and result.attempts == 1
    assert agent.generator.repair_errors == []


def test_raw_table_read_by_mistake_is_repaired(db):
    agent = make(db, [query("SELECT catname, COUNT(*) FROM category GROUP BY catname"),
                      query(GOOD_SQL)])
    result = agent.ask("Top categories?", TODAY)
    assert result.status == "answered" and result.attempts == 2
    assert "prohibited" in agent.generator.repair_errors[0]


def test_model_errors_become_results(db):
    r1 = make(db, [LLMError("The AI service is busy.", "429")]).ask("Q?", TODAY)
    r2 = make(db, [ModelRefusal("I can't help with that request.")]).ask("Q?", TODAY)
    assert (r1.status, r1.error, r1.error_detail) == ("error", "The AI service is busy.", "429")
    assert r2.status == "cannot_answer"
