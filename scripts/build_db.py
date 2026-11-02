"""Build data/tickit.db from the raw TICKIT files in data/raw/.

Steps, in order:
  1. Parse the raw files (issue #4: line endings, delimiters, NULL markers, timestamp formats).
  2. Fix data defects (issues #1-#3 and #10 in the README).
  3. Shift every date forward a whole number of weeks, from 2008 to 2025.
  4. Add demo data: Barclays Center and Brooklyn Nets home games (see demo_data.py).
  5. Write SQLite using schema.sql (tables) and views.sql (the agent-facing views),
     then run consistency checks.

The raw files are never modified. Run from the repository root:
    python scripts/build_db.py
"""

import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from demo_data import add_demo_data

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "tickit.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
VIEWS_PATH = Path(__file__).resolve().parent / "views.sql"

SOURCE_START = date(2008, 1, 1)
DATA_START = date(2025, 1, 7)  # a Tuesday, like 2008-01-01
SHIFT = DATA_START - SOURCE_START
assert SHIFT.days % 7 == 0, "shift must be whole weeks so weekdays are preserved"

NULL_MARKERS = {"", "\\N"}
WEEKDAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
USER_PREFS = ["likesports", "liketheatre", "likeconcerts", "likejazz", "likeclassical",
              "likeopera", "likerock", "likevegas", "likebroadway", "likemusicals"]
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


# ---------------------------------------------------------------------------
# 1. Parse
# ---------------------------------------------------------------------------

def read_raw(name, sep="|"):
    """Yield each record as a list of fields, with NULL markers turned into None.

    Handles CRLF and the stray doubled carriage returns (\\r\\r\\n) by stripping
    all trailing line-ending characters and skipping lines left empty.
    """
    with open(RAW_DIR / name, encoding="utf-8", newline="") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if line:
                yield [None if v in NULL_MARKERS else v for v in line.split(sep)]


def _int(v):
    return None if v is None else int(v)


def _bool(v):
    return None if v is None else int(v == "TRUE")


def load_tickit():
    """Parse every raw file into lists of typed row dicts."""
    raw_dates = {int(r[0]): date.fromisoformat(r[1]) for r in read_raw("date2008_pipe.txt")}

    category = [
        {"catid": int(r[0]), "catgroup": r[1], "catname": r[2], "catdesc": r[3]}
        for r in read_raw("category_pipe.txt")
    ]
    venue = [
        {"venueid": int(r[0]), "venuename": r[1], "venuecity": r[2], "venuestate": r[3],
         "venueseats": _int(r[4]), "is_demo": 0}
        for r in read_raw("venue_pipe.txt")
    ]
    users = [
        {"userid": int(r[0]), "username": r[1], "firstname": r[2], "lastname": r[3],
         "city": r[4], "state": r[5], "email": r[6], "phone": r[7],
         **{col: _bool(v) for col, v in zip(USER_PREFS, r[8:18])}}
        for r in read_raw("allusers_pipe.txt")
    ]
    event = [
        {"eventid": int(r[0]), "venueid": int(r[1]), "catid": int(r[2]),
         "event_date": raw_dates[int(r[3])], "eventname": r[4],
         "starttime": datetime.fromisoformat(r[5]), "is_demo": 0}
        for r in read_raw("allevents_pipe.txt")
    ]
    listing = [
        {"listid": int(r[0]), "sellerid": int(r[1]), "eventid": int(r[2]),
         "numtickets": int(r[4]), "priceperticket": float(r[5]), "totalprice": float(r[6]),
         "listtime": datetime.fromisoformat(r[7]), "is_demo": 0}
        for r in read_raw("listings_pipe.txt")
    ]
    sales = [
        {"salesid": int(r[0]), "listid": int(r[1]), "sellerid": int(r[2]), "buyerid": int(r[3]),
         "eventid": int(r[4]), "qtysold": int(r[6]), "pricepaid": float(r[7]),
         "commission": float(r[8]),
         # saletime is M/D/YYYY in the raw file; every other timestamp is ISO.
         "saletime": datetime.strptime(r[9], "%m/%d/%Y %H:%M:%S"), "is_demo": 0}
        for r in read_raw("sales_tab.txt", sep="\t")
    ]
    # listing.dateid and sales.dateid always equal the date of listtime / saletime
    # (checked when profiling), so they are recomputed from the timestamps on write.
    return {"category": category, "venue": venue, "users": users,
            "event": event, "listing": listing, "sales": sales}



# ---------------------------------------------------------------------------
# 2. Fix data defects
# ---------------------------------------------------------------------------

def fix_defects(tables):
    # #1: starttime's date part is wrong for some events; the event's dateid is right.
    for e in tables["event"]:
        e["starttime"] = datetime.combine(e.pop("event_date"), e["starttime"].time())

    # #2: events reference venue IDs that are missing from venue.
    known = {v["venueid"] for v in tables["venue"]}
    for venueid in sorted({e["venueid"] for e in tables["event"]} - known):
        tables["venue"].append({"venueid": venueid, "venuename": "Unknown venue", "venuecity": None,
                                "venuestate": None, "venueseats": None, "is_demo": 0})

    # #3: a capacity of 0 means unknown, not zero.
    for v in tables["venue"]:
        if v["venueseats"] == 0:
            v["venueseats"] = None

    # #10: the raw date table's weekdays are off by one in Jan-Feb (Feb 29 is missing).
    # Handled by rebuilding the calendar from real dates in build_calendar().


# ---------------------------------------------------------------------------
# 3. Shift dates
# ---------------------------------------------------------------------------

def shift_dates(tables):
    for e in tables["event"]:
        e["starttime"] += SHIFT
    for l in tables["listing"]:
        l["listtime"] += SHIFT
    for s in tables["sales"]:
        s["saletime"] += SHIFT


def build_calendar(start, end):
    """One row per day from start to end inclusive, with all fields computed from the date."""
    days = []
    day = start
    while day <= end:
        jan1 = date(day.year, 1, 1)
        days.append({
            "dateid": dateid(day),
            "caldate": day.isoformat(),
            "day": WEEKDAYS[day.weekday()],
            # Weeks start Monday; week 1 is the (possibly partial) week containing Jan 1.
            "week": (day.timetuple().tm_yday - 1 + jan1.weekday()) // 7 + 1,
            "month": MONTHS[day.month - 1],
            "qtr": (day.month - 1) // 3 + 1,
            "year": day.year,
            "holiday": int(day in us_holidays(day.year)),
        })
        day += timedelta(days=1)
    return days


def us_holidays(year):
    """The holidays flagged in TICKIT's original calendar, recomputed for any year."""
    def nth_weekday(month, weekday, n):
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))

    last_may = date(year, 5, 31)
    return {
        date(year, 1, 1),                                    # New Year's Day
        last_may - timedelta(days=last_may.weekday()),       # Memorial Day
        date(year, 7, 4),                                    # Independence Day
        nth_weekday(9, 0, 1),                                # Labor Day
        nth_weekday(11, 3, 4),                               # Thanksgiving
        date(year, 12, 25),                                  # Christmas Day
        date(year, 12, 26),                                  # Day after Christmas
    }


def dateid(day):
    return day.year * 10000 + day.month * 100 + day.day


# ---------------------------------------------------------------------------
# 5. Write and validate
# ---------------------------------------------------------------------------

def to_db_rows(tables):
    """Format timestamps as ISO text and derive each dateid from its row's timestamp."""
    ts = "%Y-%m-%d %H:%M:%S"
    for e in tables["event"]:
        e["dateid"] = dateid(e["starttime"].date())
        e["starttime"] = e["starttime"].strftime(ts)
    for l in tables["listing"]:
        l["dateid"] = dateid(l["listtime"].date())
        l["listtime"] = l["listtime"].strftime(ts)
    for s in tables["sales"]:
        s["dateid"] = dateid(s["saletime"].date())
        s["saletime"] = s["saletime"].strftime(ts)


def write_db(tables, path):
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    for name in ["category", "venue", "date", "users", "event", "listing", "sales"]:
        rows = tables[name]
        cols = list(rows[0])
        conn.executemany(
            f"INSERT INTO {name} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [tuple(r[c] for c in cols) for r in rows],
        )
    conn.executescript(VIEWS_PATH.read_text(encoding="utf-8"))
    conn.commit()
    return conn


# Each check is a query that must return 0.
CHECKS = {
    "foreign keys resolve": "SELECT COUNT(*) FROM pragma_foreign_key_check",
    "pricepaid = qtysold x priceperticket":
        "SELECT COUNT(*) FROM sales s JOIN listing l USING (listid) "
        "WHERE ABS(s.pricepaid - s.qtysold * l.priceperticket) > 0.005",
    "commission = 15% of pricepaid":
        "SELECT COUNT(*) FROM sales WHERE ABS(commission - ROUND(pricepaid * 0.15, 2)) > 0.005",
    "totalprice = numtickets x priceperticket":
        "SELECT COUNT(*) FROM listing WHERE ABS(totalprice - numtickets * priceperticket) > 0.005",
    "sale matches its listing's event and seller":
        "SELECT COUNT(*) FROM sales s JOIN listing l USING (listid) "
        "WHERE s.eventid <> l.eventid OR s.sellerid <> l.sellerid",
    "no sale after its event day":
        "SELECT COUNT(*) FROM sales s JOIN event e USING (eventid) WHERE s.dateid > e.dateid",
    "no listing after its event day":
        "SELECT COUNT(*) FROM listing l JOIN event e USING (eventid) WHERE l.dateid > e.dateid",
    "event starttime matches its dateid":
        "SELECT COUNT(*) FROM event e JOIN date d USING (dateid) WHERE DATE(e.starttime) <> d.caldate",
    "no venue capacity of 0": "SELECT COUNT(*) FROM venue WHERE venueseats = 0",
    "sales_detail has every sale once":
        "SELECT (SELECT COUNT(*) FROM sales) - (SELECT COUNT(DISTINCT sale_id) FROM sales_detail)",
    "venue labels are unique": "SELECT COUNT(*) - COUNT(DISTINCT venue_label) FROM venues",
    "event_summary totals match sales":
        "SELECT ABS((SELECT SUM(tickets_sold) FROM event_summary) - (SELECT SUM(qtysold) FROM sales))"
        " + ABS((SELECT SUM(revenue) FROM event_summary) - (SELECT SUM(pricepaid) FROM sales))",
    "listing_detail never sells more than listed":
        "SELECT COUNT(*) FROM listing_detail WHERE tickets_unsold < 0",
}


def validate(conn):
    failed = []
    for name, sql in CHECKS.items():
        bad = conn.execute(sql).fetchone()[0]
        print(f"  {'ok  ' if bad == 0 else 'FAIL'} {name}" + (f" ({bad} rows)" if bad else ""))
        if bad:
            failed.append(name)
    return failed


def main():
    print(f"Reading {RAW_DIR.relative_to(ROOT)}")
    tables = load_tickit()
    fix_defects(tables)
    shift_dates(tables)
    calendar_end = DATA_START + timedelta(days=(date(2008, 12, 31) - SOURCE_START).days)
    tables["date"] = build_calendar(DATA_START, calendar_end)
    demo = add_demo_data(tables, calendar_start=DATA_START)
    print(f"Dates shifted by {SHIFT.days // 7} weeks: {DATA_START} to {calendar_end}")
    print(f"Demo data added: {demo['events']} Nets home games, "
          f"{demo['listings']} listings, {demo['sales']} sales")

    to_db_rows(tables)
    conn = write_db(tables, DB_PATH)
    print(f"Wrote {DB_PATH.relative_to(ROOT)}")
    for name in ["category", "venue", "date", "users", "event", "listing", "sales"]:
        print(f"  {name:<9}{conn.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]:>9,}")

    print("Consistency checks")
    failed = validate(conn)
    conn.close()
    if failed:
        sys.exit(f"{len(failed)} check(s) failed")


if __name__ == "__main__":
    main()
