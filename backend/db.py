"""SQLite schema and connection helper.

Why SQLite: one file, no server, plenty for 12 hubs x 10 years (~44k daily rows). The SQL is plain,
so moving to Postgres later means changing the connection, not the queries.

Tables
- hubs            one row per hub, synced from data/hubs.json
- daily_weather   one row per hub per local calendar day. Station columns (ACIS) and the gust column
                  (Open-Meteo) are written by separate ingestion steps, each recording its run id.
- nri             FEMA NRI values for the hub's county, long format: (hub_id, field) -> value.
                  NULL is kept as NULL here (NRI's "not applicable"); scoring decides what it means.
- ingestion_runs  one row per ingestion step: source, time, status, rows written, details (JSON).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.settings import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS hubs (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    city          TEXT NOT NULL,
    state         TEXT NOT NULL,
    region        TEXT NOT NULL,
    station_id    TEXT NOT NULL,      -- ACIS / ICAO id, e.g. KDEN
    ghcn_id       TEXT,
    station_name  TEXT NOT NULL,
    lat           REAL NOT NULL,      -- station coordinates; all weather data is taken here
    lon           REAL NOT NULL,
    county_fips   TEXT NOT NULL,      -- county containing the station point (used for NRI)
    county_name   TEXT NOT NULL,
    aliases       TEXT NOT NULL       -- JSON list of alternative names
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,      -- 'acis' | 'open_meteo_gusts' | 'fema_nri'
    started_at    TEXT NOT NULL,      -- UTC ISO timestamp
    finished_at   TEXT,
    status        TEXT NOT NULL,      -- 'running' | 'ok' | 'failed'
    rows_written  INTEGER,
    details       TEXT                -- JSON: period, url, data-quality counts, errors
);

CREATE TABLE IF NOT EXISTS daily_weather (
    hub_id          TEXT NOT NULL REFERENCES hubs(id) ON DELETE CASCADE,
    date            TEXT NOT NULL,    -- local calendar day, YYYY-MM-DD
    snow_cm         REAL,             -- ACIS, converted from inches; trace -> 0; missing -> NULL
    tmin_c          REAL,             -- ACIS, converted from deg F
    precip_mm       REAL,             -- ACIS, converted from inches; trace -> 0
    gust_kmh        REAL,             -- Open-Meteo daily max wind gust at the station coordinates
    station_run_id  INTEGER REFERENCES ingestion_runs(id),
    gust_run_id     INTEGER REFERENCES ingestion_runs(id),
    PRIMARY KEY (hub_id, date)
);

CREATE TABLE IF NOT EXISTS nri (
    hub_id   TEXT NOT NULL REFERENCES hubs(id) ON DELETE CASCADE,
    field    TEXT NOT NULL,           -- NRI column name, e.g. WNTW_AFREQ
    value    REAL,                    -- NULL = NRI "not applicable" (kept as-is)
    run_id   INTEGER REFERENCES ingestion_runs(id),
    PRIMARY KEY (hub_id, field)
);
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    """Open the database with named-column rows and foreign keys enforced."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
