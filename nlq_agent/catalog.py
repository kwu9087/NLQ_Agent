"""A plain-English overview of the data, built from the same sources the agent uses.

  * Live figures (date coverage, sales, events, venues, categories) are queried
    through Database.run(), the same read-only, authorizer-checked path the agent's
    SQL takes.
  * Subject areas are read from scripts/views.sql: the "-- Area: <name> | <text>"
    line above each view is written for business users, and the view's real columns
    are listed as plain labels. The other comments in that file are technical notes
    for the model and are not shown. views.sql is also the schema in the agent's
    system prompt, so the overview and the agent can't drift apart.

For a large warehouse the shape is the same, but the figures would come from
precomputed table statistics rather than live counts (see README, Part 4).
"""

import re
from dataclasses import dataclass, field

from .database import Database

_VIEW = re.compile(r"CREATE VIEW (\w+) AS")
_AREA = re.compile(r"^--\s*Area:\s*(.+?)\s*\|\s*(.+?)\s*$")


def is_hidden(column: str) -> bool:
    """Columns that mean nothing to a business user: internal IDs and the demo flag."""
    return column == "is_demo" or column.endswith("_id")


@dataclass
class SubjectArea:
    view: str
    name: str            # e.g. "Sales"
    description: str     # one plain-English sentence
    fields: list[str] = field(default_factory=list)   # column names, IDs left out


@dataclass
class Overview:
    first_date: str
    last_date: str
    sales: int
    tickets: int
    revenue: float
    events: int
    venues: int
    categories: list[tuple[str, str, int]]      # (group, category, events)
    areas: list[SubjectArea]


def parse_areas(views_sql: str) -> dict[str, tuple[str, str]]:
    """{view: (area name, description)} from the "-- Area:" line in the comment block
    directly above each CREATE VIEW. Views without one are left out of the overview."""
    lines = views_sql.splitlines()
    areas = {}
    for i, line in enumerate(lines):
        view = _VIEW.search(line)
        if not view:
            continue
        j = i - 1
        while j >= 0 and lines[j].lstrip().startswith("--"):
            area = _AREA.match(lines[j].strip())
            if area:
                areas[view.group(1)] = (area.group(1), area.group(2))
                break
            j -= 1
    return areas


def load_overview(db: Database, views_sql: str) -> Overview:
    totals = db.run("SELECT MIN(sale_date), MAX(sale_date), COUNT(*), SUM(tickets), "
                    "SUM(sale_total) FROM sales_detail").rows[0]
    categories = db.run("SELECT category_group, category, COUNT(*) FROM event_summary "
                        "GROUP BY category_group, category ORDER BY 1, 3 DESC").rows
    venues = db.run("SELECT COUNT(*) FROM venues").rows[0][0]

    areas = []
    for view, (name, description) in parse_areas(views_sql).items():
        columns = db.run(f"SELECT * FROM {view} LIMIT 0").columns   # the real columns
        areas.append(SubjectArea(view, name, description,
                                 [c for c in columns if not is_hidden(c)]))

    return Overview(first_date=totals[0], last_date=totals[1], sales=totals[2],
                    tickets=totals[3], revenue=totals[4],
                    events=sum(n for _, _, n in categories), venues=venues,
                    categories=[tuple(c) for c in categories], areas=areas)
