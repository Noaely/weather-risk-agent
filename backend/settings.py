"""Shared paths and the analysis period. Imported by ingestion, scoring and tools."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
HUBS_FILE = DATA_DIR / "hubs.json"
DB_PATH = DATA_DIR / "weather.db"
FRONTEND_DIST = ROOT / "frontend" / "dist"

# "Last year" = the previous full calendar year (Jan-Dec). Computed, so a nightly job that re-ingests
# keeps working next year without a code change. On 2026-11-22 this is 2025.
# ANALYSIS_LAST_YEAR pins it. Production sets it, because it ships a fixed database snapshot: without
# the pin, on 1 January the window would move to years the snapshot does not contain.
LAST_FULL_YEAR: int = int(os.getenv("ANALYSIS_LAST_YEAR") or date.today().year - 1)

# Scores use a 10-year window ending last year (decision D1): 2016-2025 today.
SCORING_YEARS: int = 10
FIRST_YEAR: int = LAST_FULL_YEAR - SCORING_YEARS + 1

PERIOD_START = f"{FIRST_YEAR}-01-01"
PERIOD_END = f"{LAST_FULL_YEAR}-12-31"
