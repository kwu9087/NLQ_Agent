"""Display decisions for a query result: chart choice, headline figures, formatting.

Plain Python with no Streamlit import, so it can be tested on its own. app.py draws what
these functions decide.
"""

import re
from dataclasses import dataclass
from typing import Any, Literal, Optional

MAX_BAR_ROWS = 25

_MONEY = re.compile(r"revenue|price|spent|sales?_total|total_paid|commission|earn|amount", re.I)
_ID = re.compile(r"(^|_)id$", re.I)
# Values that read as points in time: 2025, 2025-03, 2025-03-14, 2025-Q3.
_TIME_VALUE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?|-Q[1-4])?$")
_TIME_NAME = re.compile(r"date|month|quarter|year|week|day", re.I)
# The first sort key of a query's last ORDER BY, when it is a plain column name.
_ORDER_BY = re.compile(r"\border\s+by\s+(?:\w+\.)?(\w+)\s*(?:asc|desc)?\s*(?:,|limit\b|$)", re.I)


def label(column: str) -> str:
    """tickets_sold -> Tickets sold."""
    text = column.replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def is_money(column: str) -> bool:
    return bool(_MONEY.search(column))


def format_value(column: str, value: Any) -> str:
    if value is None:
        return "–"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if is_money(column):
        return f"${value:,.2f}"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}"
    return f"{int(value):,}"


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _numeric_columns(columns: list[str], rows: list[tuple]) -> list[int]:
    """Columns whose values are all numbers (ignoring NULLs), excluding IDs."""
    found = []
    for i, name in enumerate(columns):
        values = [r[i] for r in rows if r[i] is not None]
        if values and all(_is_number(v) for v in values) and not _ID.search(name):
            found.append(i)
    return found


def _is_time_column(name: str, values: list) -> bool:
    if not values or not _TIME_NAME.search(name):
        return False
    return all(_TIME_VALUE.match(str(v)) for v in values)


def sort_column(sql: Optional[str]) -> Optional[str]:
    """The column a query ranks by: ORDER BY revenue DESC LIMIT 5 -> 'revenue'."""
    matches = _ORDER_BY.findall((sql or "").strip().rstrip(";"))
    return matches[-1] if matches else None


def headline(columns: list[str], rows: list[tuple]) -> list[tuple[str, str]]:
    """For a one-row result, the figures to show large: [(label, formatted value)]."""
    if len(rows) != 1:
        return []
    return [(label(columns[i]), format_value(columns[i], rows[0][i]))
            for i in _numeric_columns(columns, rows)[:3]]


@dataclass
class ChartSpec:
    kind: Literal["bar", "line"]
    x: str          # category or time column
    y: str          # numeric column
    money: bool
    x_with: Optional[str] = None   # bar charts: column appended to x when x repeats


def bar_labels(spec: ChartSpec, columns: list[str], rows: list[tuple]) -> list[str]:
    """The text of each bar, e.g. 'Brooklyn Nets vs. Houston Rockets (2025-01-20)'."""
    x = columns.index(spec.x)
    if not spec.x_with:
        return [str(r[x]) for r in rows]
    w = columns.index(spec.x_with)
    return [f"{r[x]} ({r[w]})" for r in rows]


def choose_chart(columns: list[str], rows: list[tuple],
                 order_by: Optional[str] = None) -> Optional[ChartSpec]:
    """A line chart for values over time, a bar chart for a ranking or breakdown,
    or None when a chart wouldn't help (one row, no numbers, too many bars).

    order_by is the column the query sorted on (see sort_column). A ranking's bars show
    that column, so "top categories by revenue" charts revenue, not tickets."""
    if len(rows) < 2:
        return None
    numeric = _numeric_columns(columns, rows)
    if not numeric:
        return None

    time_columns = []
    name_seen = False      # a text column (a name or label) appears before this one
    for i, name in enumerate(columns):
        values = [r[i] for r in rows]
        if _is_time_column(name, values) and len(set(values)) == len(values):
            time_columns.append(i)
            # A series over time leads with the time column and is in time order. A
            # ranking that carries a date per row (top games by price) leads with the
            # name instead, and gets a bar chart.
            others = [j for j in numeric if j != i]
            in_order = [str(v) for v in values] == sorted(str(v) for v in values)
            if others and in_order and not name_seen:
                return ChartSpec("line", name, columns[others[0]], is_money(columns[others[0]]))
        elif i not in numeric and not _ID.search(name):
            name_seen = True

    if len(rows) > MAX_BAR_ROWS:
        return None
    # The value to chart: a number that isn't a time axis, preferring one whose name
    # doesn't read as a date part (sale_year is a label, tickets is a value).
    values = [j for j in numeric if j not in time_columns]
    values.sort(key=lambda j: (bool(_TIME_NAME.search(columns[j])), columns[j] != order_by))
    if not values or _TIME_NAME.search(columns[values[0]]):
        return None
    y = columns[values[0]]
    for i, name in enumerate(columns):
        values = [r[i] for r in rows]
        if i in numeric or i in time_columns or _ID.search(name):
            continue
        if not all(isinstance(v, str) for v in values):
            continue
        if len(set(values)) == len(values):
            return ChartSpec("bar", name, y, is_money(y))
        # Repeated names (two games against the same opponent): add the date.
        if time_columns:
            return ChartSpec("bar", name, y, is_money(y), x_with=columns[time_columns[0]])
    return None
