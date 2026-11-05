"""Offline tests for app.py: the page is run headlessly with a fake agent (no API calls)."""

import sqlite3
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from nlq_agent import catalog
from nlq_agent.agent import Agent, AgentResult

APP = str(Path(__file__).resolve().parent.parent / "app.py")


OVERVIEW = catalog.Overview(
    first_date="2025-01-07", last_date="2026-01-07", sales=174849, tickets=350159,
    revenue=112355166.0, events=8844, venues=206,
    categories=[("Concerts", "Pop", 4998), ("Sports", "NBA", 46)],
    areas=[catalog.SubjectArea("sales_detail", "Sales", "Every ticket purchase.",
                               ["sale_date", "tickets", "sale_total"])])
OVERVIEW_METRICS = ["8,844", "206", "350,159", "$112.4M"]


@pytest.fixture(autouse=True)
def fixed_overview(monkeypatch):
    """The page's data overview, without querying the real database."""
    monkeypatch.setattr(catalog, "load_overview", lambda db, views_sql: OVERVIEW)
    st.cache_data.clear()


class FakeAgent:
    db = None

    def __init__(self, result: AgentResult):
        self.result = result
        self.questions = []

    def ask(self, question):
        self.questions.append(question)
        return self.result


@pytest.fixture
def run_app(monkeypatch):
    """Ask `question` on the page, with the agent returning `result`."""
    def run(result: AgentResult, question="A question"):
        fake = FakeAgent(result)
        monkeypatch.setattr(Agent, "create", classmethod(lambda cls: fake))
        st.cache_resource.clear()
        at = AppTest.from_file(APP, default_timeout=30).run()
        at.text_area(key="question").input(question)
        at.button[0].click().run()          # the form's Ask button
        assert not at.exception, at.exception
        return at, fake
    return run


def test_answer_shows_figures_assumptions_table_and_sql(run_app):
    result = AgentResult("q", "answered", answer="829 tickets were sold.",
                         explanation="Adds up tickets.", assumptions=["Uses December 2025."],
                         sql="SELECT 1", columns=["tickets_sold", "revenue"],
                         rows=[(829, 242452.0)], attempts=1)
    at, fake = run_app(result, "Nets tickets last month?")
    assert fake.questions == ["Nets tickets last month?"]
    assert [m.value for m in at.metric] == OVERVIEW_METRICS + ["829", "$242,452.00"]
    assert any("829 tickets were sold." in m.value for m in at.markdown)
    assert any("Uses December 2025." in m.value for m in at.markdown)
    assert len(at.dataframe) == 1
    assert at.code[0].value == "SELECT 1"


def test_no_data_is_a_warning(run_app):
    at, _ = run_app(AgentResult("q", "no_data", answer="No sales were found for Hamilton.",
                                sql="SELECT 1"))
    assert "No sales were found for Hamilton." in at.warning[0].value
    assert len(at.dataframe) == 0


def test_clarify_shows_the_question(run_app):
    at, _ = run_app(AgentResult("q", "clarify", clarifying_question="Which show do you mean?"))
    assert "Which show do you mean?" in at.info[0].value


def test_cannot_answer_shows_the_reason(run_app):
    at, _ = run_app(AgentResult("q", "cannot_answer", reason="Attendance isn't recorded."))
    assert "Attendance isn't recorded." in at.warning[0].value


def test_error_shows_message_and_folds_away_detail(run_app):
    at, _ = run_app(AgentResult("q", "error", error="The AI service is busy.",
                                error_detail="429: rate limited"))
    assert at.error[0].value == "The AI service is busy."
    assert at.code[0].value == "429: rate limited"


def test_example_button_asks_its_question(run_app, monkeypatch):
    fake = FakeAgent(AgentResult("q", "clarify", clarifying_question="?"))
    monkeypatch.setattr(Agent, "create", classmethod(lambda cls: fake))
    st.cache_resource.clear()
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.button[1].click().run()                # first example button
    assert fake.questions == [
        "How many tickets were sold for Brooklyn Nets home games in December 2025?"]


def test_setup_problem_is_shown_not_raised(monkeypatch):
    def missing_key(cls):
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    monkeypatch.setattr(Agent, "create", classmethod(missing_key))
    st.cache_resource.clear()
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert "ANTHROPIC_API_KEY is not set." in at.error[0].value


def test_overview_is_shown_before_any_question(monkeypatch):
    monkeypatch.setattr(Agent, "create", classmethod(lambda cls: FakeAgent(None)))
    st.cache_resource.clear()
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert [m.value for m in at.metric] == OVERVIEW_METRICS
    assert "January 7, 2025 to January 7, 2026" in at.caption[0].value
    text = " ".join(m.value for m in at.markdown)
    assert "Pop (4,998 events)" in text and "**Sales:** Every ticket purchase." in text
    assert any("Includes: Sale date, Tickets, Sale total" in c.value for c in at.caption)


def test_stale_database_shows_a_rebuild_message(monkeypatch):
    def stale(db, views_sql):
        raise sqlite3.OperationalError("no such table: sales_detail")
    monkeypatch.setattr(catalog, "load_overview", stale)
    monkeypatch.setattr(Agent, "create", classmethod(lambda cls: FakeAgent(None)))
    st.cache_resource.clear()
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert "build_db.py" in at.error[0].value
