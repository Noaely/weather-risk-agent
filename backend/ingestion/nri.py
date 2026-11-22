"""FEMA National Risk Index (December 2025, v1.20.0) county data (decisions D2, D3, D8, D9).

Source: FEMA's official ArcGIS Online feature service (hazards.fema.gov blocks our network):
  https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/National_Risk_Index_Counties/FeatureServer/0
  item page: https://www.arcgis.com/home/item.html?id=39485e8035d446a5bff03259508ae355

The selected fields for ALL US counties are saved once to data/raw/nri_counties.csv (+ .meta.json).
Later runs read that snapshot unless --refresh-nri is given, so ingestion is reproducible and does
not depend on ArcGIS. The national table also lets Stage 3 set anchors from the US distribution.

Only the hub counties are loaded into the `nri` table. NULL ("not applicable") is kept as NULL.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timezone

import httpx

from backend.ingestion.common import Hub, finish_run, start_run
from backend.settings import RAW_DIR

NRI_LAYER_URL = ("https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/"
                 "National_Risk_Index_Counties/FeatureServer/0")
SNAPSHOT = RAW_DIR / "nri_counties.csv"
SNAPSHOT_META = RAW_DIR / "nri_counties.meta.json"

# Scored hazards (WNTW, HRCN, IFLD, CFLD) and context-only hazards (ISTM, TRND, HAIL).
HAZARDS = {
    "WNTW": "Winter weather", "HRCN": "Hurricane", "IFLD": "Inland flooding",
    "CFLD": "Coastal flooding", "ISTM": "Ice storm", "TRND": "Tornado", "HAIL": "Hail",
}
# AFREQ is what we score (D8). EVNTS (recorded events) and ALR_NPCTL / RISKS are kept for
# explanation and comparison only.
SUFFIXES = ["AFREQ", "EVNTS", "ALR_NPCTL", "RISKS"]
ID_FIELDS = ["STCOFIPS", "STATEABBRV", "COUNTY", "NRI_VER"]
NUMERIC_FIELDS = ["AREA", "POPULATION"] + [f"{h}_{s}" for h in HAZARDS for s in SUFFIXES]
FIELDS = ID_FIELDS + NUMERIC_FIELDS


def download_snapshot(client: httpx.Client) -> dict:
    """Page through all counties (service max is 2000 records per request) and write the CSV."""
    rows: list[dict] = []
    offset = 0
    while True:
        r = client.get(f"{NRI_LAYER_URL}/query", params={
            "where": "1=1", "outFields": ",".join(FIELDS), "returnGeometry": "false",
            "orderByFields": "OBJECTID", "resultOffset": offset, "resultRecordCount": 2000,
            "f": "json",
        })
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            raise RuntimeError(f"NRI query error: {body['error']}")
        rows += [f["attributes"] for f in body["features"]]
        if not body.get("exceededTransferLimit"):
            break
        offset += len(body["features"])

    versions = sorted({r["NRI_VER"] for r in rows})
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    with SNAPSHOT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in sorted(rows, key=lambda r: r["STCOFIPS"]):
            w.writerow({k: ("" if r[k] is None else r[k]) for k in FIELDS})
    meta = {"source": NRI_LAYER_URL, "nri_version": versions,
            "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "counties": len(rows), "fields": FIELDS}
    SNAPSHOT_META.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta


def read_snapshot() -> dict[str, dict[str, float | str | None]]:
    """FIPS -> {field: value}. Empty CSV cells (NRI null) become None."""
    out: dict[str, dict] = {}
    with SNAPSHOT.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rec: dict = {k: r[k] for k in ID_FIELDS}
            rec.update({k: (float(r[k]) if r[k] != "" else None) for k in NUMERIC_FIELDS})
            out[r["STCOFIPS"]] = rec
    return out


def ingest_nri(conn: sqlite3.Connection, client: httpx.Client, hubs: list[Hub],
               refresh: bool) -> int:
    run_id = start_run(conn, "fema_nri", {"url": NRI_LAYER_URL})
    try:
        if refresh or not SNAPSHOT.exists():
            meta = download_snapshot(client)
            print(f"  nri   downloaded {meta['counties']} counties -> {SNAPSHOT.name} ({meta['nri_version']})")
        else:
            meta = json.loads(SNAPSHOT_META.read_text(encoding="utf-8"))
            print(f"  nri   using snapshot {SNAPSHOT.name} from {meta['downloaded_at']} ({meta['nri_version']})")
        table = read_snapshot()

        records = []
        for hub in hubs:
            row = table.get(hub.county_fips)
            if row is None:
                raise RuntimeError(f"{hub.id}: county FIPS {hub.county_fips} not in NRI")
            if row["COUNTY"] != hub.county_name:
                raise RuntimeError(f"{hub.id}: FIPS {hub.county_fips} is {row['COUNTY']} in NRI, "
                                   f"hubs.json says {hub.county_name}")
            records += [(hub.id, k, row[k], run_id) for k in NUMERIC_FIELDS]
        conn.executemany(
            """INSERT INTO nri (hub_id, field, value, run_id) VALUES (?, ?, ?, ?)
               ON CONFLICT(hub_id, field) DO UPDATE SET value=excluded.value, run_id=excluded.run_id""",
            records,
        )
        conn.commit()
    except Exception as e:
        finish_run(conn, run_id, "failed", 0, {"url": NRI_LAYER_URL, "error": repr(e)})
        raise
    finish_run(conn, run_id, "ok", len(records), {
        "url": NRI_LAYER_URL, "snapshot": SNAPSHOT.name, "nri_version": meta["nri_version"],
        "snapshot_downloaded_at": meta["downloaded_at"], "fields_per_hub": len(NUMERIC_FIELDS)})
    print(f"  nri   {len(hubs)} hub counties x {len(NUMERIC_FIELDS)} fields = {len(records)} values")
    return len(records)
