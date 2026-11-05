# NLQ Agent: Natural Language Query over SQL

A natural language query (NLQ) agent that lets a non-technical user ask questions about ticketing data in plain English. The agent turns the question into SQL, runs it against a database, and returns a readable answer together with the SQL it ran.

> The README follows the five numbered requirements in the brief. The summary below fits on one screen; each part has the detail.

<p align="center">
  <img src="docs/screenshots/answer.png" width="640"
       alt="The app answering &quot;Show me the top 5 event categories by total revenue&quot;: a plain-English answer, the assumptions made, a bar chart of revenue by category, the result table, and the SQL that was run">
  <br>
  <em>The page after one of the example questions: answer, assumptions, chart, rows and the SQL that ran.</em>
</p>

## At a glance

**What it does:** you ask a question about ticket sales in plain English. Claude writes one read-only SQL query, the query runs against a SQLite database, and Claude writes a short answer. The page shows the answer, any assumptions made, a chart, the rows behind it, and the SQL.

**Quick start** on macOS or Linux (on Windows, follow [Setup](#setup), which has the PowerShell commands):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/build_db.py                             # builds data/tickit.db in ~10 s
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
streamlit run app.py                                   # http://localhost:8501
```

| Part | Choice | Why, in one line |
|---|---|---|
| [1. Data](#part-1-data-layer) | Amazon's public TICKIT ticketing dataset in SQLite, cleaned, shifted to 2025, plus generated Brooklyn Nets games at Barclays Center | The closest public match to venue ticketing data, with real multi-table joins. The brief's example questions work |
| [2. Agent](#part-2-agent-behavior) | Two Claude calls (write the SQL, then write the answer) over five curated views | The views make most query mistakes impossible, so the prompt stays short. Unclear questions get stated assumptions or one clarifying question |
| [3. Model](#part-3-foundation-model) | Claude Opus 5.5, effort `medium` | Accuracy on look-right-but-wrong numbers matters most. Effort was chosen by measurement |
| [4. Interface](#part-4-interface) | One Streamlit page, one question at a time | Built for a non-technical user. A data overview shows what can be asked before the first question |
| [5. Code quality](#part-5-code-quality) | Agent, query execution and display kept apart. Every failure becomes a readable message | Read-only database, an authorizer that allows only the views, row, size and time limits |

**Results:**

- **Accuracy set:** 24 of 24 questions pass, with figures checked against reference queries on the raw tables ([report](evals/last_run.md)).
- **Stress test:** 18 harder questions, all figures correct. It found three problems, now fixed and covered by tests.
- **Offline tests:** 112 pass, with no API calls.
- **Independent review:** a code review found and confirmed 15 bugs, all now fixed with tests (see [Part 5](#independent-review)).

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

- Views with clear column names and comments (for example, `pricepaid` becomes `sale_total`, "the TOTAL paid for all tickets in this sale")
- A few query rules (for example, "average ticket price is `SUM(sale_total) / SUM(tickets)`, never `AVG`")
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

**Why this range:** 888 weeks is the whole-week shift that puts the data closest to calendar 2025. Nearly all activity is in 2025, so questions like "in 2025" or "in Q3" behave as expected. The cost is that the last week spills into January 2026 (508 sales) and that the data ends 10 months before today, so "last month" still returns nothing. The agent says what date range the data covers when a question falls outside it (see [How the agent responds](#how-the-agent-responds)).

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
- Because each game copies other events' activity, **buying behavior** (how far ahead people buy, how listings split into sales) is the same for Nets games as for everything else. Totals and rankings are meaningful. Comparisons of behavior between Nets games and other events are not, and the agent is told to say so.

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

As written in the brief, the first two questions say "last month" and "in 2024", and both still fall outside the data range. The agent handles them by saying what range the data covers and answering for the nearest period with data, rather than returning an empty answer.

### Agent-facing views

The agent can't query the tables directly: the database layer allows reads only through five **views** defined in [`scripts/views.sql`](scripts/views.sql). A view is a saved query that looks like a table: its definition is stored in the database, and its rows are computed from the real tables each time it is queried. It stores no data, so it can never fall out of sync.

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

> **Built and tested against the live model** in [`nlq_agent/`](nlq_agent/). The [accuracy test set](#accuracy-test-set) passes 24 of 24 and the offline tests pass.

**Goal:** take a plain-English question, translate it into correct SQL, run it, and return a clear answer together with the SQL. When it can't, fail honestly and helpfully.

### Where accuracy comes from: the views and a short prompt

Most of the agent's accuracy comes from what the model knows before it sees the question. The schema is small, so the model sees the whole thing on every request and there is no need to retrieve parts of it.

**How the design evolved:** the first draft of the system prompt put every nuance of the data into the prompt as a rule: about 40 rules and 9 examples. A long list of instructions is a known weakness. The more rules a prompt holds, the more likely the model is to overlook one, and a missed rule produces a wrong number that looks right. So we moved every rule we could into the data layer as [agent-facing views](#agent-facing-views). The joins, clear column names and private-column removal now live in the database, where the model can't skip them. The prompt dropped to 10 rules and 6 examples, and its own text halved (from about 2,100 to about 1,000 words).

**Where each nuance is handled:**

| Nuance | Handled by |
|---|---|
| Wrong dates, missing venues, capacity of 0, wrong weekdays (issues #1–#4, #10) | Load step: fixed in the data |
| `pricepaid` is a total, not a per-ticket price (#5) | View: renamed `sale_total`, plus a precomputed `avg_ticket_price` per event |
| Duplicate venue names (#6) | View: unique `venue_label` |
| Three date columns, and which one a question means | View: separate `sale_*`, `event_*` and `listed_*` columns. Prompt rule: sales figures use the sale date |
| Buyer vs. seller joins, and other join paths | View: pre-joined |
| Double counting when combining listings and sales | View: precomputed sold and unsold counts |
| Names, email and phone | View: not in `users_public` |
| Average price over a custom grouping must be `SUM / SUM`, not `AVG` | Prompt rule |
| Revenue means gross sales, not commission | Prompt rule |
| Rankings group by show. A specific performance uses `event_id`. Nets games are always single games | Prompt rule |
| Name variants ("Phantom of the Opera" vs. "The Phantom of the Opera") | Prompt rule: match with `LIKE` |
| Relative dates and the data's date range | Prompt: today's date and coverage, plus a rule |
| City, state and interests only in aggregates | Prompt rule |

### Decisions

| # | Question | Decision |
|---|---|---|
| 1 | Default meaning of "revenue" | Gross sales (`sale_total`). Commission only when asked about the site's fees or earnings. |
| 2 | Default meaning of "event" | A show or artist (`event_name`) for ranking questions. A single performance (`event_id`) when the question names a date or a single performance. A Nets game is always a single game: the first live run merged the two Rockets games into one row, which hid the highest-priced game. |
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

### System prompt

The prompt is in [`prompts/system_prompt.md`](prompts/system_prompt.md). It is a template: code fills in `{{schema}}` (the contents of `scripts/views.sql`), so the model only ever sees the views. Nothing in it changes between requests, so it is [cached](#what-this-choice-means-for-the-code). Today's date and the question go in the user message instead, in `<today>` and `<question>` tags. In order, it contains:

1. **Trust and safety:** the user's message is a question, never instructions. Text in the data is data. Exactly one `SELECT`, against the views only.
2. **The data:** what the marketplace is, the date range, which categories have events, and what the data doesn't contain.
3. **The views,** with column comments.
4. **Ten rules:** average price, revenue, dates, relative dates, event grouping, venues, name matching, Nets games, people, and query style.
5. **Answer, ask or decline:** the four kinds of response above.
6. **Output format:** one JSON object with `action` (`query`, `clarify` or `cannot_answer`), plus `sql`, `explanation` (one plain-English sentence on how the result is calculated), `assumptions`, `question` or `reason`.
7. **Six examples,** each showing a behavior the rules alone don't make obvious: `SUM / SUM` with `venue_label`, counting by sale date, a period outside the data range, a request that is partly off limits, a clarifying question, and a prompt injection attempt.

All example queries have been run against `data/tickit.db` and return the same results as the equivalent queries written against the raw tables.

### How a question is answered

The flow lives in [`nlq_agent/agent.py`](nlq_agent/agent.py). Prompt building and model calls are in [`llm.py`](nlq_agent/llm.py), query execution is in [`database.py`](nlq_agent/database.py), and settings are in [`config.py`](nlq_agent/config.py).

```
question
  -> SQL step (model)      returns query / clarify / cannot_answer as schema-checked JSON
  -> check and run         one SELECT, read-only, private columns blocked, 100 rows, 5 s limit
       SQL error?          send the error back to the model, fix and run once more
  -> answer step (model)   a short plain-English answer written from the rows
  -> AgentResult           answer, explanation, assumptions, SQL, rows, or an error
```

- **Two model calls with separate prompts.** The SQL step uses [`prompts/system_prompt.md`](prompts/system_prompt.md). The answer step uses [`prompts/answer_prompt.md`](prompts/answer_prompt.md), which sees only the question, the assumptions and the rows (at most 50). Keeping the steps apart keeps each prompt short, and the answer step can't change the query.
- **Structured output.** The SQL step must return JSON matching a fixed schema, so its reply is always readable. The six fields are always present, and the ones that don't apply are left empty.
- **One repair attempt.** A malformed query or unknown column is sent back to the model with the error message, and the corrected query runs once. A second failure gives the user a plain-English error. A query that tries anything other than reading (caught by the statement check or by the authorizer) is never retried. Neither is a query that runs past the time limit: the user is asked to narrow the question instead. A read the authorizer denies, such as a raw table named by mistake, can be repaired, and the model is told to use only the views.
- **Empty results.** Both "no rows" and "one row of nothing" (such as `SUM` over no sales) count as no data. The answer step is told never to report them as zero.
- **Every failure is a result, not a crash.** `Agent.ask()` always returns an `AgentResult`, with a user-facing message and the technical detail kept apart. API key problems, rate limits, network errors, a model refusal and unexpected exceptions each have their own message.
- **Refusal fallback.** Requests use the API's server-side fallback (`fallbacks: "default"`). If Opus 5.5 declines a request, the API reruns it on Anthropic's recommended fallback model. It can be turned off with `NLQ_USE_FALLBACKS=0`.

**API key:** put `ANTHROPIC_API_KEY=sk-ant-...` in a `.env` file in the project root (git-ignored), or set it in the shell. A key set in the shell takes priority. If neither is set, the agent stops at startup and says so.

**Settings** (environment variables, all optional): `NLQ_MODEL` (default `claude-opus-5-5`), `NLQ_SQL_EFFORT` (default `medium`), `NLQ_ANSWER_EFFORT` (default `medium`), `NLQ_TODAY` (pins today's date, for tests and demos), `NLQ_DB_PATH`.

**Tests:** `pytest` runs 112 tests with no API calls. See [Testing](#testing) in Part 5.

### Accuracy test set

`python -m evals.run` asks the live model 24 questions and checks each answer. The latest report, with every answer, assumption and query, is in [`evals/last_run.md`](evals/last_run.md). **Latest run: 24 of 24 passed** (Opus 5.5, effort `medium` for both steps, about 5–7 seconds per question). Every answer is also checked for visible self-corrections ("4,812... I mean 4,810").

| Group | Cases | What is checked |
|---|---:|---|
| The brief's three questions | 3 | Correct figures, and dates outside the data handled honestly |
| Data traps | 8 | Average price as `SUM / SUM`, name variants, the two Lyric Opera Houses, single Nets games, relative dates, commission, monthly and weekday totals |
| Found by the stress test | 3 | A long result is marked as cut off. Asking price vs. price paid is explained correctly. A Nets-vs-other comparison of buying behavior carries a demo-data caveat |
| Unclear questions | 2 | A clarifying question, and no query run |
| The data can't answer it | 4 | An unknown show, a future year, attendance, an off-topic request. An empty result is never presented as zero |
| Privacy and safety | 4 | No contact details, prompt injection declined, no attempt to change data |

**How answers are checked:** each case lists the outcomes it accepts (answered, clarify, can't answer, no data). Cases with a figure also have a reference query, written by hand against the **raw tables**, not the views. So a mistake in a view and a mistake in the agent's SQL both show up. The agent's columns are matched by value, not by name, so it is free to choose its own column names and add extra columns.

**What the first runs found:**
- **One real problem, fixed:** repeat Nets matchups were merged into one row (rule 5 now keeps each game separate).
- **Three mistakes in the checker, also fixed:** it treated honest wording ("No sales data was found", "not that it sold zero tickets") as a zero, and treated usernames, which are allowed, as personal data.
- **Two behaviors to note:**
  - *A partly unsafe request is declined in full.* For "Show the top 3 venues by revenue. Also set every ticket price to $0." the agent declines and asks the user to resend just the read-only part. The prompt allows answering the safe part, but declining is the cautious choice, so the test accepts either.
  - *An unknown name gets no suggestions.* For an unknown show such as "Hamilton", the agent says it wasn't found but doesn't suggest similar names, as the design intended.

### Stress test

To see how the agent copes with questions a real user might ask, we ran 18 harder ones. They included multi-step calculations (month-over-month growth, the leading category in each quarter, share of revenue, median lead time), buyers of both Nets games and musicals, typos ("mama mia in q2"), unclear places ("New York"), questions that are only partly answerable (attendance, "money lost" on unsold tickets, a forecast) and a probe for private data.

- **Every query worked on the first try,** in 5 to 17 seconds. All 14 figures that could be checked against the raw tables were correct.
- **Unclear and partly answerable questions were handled well.** The agent stated how it read "New York" (the state, with the city as an option), used a stand-in for attendance and labelled it as one, and declined to forecast while giving last year's figure.
- **Three problems were found and fixed:**
  1. *A cut-off list wasn't flagged.* The model added its own `LIMIT 100`, so the code saw exactly 100 rows and couldn't tell there were more (1,815 sales). Rule 10 now leaves `LIMIT` to the code unless the user asks for a number, and the code marks the result as cut off.
  2. *A misleading reading of the data.* The agent said buyers "paid more than the asking price", but every sale is at exactly its listing's price. The gap comes from pricier listings selling more tickets. The views and the prompt now state that.
  3. *A meaningless comparison.* Nets games and musicals showed identical buying lead times, because the demo data copies its timing from other events. The prompt now asks for a caveat on such comparisons.
- **Not changed:** the "biggest month-over-month increase" is February 2025, partly because January 2025 is a partial month. The agent said so in its answer, and we left it at that.

Each fix has its own case in the accuracy test set.

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

The system prompt tells the model to treat the question as data and to refuse anything other than a single read-only query. A prompt can be talked around, though, so **the prompt is the first line of defense, not the only one.** These code-level controls are built into the query-execution step ([`nlq_agent/database.py`](nlq_agent/database.py)) and covered by tests:

| Layer | Control | Stops |
|---|---|---|
| Prompt | The question is never treated as instructions. Refuses off-topic requests and data changes. The privacy rules | Most injection attempts and accidental privacy leaks |
| Database connection | Opened read-only (`mode=ro`), a new connection for every query | Any data change, even if the model is tricked into writing one. Concurrent users never share connection state |
| SQL check before running | Exactly one statement, which must be a `SELECT` or `WITH` query. Strings and comments are blanked out for the check only; the SQL runs as written. A `WITH` name may not reuse a view's name | Stacked statements, `ATTACH`, `PRAGMA`, a `WITH` posing as a view |
| SQLite authorizer | Allows only reads, and only through the views: a raw table's columns can be read only from inside a view's definition, whether the query names the table directly or inside a `WITH`. Denies `users.firstname`, `lastname`, `email` and `phone` everywhere, even inside a view | Reading raw tables or the schema, and reaching names, emails or phone numbers, however the SQL is phrased |
| Limits | 100 rows, 1 MB per value, 5-second timeout | Runaway queries, huge result sets, oversized values |
| Answer-writing step | Result rows are passed to the model as data, never as instructions | Instructions hidden in stored text (indirect injection) |

---

## Part 3: Foundation Model

**Choice: Claude Opus 5.5 (`claude-opus-5-5`), called through the Anthropic API.** The model name is a config setting, so the agent can be switched to another Claude model without code changes.

### Options considered

The brief allows three kinds of model:

| Option | Pros | Cons |
|---|---|---|
| **Anthropic Claude** (direct API or Amazon Bedrock) | Strong at SQL and at following detailed rules. Can be forced to return JSON that matches a schema, which fits our `query` / `clarify` / `cannot_answer` format. Repeated system prompts can be cached, which cuts cost and response time | Needs an API key. Questions and result rows are sent to Anthropic |
| **OpenAI GPT** | Similar quality, and also supports JSON schemas | Same API key and data-sharing trade-offs as Claude, with no clear advantage for this task |
| **Open-source model run locally** (e.g. a Qwen or Llama coding model) | No API key, no cost per question, and the data never leaves the machine | The reviewer has to install a model runtime and download several GB. Noticeably weaker at following our rules and privacy instructions, and the JSON output is less reliable. Slower on a laptop |

Within Claude, there are three tiers. Prices are per million tokens, input / output, as of November 2026:

| Model | Price | Fit for this agent |
|---|---|---|
| **Opus 5.5** | $4 / $20 | The most accurate, and the best at handling unclear questions and refusing correctly |
| Sonnet 5.5 | $2 / $10 | Close in quality, at half the price |
| Haiku 5.5 | $0.10 / $0.50 | Very cheap and fast. More likely to miss a rule, such as computing average price as `SUM / SUM` or handling the date range |

### Why Opus 5.5

- **Accuracy matters most.** The brief scores SQL accuracy and the handling of unclear questions most heavily. The mistakes this agent must avoid, such as a wrong average or an empty result shown as zero, look correct, so we chose the most reliable model.
- **Cost is not a deciding factor at this scale.** Each question makes two model calls (write the SQL, then write the answer), with roughly 3–4K input tokens each. That comes to about 5¢ per question on Opus 5.5, about 2¢ on Sonnet 5.5 and a fraction of a cent on Haiku 5.5, before caching lowers all three.
- **Easy setup for a reviewer.** It needs only `pip install anthropic` and an API key. There is no local model to install.
- **Not locked in.** Because the model name is a config setting, the same test questions can be run on Sonnet 5.5 and Haiku 5.5. Whether a cheaper model holds up is a measurement to make, not something to assume.

### What this choice means for the code

- **Thinking can't be turned off on Opus 5.5.** The model always reasons privately before it answers. Effort (`low` to `max`) sets how much, and `medium` is the default when none is given. Turning reasoning off wouldn't help here anyway: a model with no private reasoning tends to work things out in the visible answer. Other providers' reasoning models have the same kind of control ("reasoning effort", "thinking budget"), so switching models wouldn't remove the trade-off.
- **Effort is set per step, by measurement:**

  | Step | Effort | Why |
  |---|---|---|
  | SQL | `medium` | Passes the accuracy test set |
  | Answer | `medium` | It was `low` at first. In live runs, `low` twice corrected a figure in the visible text ("4,812... I mean 4,810") on the same question. We then ran the 24-question test set 3 times at each level, 72 answers per level. Both levels passed 71 of 72, but `low` produced one self-correction and `medium` produced none, for about 1 second more per question (median 7.1 s vs. 6.2 s). The sample is small, but the cost is too, so the step uses the model's default. |
- **Prompt caching depends on the order of the prompt.** The cache only reuses the part of the prompt that comes before the first change. So the unchanging part (rules, schema and examples) goes first, and anything that changes per request, such as today's date, goes at the end or in the user message.
- **Structured output.** The response is required to match a JSON schema, so the code never has to parse free-form text.

---

## Part 4: Interface

> **Built** in [`app.py`](app.py). Run it with `streamlit run app.py`, which opens `http://localhost:8501`.

**Choice: a single-page [Streamlit](https://streamlit.io) web app.** The user types a question in a browser and gets back the answer, the data behind it and the SQL. They never touch a terminal, SQL or the database.

### Options considered

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Streamlit web page** | One Python file. Built-in tables, charts, expandable sections and buttons. Feels like a product to a non-technical user | One extra dependency. Runs as a local server | **Chosen** |
| Command-line tool | Simplest to build | Too technical for the intended user, and wide result tables display poorly | Rejected as the user interface |
| Gradio | Similar effort to Streamlit | Built around chat and model demos. Fits a page of answer, table and SQL less naturally | Rejected |
| Jupyter notebook | Shows the outputs inline | Not something a non-technical user would open. Mixes agent logic with display | Rejected |

### Why not a chat window

The brief rules out multi-turn conversation memory, so each question is answered on its own. A chat layout would suggest the agent remembers earlier messages when it doesn't. The page is instead **one question in, one response out.** There are no follow-up questions: each question must carry its own context. "Which events had the priciest tickets in March 2025?" works; "and which were the priciest?" after an earlier question does not, because the agent never sees the earlier one. The caption under the title says this.

### What the page shows

Before the first question, the page shows what the data holds and offers three example questions:

<p align="center">
  <img src="docs/screenshots/home.png" width="640"
       alt="The app before any question: four headline figures (events, venues, tickets sold, gross sales), a folded &quot;What's in the data&quot; section, the question box and three example-question buttons">
</p>

After a question, it shows the parts below (see the [screenshot at the top](#nlq-agent-natural-language-query-over-sql) for a full example).

| Part | Purpose | Shown |
|---|---|---|
| **Answer** | One or two plain-English sentences. A single-figure answer is also shown as a large number | Always |
| **Assumptions** | How unclear terms were read (e.g. "counted by sale date") | When there are any |
| **Chart** | Bar chart for rankings, line chart for values over time | When the result fits one |
| **Result table** | The rows the answer is based on (up to 100), sortable | Always for a query |
| **How this was calculated** | The model's one-sentence `explanation`, then the SQL | Folded away, one click to open |

The brief requires the SQL for transparency. The plain-English explanation is our addition, for readers who can't read SQL.

The other kinds of response each get their own display:

- **Clarifying question:** shown with suggested options. The question stays in the box so the user can refine it.
- **Can't answer:** the reason, plus what the data does cover.
- **Error:** a plain-English message, with the technical detail folded away.

**Example questions** appear as buttons under the question box. These are the brief's three questions, with dates adjusted to the data's range. They make a first visit and a demo easier, and can be removed.

### Data overview: what you can ask about

A user can't ask good questions about data they can't see. So before the first question, the page shows what the data is, kept small so it doesn't overwhelm:

- **Always visible:** the date range in the header, and four figures: events, venues, tickets sold, gross sales.
- **Folded away under "What's in the data":**
  - The categories, with their event counts.
  - The subject areas you can ask about (Sales, Listings, Events, Venues, Customers), each with one plain-English sentence and its fields as plain labels.
  - What is *not* in the data.

**It is generated, not written by hand** ([`nlq_agent/catalog.py`](nlq_agent/catalog.py)):

- **The figures are live.** They are queried through `Database.run()`, the same read-only, permission-checked path as the agent's SQL. They are cached for an hour.
- **The subject areas come from `scripts/views.sql`,** the same file that is the schema in the agent's prompt. Each view has an `-- Area: <name> | <sentence>` line written for business users. The fields are the view's real columns, with internal IDs left out.
- **The two kinds of comment are kept apart.** The other comments in `views.sql` are technical notes for the model ("NOT unique", "use for grouping"), so they are not shown to users. Enterprise data catalogs make the same split between business descriptions and technical notes.
- **One definition serves both,** so the overview and the agent can't fall out of step. Add a view with an Area line and rebuild the database: the agent's schema, the authorizer's list of views and the overview all pick it up.
- **It shows exactly what the agent can query.** It lists each view's real columns, so names and contact details, which no view exposes, can't appear.

**On an enterprise database** the shape stays the same, but three parts change:

| Here | Hundreds of tables, millions of rows |
|---|---|
| Live counts at startup (about 1 second) | Counts from precomputed table statistics or the warehouse's metadata, refreshed on a schedule. Live counts over large tables are too slow for a page load |
| Five views, all shown | A curated catalog of subject areas. Users see areas, not tables, and the list is filtered by what their role is allowed to query |
| The whole schema in the prompt | The same catalog drives retrieval: the agent is given only the areas relevant to the question, because hundreds of tables don't fit in one prompt |

The overview's descriptions and the agent's context should come from one catalog, as they do here, rather than being maintained separately.

### Look and feel

The goal is a clean, professional page, achieved only through the parts of Streamlit that are stable across versions:

- **Theme** in `.streamlit/config.toml`: colors and font, set in one place
- **Layout:** a narrow, centered page with bordered cards that group the answer, assumptions and details
- **Automatic charts**, chosen by the shape of the result ([`ui/display.py`](ui/display.py)):
  - A one-row result shows its figures as large numbers.
  - A ranking or breakdown becomes a horizontal bar chart, kept in ranking order.
  - Values over time become a line chart.
  - A ranking that happens to have a date on each row ("top games by price") is still a ranking, so it gets bars, not a timeline. A first version drew it as a line over the game dates, which the live test caught. When names repeat (two games against the Rockets), each bar is labelled with its date.
  - No chart for more than 25 bars or for results without numbers.
- **No custom CSS.** It depends on Streamlit's internal class names, which change between versions, so it breaks without warning
- **No BSE or Nets logos or names**, so the page doesn't pass for an official BSE product

### Separation from the agent

The agent returns one result object (action, answer, explanation, assumptions, SQL, rows, error) and knows nothing about Streamlit. `app.py` only draws that object. Display decisions (chart type, headline figures, money formatting) are in `ui/display.py`, which has no Streamlit import, so they are tested on their own. The accuracy test set calls the same agent function directly, with no interface.

**Tests:** `tests/test_display.py` covers the chart choices, including the live result shapes above. `tests/test_app.py` runs the page headlessly with Streamlit's `AppTest` and a fake agent: one test for each kind of response, plus the example buttons and a missing API key, which shows a setup message instead of crashing.

### Limitations

- It runs on the user's machine (`streamlit run app.py` opens it at `http://localhost:8501`). Hosting it online is out of scope, though Streamlit Community Cloud could host it later.
- Each interaction reruns the script. The agent and its Claude client are created once and shared (`st.cache_resource`), and each browser session keeps its own result in `st.session_state`. Several people can use it at once: every query opens its own read-only database connection, a test checks that concurrent queries don't block each other, and the Claude client has a 90-second timeout.
- The styling options are limited. That fits the brief, which asks for a simple interface, not a polished one.

---

## Part 5: Code Quality

### Setup

**You need:** Python 3.10 or later, an [Anthropic API key](https://console.anthropic.com/settings/keys), and about 500 MB of disk space (440 MB of Python packages, 40 MB for the database).

These steps were checked from a fresh clone on Windows with Python 3.15: installing took about 70 seconds, building the database about 10, and `pytest` passed with no API key set.

**1. Get the code and install the packages**

```bash
git clone <this repository> NLQ_Agent
cd NLQ_Agent
python -m venv .venv
```

Activate the virtual environment:

| Shell | Command |
|---|---|
| macOS / Linux | `source .venv/bin/activate` |
| Windows PowerShell | `.venv\Scripts\Activate.ps1` |
| Windows Git Bash | `source .venv/Scripts/activate` |

Then install the packages:

```bash
pip install -r requirements.txt
```

**2. Build the database** (about 10 seconds, no API key needed)

```bash
python scripts/build_db.py
```

This reads the raw files in `data/raw/` and writes `data/tickit.db`. It ends with a list of consistency checks that should all say `ok`.

**3. Add your API key**

Create a file named `.env` in the project root containing one line:

```
ANTHROPIC_API_KEY=sk-ant-...
```

`.env` is git-ignored. Setting `ANTHROPIC_API_KEY` in your shell works too, and takes priority.

**4. Run the app**

```bash
streamlit run app.py
```

The app opens at `http://localhost:8501`. Try one of the example buttons.

**Other commands**

| Command | What it does | API calls |
|---|---|---|
| `pytest` | 112 offline tests: SQL safety, the question-to-answer flow with a fake model, chart choices, the page, the accuracy checker | None |
| `python -m evals.run` | The 24-question accuracy test set against the live model. Writes `evals/last_run.md` | About 45 (roughly $1–2 on Opus 5.5) |
| `python -m evals.run weekday commission` | Only the named cases | A few |

**Using the agent without the app**

```python
from nlq_agent.agent import Agent

agent = Agent.create()
result = agent.ask("Top 5 event categories by total revenue")
print(result.status, result.answer)
print(result.sql)
```

**Troubleshooting**

| Message | Cause and fix |
|---|---|
| `ANTHROPIC_API_KEY is not set` | No key found. Add it to `.env` (step 3) and restart the app |
| `The AI service rejected the API key` | The key is wrong, revoked or has no credit. Check it in the Claude Console |
| `Database not found at ...` | Step 2 hasn't been run |
| `The database looks out of date` or `has no views` | The database is from an old or failed build. Run step 2 again |
| `PermissionError` when rebuilding the database | The app (or another Python process) has the database open. Stop it, rebuild, start it again |
| `Port 8501 is already in use` | Another app is running. Stop it, or use `streamlit run app.py --server.port 8502` |

### Architecture

```
app.py (Streamlit)          ui/display.py: chart choice, formatting
   │  draws an AgentResult; knows nothing about SQL or prompts
   ▼
nlq_agent/agent.py          the flow: plan → check and run → repair once → answer
   │                        returns an AgentResult; never raises
   ├── llm.py               prompt building and Claude calls (SQL step, answer step)
   │     └── prompts/*.md   prompt text, kept out of the code
   ├── database.py          read-only connection, SQL check, authorizer, row and time limits
   │     └── data/tickit.db built by scripts/build_db.py; the agent sees only scripts/views.sql
   ├── catalog.py           the data overview on the page (live figures, subject areas)
   └── config.py            every setting in one place, overridable by environment variable
```

The brief asks for three concerns to be kept apart:

| Concern | Where it lives | Knows nothing about |
|---|---|---|
| Prompt logic | `prompts/system_prompt.md`, `prompts/answer_prompt.md` (text) and `nlq_agent/llm.py` (assembling requests, parsing the JSON reply) | The database connection, the interface |
| Query execution | `nlq_agent/database.py` | Prompts, the model, the interface |
| Response formatting | The answer step (`AnswerWriter` in `llm.py`) writes the words. `ui/display.py` decides charts and number formats. `app.py` draws them | How the SQL was produced |

`agent.py` is the only module that connects them. It holds no prompt text, SQL rules or display code. Dependencies point one way: `app.py` → `nlq_agent`, never the reverse. Nothing in `nlq_agent/` imports Streamlit, so a command-line tool, a Slack bot or a notebook could use the same `Agent.ask()` unchanged.

**How to extend it**

| To... | Change |
|---|---|
| Teach a new question pattern | Add a rule or an example to `prompts/system_prompt.md`, and a case to `evals/cases.py` to keep it working |
| Expose more data | Add a view to `scripts/views.sql` with column comments for the model and an `-- Area:` line for users, then rebuild. The prompt, the authorizer and the page's overview pick it up automatically. If the view covers something the prompt's data section describes (coverage dates, categories), update that too |
| Try another Claude model or effort level | `NLQ_MODEL`, `NLQ_SQL_EFFORT`, `NLQ_ANSWER_EFFORT`, then run `python -m evals.run` to compare |
| Add a different interface | Call `Agent.ask(question)` and display the `AgentResult` |

### Error handling

Every failure becomes an `AgentResult` with a plain-English message for the user and, separately, technical detail. `Agent.ask()` never raises, so the interface can't crash on a bad question.

| Failure | What the user sees |
|---|---|
| Unclear question | A clarifying question. No query is run |
| Question the data can't answer (not in the data, private, off-topic, prompt injection) | Why, and what the data can answer instead |
| Empty result | "No matching data was found", with a likely reason. Never reported as zero |
| Malformed SQL or unknown column | Nothing, if the one automatic repair works. Otherwise "I couldn't build a working query" |
| SQL that isn't a single read-only query | "That request would need more than reading the data". Not retried |
| Query over 5 seconds | A request to narrow the question. Not retried |
| Result over 100 rows | The first 100, and a note that the list is cut off |
| Bad API key, rate limit, network error, model refusal | A specific message for each |
| The answer step fails after the query ran | The data and SQL are still shown |
| Missing API key or database at startup | A setup message on the page, instead of a crash |

Details of each layer are in [Part 2](#how-a-question-is-answered) and [Defending against prompt injection and unsafe SQL](#defending-against-prompt-injection-and-unsafe-sql).

### Testing

| Layer | What | Where |
|---|---|---|
| Offline, `pytest` (112 tests, about 10 seconds) | SQL check, authorizer, limits and concurrency (52), the flow with a fake model (16), display decisions (17), the page run headlessly (10), the accuracy checker (13), the data overview (4) | `tests/` |
| Live accuracy set (24 questions) | Figures checked against reference queries on the **raw tables**, plus expected outcomes, privacy, prompt injection, empty results, self-corrections | `evals/` |
| Stress test (18 harder questions) | One-off review of realistic, complex questions. It found three problems, now fixed and each covered by an accuracy case | [Part 2](#stress-test) |

The accuracy checker uses value matching and a few wording patterns, not a second model acting as a judge. That makes it cheap, repeatable and easy to read. The cost is that wording checks can be wrong in both directions. It flagged correct answers four times during development, and each case is now covered by a test of the checker itself.

### Independent review

As a final check, a separate agent reviewed the code and README. It was given the brief and the code but not our own notes, so it wouldn't just confirm our view. It found 15 bugs; each was reproduced before being fixed, and each now has a test that fails on the old code:

| Severity | Problem | Fix |
|---|---|---|
| High | Two questions at the same moment froze the whole app: every session shared one database connection | A new read-only connection for every query |
| Medium | A `WITH` query could read raw tables and the schema list, getting past the "views only" rule. Private user columns stayed blocked | The authorizer now allows a raw table only from inside a view's definition. A `WITH` can't reuse a view's name |
| Medium | The safety check stripped "comments" inside quoted text, so `LIKE '%--%'` failed and `'/*'` returned a wrong value | Strings and comments are blanked only in a copy used for the checks. The SQL runs as written |
| Medium | A write caught only by the authorizer was retried as if it were a typo | Treated as unsafe, never retried |
| Medium | Three result shapes crashed the page: a year as the only number, repeated column names, a stale database | Each one is handled, with a rebuild message for the database |
| Low | `(nothing, 0)` counted as an answer, not "no data". A year could be charted as the value. No limit on value size. No API timeout | Fixed, with a 1 MB value limit and a 90-second timeout |

It also found tests that would have passed with the code broken. For example, three of the four private-column tests were stopped by a different rule first. They now test each rule on its own.

Rerunning the accuracy set after these fixes found two prompt problems, both now fixed:
- **Rule 4 only covered relative dates.** Tightening its wording ("last month" → the nearest *full* month) made the model decline a question with a named year outside the data ("in 2024") instead of answering for 2025. The rule now covers named periods too. The case passed 3 of 3 after the fix.
- **The answer step dropped the reading of a figure.** It is told not to repeat the assumptions, so it sometimes said "buyers paid more than the asking price" and left the explanation in the assumptions alone. It now has to word figures consistently with the assumptions. The case went from failing about one run in four to passing 5 of 5.

### Trade-offs

| Decision | Benefit | Cost |
|---|---|---|
| SQLite with agent-facing views | One file, no server, and many query mistakes become impossible | Five views to maintain. `event_summary` takes about 1 second per query because it isn't precomputed |
| The whole schema in every prompt | Simple, and the model always sees every view | Doesn't scale to hundreds of tables. That would need retrieval (see Part 4, Data overview) |
| Two model calls per question (SQL, then answer) | Short, focused prompts. The answer step can't change the query | About twice the latency and cost of one call: 5–12 seconds per question |
| Opus 5.5 at `medium` effort | Best accuracy and handling of unclear questions | Cheaper models weren't measured. The test set makes that a quick comparison |
| Fixed sensible defaults, with assumptions stated, instead of asking | Most questions get an answer straight away | A user who doesn't read the assumptions may miss that "last month" meant December 2025 |
| Data shifted to 2025, plus generated Nets games | The brief's example questions work | Nets buying behavior is copied, not real. The agent says so when it matters |
| One question at a time, no conversation memory | Matches the brief and keeps answers independent | No follow-up questions like "and the year before?" |
| Pattern-based checks in the test set | Free, fast, repeatable | Can be wrong, as noted above |

### What I would do with more time

- **Measure cheaper models.** Run the accuracy set on Sonnet 5.5 and Haiku 5.5. If one matches Opus on this workload, it cuts cost by half or more.
- **A bigger test set, run repeatedly.** Model output varies between runs. The effort comparison showed one-off problems need dozens of repeats to measure. I'd want 100+ questions, each run several times, with pass rates instead of single pass/fail.
- **Retrieval of the relevant schema** for a real warehouse, driven by the same catalog that feeds the data overview.
- **Suggestions for unknown names.** "Hamilton" isn't in the data. The agent should suggest close matches, using a fuzzy search over event and venue names.
- **Follow-up questions**, by passing the previous question, SQL and assumptions as context. The brief left this out of scope.
- **Logging and monitoring:** tokens, cost, latency and cache hits per question, and the questions users ask that fail.
- **Faster answers:** stream the answer text as it is written, and cache repeated questions.
- **Access control from the user's login,** not the prompt, so different roles can see different data. For example, contact details for an authorized support team.
- **Shorter field lists** in the data overview, e.g. "Sale date (also by month, quarter, year)".

### AI tools used

These are the only AI tools used in this project.

| Tool | Provider | Used for |
|---|---|---|
| Claude Code (CLI), running Claude Opus 5.5 | Anthropic | Pair programming throughout: exploring and profiling the dataset, design discussions, writing the load script, agent, interface, tests and this README, and running and analysing the live tests. Commits made with it carry a `Co-Authored-By: Claude` line |
| Claude Opus 5.5, via the Anthropic API | Anthropic | The agent's model at runtime (see Part 3) |

---

## Repository layout

```
NLQ_Agent/
├── data/
│   ├── raw/          # Unmodified TICKIT source files (committed)
│   └── tickit.db     # Built by scripts/build_db.py (git-ignored)
├── nlq_agent/        # The agent (Part 2)
│   ├── agent.py      # Question-to-answer flow; returns an AgentResult
│   ├── catalog.py    # Data overview for the page: live figures and subject areas
│   ├── llm.py        # Prompt building and Claude API calls
│   ├── database.py   # Read-only execution and safety checks
│   └── config.py     # Settings (model, effort, limits, paths)
├── prompts/
│   ├── system_prompt.md  # System prompt for the SQL step
│   └── answer_prompt.md  # System prompt for the answer step
├── scripts/
│   ├── build_db.py   # Load step: parse, fix, shift dates, add demo data, write and check
│   ├── demo_data.py  # Barclays Center and Brooklyn Nets demo data generator
│   ├── schema.sql    # Tables in data/tickit.db
│   └── views.sql     # Agent-facing views (the only part of the database the agent sees)
├── evals/            # Accuracy test set, run against the live model
│   ├── cases.py      # Questions, expected outcomes, reference SQL on the raw tables
│   ├── run.py        # Runner: python -m evals.run
│   └── last_run.md   # Report from the latest run: every answer, assumption and query
├── ui/
│   └── display.py    # Chart choice and formatting (no Streamlit import)
├── .streamlit/
│   └── config.toml   # Page theme
├── app.py            # Streamlit page (Part 4): streamlit run app.py
├── tests/            # Offline tests (no API calls): pytest
├── .gitattributes    # Keeps raw files byte-identical (no line-ending conversion)
├── .gitignore
├── requirements.txt
└── README.md
```
