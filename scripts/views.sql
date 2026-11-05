-- Agent-facing views over the tables in schema.sql. The NLQ agent sees and queries
-- only these views. They hold the joins, clear column names and business logic, so
-- the system prompt doesn't have to. Built by scripts/build_db.py.
--
-- Money is in US dollars. Dates are ISO text: YYYY-MM-DD, months YYYY-MM, quarters YYYY-Qn.
-- is_demo = 1 marks generated demo rows (Barclays Center, Brooklyn Nets games).

-- Area: Venues | The theatres, arenas and halls where events take place, with city, state and capacity.
-- One row per venue.
CREATE VIEW venues AS
SELECT
    v.venueid    AS venue_id,
    CASE WHEN v.venuecity IS NULL THEN v.venuename || ' (#' || v.venueid || ')'
         ELSE v.venuename || ' (' || v.venuecity || ', ' || v.venuestate || ')'
    END          AS venue_label,   -- unique, readable name: use for grouping and display
    v.venuename  AS venue_name,    -- NOT unique (two "Lyric Opera House", three "Unknown venue")
    v.venuecity  AS venue_city,
    v.venuestate AS venue_state,
    v.venueseats AS venue_seats,   -- capacity; NULL = unknown
    v.is_demo
FROM venue v;

-- Area: Sales | Every ticket purchase: which event, when it was bought, how many tickets and how much was paid.
-- One row per ticket sale (a purchase of one or more tickets from one listing).
CREATE VIEW sales_detail AS
SELECT
    s.salesid                          AS sale_id,
    s.listid                           AS listing_id,
    s.saletime                         AS sale_time,
    DATE(s.saletime)                   AS sale_date,
    strftime('%Y-%m', s.saletime)      AS sale_month,
    strftime('%Y', s.saletime) || '-Q' || ((CAST(strftime('%m', s.saletime) AS INTEGER) + 2) / 3)
                                       AS sale_quarter,
    CAST(strftime('%Y', s.saletime) AS INTEGER) AS sale_year,
    sd.day                             AS sale_weekday,   -- MO TU WE TH FR SA SU
    s.eventid                          AS event_id,
    e.eventname                        AS event_name,     -- show, artist or matchup; repeats across performances
    DATE(e.starttime)                  AS event_date,
    strftime('%Y-%m', e.starttime)     AS event_month,
    e.starttime                        AS event_start_time,
    c.catname                          AS category,       -- e.g. Pop, Musicals, NBA
    c.catgroup                         AS category_group, -- Sports, Shows, Concerts
    vn.venue_id,
    vn.venue_label,
    vn.venue_name,
    vn.venue_city,
    vn.venue_state,
    s.buyerid                          AS buyer_id,
    s.sellerid                         AS seller_id,
    s.qtysold                          AS tickets,        -- tickets in this sale
    s.pricepaid                        AS sale_total,     -- TOTAL paid for all tickets in this sale (tickets x the listing's asking price)
    s.commission                       AS commission,     -- the site's 15% fee on sale_total
    s.is_demo
FROM sales s
JOIN date     sd ON sd.dateid  = s.dateid
JOIN event    e  ON e.eventid  = s.eventid
JOIN category c  ON c.catid    = e.catid
JOIN venues   vn ON vn.venue_id = e.venueid;

-- Area: Listings | Tickets that sellers put up for sale, at what asking price, and how many of them sold.
-- One row per listing (tickets a seller put up for sale), with how many of them sold.
CREATE VIEW listing_detail AS
SELECT
    l.listid                           AS listing_id,
    l.sellerid                         AS seller_id,
    l.listtime                         AS listed_time,
    DATE(l.listtime)                   AS listed_date,
    strftime('%Y-%m', l.listtime)      AS listed_month,
    l.eventid                          AS event_id,
    e.eventname                        AS event_name,
    DATE(e.starttime)                  AS event_date,
    c.catname                          AS category,
    c.catgroup                         AS category_group,
    vn.venue_id,
    vn.venue_label,
    l.numtickets                       AS tickets_listed,
    l.priceperticket                   AS asking_price_per_ticket, -- every sale is at exactly this price
    l.totalprice                       AS asking_total,
    COALESCE(sold.tickets, 0)          AS tickets_sold,
    l.numtickets - COALESCE(sold.tickets, 0) AS tickets_unsold,
    CAST(julianday(DATE(e.starttime)) - julianday(DATE(l.listtime)) AS INTEGER)
                                       AS days_listed_before_event,
    l.is_demo
FROM listing l
JOIN event    e  ON e.eventid  = l.eventid
JOIN category c  ON c.catid    = e.catid
JOIN venues   vn ON vn.venue_id = e.venueid
LEFT JOIN (SELECT listid, SUM(qtysold) AS tickets FROM sales GROUP BY listid) sold
       ON sold.listid = l.listid;

-- Area: Events | Each performance or game, with its date, venue and lifetime totals for tickets and revenue.
-- One row per event (a single performance or game), with lifetime totals across all
-- its listings and sales. For totals within a sales period, use sales_detail instead.
CREATE VIEW event_summary AS
SELECT
    e.eventid                          AS event_id,
    e.eventname                        AS event_name,
    DATE(e.starttime)                  AS event_date,
    strftime('%Y-%m', e.starttime)     AS event_month,
    e.starttime                        AS event_start_time,
    ed.day                             AS event_weekday,  -- MO TU WE TH FR SA SU
    c.catname                          AS category,
    c.catgroup                         AS category_group,
    vn.venue_id,
    vn.venue_label,
    vn.venue_name,
    vn.venue_city,
    vn.venue_state,
    vn.venue_seats,
    COALESCE(lst.tickets, 0)           AS tickets_listed,
    COALESCE(sld.tickets, 0)           AS tickets_sold,
    COALESCE(sld.revenue, 0)           AS revenue,        -- gross: total paid by buyers
    COALESCE(sld.commission, 0)        AS commission,
    ROUND(sld.revenue / sld.tickets, 2) AS avg_ticket_price,  -- NULL when nothing sold
    e.is_demo
FROM event e
JOIN date     ed ON ed.dateid  = e.dateid
JOIN category c  ON c.catid    = e.catid
JOIN venues   vn ON vn.venue_id = e.venueid
LEFT JOIN (SELECT eventid, SUM(numtickets) AS tickets FROM listing GROUP BY eventid) lst
       ON lst.eventid = e.eventid
LEFT JOIN (SELECT eventid, SUM(qtysold) AS tickets, SUM(pricepaid) AS revenue,
                  SUM(commission) AS commission
           FROM sales GROUP BY eventid) sld
       ON sld.eventid = e.eventid;

-- Area: Customers | Buyers and sellers by user ID, with home city and state and their interests. No names or contact details.
-- One row per user, without names or contact details.
CREATE VIEW users_public AS
SELECT
    userid        AS user_id,
    username,                      -- pseudonymous code, e.g. JSG99FHE
    city,
    state,                         -- US states plus Canadian provinces/territories
    likesports    AS likes_sports, -- preference flags: 1 = yes, 0 = no, NULL = unknown
    liketheatre   AS likes_theatre,
    likeconcerts  AS likes_concerts,
    likejazz      AS likes_jazz,
    likeclassical AS likes_classical,
    likeopera     AS likes_opera,
    likerock      AS likes_rock,
    likevegas     AS likes_vegas,
    likebroadway  AS likes_broadway,
    likemusicals  AS likes_musicals
FROM users;
