-- Curated TICKIT schema for SQLite, built by scripts/build_db.py.
-- Rows with is_demo = 1 are generated demo data (Barclays Center / Brooklyn Nets),
-- not part of Amazon's original TICKIT sample.

CREATE TABLE category (
    catid    INTEGER PRIMARY KEY,
    catgroup TEXT NOT NULL,          -- Sports, Shows, Concerts
    catname  TEXT NOT NULL,          -- e.g. NBA, Musicals, Pop
    catdesc  TEXT
);

CREATE TABLE venue (
    venueid    INTEGER PRIMARY KEY,
    venuename  TEXT NOT NULL,
    venuecity  TEXT,
    venuestate TEXT,
    venueseats INTEGER,              -- NULL when capacity is unknown
    is_demo    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE date (
    dateid  INTEGER PRIMARY KEY,     -- YYYYMMDD
    caldate TEXT NOT NULL UNIQUE,    -- ISO date, YYYY-MM-DD
    day     TEXT NOT NULL,           -- MO, TU, WE, TH, FR, SA, SU
    week    INTEGER NOT NULL,        -- week of year, weeks start Monday, week 1 = week containing Jan 1
    month   TEXT NOT NULL,           -- JAN .. DEC
    qtr     INTEGER NOT NULL,
    year    INTEGER NOT NULL,
    holiday INTEGER NOT NULL         -- 1 = US holiday
);

CREATE TABLE users (
    userid        INTEGER PRIMARY KEY,
    username      TEXT NOT NULL,
    firstname     TEXT,
    lastname      TEXT,
    city          TEXT,
    state         TEXT,
    email         TEXT,
    phone         TEXT,
    likesports    INTEGER,           -- 1 = yes, 0 = no, NULL = unknown
    liketheatre   INTEGER,
    likeconcerts  INTEGER,
    likejazz      INTEGER,
    likeclassical INTEGER,
    likeopera     INTEGER,
    likerock      INTEGER,
    likevegas     INTEGER,
    likebroadway  INTEGER,
    likemusicals  INTEGER
);

CREATE TABLE event (
    eventid   INTEGER PRIMARY KEY,
    venueid   INTEGER NOT NULL REFERENCES venue (venueid),
    catid     INTEGER NOT NULL REFERENCES category (catid),
    dateid    INTEGER NOT NULL REFERENCES date (dateid),
    eventname TEXT NOT NULL,
    starttime TEXT NOT NULL,         -- ISO timestamp, YYYY-MM-DD HH:MM:SS
    is_demo   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE listing (
    listid         INTEGER PRIMARY KEY,
    sellerid       INTEGER NOT NULL REFERENCES users (userid),
    eventid        INTEGER NOT NULL REFERENCES event (eventid),
    dateid         INTEGER NOT NULL REFERENCES date (dateid),  -- date of listtime
    numtickets     INTEGER NOT NULL,
    priceperticket REAL NOT NULL,
    totalprice     REAL NOT NULL,    -- numtickets * priceperticket
    listtime       TEXT NOT NULL,
    is_demo        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE sales (
    salesid    INTEGER PRIMARY KEY,
    listid     INTEGER NOT NULL REFERENCES listing (listid),
    sellerid   INTEGER NOT NULL REFERENCES users (userid),
    buyerid    INTEGER NOT NULL REFERENCES users (userid),
    eventid    INTEGER NOT NULL REFERENCES event (eventid),
    dateid     INTEGER NOT NULL REFERENCES date (dateid),     -- date of saletime
    qtysold    INTEGER NOT NULL,
    pricepaid  REAL NOT NULL,        -- TOTAL for the sale, not per ticket
    commission REAL NOT NULL,        -- 15% of pricepaid
    saletime   TEXT NOT NULL,
    is_demo    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_event_venue    ON event (venueid);
CREATE INDEX idx_event_cat      ON event (catid);
CREATE INDEX idx_event_date     ON event (dateid);
CREATE INDEX idx_listing_event  ON listing (eventid);
CREATE INDEX idx_listing_date   ON listing (dateid);
CREATE INDEX idx_sales_event    ON sales (eventid);
CREATE INDEX idx_sales_date     ON sales (dateid);
CREATE INDEX idx_sales_listing  ON sales (listid);
CREATE INDEX idx_sales_buyer    ON sales (buyerid);
CREATE INDEX idx_sales_seller   ON sales (sellerid);
