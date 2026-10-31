# NLQ Agent: Natural Language Query over SQL

A natural language query (NLQ) agent that lets a non-technical user ask questions about ticketing data in plain English. The agent turns the question into SQL, runs it against a database, and returns a readable answer together with the SQL it ran.

> **Status:** Draft. This README is being written part by part. **Part 1 (Data)** is below. Later parts (agent design, model choice, interface, error handling, setup) will be added as they are built.

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

Minor issues that do not affect results: 47 users have the first name misspelled as "acqueline", 5 sales have the same user as buyer and seller, and listing IDs have gaps (up to 235,497 for 192,497 rows), which suggests a cut-down sample.

### How we handle the issues: clean the data vs. teach the agent

The issues fall into two kinds, and each kind is handled differently.

**Type A, data defects: fixed in the load step.** These are objectively wrong values with exactly one correct fix, and they are corrected before the agent ever sees the data:

| Issue | Fix |
|---|---|
| #1 Wrong `starttime` dates | Rebuild the date part from `dateid` and keep the time of day |
| #2 Missing venues | Add placeholder rows ("Unknown venue") for IDs 12, 51 and 64 |
| #3 `venueseats = 0` | Store as NULL (unknown) |
| #4 File formats | Normalize line endings, delimiters, NULLs and timestamps to ISO 8601 |

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

### Planned data preparation

> **Not built yet.** This is the proposed approach for the load step.

A load script will read `data/raw/` and build a SQLite database at `data/tickit.db`. It will:

1. **Apply the Type A fixes** listed above.
2. **Close the coverage gaps (#8, #9).** These are enhancements, not bug fixes:
   - **Shift dates forward** so the data covers recent dates and relative-date questions return results. The exact offset will be chosen when the script is built. Shifting by a whole number of weeks keeps weekday patterns intact. Shifting by whole years does not, so the `date` table's day and holiday fields would have to be recomputed.
   - **Optionally add audience-specific demo data:** for example, a particular venue and a season of a team's home games, so a demo can use questions in the audience's own terms. Listings and sales would be generated to match TICKIT's existing price and volume distributions. Added rows will be flagged so they can be told apart from Amazon's original data.

### Repository layout (so far)

```
NLQ_Agent/
├── data/
│   ├── raw/          # Unmodified TICKIT source files (committed)
│   └── tickit.db     # Built by the load script (git-ignored)
├── .gitattributes    # Keeps raw files byte-identical (no line-ending conversion)
├── .gitignore
└── README.md
```
