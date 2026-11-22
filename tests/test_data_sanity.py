"""Sanity checks on the ingested database. Skipped if the DB has not been built yet.

These check the data against independent facts (NWS NOWData) and obvious climate expectations,
so a unit mix-up, a wrong station or a broken upsert shows up here.
"""

from datetime import date

import pytest

from backend.db import connect
from backend.ingestion.common import load_hubs_file
from backend.settings import DB_PATH, FIRST_YEAR, LAST_FULL_YEAR

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="run backend.ingestion.run first")

EXPECTED_DAYS = (date(LAST_FULL_YEAR, 12, 31) - date(FIRST_YEAR, 1, 1)).days + 1


@pytest.fixture(scope="module")
def conn():
    c = connect()
    yield c
    c.close()


def days_per_year(conn, hub: str, condition: str) -> float:
    """Average number of days per year meeting a SQL condition over the scoring window."""
    n = conn.execute(f"SELECT COUNT(*) FROM daily_weather WHERE hub_id=? AND {condition}", (hub,)).fetchone()[0]
    return n / (LAST_FULL_YEAR - FIRST_YEAR + 1)


def test_every_hub_has_every_day(conn):
    hubs = [h.id for h in load_hubs_file()]
    rows = dict(conn.execute("SELECT hub_id, COUNT(*) FROM daily_weather GROUP BY hub_id").fetchall())
    assert set(rows) == set(hubs)
    assert all(n == EXPECTED_DAYS for n in rows.values()), rows


def test_no_missing_temperature_or_gusts(conn):
    nulls = conn.execute(
        "SELECT SUM(tmin_c IS NULL), SUM(gust_kmh IS NULL) FROM daily_weather").fetchone()
    assert tuple(nulls) == (0, 0)


def test_missing_snow_is_rare(conn):
    # Miami has the most (106 of 3653) and never gets snow; everywhere else it must be < 1%.
    rows = conn.execute("SELECT hub_id, SUM(snow_cm IS NULL) FROM daily_weather GROUP BY hub_id").fetchall()
    for hub, missing in rows:
        assert missing / EXPECTED_DAYS < (0.03 if hub == "miami" else 0.01), (hub, missing)


@pytest.mark.skipif(LAST_FULL_YEAR != 2025, reason="reference value is for calendar year 2025")
def test_denver_2025_measurable_snow_days_match_nws_nowdata(conn):
    # Checked by hand on NWS NOWData (monthly snowfall days >= 0.1 in): 23 Jan-Jun + 3 Jul-Dec = 26.
    n = conn.execute("""SELECT COUNT(*) FROM daily_weather
                        WHERE hub_id='denver' AND date BETWEEN '2025-01-01' AND '2025-12-31'
                          AND snow_cm >= 0.254""").fetchone()[0]
    assert n == 26


def test_climate_common_sense(conn):
    assert days_per_year(conn, "miami", "snow_cm >= 0.254") == 0
    assert days_per_year(conn, "minneapolis", "tmin_c <= -15") > days_per_year(conn, "atlanta", "tmin_c <= -15")
    assert days_per_year(conn, "houston", "precip_mm >= 25") > days_per_year(conn, "phoenix", "precip_mm >= 25")
    assert days_per_year(conn, "denver", "snow_cm >= 2.5") > days_per_year(conn, "dallas", "snow_cm >= 2.5")


def test_nri_loaded_for_every_hub(conn):
    rows = dict(conn.execute("SELECT hub_id, COUNT(*) FROM nri GROUP BY hub_id").fetchall())
    assert len(rows) == len(load_hubs_file()) and set(rows.values()) == {30}
    v = conn.execute("SELECT value FROM nri WHERE hub_id='minneapolis' AND field='WNTW_AFREQ'").fetchone()[0]
    assert v == pytest.approx(16.6, abs=0.1)  # seen in the Stage 1 probe


def test_last_runs_succeeded(conn):
    for source in ["acis", "open_meteo_gusts", "fema_nri"]:
        status = conn.execute("SELECT status FROM ingestion_runs WHERE source=? ORDER BY id DESC LIMIT 1",
                              (source,)).fetchone()
        assert status is not None and status[0] == "ok", source
