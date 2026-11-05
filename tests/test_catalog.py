"""Tests for nlq_agent/catalog.py: the data overview shown on the page."""

from nlq_agent import config
from nlq_agent.catalog import is_hidden, load_overview, parse_areas
from nlq_agent.database import Database

VIEWS_SQL = config.VIEWS_SQL_PATH.read_text(encoding="utf-8")


def test_every_view_has_a_plain_english_area():
    areas = parse_areas(VIEWS_SQL)
    assert set(areas) == {"venues", "sales_detail", "listing_detail", "event_summary",
                          "users_public"}
    assert areas["sales_detail"][0] == "Sales"
    assert all(description.endswith(".") for _, description in areas.values())


def test_area_line_must_be_in_the_comment_block_above_the_view():
    sql = ("-- Area: A | First view.\n-- One row per thing.\nCREATE VIEW a AS SELECT 1;\n\n"
           "-- No area here.\nCREATE VIEW b AS SELECT 1;\n")
    assert parse_areas(sql) == {"a": ("A", "First view.")}


def test_ids_and_demo_flag_are_hidden():
    assert is_hidden("event_id") and is_hidden("is_demo")
    assert not is_hidden("event_name") and not is_hidden("tickets")


def test_overview_matches_the_database():
    o = load_overview(Database(), VIEWS_SQL)
    assert (o.first_date, o.last_date) == ("2025-01-07", "2026-01-07")
    assert (o.sales, o.tickets, o.events, o.venues) == (174849, 350159, 8844, 206)
    assert ("Concerts", "Pop", 4998) in o.categories
    customers = next(a for a in o.areas if a.view == "users_public")
    assert "username" in customers.fields
    # The overview lists each view's real columns, so it shows exactly what the agent
    # can query: users_public defines no names or contact details.
    assert not {"email", "phone", "firstname", "lastname"} & set(customers.fields)
