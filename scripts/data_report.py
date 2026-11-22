"""Print what is in the database after ingestion: coverage, 10-year raw metrics, NRI frequencies.

Run:  .venv/Scripts/python scripts/data_report.py

This is a human-readable check, not the scoring code. The scoring metrics in Stage 3
(backend/scoring/metrics.py) are the source of truth; this report only eyeballs the raw data.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.db import connect  # noqa: E402
from backend.settings import FIRST_YEAR, LAST_FULL_YEAR  # noqa: E402

YEARS = LAST_FULL_YEAR - FIRST_YEAR + 1
conn = connect()

print(f"Coverage ({FIRST_YEAR}-{LAST_FULL_YEAR})")
print(f"  {'hub':12s} {'rows':>5s} {'first':>11s} {'last':>11s}  nulls: snow tmin precip gust")
for r in conn.execute("""SELECT hub_id, COUNT(*) n, MIN(date) a, MAX(date) b,
                           SUM(snow_cm IS NULL) s, SUM(tmin_c IS NULL) t,
                           SUM(precip_mm IS NULL) p, SUM(gust_kmh IS NULL) g
                         FROM daily_weather GROUP BY hub_id ORDER BY hub_id"""):
    print(f"  {r['hub_id']:12s} {r['n']:5d} {r['a']:>11s} {r['b']:>11s}  "
          f"{r['s']:12d} {r['t']:4d} {r['p']:6d} {r['g']:4d}")

print(f"\n10-year averages per year (raw, before scoring)")
cols = ["snow>=0.1in", "snow>=1in", "tmin<=-15C", "rain>=25mm", "max3day_mm", "gust>=75", "precip_mm/yr"]
print(f"  {'hub':12s} {'region':8s}" + "".join(f"{c:>13s}" for c in cols))
q = f"""
WITH w AS (
  SELECT hub_id, date, snow_cm, tmin_c, precip_mm, gust_kmh,
         SUM(precip_mm) OVER (PARTITION BY hub_id ORDER BY date ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) p3
  FROM daily_weather)
SELECT h.id, h.region,
  SUM(snow_cm >= 0.254) * 1.0 / {YEARS}, SUM(snow_cm >= 2.5) * 1.0 / {YEARS},
  SUM(tmin_c <= -15) * 1.0 / {YEARS},    SUM(precip_mm >= 25) * 1.0 / {YEARS},
  MAX(p3),                               SUM(gust_kmh >= 75) * 1.0 / {YEARS},
  SUM(precip_mm) / {YEARS}
FROM w JOIN hubs h ON h.id = w.hub_id GROUP BY h.id ORDER BY h.region, h.id"""
for r in conn.execute(q):
    print(f"  {r[0]:12s} {r[1]:8s}" + "".join(f"{v:13.1f}" for v in list(r)[2:]))

print("\nFEMA NRI annualized frequency (AFREQ, events/yr) for the hub county; '-' = not applicable")
haz = ["WNTW", "HRCN", "IFLD", "CFLD", "ISTM", "TRND", "HAIL"]
print(f"  {'hub':12s} {'county':12s} {'area mi2':>9s}" + "".join(f"{h:>8s}" for h in haz))
nri = {}
for r in conn.execute("SELECT hub_id, field, value FROM nri"):
    nri.setdefault(r[0], {})[r[1]] = r[2]
for h in conn.execute("SELECT id, county_name FROM hubs ORDER BY region, id"):
    v = nri[h[0]]
    print(f"  {h[0]:12s} {h[1]:12s} {v['AREA']:9.0f}" +
          "".join(f"{'-':>8s}" if v[f'{x}_AFREQ'] is None else f"{v[f'{x}_AFREQ']:8.3g}" for x in haz))

print("\nLast ingestion runs")
for r in conn.execute("""SELECT source, status, rows_written, started_at FROM ingestion_runs
                         WHERE id IN (SELECT MAX(id) FROM ingestion_runs GROUP BY source)"""):
    print(f"  {r[0]:18s} {r[1]:6s} {r[2]:6d} rows  {r[3]}")
