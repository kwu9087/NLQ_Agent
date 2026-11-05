"""Streamlit page for the NLQ agent: one question in, one response out.

    streamlit run app.py

This file only draws an AgentResult. The agent (nlq_agent/) knows nothing about
Streamlit, and display decisions such as chart choice live in ui/display.py.
"""

import sqlite3
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from nlq_agent import catalog, config
from nlq_agent.agent import Agent, AgentResult
from ui.display import bar_labels, choose_chart, headline, is_money, label

# What the data doesn't hold. Shown in the overview; the agent's prompt says the same.
NOT_IN_DATA = ("attendance, seats or sections, refunds, costs or profit, weather, "
               "and customer names or contact details")

# The brief's three questions, with dates moved into the data's range.
EXAMPLES = {
    "Nets tickets, Dec 2025":
        "How many tickets were sold for Brooklyn Nets home games in December 2025?",
    "Priciest Barclays events":
        "Which events at Barclays Center had the highest average ticket price in 2025?",
    "Top categories by revenue":
        "Show me the top 5 event categories by total revenue.",
}


@st.cache_resource
def get_agent() -> Agent:
    """One agent (database connection and Claude client) shared across reruns."""
    return Agent.create()


@st.cache_data(ttl=3600, show_spinner="Loading the data overview…")
def get_overview(_agent: Agent) -> catalog.Overview:
    """Live figures and subject areas, refreshed at most once an hour."""
    return catalog.load_overview(_agent.db, config.VIEWS_SQL_PATH.read_text(encoding="utf-8"))


def long_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%B} {d.day}, {d.year}"


def compact_money(value: float) -> str:
    return f"${value / 1e6:,.1f}M" if value >= 1e6 else f"${value:,.0f}"


def use_example(question: str) -> None:
    st.session_state.question = question
    st.session_state.run = True


# --- Rendering ------------------------------------------------------------------------

def show_overview(o: catalog.Overview) -> None:
    """What the data is, before the first question: four figures, details folded away."""
    for column, (name, value) in zip(st.columns(4), [
            ("Events", f"{o.events:,}"), ("Venues", f"{o.venues:,}"),
            ("Tickets sold", f"{o.tickets:,}"), ("Gross sales", compact_money(o.revenue))]):
        column.metric(name, value)
    with st.expander("What's in the data"):
        groups: dict[str, list[str]] = {}
        for group, category, events in o.categories:
            groups.setdefault(group, []).append(f"{category} ({events:,} events)")
        st.markdown("**Categories:** " + " · ".join(
            f"{group}: {', '.join(items)}" for group, items in groups.items()))
        st.markdown("**What you can ask about:**")
        for area in o.areas:
            st.markdown(f"- **{area.name}:** {area.description}")
            st.caption("Includes: " + ", ".join(label(f) for f in area.fields))
        st.markdown(f"**Not in the data:** {NOT_IN_DATA}.")
        st.caption("Ticket activity is a sample resale marketplace dataset. Brooklyn Nets "
                   "games at Barclays Center are demo data added for this project.")


def show_details(result: AgentResult) -> None:
    """The explanation and SQL, folded away."""
    if not result.sql:
        return
    with st.expander("How this was calculated"):
        if result.explanation:
            st.write(result.explanation)
        st.code(result.sql.strip(), language="sql")
        if result.attempts > 1:
            st.caption("The first query had an error and was corrected automatically.")


def show_assumptions(result: AgentResult) -> None:
    if result.assumptions:
        with st.container(border=True):
            st.markdown("**Assumptions**")
            st.markdown("\n".join(f"- {a}" for a in result.assumptions))


def show_chart(result: AgentResult) -> None:
    spec = choose_chart(result.columns, result.rows)
    if not spec:
        return
    data = pd.DataFrame(result.rows, columns=result.columns)
    y_format = "$,.0f" if spec.money else ",.0f"
    value_tip = alt.Tooltip(f"{spec.y}:Q", title=label(spec.y),
                            format="$,.2f" if spec.money else ",")
    if spec.kind == "line":
        chart = alt.Chart(data).mark_line(point=True).encode(
            x=alt.X(f"{spec.x}:O", title=label(spec.x)),
            y=alt.Y(f"{spec.y}:Q", title=label(spec.y), axis=alt.Axis(format=y_format)),
            tooltip=[alt.Tooltip(spec.x, title=label(spec.x)), value_tip])
    else:
        # Horizontal bars keep long names readable; sort=None keeps the ranking order.
        data["_bar"] = bar_labels(spec, result.columns, result.rows)
        chart = alt.Chart(data).mark_bar().encode(
            y=alt.Y("_bar:N", title=None, sort=None, axis=alt.Axis(labelLimit=320)),
            x=alt.X(f"{spec.y}:Q", title=label(spec.y), axis=alt.Axis(format=y_format)),
            tooltip=[alt.Tooltip("_bar", title=label(spec.x)), value_tip])
    st.altair_chart(chart, width="stretch")


def show_table(result: AgentResult) -> None:
    data = pd.DataFrame(result.rows, columns=result.columns)
    config = {}
    for column in result.columns:
        if is_money(column):
            config[column] = st.column_config.NumberColumn(label(column), format="dollar")
        else:
            config[column] = st.column_config.Column(label(column))
    st.dataframe(data, hide_index=True, width="stretch", column_config=config)
    if result.truncated:
        st.caption(f"Showing the first {len(result.rows)} rows.")


def show_result(result: AgentResult, coverage: str) -> None:
    if result.status == "answered":
        with st.container(border=True):
            figures = headline(result.columns, result.rows)
            if figures:
                for column, (name, value) in zip(st.columns(len(figures)), figures):
                    column.metric(name, value)
            st.write(result.answer)
        show_assumptions(result)
        show_chart(result)
        show_table(result)
        show_details(result)

    elif result.status == "no_data":
        st.warning(result.answer or "No matching data was found.")
        show_assumptions(result)
        show_details(result)

    elif result.status == "clarify":
        st.info(f"**Could you be more specific?** {result.clarifying_question}")
        st.caption("Edit your question above and ask again.")

    elif result.status == "cannot_answer":
        st.warning(result.reason)
        st.caption(f"The data covers ticket sales from {coverage}: events, venues, "
                   "categories, listings, sales and commission.")

    else:  # error
        st.error(result.error)
        if result.error_detail or result.sql:
            with st.expander("Technical details"):
                if result.error_detail:
                    st.code(result.error_detail, language=None)
                if result.sql:
                    st.code(result.sql.strip(), language="sql")


# --- Page -----------------------------------------------------------------------------

st.set_page_config(page_title="Ticket Sales Q&A", page_icon="🎟️", layout="centered")
st.title("Ask about ticket sales")

try:
    agent = get_agent()
except (RuntimeError, FileNotFoundError) as e:     # missing API key or database
    st.error(f"The app isn't set up yet. {e}")
    st.stop()

try:
    overview = get_overview(agent)
except sqlite3.Error as e:                          # a stale or incomplete database
    st.error(f"The database looks out of date ({e}). Rebuild it with "
             "`python scripts/build_db.py`, then restart the app.")
    st.stop()
coverage = f"{long_date(overview.first_date)} to {long_date(overview.last_date)}"
st.caption(f"Ask a question in plain English about ticket sales from {coverage}. "
           "Each question is answered on its own: to follow up, edit it and ask again.")
show_overview(overview)

with st.form("ask", border=False):
    st.text_area("Your question", key="question", height=80,
                 placeholder="e.g. Which venues had the most revenue in March 2025?")
    submitted = st.form_submit_button("Ask", type="primary")

st.caption("Or try an example:")
for column, (name, question) in zip(st.columns(len(EXAMPLES)), EXAMPLES.items()):
    column.button(name, on_click=use_example, args=(question,), width="stretch")

if submitted or st.session_state.pop("run", False):
    question = st.session_state.get("question", "")
    with st.spinner("Working it out…"):
        st.session_state.result = agent.ask(question)

if "result" in st.session_state:
    show_result(st.session_state.result, coverage)
