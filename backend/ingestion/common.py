"""Helpers shared by the ingestion steps: hub loading/sync and ingestion-run bookkeeping."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel

from backend.settings import HUBS_FILE


class Hub(BaseModel):
    id: str
    name: str
    city: str
    state: str
    region: str
    station_id: str
    ghcn_id: str | None = None
    station_name: str
    lat: float
    lon: float
    county_fips: str
    county_name: str
    aliases: list[str] = []


def load_hubs_file() -> list[Hub]:
    raw = json.loads(HUBS_FILE.read_text(encoding="utf-8"))
    return [Hub(**h) for h in raw["hubs"]]


def sync_hubs(conn: sqlite3.Connection, hubs: list[Hub]) -> None:
    """Make the hubs table match hubs.json exactly.

    Hubs removed from the file are deleted together with their weather and NRI rows (ON DELETE
    CASCADE), so replacing hubs.json with the client's list leaves no stale data behind.
    """
    ids = [h.id for h in hubs]
    conn.execute(f"DELETE FROM hubs WHERE id NOT IN ({','.join('?' * len(ids))})", ids)
    conn.executemany(
        """INSERT INTO hubs (id, name, city, state, region, station_id, ghcn_id, station_name,
                             lat, lon, county_fips, county_name, aliases)
           VALUES (:id, :name, :city, :state, :region, :station_id, :ghcn_id, :station_name,
                   :lat, :lon, :county_fips, :county_name, :aliases)
           ON CONFLICT(id) DO UPDATE SET
             name=excluded.name, city=excluded.city, state=excluded.state, region=excluded.region,
             station_id=excluded.station_id, ghcn_id=excluded.ghcn_id,
             station_name=excluded.station_name, lat=excluded.lat, lon=excluded.lon,
             county_fips=excluded.county_fips, county_name=excluded.county_name,
             aliases=excluded.aliases""",
        [{**h.model_dump(), "aliases": json.dumps(h.aliases)} for h in hubs],
    )
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def start_run(conn: sqlite3.Connection, source: str, details: dict[str, Any]) -> int:
    cur = conn.execute(
        "INSERT INTO ingestion_runs (source, started_at, status, details) VALUES (?, ?, 'running', ?)",
        (source, _now(), json.dumps(details)),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, rows: int,
               details: dict[str, Any]) -> None:
    conn.execute(
        "UPDATE ingestion_runs SET finished_at=?, status=?, rows_written=?, details=? WHERE id=?",
        (_now(), status, rows, json.dumps(details), run_id),
    )
    conn.commit()


def http_client() -> httpx.Client:
    # Retries cover dropped connections; HTTP errors are raised by each caller.
    return httpx.Client(timeout=120, transport=httpx.HTTPTransport(retries=3))
