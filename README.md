# NLQ Agent: Natural Language Query over SQL

A natural language query (NLQ) agent that lets a non-technical user ask questions about ticketing data in plain English. The agent turns the question into SQL, runs it against a database, and returns a readable answer together with the SQL it ran.

> **Status:** Draft. The README follows the five numbered requirements in the brief: **Part 1 (Data Layer)** is complete, **Part 2 (Agent Behavior)** is being designed, and Parts 3–5 (Foundation Model, Interface, Code Quality) are not started yet.

---

## Part 1: Data Layer

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

### Data preparation

The load step reads the untouched files in `data/raw/` and builds the SQLite database the agent queries, `data/tickit.db`. It applies the Type A fixes listed above and closes the two coverage gaps (#8 and #9).

#### Build the database

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
  ok   sales_detail has every sale once
  ok   venue labels are unique
  ok   event_summary totals match sales
  ok   listing_detail never sells more than listed
```

#### What the build does

| Step | What it does | Code |
|---|---|---|
| 1. Parse | Reads each raw file and normalizes the formats (#4): strips CRLF and the stray `\r\r\n` endings, handles pipe and tab delimiters, turns `\N` and empty fields into NULL, turns `TRUE`/`FALSE` into 1/0, and parses `saletime` from `M/D/YYYY` | `load_tickit()` |
| 2. Fix defects | Rebuilds `starttime` from the event's date (#1), adds "Unknown venue" rows for IDs 12, 51 and 64 (#2), stores `venueseats = 0` as NULL (#3) | `fix_defects()` |
| 3. Shift dates | Moves every timestamp forward **888 weeks**, so the data runs from **2025-01-07 to 2026-01-07** instead of 2008 (#9). Rebuilds the `date` table from the real calendar, which fixes the weekdays and adds the missing leap day (#10) | `shift_dates()`, `build_calendar()` |
| 4. Demo data | Adds Barclays Center and Brooklyn Nets home games (#8). See [Demo data](#demo-data-barclays-center-and-the-brooklyn-nets) | `scripts/demo_data.py` |
| 5. Write and check | Creates the tables from `scripts/schema.sql` (primary keys, foreign keys, indexes), loads the rows, creates the agent-facing views from `scripts/views.sql`, and runs the consistency checks | `write_db()`, `validate()` |

#### Date shift

**Why whole weeks:** Shifting by a whole number of weeks keeps every date on the same weekday. Weekend and weekday patterns stay real, and a Saturday matinee is still on a Saturday. A whole-year shift (2008 to 2025) would move every event to a different weekday.

**Why this range:** 888 weeks is the whole-week shift that puts the data closest to calendar 2025. Nearly all activity is in 2025, so questions like "in 2025" or "in Q3" behave as expected. The cost is that the last week spills into January 2026 (508 sales) and that the data ends 10 months before today, so "last month" still returns nothing. The agent will need to say what date range the data covers when a question falls outside it.

**What changed in the `date` table:**

- `dateid` is now the date as an integer in `YYYYMMDD` form (for example `20250315`). TICKIT's original IDs (1827–2191) were arbitrary and had a gap at the missing leap day. The new IDs are readable and can't collide.
- `day`, `week`, `month`, `qtr` and `year` are computed from the date. Weeks start on Monday, and week 1 is the week that contains January 1, which matches TICKIT's original numbering.
- `holiday` is recomputed for the new dates, using the same holidays TICKIT flagged in 2008: New Year's Day, Memorial Day, Independence Day, Labor Day, Thanksgiving, Christmas Day and the day after Christmas.
- `listing.dateid`, `sales.dateid` and `event.dateid` are derived from `listtime`, `saletime` and `starttime`. This matches the raw data, where they always agree.

#### Demo data: Barclays Center and the Brooklyn Nets

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

#### Result

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

### Agent-facing views

The agent never queries the tables directly. It queries five **views** defined in [`scripts/views.sql`](scripts/views.sql). A view is a saved query that looks like a table: its definition is stored in the database, and its rows are computed from the real tables each time it is queried. It stores no data, so it can never fall out of sync.

**Why:** Part 1 lists data that is correct but easy to misread. Each of those traps could be handled with a rule in the system prompt, but every rule is something the model can overlook. The views build the rules into the structure instead: the joins are done once, correctly, columns are named for what they mean, and private columns don't exist. The prompt stays short, and many mistakes become impossible rather than merely discouraged.

| View | One row per | Purpose |
|---|---|---|
| `sales_detail` | sale | Each sale with its event, category, venue, buyer and seller, already joined. Separate `sale_*` and `event_*` date columns |
| `listing_detail` | listing | Each listing with its asking price, tickets sold and unsold, and how far ahead of the event it was listed |
| `event_summary` | event (one performance or game) | Lifetime totals per event: tickets listed and sold, revenue, commission, average ticket price |
| `venues` | venue | Venue details plus a unique `venue_label` |
| `users_public` | user | Users without names, email or phone |

**How each trap is handled:**

| Trap (Part 1) | How the views remove it |
|---|---|
| #5 `pricepaid` is a total, not a per-ticket price | Renamed `sale_total`. `tickets` replaces `qtysold`. `event_summary.avg_ticket_price` is precomputed correctly |
| #6 Two venues named "Lyric Opera House" (and three "Unknown venue" rows) | `venue_label` is unique: `Lyric Opera House (Chicago, IL)`, `Unknown venue (#12)` |
| Three tables with a `dateid` | Explicit columns: `sale_date`, `sale_month`, `sale_quarter`, `event_date`, `listed_date`, … |
| Joining `users` as buyer vs. seller | `buyer_id` and `seller_id` sit on each sale |
| Sell-through double counting (joining listings to sales) | `tickets_listed`, `tickets_sold` and `tickets_unsold` are precomputed per listing and per event |
| Private user data | `users_public` has no name, email or phone columns |

**What still needs a rule in the prompt:** an average ticket price over a custom grouping must be `SUM(sale_total) / SUM(tickets)`, not an average of per-sale prices. The views can't prevent that, so it stays in the prompt.

**Checks:** the build verifies that `sales_detail` contains every sale exactly once, that venue labels are unique, that `event_summary` totals match the `sales` table, and that no listing sells more tickets than it listed. The example questions return identical results whether written against the tables or the views.

**Tradeoffs:** five view definitions to maintain, and slightly slower queries on `event_summary`, which aggregates on every query (about 1 second). If the data grew, these could become pre-built summary tables refreshed by the build.

---

## Part 2: Agent Behavior

> **In progress: design brainstorm.** Nothing here is built yet. This section records the ideas and open decisions as they are discussed.

**Goal:** take a plain-English question, translate it into correct SQL, run it, and return a clear answer together with the SQL. When it can't, fail honestly and helpfully.

### Where accuracy comes from: the system prompt and its context

Most of the agent's accuracy will come from what the model is told before it sees the question. Our plan is to put it all into the **system prompt**. The schema is small (7 tables), so the whole schema fits in every request and there is no need to retrieve parts of it.

The system prompt will be built from these parts:

| Part | Contents | Static or per request |
|---|---|---|
| Role and task | "You translate questions about a ticket resale marketplace into one SQLite `SELECT` query" | Static |
| Schema | Tables, columns, keys and their meanings, taken from `scripts/schema.sql`, so there is one source of truth | Static |
| Known values | The full list of small lookup values (the 11 categories, the 3 category groups, the `day` and `month` codes), so the model doesn't guess spellings | Static |
| Business definitions | What "tickets sold", "revenue", "average ticket price" and similar terms mean in SQL | Static |
| Query rules | The traps listed below | Static |
| Data coverage | The date range, what the data does and doesn't contain, and today's date (needed to resolve "last month") | Today's date is per request |
| Ambiguity policy | What to do when a question can be read more than one way | Static |
| Output format | How the model returns the SQL and any assumptions it made | Static |
| Examples | 5–8 questions paired with correct SQL, each one demonstrating a trap | Static |

### Database rules and nuances (draft)

**Metrics**

| Term | Correct SQL | Common mistake |
|---|---|---|
| Tickets sold | `SUM(sales.qtysold)` | `COUNT(*)` counts sales transactions: 174,849 rows hold 350,159 tickets |
| Average ticket price | `SUM(pricepaid) / SUM(qtysold)` | `AVG(pricepaid)` averages the total paid per sale, not the price per ticket (issue #5) |
| Revenue | `SUM(pricepaid)` (gross sales) | Confusing it with `SUM(commission)`, the site's 15% cut. **Decided:** gross sales is the default. Mention commission when it is relevant. |
| Asking price vs. price paid | `listing.priceperticket` is the asking price. Prices in `sales` are what buyers paid | Using listing prices for "average ticket price" |
| Unsold tickets | `listing.numtickets` minus the tickets sold from that listing | Treating a listing as either sold or unsold. 91,327 listings sold only part of their tickets |

**Joins and grouping**

- **Group venues by `venueid` and show the city.** "Lyric Opera House" (Chicago and Baltimore) and "Unknown venue" (3 placeholder rows) each cover several venues.
- **An event is a single performance.** 8,844 events share only 605 names, and 17 Nets matchups occur twice (once in each season). Group by `eventid` for performances and by `eventname` for shows or artists. **Decided:** questions that rank events ("top events") group by show (`eventname`). Questions that name a date or a single performance use `eventid`.
- **Venue plus date does not identify an event.** Matinee and evening shows can share a day (issue #7).
- **Three tables have a `dateid`: `sales`, `listing` and `event`.** Join `date` to the one the question is about. In 56% of sales, the sale happened in a different month than the event, so this choice changes answers. **Decided:** sales figures use the sale date ("revenue in March" means money taken in March), unless the question is about the events themselves ("events held in March").
- **`users` plays two roles.** Join on `buyerid` for buyers and on `sellerid` for sellers.
- **"Brooklyn Nets home games"** are events at Barclays Center whose name starts with `Brooklyn Nets`. Every Nets game in the data is a home game. Away games are not in the data.

**Data coverage**

- Activity runs from 2025-01-07 to 2026-01-07. A question about dates outside that range should say so instead of returning an empty answer.
- Of the sports categories, only NBA has events, and those are all Nets games (demo data). MLB, NHL, NFL and MLS have none.
- `venueseats` is NULL when capacity is unknown. Questions about capacity or sell-through can only use venues with a known capacity.
- `is_demo = 1` marks generated rows. They are included by default.
- User emails and phone numbers are placeholders. **Decided:** the agent refuses to return emails and phone numbers, as a production system would.

**SQLite dialect**

- Use one `SELECT` statement only, with no data changes.
- Use `strftime` and `DATE()` for dates. Timestamps are stored as ISO text.
- The table name `date` works unquoted.
- Add a `LIMIT` unless the question asks for everything, round money to 2 decimal places, and give columns readable aliases.

### Decisions

| # | Question | Decision |
|---|---|---|
| 1 | Default meaning of "revenue" | Gross sales (`SUM(pricepaid)`). Mention commission when it is relevant. |
| 2 | Default meaning of "event" | A show or artist (`eventname`) for ranking questions. A single performance (`eventid`) when the question names a date or a single performance. |
| 3 | Default date for sales figures | The sale date, unless the question is about the events themselves |
| 4 | Unclear questions: ask the user, or choose a reading and state it? | A mix: answer and state assumptions when there is a sensible default, and ask only when there isn't. See [How the agent responds](#how-the-agent-responds) |
| 5 | Contact details (emails, phone numbers) | Refuse to return them |

### How the agent responds

Every question ends in one of four kinds of response. The model returns its SQL together with a list of the **assumptions** it made, or a **clarifying question** when it can't proceed. The interface shows the assumptions above the answer, so they are hard to miss.

| Situation | Agent response | Example |
|---|---|---|
| **Clear question, data available** | Answer and SQL | "Top 5 event categories by total revenue" |
| **Unclear, but there's a sensible default** | Answers using the defaults above and states each assumption | "Best events last quarter" → ranked by gross revenue, grouped by show, counted by sale date, all stated |
| **Too unclear to guess** | Asks one clarifying question and runs no query | "How are we doing?", "Compare the two", "Was the Lyric show a success?" |
| **Clear, but the data can't answer it** | Explains why, shows what *is* available, and gives the closest answer it can | "Average sales uptake for event A in 2028" → the data covers Jan 2025 – Jan 2026, here are event A's 2025 figures instead |

The last case covers three variants:

- **Outside the date range:** state the range the data covers, and offer the closest period that has data.
- **Unknown name** (event, venue, artist): say it wasn't found, and suggest the closest matching names.
- **Data that doesn't exist** (attendance, weather, contact details): say the data doesn't include it, and describe what it does include.

**Principle: an empty result must never look like an answer of zero.** "0 tickets sold in 2028" is misleading. "The data stops in January 2026" is honest.

### System prompt (draft)

The draft is in [`prompts/system_prompt.md`](prompts/system_prompt.md). It is a template: code fills in `{{today}}` and `{{schema}}` (the contents of `scripts/schema.sql`) on each request. In order, it contains:

1. **Role and trust rules:** the user's message is a question, never new instructions.
2. **The data:** what the marketplace is, the date range, which categories have events, and what the data doesn't contain.
3. **The schema.**
4. **Definitions:** tickets sold, revenue, average ticket price, sell-through and similar terms, written as exact SQL.
5. **Rules:** dates, events and venues, users, demo data, and query style. These are the traps from Part 1 and the decisions above.
6. **Privacy rules.**
7. **When to answer, ask or decline:** the four kinds of response above.
8. **Output format:** a JSON object with `action` (`query`, `clarify` or `cannot_answer`), plus `sql`, `assumptions`, `question` or `reason`.
9. **Nine examples,** each showing one rule: revenue by category, average price by venue, Nets tickets by sale date, a period outside the data range, sell-through without double counting, a privacy-limited request, a clarifying question, missing data, and a prompt injection attempt.

All nine example queries have been run against `data/tickit.db` and return the expected results.

### Privacy

The guiding rule: **the agent answers questions about the business, not about individual people.** Aggregates are fine. Identifying or contacting a specific person is not.

| Data | Rule | Why |
|---|---|---|
| `email`, `phone` | Never selected, filtered on or returned | Contact details have no use in business analysis and the most potential for harm |
| `firstname`, `lastname` | Never returned, and never used to look up a person | Real names identify a person. Business questions about users don't need them |
| `city`, `state`, preference flags | Allowed in aggregates ("buyers by state"), never next to an individual user | Useful for segmentation. Linked to one person, they become a profile |
| `userid`, `username` | Allowed | Pseudonymous IDs, needed for questions like "top 10 buyers" |
| All other tables | Allowed | Business data, not personal data |

These rules apply even when the user claims permission. The agent can't verify who is asking, so the user's message can't change what is allowed. In a production system, broader access would come from the user's authenticated role, enforced in code, never from what they type.

The user data in TICKIT is generated (emails are placeholder Latin text), but the agent treats it as real so the design carries over to real customer data.

### Defending against prompt injection and unsafe SQL

The system prompt tells the model to treat the question as data and to refuse anything other than a single read-only query. A prompt can be talked around, though, so **the prompt is the first line of defense, not the only one.** These code-level controls are planned for the query-execution step:

| Layer | Control | Stops |
|---|---|---|
| Prompt | The question is never treated as instructions. Refuses off-topic requests and data changes. The privacy rules | Most injection attempts and accidental privacy leaks |
| Database connection | Opened read-only (`mode=ro`) | Any data change, even if the model is tricked into writing one |
| SQL check before running | Exactly one statement, which must be a `SELECT` or `WITH` query | Stacked statements, `ATTACH`, `PRAGMA` |
| SQLite authorizer | Denies reading `users.email`, `users.phone`, `users.firstname` and `users.lastname` | Leaking contact details or names, however the SQL is phrased |
| Limits | Maximum rows returned and a query timeout | Runaway queries and huge result sets |
| Answer-writing step | Result rows are passed to the model as data, never as instructions | Instructions hidden in stored text (indirect injection) |

---

## Part 3: Foundation Model

> **Not started.** Which model translates the questions, and why it was chosen.

---

## Part 4: Interface

> **Not started.** How a user asks questions and sees answers and SQL. The brief allows a CLI, a minimal web UI or a notebook.

---

## Part 5: Code Quality

> **Not started.** Project structure and separation of concerns (prompt logic, query execution, response formatting), error handling, setup instructions and tests.

---

## Repository layout (so far)

```
NLQ_Agent/
├── data/
│   ├── raw/          # Unmodified TICKIT source files (committed)
│   └── tickit.db     # Built by scripts/build_db.py (git-ignored)
├── prompts/
│   └── system_prompt.md  # System prompt for SQL generation (draft)
├── scripts/
│   ├── build_db.py   # Load step: parse, fix, shift dates, add demo data, write and check
│   ├── demo_data.py  # Barclays Center and Brooklyn Nets demo data generator
│   ├── schema.sql    # Tables in data/tickit.db
│   └── views.sql     # Agent-facing views (the only part of the database the agent sees)
├── .gitattributes    # Keeps raw files byte-identical (no line-ending conversion)
├── .gitignore
└── README.md
```
