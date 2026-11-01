# NLQ Agent: Natural Language Query over SQL

A natural language query (NLQ) agent that lets a non-technical user ask questions about ticketing data in plain English. The agent turns the question into SQL, runs it against a database, and returns a readable answer together with the SQL it ran.

> **Status:** Draft. This README is being written part by part. **Part 1 (Data)** and **Part 2 (Data preparation)** are below. Later parts (agent design, model choice, interface, error handling, setup) will be added as they are built.

---

## Part 1: Data

### Dataset choice

This project uses the **TICKIT sample database** that Amazon publishes for Amazon Redshift.

- **Documentation:** [Amazon Redshift: Sample database](https://docs.aws.amazon.com/redshift/latest/dg/c_sampledb.html)
- **Source file:** [`tickitdb.zip`](https://docs.aws.amazon.com/redshift/latest/gsg/samples/tickitdb.zip) (about 11 MB zipped, 39 MB unzipped)
- **In this repo:** `data/raw/`. The files are unmodified, byte for byte, from the zip.

TICKIT models one year (calendar 2008) of activity on a fictional ticket resale website. Sellers list tickets to concerts, shows and sporting events, and buyers purchase them.

### Why TICKIT

| Requirement | How TICKIT meets it |
|---|---|
| Relevant to sports, entertainment or ticketing | It is a ticketing dataset built around venues, events, event categories and ticket sales, which is the same shape of data a venue operator works with. |
| Supports meaningful multi-table queries | It is a star schema with 2 fact tables and 5 dimension tables. Ordinary business questions, such as "revenue by category" (sales → event → category), need real joins. That is a good test of the agent's SQL. |
| Clean and well documented | Amazon documents every column. In the data, every foreign key resolves except a small gap in `venue` (see [Known data issues](#known-data-issues)). There are no duplicate primary keys. |
| Realistic volume | About 424,000 rows in total (172K sales, 192K listings). That is large enough for aggregations to be meaningful and small enough to run locally in SQLite. |
| Answers typical business questions | It has the fields needed for tickets sold, average ticket price by venue or event, and revenue by category. |
| Reproducible | It is a public, stable download. A reviewer can check it against Amazon's original. |

### Alternatives considered

| Dataset | Why it was not chosen |
|---|---|
| [Gametime dataset](https://huggingface.co/datasets/rebrowser/gametime-dataset) (Hugging Face) | Real scraped events, venues, performers and listings. It has asking prices but **no completed sales**, so it cannot answer "tickets sold" or "revenue" questions. |
| [SeatGeek dataset](https://awesome.ecosyste.ms/projects/github.com%2Frebrowser%2Fseatgeek-dataset) | The same limitation as Gametime: listings only, no sales. |
| NBA SQLite database (Kaggle) | Real games, teams and attendance. It has **no ticket prices or revenue**, and downloading it requires a Kaggle account. |
| [Chinook](https://www.sqlitetutorial.net/?p=89) | Clean and widely used, but it is a digital music store, not ticketing. |
| Fully synthetic data | Possible, but it would mean designing and generating every table ourselves, and the data would be only as realistic as the generator. |

Real ticket sales data from any particular venue or team is private, so no public dataset can stand in for it exactly. TICKIT was the closest public match in both schema and domain.

### Schema

```
                 ┌──────────┐
                 │ category │
                 └────┬─────┘
                      │ catid
┌───────┐ venueid ┌───┴───┐ dateid ┌──────┐
│ venue ├─────────┤ event ├────────┤ date │
└───────┘         └───┬───┘        └──┬───┘
                      │ eventid       │ dateid
                 ┌────┴────┐          │
                 │ listing ├──────────┤
                 └────┬────┘          │
                      │ listid        │
┌───────┐ sellerid ┌──┴────┐          │
│ users ├──────────┤ sales ├──────────┘
└───────┘ buyerid  └───────┘
```

`sales` and `listing` are the fact tables. The other five are dimensions. `listing` and `sales` also link to `users` (seller and buyer).

| Table | Source file | Rows | Columns |
|---|---|---:|---|
| `venue` | `venue_pipe.txt` | 202 | `venueid`, `venuename`, `venuecity`, `venuestate`, `venueseats` |
| `category` | `category_pipe.txt` | 11 | `catid`, `catgroup` (Sports / Shows / Concerts), `catname` (e.g. NBA, Musicals, Pop), `catdesc` |
| `date` | `date2008_pipe.txt` | 365 | `dateid`, `caldate`, `day`, `week`, `month`, `qtr`, `year`, `holiday` |
| `event` | `allevents_pipe.txt` | 8,798 | `eventid`, `venueid`, `catid`, `dateid`, `eventname`, `starttime` |
| `listing` | `listings_pipe.txt` | 192,497 | `listid`, `sellerid`, `eventid`, `dateid`, `numtickets`, `priceperticket`, `totalprice`, `listtime` |
| `sales` | `sales_tab.txt` | 172,456 | `salesid`, `listid`, `sellerid`, `buyerid`, `eventid`, `dateid`, `qtysold`, `pricepaid`, `commission`, `saletime` |
| `users` | `allusers_pipe.txt` | 49,990 | `userid`, `username`, `firstname`, `lastname`, `city`, `state`, `email`, `phone`, plus 10 preference flags (`likesports`, `liketheatre`, `likeconcerts`, `likejazz`, `likeclassical`, `likeopera`, `likerock`, `likevegas`, `likebroadway`, `likemusicals`) |

Column meanings that matter for querying:

- `sales.pricepaid` is the **total** paid for the sale, not the price per ticket. Price per ticket is `pricepaid / qtysold`.
- `sales.commission` is the 15% fee the site takes on each sale.
- `listing` holds tickets offered for sale. 91,413 listings never sold.

### Data profile

Measured directly from the raw files:

| Metric | Value |
|---|---:|
| Date coverage (events and sales) | 2008-01-01 to 2008-12-31 |
| Tickets sold | 345,349 |
| Gross sales (`pricepaid`) | $110,765,431 |
| Commission | $16,614,815 |
| Events by category | Pop 4,998 · Plays 2,000 · Musicals 1,300 · Opera 500 |

### What the data looks like

Highlights from exploring the raw files:

- **The ticket flow.** A seller posts a **listing** (1–30 tickets at $20–$2,500 each). Buyers make **sales** of 1–8 tickets at a time, about 2 on average. One listing can be split across up to 5 separate sales, and 91,413 listings never sell. Tickets are listed 0–60 days before the event (27 days on average).
- **Real names, made-up activity.** Venues, shows and artists are real (Mamma Mia!, Jersey Boys, Jimmy Buffett, Madison Square Garden). The 2008 ticket activity and all user details are generated. User emails are placeholder Latin text, and the most common user "states" are Canadian provinces and territories.
- **Repeat performances.** The 8,798 events are performances of only 576 distinct shows or artists. For example, Mamma Mia! has 92 performances and Jimmy Buffett plays 19 venues. Start times are matinees (14:00–15:00) or evenings (19:00–20:00).
- **Seasonal pattern.** Sales run about 31,000–33,000 tickets a month from March through October, and roughly half that in January and December.
- **Revenue concentration.** Pop brings in the most revenue ($62.4M), followed by Plays ($25.5M), Musicals ($16.3M) and Opera ($6.5M). Broadway theatres dominate venue revenue.
- **Consistency checks that pass:**
  - Every sale's `pricepaid` equals `qtysold × priceperticket` from its listing.
  - `commission` is exactly 15% of `pricepaid` on every sale.
  - Every listing's `totalprice` equals `numtickets × priceperticket`.
  - Every sale's event and seller match its listing.
  - No sale happens after its event (measured by `dateid`).
  - There are no duplicate primary keys.

### Known data issues

| # | Issue | Scale | Type |
|---|---|---|---|
| 1 | `event.starttime` has its date reset to the 1st of the month, while `event.dateid` holds the correct date. Using `starttime` makes sales appear to happen after the event. | 703 events, 6,860 affected sales | **A**: data defect |
| 2 | Events point to venue IDs `12`, `51` and `64`, which do not exist in `venue`. An inner join to `venue` silently drops those sales. | 139 events, 2,777 sales | **A**: data defect |
| 3 | `venueseats` is `0` for venues whose capacity is unknown, which is false, not zero. | 130 of 202 venues (plus 15 NULL) | **A**: data defect |
| 4 | Inconsistent file formats: CRLF line endings, 9 lines ending in a doubled carriage return (`\r\r\n`), pipe vs tab delimiters, `\N` vs empty-string NULLs, and `saletime` in `M/D/YYYY` while every other timestamp is ISO. | All files | **A**: data defect |
| 5 | `sales.pricepaid` is the **total** for the sale, not the price per ticket. "Average ticket price" is `SUM(pricepaid) / SUM(qtysold)`, not `AVG(pricepaid)`. | Every price question | **B**: easy to misread |
| 6 | Two different venues are both named "Lyric Opera House" (Chicago and Baltimore). Grouping by name merges them. | 2 venues, 110 events | **B**: easy to misread |
| 7 | Several events can share a venue on the same day (matinee and evening shows), so venue and date do not identify one event. | 493 venue-days | **B**: easy to misread |
| 8 | The sports categories (MLB, NHL, NFL, NBA, MLS) exist but have no events. | 5 categories | Coverage gap |
| 9 | All activity is in 2008, so relative-date questions ("last month", "in 2024") return nothing. | All data | Coverage gap |
| 10 | The `date` table has no row for Feb 29, 2008 (a leap day), and its `day` (weekday) field is one day off for every date in January and February. For example, Jan 1, 2008 was a Tuesday but is labeled `WE`. | 59 dates | **A**: data defect |

Minor issues that do not affect results: 47 users have the first name misspelled as "acqueline", 5 sales have the same user as buyer and seller, 2,965 sales have a `saletime` a few hours earlier than their listing's `listtime` (always on the same day), and listing IDs have gaps (up to 235,497 for 192,497 rows), which suggests a cut-down sample.

### How we handle the issues: clean the data vs. teach the agent

The issues fall into two kinds, and each kind is handled differently.

**Type A, data defects: fixed in the load step.** These are objectively wrong values with exactly one correct fix, and they are corrected before the agent ever sees the data:

| Issue | Fix |
|---|---|
| #1 Wrong `starttime` dates | Rebuild the date part from `dateid` and keep the time of day |
| #2 Missing venues | Add placeholder rows ("Unknown venue") for IDs 12, 51 and 64 |
| #3 `venueseats = 0` | Store as NULL (unknown) |
| #4 File formats | Normalize line endings, delimiters, NULLs and timestamps to ISO 8601 |
| #10 Wrong weekdays, missing leap day | Rebuild the calendar from the real dates, one row per day with no gaps |

**Type B, correct data that is easy to misread: left as is and taught to the agent.** These values are accurate. They are simply easy to query wrongly. The agent learns about them through its context instead:

- Column descriptions in the schema it receives (for example, "`pricepaid` is the total for the sale; average ticket price is `SUM(pricepaid) / SUM(qtysold)`")
- Query rules (for example, "group venues by `venueid` and show the city alongside the name")
- A few example questions paired with correct SQL that show these patterns

**Why we split it this way:**

1. **It matches how production systems work.** Data engineers clean data when it is loaded, and an agent queries a curated layer it does not own. An NLQ agent in production should never edit the source data, so a wrong value has to be fixed before the agent sees it. A misleading but correct column has to be explained to the agent, because it cannot be "fixed."
2. **Changing correct data would be inventing data.** The two Lyric Opera Houses really are different venues, and matinee and evening shows really do share a day. Renaming or merging them to make queries easier would make the dataset less true.
3. **It tests the agent where it matters.** What makes an NLQ agent useful is prompt engineering and accuracy. "Which events had the highest average ticket price?" is one of the most natural questions to ask of ticketing data, and it walks straight into issue #5. Answering it correctly is a direct demonstration of good context design. Fixing every quirk upstream would leave nothing to show.
4. **It keeps every change transparent and reproducible.** The raw files in `data/raw/` are never edited. Every Type A fix is in the load script and listed here, so anyone can rebuild the database and see exactly what changed and why.

**What we deliberately avoided:** having the agent detect and fix data problems by itself while it answers questions. It sounds appealing, but it is hard to verify, it can give different answers to the same question on different runs, and it hides why a number changed. Known issues are fixed once in the load step or written into the agent's instructions, never guessed at query time.

---

## Part 2: Data preparation

The load step reads the untouched files in `data/raw/` and builds the SQLite database the agent queries, `data/tickit.db`. It applies the Type A fixes from Part 1 and closes the two coverage gaps (#8 and #9).

### Build the database

Requires Python 3.10 or later. It uses only the standard library, so there is nothing to install.

```bash
python scripts/build_db.py
```

The build takes about 10 seconds and is deterministic: every run produces the same database. It ends by running consistency checks and exits with an error if any check fails:

```
Dates shifted by 888 weeks: 2025-01-07 to 2026-01-07
Demo data added: 46 Nets home games, 2847 listings, 2393 sales
...
Consistency checks
  ok   foreign keys resolve
  ok   pricepaid = qtysold x priceperticket
  ok   commission = 15% of pricepaid
  ok   totalprice = numtickets x priceperticket
  ok   sale matches its listing's event and seller
  ok   no sale after its event day
  ok   no listing after its event day
  ok   event starttime matches its dateid
  ok   no venue capacity of 0
```

### What the build does

| Step | What it does | Code |
|---|---|---|
| 1. Parse | Reads each raw file and normalizes the formats (#4): strips CRLF and the stray `\r\r\n` endings, handles pipe and tab delimiters, turns `\N` and empty fields into NULL, turns `TRUE`/`FALSE` into 1/0, and parses `saletime` from `M/D/YYYY` | `load_tickit()` |
| 2. Fix defects | Rebuilds `starttime` from the event's date (#1), adds "Unknown venue" rows for IDs 12, 51 and 64 (#2), stores `venueseats = 0` as NULL (#3) | `fix_defects()` |
| 3. Shift dates | Moves every timestamp forward **888 weeks**, so the data runs from **2025-01-07 to 2026-01-07** instead of 2008 (#9). Rebuilds the `date` table from the real calendar, which fixes the weekdays and adds the missing leap day (#10) | `shift_dates()`, `build_calendar()` |
| 4. Demo data | Adds Barclays Center and Brooklyn Nets home games (#8). See [Demo data](#demo-data-barclays-center-and-the-brooklyn-nets) | `scripts/demo_data.py` |
| 5. Write and check | Creates the tables from `scripts/schema.sql` (primary keys, foreign keys, indexes), loads the rows and runs the consistency checks shown above | `write_db()`, `validate()` |

### Date shift

**Why whole weeks:** Shifting by a whole number of weeks keeps every date on the same weekday. Weekend and weekday patterns stay real, and a Saturday matinee is still on a Saturday. A whole-year shift (2008 to 2025) would move every event to a different weekday.

**Why this range:** 888 weeks is the whole-week shift that puts the data closest to calendar 2025. Nearly all activity is in 2025, so questions like "in 2025" or "in Q3" behave as expected. The cost is that the last week spills into January 2026 (508 sales) and that the data ends 10 months before today, so "last month" still returns nothing. The agent will need to say what date range the data covers when a question falls outside it.

**What changed in the `date` table:**

- `dateid` is now the date as an integer in `YYYYMMDD` form (for example `20250315`). TICKIT's original IDs (1827–2191) were arbitrary and had a gap at the missing leap day. The new IDs are readable and can't collide.
- `day`, `week`, `month`, `qtr` and `year` are computed from the date. Weeks start on Monday, and week 1 is the week that contains January 1, which matches TICKIT's original numbering.
- `holiday` is recomputed for the new dates, using the same holidays TICKIT flagged in 2008: New Year's Day, Memorial Day, Independence Day, Labor Day, Thanksgiving, Christmas Day and the day after Christmas.
- `listing.dateid`, `sales.dateid` and `event.dateid` are derived from `listtime`, `saletime` and `starttime`. This matches the raw data, where they always agree.

### Demo data: Barclays Center and the Brooklyn Nets

The brief's example questions ask about Brooklyn Nets home games and Barclays Center. TICKIT has neither, and none of its sports categories has any events. The build adds:

| Added | Details |
|---|---|
| 1 venue | Barclays Center, Brooklyn, NY, 17,732 seats (`venueid` 310) |
| 46 events | Brooklyn Nets home games (category NBA), named like `Brooklyn Nets vs. Boston Celtics`. They cover the two parts of NBA seasons that fall inside the data range: January–April 2025 (2024–25 season) and late October 2025–January 2026 (2025–26 season). There is one game every 2–6 days, as in a real 41-game home schedule. Weeknight tip-off is 19:30. Weekend games are at 15:30 or 18:00. |
| 2,847 listings, 2,393 sales | Copied from real TICKIT activity (see below) |

**How listings and sales are generated:** Rather than inventing distributions, each game copies the listings and sales of three randomly chosen TICKIT events. Every listing and sale keeps its distance from the event date, its ticket counts, its seller and buyer, and the way a listing splits into separate sales. Only the price changes. The new price is the original price × an opponent demand factor (Knicks 1.8, Lakers 1.7, … Wizards 0.75) × a random ±10%, rounded to whole dollars. Totals and the 15% commission are recalculated, so the generated rows pass every consistency check that TICKIT's own rows pass. Like TICKIT itself, there is no activity before the first day of the data. Listings that would fall earlier are dropped.

**How to tell demo rows apart:** `venue`, `event`, `listing` and `sales` each have an `is_demo` column, which is `1` for generated rows and `0` for Amazon's originals. Filtering on `is_demo = 0` gives the original TICKIT data (date-shifted and cleaned). The generator is seeded, so the demo data is the same on every build.

**Known limitations of the demo data:**

- A Nets game averages about 107 tickets sold, compared with about 41 for a TICKIT event. That reflects higher demand, but both are far below a real arena's sales. TICKIT is a small resale marketplace, not a primary ticketing system.
- Prices follow TICKIT's very wide range ($20–$2,500 per ticket) and depend on which events were copied, so one game's average price can be far from another's even against similar opponents.
- The schedule, opponents and prices are made up. Only the team, the venue and its capacity are real.

### Result

| Table | Rows | Of which demo |
|---|---:|---:|
| `category` | 11 | 0 |
| `venue` | 206 | 1 |
| `date` | 366 | n/a |
| `users` | 49,990 | 0 |
| `event` | 8,844 | 46 |
| `listing` | 195,344 | 2,847 |
| `sales` | 174,849 | 2,393 |

The three example questions in the brief now return data:

| Question | Result (abbreviated) |
|---|---|
| Tickets sold for Brooklyn Nets home games, by month | 83 (Jan 2025), 398, 993, 452 (Apr), 192 (Oct), 1,130, 1,308 (Dec), 254 (Jan 2026) |
| Barclays Center events with the highest average ticket price in 2025 | Nets vs. 76ers (Feb 5) $1,149.71, Nets vs. Rockets (Jan 20) $1,007.80, Nets vs. Celtics (Feb 25) $695.92 |
| Top 5 event categories by total revenue | Pop $62.4M, Plays $25.5M, Musicals $16.3M, Opera $6.5M, NBA $1.6M |

As written in the brief, the first two questions say "last month" and "in 2024", and both still fall outside the data range. Handling them is the agent's job: it should say what range the data covers rather than return an empty answer.

### Repository layout (so far)

```
NLQ_Agent/
├── data/
│   ├── raw/          # Unmodified TICKIT source files (committed)
│   └── tickit.db     # Built by scripts/build_db.py (git-ignored)
├── scripts/
│   ├── build_db.py   # Load step: parse, fix, shift dates, add demo data, write and check
│   ├── demo_data.py  # Barclays Center and Brooklyn Nets demo data generator
│   └── schema.sql    # SQLite schema for data/tickit.db
├── .gitattributes    # Keeps raw files byte-identical (no line-ending conversion)
├── .gitignore
└── README.md
```
