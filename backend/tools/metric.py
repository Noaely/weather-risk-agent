"""get_hub_metric: one factual metric for one hub, for a single year or the 10-year window.

Answers questions like "What percentage of days in Denver last year had snowfall?". Returns the
count, the observed days, the % of observed days, days/yr, coverage, threshold and source, so the
agent can state exactly what was counted. A year outside the stored data returns status "no_data".
"""

from __future__ import annotations

import sqlite3
from datetime import date

from backend.scoring.config import MIN_COVERAGE
from backend.scoring.metrics import (
    DAY_METRICS, NRI_METRICS, SOURCE_STATION, avg_annual_max_3day_precip, day_count_metric,
    load_daily, nri_metric,
)
from backend.settings import FIRST_YEAR, LAST_FULL_YEAR
from backend.tools.hubs import not_found, resolve_hub
from backend.tools.scores import sig3

TOTALS = {"total_snowfall": ("snow_cm", "cm"), "total_precipitation": ("precip_mm", "mm")}
METRICS = [*DAY_METRICS, "avg_annual_max_3day_precip", *TOTALS, *NRI_METRICS]


def get_hub_metric(conn: sqlite3.Connection, hub: str, metric: str, year: int | None = None) -> dict:
    hid = resolve_hub(conn, hub)
    if hid is None:
        return not_found(conn, [hub])
    name = conn.execute("SELECT name FROM hubs WHERE id = ?", (hid,)).fetchone()[0]
    base = {"hub_id": hid, "name": name, "metric": metric}

    if metric in NRI_METRICS:
        m = nri_metric(conn, hid, metric)
        return {"status": "ok" if m.value is not None else "no_data", **base, "value": sig3(m.value),
                "unit": m.unit, "period": "long-term (NRI)", "source": m.source, "detail": m.note,
                **({"note": "NRI is a long-term county estimate; a specific year does not apply."} if year else {})}

    if year is not None and not FIRST_YEAR <= year <= LAST_FULL_YEAR:
        return {"status": "no_data", **base, "requested_year": year,
                "available_years": [FIRST_YEAR, LAST_FULL_YEAR],
                "message": f"No data stored for {year}. Data covers {FIRST_YEAR}-{LAST_FULL_YEAR}. Do not estimate."}

    start, end = (year, year) if year else (FIRST_YEAR, LAST_FULL_YEAR)
    rows = load_daily(conn, hid, start, end)
    period = str(start) if start == end else f"{start}-{end}"

    if metric in DAY_METRICS:
        per_year = day_count_metric(rows, metric, start, end)
        pct = day_count_metric(rows, metric, start, end, as_percent=True)
        return {"status": "ok" if pct.value is not None else "no_data", **base, "period": period,
                "qualifying_days": per_year.count, "observed_days": per_year.observed_days,
                "days_in_period": per_year.expected_days, "coverage": per_year.coverage,
                "percent_of_observed_days": pct.value,
                "days_per_year": per_year.value if start != end else None,
                "threshold": DAY_METRICS[metric][2], "source": per_year.source,
                "calendar_year_note": "Calendar year Jan-Dec (NWS NOWData shows Jul-Jun snow seasons by default)."}

    if metric == "avg_annual_max_3day_precip":
        m = avg_annual_max_3day_precip(rows, start, end)
        return {"status": "ok" if m.value is not None else "no_data", **base, "period": period,
                "value": m.value, "unit": m.unit, "coverage": m.coverage, "source": m.source,
                "meaning": "the year's largest 3-day precipitation total" if start == end
                           else "average over the years of each year's largest 3-day total",
                "detail": m.note}

    column, unit = TOTALS[metric]
    values = [getattr(r, column) for r in rows if getattr(r, column) is not None]
    expected = (date(end, 12, 31) - date(start, 1, 1)).days + 1
    coverage = len(values) / expected
    if coverage < MIN_COVERAGE:
        return {"status": "no_data", **base, "period": period, "coverage": round(coverage, 4),
                "message": f"Only {coverage:.0%} of days have data; below the {MIN_COVERAGE:.0%} minimum. Do not estimate."}
    return {"status": "ok", **base, "period": period, "value": round(sum(values), 1), "unit": unit,
            "observed_days": len(values), "days_in_period": expected, "coverage": round(coverage, 4),
            "per_year": round(sum(values) / (end - start + 1), 1) if start != end else None,
            "note": "sum over observed days only (missing days are not filled)", "source": SOURCE_STATION}
