"""Hub lookup by the names people actually type ("Dallas-Fort Worth", "KC", "Kansas City, MO")."""

from __future__ import annotations

import json
import re
import sqlite3


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower().replace("-", " ")).strip()


def hub_index(conn: sqlite3.Connection) -> dict[str, str]:
    """normalized name -> hub id, from id, name, city, 'city st', station id and aliases."""
    index: dict[str, str] = {}
    for r in conn.execute("SELECT id, name, city, state, station_id, aliases FROM hubs"):
        names = [r["id"].replace("_", " "), r["name"], r["city"], f"{r['city']} {r['state']}",
                 r["station_id"], *json.loads(r["aliases"])]
        for n in names:
            index[_norm(n)] = r["id"]
    return index


def resolve_hub(conn: sqlite3.Connection, name: str) -> str | None:
    """Return the hub id for a user-typed name, or None if it is not one of our hubs."""
    index = hub_index(conn)
    key = _norm(name)
    if key in index:
        return index[key]
    # "Kansas City, MO" / "Houston TX": drop a trailing 2-letter state and try again
    key = re.sub(r" [a-z]{2}$", "", key)
    return index.get(key)


def known_hub_names(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT name FROM hubs ORDER BY name")]


def not_found(conn: sqlite3.Connection, names: list[str]) -> dict:
    return {
        "status": "not_found",
        "unknown_hubs": names,
        "message": "Not one of the client's hubs in this system. No data exists for it; do not estimate.",
        "known_hubs": known_hub_names(conn),
    }


def list_hubs(conn: sqlite3.Connection, region: str | None = None) -> dict:
    q = "SELECT id, name, state, region, station_id, station_name, county_name FROM hubs"
    rows = conn.execute(q + (" WHERE region = ?" if region else "") + " ORDER BY region, name",
                        (region,) if region else ()).fetchall()
    return {
        "status": "ok",
        "region": region,
        "hubs": [{"hub_id": r["id"], "name": r["name"], "state": r["state"], "region": r["region"],
                  "weather_station": f"{r['station_name']} ({r['station_id']})",
                  "nri_county": f"{r['county_name']} County"} for r in rows],
        "regions": [r[0] for r in conn.execute("SELECT DISTINCT region FROM hubs ORDER BY region")],
        "note": "Assumed hub list (the client did not provide one). Each hub is located at its main-airport NWS station.",
    }
