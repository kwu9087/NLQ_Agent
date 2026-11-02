"""Generate demo data: Barclays Center and Brooklyn Nets home games.

TICKIT has no sports events and no Barclays Center, so the example questions
in the brief ("tickets sold for Brooklyn Nets home games", "events at Barclays
Center") would return nothing. This module adds them.

Listings and sales are not invented from scratch. Each game copies the
listings and sales of a few real TICKIT events (its "templates"), moved to the
game's date and repriced for the opponent. That keeps every TICKIT pattern
intact: listing sizes, how listings split into sales, how far ahead tickets are
listed and sold, and the price, total and commission rules.

Every row added here has is_demo = 1. The output is deterministic for a given
seed.
"""

import random
from datetime import date, datetime, time, timedelta

BARCLAYS = {
    "venuename": "Barclays Center",
    "venuecity": "Brooklyn",
    "venuestate": "NY",
    "venueseats": 17732,  # basketball capacity
}
NBA_CATID = 4

# The parts of two Nets seasons that fall inside the data window
# (Jan 2025 to Jan 2026, after the date shift).
SEASON_WINDOWS = [
    (date(2025, 1, 7), date(2025, 4, 13)),   # 2024-25 regular season, second half
    (date(2025, 10, 22), date(2026, 1, 6)),  # 2025-26 regular season, first half
]

# Opponent -> ticket demand multiplier. Rivals and marquee teams sell higher.
OPPONENTS = {
    "New York Knicks": 1.8, "Los Angeles Lakers": 1.7, "Golden State Warriors": 1.6,
    "Boston Celtics": 1.5, "Philadelphia 76ers": 1.3, "Milwaukee Bucks": 1.2,
    "Miami Heat": 1.2, "Denver Nuggets": 1.2, "Dallas Mavericks": 1.2,
    "Phoenix Suns": 1.1, "Oklahoma City Thunder": 1.1, "Cleveland Cavaliers": 1.1,
    "Minnesota Timberwolves": 1.0, "Los Angeles Clippers": 1.0, "Chicago Bulls": 1.0,
    "Atlanta Hawks": 0.9, "Indiana Pacers": 0.9, "Orlando Magic": 0.9,
    "Sacramento Kings": 0.9, "Memphis Grizzlies": 0.9, "New Orleans Pelicans": 0.9,
    "Houston Rockets": 0.9, "Toronto Raptors": 0.85, "San Antonio Spurs": 0.85,
    "Utah Jazz": 0.8, "Portland Trail Blazers": 0.8, "Charlotte Hornets": 0.8,
    "Detroit Pistons": 0.8, "Washington Wizards": 0.75,
}

TEMPLATES_PER_GAME = 3   # a Nets game draws about 3x the volume of a typical TICKIT event
MIN_TICKET_PRICE = 20    # TICKIT's lowest list price


def add_demo_data(tables, calendar_start, seed=2025):
    """Append Barclays Center, Nets home games and their listings and sales to `tables`.

    `tables` holds TICKIT rows after defect fixes and the date shift. Listings
    that would fall before `calendar_start` are dropped, together with their
    sales, matching how TICKIT itself has no activity before its first day.
    """
    rng = random.Random(seed)

    venueid = max(v["venueid"] for v in tables["venue"]) + 1
    tables["venue"].append({"venueid": venueid, **BARCLAYS, "is_demo": 1})

    templates = _template_events(tables, calendar_start)
    listings_by_event = _group(tables["listing"], "eventid")
    sales_by_listing = _group(tables["sales"], "listid")

    next_eventid = max(e["eventid"] for e in tables["event"]) + 1
    next_listid = max(l["listid"] for l in tables["listing"]) + 1
    next_salesid = max(s["salesid"] for s in tables["sales"]) + 1

    new_events, new_listings, new_sales = [], [], []
    opponents = _opponent_cycle(rng)
    for game_day in _schedule(rng):
        opponent = next(opponents)
        starttime = datetime.combine(game_day, _tipoff(game_day, rng))
        new_events.append({
            "eventid": next_eventid,
            "venueid": venueid,
            "catid": NBA_CATID,
            "eventname": f"Brooklyn Nets vs. {opponent}",
            "starttime": starttime,
            "is_demo": 1,
        })

        price_factor = OPPONENTS[opponent]
        for template in rng.sample(templates, TEMPLATES_PER_GAME):
            # Move the template's activity so it sits the same distance before this game.
            offset = game_day - template["starttime"].date()
            for lst in listings_by_event[template["eventid"]]:
                listtime = lst["listtime"] + offset
                if listtime.date() < calendar_start:
                    continue
                price = max(MIN_TICKET_PRICE,
                            round(lst["priceperticket"] * price_factor * rng.uniform(0.9, 1.1)))
                new_listings.append({
                    "listid": next_listid,
                    "sellerid": lst["sellerid"],
                    "eventid": next_eventid,
                    "numtickets": lst["numtickets"],
                    "priceperticket": float(price),
                    "totalprice": float(lst["numtickets"] * price),
                    "listtime": listtime,
                    "is_demo": 1,
                })
                for sale in sales_by_listing.get(lst["listid"], []):
                    pricepaid = sale["qtysold"] * price
                    new_sales.append({
                        "salesid": next_salesid,
                        "listid": next_listid,
                        "sellerid": sale["sellerid"],
                        "buyerid": sale["buyerid"],
                        "eventid": next_eventid,
                        "qtysold": sale["qtysold"],
                        "pricepaid": float(pricepaid),
                        "commission": round(pricepaid * 0.15, 2),
                        "saletime": sale["saletime"] + offset,
                        "is_demo": 1,
                    })
                    next_salesid += 1
                next_listid += 1
        next_eventid += 1

    tables["event"].extend(new_events)
    tables["listing"].extend(new_listings)
    tables["sales"].extend(new_sales)
    return {"events": len(new_events), "listings": len(new_listings), "sales": len(new_sales)}


def _template_events(tables, calendar_start):
    """TICKIT events with sales whose listing window is not cut off by the start of the data."""
    with_sales = {s["eventid"] for s in tables["sales"]}
    earliest = calendar_start + timedelta(days=61)  # listings run up to 60 days before an event
    return sorted(
        (e for e in tables["event"] if e["eventid"] in with_sales and e["starttime"].date() >= earliest),
        key=lambda e: e["eventid"],
    )


def _schedule(rng):
    """Home game dates: one every 2 to 6 days through each season window (about 41 a season)."""
    for start, end in SEASON_WINDOWS:
        day = start + timedelta(days=rng.randint(0, 2))
        while day <= end:
            yield day
            day += timedelta(days=rng.choice([2, 2, 3, 3, 4, 5, 6]))


def _tipoff(day, rng):
    if day.weekday() >= 5:  # weekend: matinee or early evening
        return rng.choice([time(15, 30), time(18, 0)])
    return time(19, 30)


def _opponent_cycle(rng):
    """Every opponent once, in random order, before any repeats."""
    while True:
        order = list(OPPONENTS)
        rng.shuffle(order)
        yield from order


def _group(rows, key):
    groups = {}
    for row in rows:
        groups.setdefault(row[key], []).append(row)
    return groups
