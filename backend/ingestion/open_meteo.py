"""Open-Meteo Historical (archive) API: daily max wind gusts only (decision D7).

Requested at the hub's station coordinates so every metric for a hub comes from the same place.
Open-Meteo snaps to the nearest model grid cell; the returned cell is recorded in the run details.
timezone=auto -> local calendar days, matching the station data. (Open-Meteo days follow local
clock time incl. daylight saving; NWS climate days use local standard time. The 1-hour difference
in summer is negligible for a daily maximum.)
"""

from __future__ import annotations

import sqlite3
import time

import httpx

from backend.ingestion.common import Hub, finish_run, start_run

OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"
VARIABLE = "wind_gusts_10m_max"


def fetch_gusts(client: httpx.Client, hub: Hub, start: str, end: str) -> dict:
    params = {"latitude": hub.lat, "longitude": hub.lon, "start_date": start, "end_date": end,
              "daily": VARIABLE, "timezone": "auto"}
    for attempt in range(3):
        r = client.get(OPEN_METEO_URL, params=params)
        if r.status_code == 429:  # free-tier rate limit: wait and retry
            time.sleep(10 * (attempt + 1))
            continue
        r.raise_for_status()
        body = r.json()
        if body["daily_units"][VARIABLE] != "km/h":
            raise RuntimeError(f"Unexpected gust unit: {body['daily_units'][VARIABLE]}")
        return body
    raise RuntimeError(f"Open-Meteo rate limit not cleared for {hub.id}")


def ingest_gusts(conn: sqlite3.Connection, client: httpx.Client, hubs: list[Hub],
                 start: str, end: str) -> int:
    details: dict = {"url": OPEN_METEO_URL, "variable": VARIABLE, "period": [start, end], "per_hub": {}}
    run_id = start_run(conn, "open_meteo_gusts", details)
    total = 0
    try:
        for hub in hubs:
            body = fetch_gusts(client, hub, start, end)
            days, gusts = body["daily"]["time"], body["daily"][VARIABLE]
            records = [(hub.id, d, g, run_id) for d, g in zip(days, gusts)]
            conn.executemany(
                """INSERT INTO daily_weather (hub_id, date, gust_kmh, gust_run_id) VALUES (?, ?, ?, ?)
                   ON CONFLICT(hub_id, date) DO UPDATE SET
                     gust_kmh=excluded.gust_kmh, gust_run_id=excluded.gust_run_id""",
                records,
            )
            conn.commit()
            details["per_hub"][hub.id] = {
                "grid_cell": [body["latitude"], body["longitude"]],
                "timezone": body["timezone"], "days": len(days),
                "missing": sum(g is None for g in gusts),
            }
            total += len(records)
            print(f"  gusts {hub.id:12s} grid ({body['latitude']:.3f}, {body['longitude']:.3f})  "
                  f"{len(days)} days  missing = {details['per_hub'][hub.id]['missing']}")
    except Exception as e:
        details["error"] = repr(e)
        finish_run(conn, run_id, "failed", total, details)
        raise
    finish_run(conn, run_id, "ok", total, details)
    return total
