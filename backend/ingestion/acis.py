"""NOAA ACIS station data: daily snowfall, min temperature, precipitation (decision D7).

ACIS (data.rcc-acis.org) serves the official NWS climate station records; it is the service behind
NWS NOWData. One POST per station returns the whole period.

Value codes seen in 2016-2025 for our 12 stations (checked in Stage 2): plain numbers, 'T' (trace)
and 'M' (missing). Anything else raises, so a new code is noticed instead of silently misread.
Units: snow and precipitation in inches, temperature in deg F. Stored in metric.
Days are the station's local calendar day.
"""

from __future__ import annotations

import re
import sqlite3

import httpx

from backend.ingestion.common import Hub, finish_run, start_run

ACIS_URL = "https://data.rcc-acis.org/StnData"
_NUMBER = re.compile(r"-?\d+(\.\d+)?")


def parse_acis_value(raw: str) -> float | None:
    """'T' (trace, too small to measure) -> 0.0; 'M' (missing) -> None; number -> float."""
    if raw == "M":
        return None
    if raw == "T":
        return 0.0
    if _NUMBER.fullmatch(raw):
        return float(raw)
    raise ValueError(f"Unexpected ACIS value code: {raw!r}")


def inches_to_cm(x: float | None) -> float | None:
    return None if x is None else round(x * 2.54, 3)


def inches_to_mm(x: float | None) -> float | None:
    return None if x is None else round(x * 25.4, 3)


def f_to_c(x: float | None) -> float | None:
    return None if x is None else round((x - 32) * 5 / 9, 2)


def fetch_station(client: httpx.Client, station_id: str, start: str, end: str) -> list[list[str]]:
    r = client.post(ACIS_URL, json={"sid": station_id, "sdate": start, "edate": end,
                                    "elems": "snow,mint,pcpn"})
    r.raise_for_status()
    body = r.json()
    if "error" in body:
        raise RuntimeError(f"ACIS error for {station_id}: {body['error']}")
    return body["data"]  # rows of [date, snow, mint, pcpn]


def ingest_acis(conn: sqlite3.Connection, client: httpx.Client, hubs: list[Hub],
                start: str, end: str) -> int:
    details: dict = {"url": ACIS_URL, "period": [start, end], "per_hub": {}}
    run_id = start_run(conn, "acis", details)
    total = 0
    try:
        for hub in hubs:
            rows = fetch_station(client, hub.station_id, start, end)
            records = []
            counts = {"days": 0, "snow_trace": 0, "snow_missing": 0, "tmin_missing": 0,
                      "precip_trace": 0, "precip_missing": 0}
            for day, snow, tmin, pcpn in rows:
                counts["days"] += 1
                counts["snow_trace"] += snow == "T"
                counts["snow_missing"] += snow == "M"
                counts["tmin_missing"] += tmin == "M"
                counts["precip_trace"] += pcpn == "T"
                counts["precip_missing"] += pcpn == "M"
                records.append((
                    hub.id, day,
                    inches_to_cm(parse_acis_value(snow)),
                    f_to_c(parse_acis_value(tmin)),
                    inches_to_mm(parse_acis_value(pcpn)),
                    run_id,
                ))
            # Upsert only the station columns, so the gust column from Open-Meteo is untouched.
            conn.executemany(
                """INSERT INTO daily_weather (hub_id, date, snow_cm, tmin_c, precip_mm, station_run_id)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(hub_id, date) DO UPDATE SET
                     snow_cm=excluded.snow_cm, tmin_c=excluded.tmin_c,
                     precip_mm=excluded.precip_mm, station_run_id=excluded.station_run_id""",
                records,
            )
            conn.commit()
            details["per_hub"][hub.id] = {"station": hub.station_id, **counts}
            total += len(records)
            print(f"  acis  {hub.id:12s} {hub.station_id}  {len(records)} days  "
                  f"missing snow/tmin/precip = {counts['snow_missing']}/{counts['tmin_missing']}/"
                  f"{counts['precip_missing']}")
    except Exception as e:
        details["error"] = repr(e)
        finish_run(conn, run_id, "failed", total, details)
        raise
    finish_run(conn, run_id, "ok", total, details)
    return total
