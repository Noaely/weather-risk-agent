"""Run ingestion: sync hubs, then fetch station data, gusts and NRI into SQLite.

    .venv/Scripts/python -m backend.ingestion.run                 # everything
    .venv/Scripts/python -m backend.ingestion.run --only acis     # one source
    .venv/Scripts/python -m backend.ingestion.run --refresh-nri   # re-download the NRI snapshot

Safe to re-run: every write is an upsert keyed on (hub, date) or (hub, field), so a second run
overwrites instead of duplicating. Each source records its own row in ingestion_runs.
"""

from __future__ import annotations

import argparse

from backend.db import connect, init_db
from backend.ingestion.acis import ingest_acis
from backend.ingestion.common import http_client, load_hubs_file, sync_hubs
from backend.ingestion.nri import ingest_nri
from backend.ingestion.open_meteo import ingest_gusts
from backend.settings import DB_PATH, PERIOD_END, PERIOD_START

SOURCES = ["acis", "gusts", "nri"]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--only", choices=SOURCES, action="append", help="run only these sources")
    p.add_argument("--refresh-nri", action="store_true", help="re-download the NRI snapshot")
    p.add_argument("--start", default=PERIOD_START)
    p.add_argument("--end", default=PERIOD_END)
    args = p.parse_args()
    sources = args.only or SOURCES

    conn = connect()
    init_db(conn)
    hubs = load_hubs_file()
    sync_hubs(conn, hubs)
    print(f"DB {DB_PATH}  |  {len(hubs)} hubs  |  period {args.start} .. {args.end}")

    with http_client() as client:
        if "acis" in sources:
            ingest_acis(conn, client, hubs, args.start, args.end)
        if "gusts" in sources:
            ingest_gusts(conn, client, hubs, args.start, args.end)
        if "nri" in sources:
            ingest_nri(conn, client, hubs, refresh=args.refresh_nri)
    conn.close()
    print("done")


if __name__ == "__main__":
    main()
