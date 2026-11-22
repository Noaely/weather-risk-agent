"""Raw metrics per hub: station/gust metrics from daily rows, NRI metrics from the nri table.

Missing days (user decision, Stage 3): a day with no data is NOT a day without snow/rain.
- Missing days are excluded from both the count and the number of days.
- "days/yr" = (count / observed days) x (days in period / years), i.e. the rate on observed days.
- Every result carries its coverage (observed / days in period) so the agent can mention gaps.
- Below MIN_COVERAGE the value is None (unknown), never estimated.

The calculation functions take plain rows, so they are unit-tested without a database and reused
by the agent tools for single-year factual questions (e.g. Denver 2025).
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from functools import lru_cache
from typing import Callable, NamedTuple

from pydantic import BaseModel

from backend.ingestion.nri import read_snapshot
from backend.scoring import config as cfg


class DailyRow(NamedTuple):
    date: str            # YYYY-MM-DD
    snow_cm: float | None
    tmin_c: float | None
    precip_mm: float | None
    gust_kmh: float | None


class MetricValue(BaseModel):
    key: str
    value: float | None          # None = unknown (no data or coverage too low)
    unit: str
    period: str                  # e.g. "2016-2025" or "2025"
    count: int | None = None     # qualifying days (day-count metrics only)
    observed_days: int | None = None
    expected_days: int | None = None
    coverage: float | None = None    # 0-1; None for NRI metrics
    source: str
    note: str | None = None


# key -> (column, test on a single day's value, unit, threshold text)
DAY_METRICS: dict[str, tuple[str, Callable[[float], bool], str]] = {
    "measurable_snow_days": ("snow_cm", lambda v: v >= cfg.MEASURABLE_SNOW_CM, "snowfall >= 0.1 in (0.254 cm), NWS measurable"),
    "heavy_snow_days": ("snow_cm", lambda v: v >= cfg.HEAVY_SNOW_CM, "snowfall >= 2.5 cm (~1 in)"),
    "extreme_cold_days": ("tmin_c", lambda v: v <= cfg.EXTREME_COLD_C, "min temperature <= -15 C (5 F)"),
    "heavy_rain_days": ("precip_mm", lambda v: v >= cfg.HEAVY_RAIN_MM, "precipitation >= 25 mm (~1 in)"),
    "strong_gust_days": ("gust_kmh", lambda v: v >= cfg.STRONG_GUST_KMH, "max wind gust >= 75 km/h (~46 mph)"),
}

SOURCE_STATION = "NOAA ACIS station data"
SOURCE_GUST = "Open-Meteo historical (modelled) wind gusts"
SOURCE_NRI = "FEMA National Risk Index v1.20.0 (December 2025)"


def _period(start_year: int, end_year: int) -> tuple[str, int]:
    label = str(start_year) if start_year == end_year else f"{start_year}-{end_year}"
    expected = (date(end_year, 12, 31) - date(start_year, 1, 1)).days + 1
    return label, expected


def _source(column: str) -> str:
    return SOURCE_GUST if column == "gust_kmh" else SOURCE_STATION


def day_count_metric(rows: list[DailyRow], key: str, start_year: int, end_year: int,
                     as_percent: bool = False) -> MetricValue:
    """Days meeting a threshold: average days/yr, or (as_percent) % of observed days."""
    column, test, threshold = DAY_METRICS[key]
    label, expected = _period(start_year, end_year)
    values = [getattr(r, column) for r in rows]
    observed = [v for v in values if v is not None]
    coverage = len(observed) / expected
    value: float | None = None
    count = sum(test(v) for v in observed)
    if coverage >= cfg.MIN_COVERAGE and observed:
        if as_percent:
            value = round(count / len(observed) * 100, 2)
        else:
            years = end_year - start_year + 1
            value = round(count / len(observed) * expected / years, 2)
    return MetricValue(
        key=key, value=value, unit="% of days" if as_percent else "days/yr", period=label,
        count=count, observed_days=len(observed), expected_days=expected, coverage=round(coverage, 4),
        source=_source(column),
        note=f"threshold: {threshold}; {count} qualifying days out of {len(observed)} observed",
    )


def avg_annual_max_3day_precip(rows: list[DailyRow], start_year: int, end_year: int) -> MetricValue:
    """Mean over years of each calendar year's maximum 3-day precipitation total.

    A window counts only if all 3 days are consecutive and observed; it belongs to the year of its
    last day. Averaging yearly maxima keeps one extreme event (Harvey) from defining the metric.
    """
    label, expected = _period(start_year, end_year)
    observed = sum(r.precip_mm is not None for r in rows)
    coverage = observed / expected
    by_date = {r.date: r.precip_mm for r in rows}
    yearly_max: dict[int, float] = defaultdict(lambda: -1.0)
    for r in rows:
        d = date.fromisoformat(r.date)
        window = [by_date.get((d - timedelta(days=k)).isoformat()) for k in (2, 1, 0)]
        if None not in window:
            yearly_max[d.year] = max(yearly_max[d.year], sum(window))  # type: ignore[arg-type]
    value = None
    if coverage >= cfg.MIN_COVERAGE and yearly_max:
        value = round(sum(yearly_max.values()) / len(yearly_max), 1)
    top_year = max(yearly_max, key=yearly_max.get) if yearly_max else None  # type: ignore[arg-type]
    return MetricValue(
        key="avg_annual_max_3day_precip", value=value, unit="mm", period=label,
        observed_days=observed, expected_days=expected, coverage=round(coverage, 4), source=SOURCE_STATION,
        note=(f"averaged over {len(yearly_max)} years; wettest 3 days in the period: "
              f"{yearly_max[top_year]:.0f} mm in {top_year}") if top_year else None,
    )


def load_daily(conn: sqlite3.Connection, hub_id: str, start_year: int, end_year: int) -> list[DailyRow]:
    rows = conn.execute(
        """SELECT date, snow_cm, tmin_c, precip_mm, gust_kmh FROM daily_weather
           WHERE hub_id = ? AND date BETWEEN ? AND ? ORDER BY date""",
        (hub_id, f"{start_year}-01-01", f"{end_year}-12-31"),
    ).fetchall()
    return [DailyRow(*r) for r in rows]


# --------------------------------------------------------------------------- NRI
# key -> NRI fields summed (null = "not applicable" = 0 events), label
NRI_METRICS: dict[str, tuple[list[str], str]] = {
    "nri_winter_freq": (["WNTW_AFREQ"], "Winter weather"),
    "nri_hurricane_freq": (["HRCN_AFREQ"], "Hurricane"),
    "nri_flood_freq": (["IFLD_AFREQ", "CFLD_AFREQ"], "Inland + coastal flooding"),
    "nri_tornado_freq": (["TRND_AFREQ"], "Tornado"),
    "nri_hail_freq": (["HAIL_AFREQ"], "Hail"),
    "nri_ice_storm_freq": (["ISTM_AFREQ"], "Ice storm"),
}


def _nri_raw(fields: list[str], row: dict, per_area: bool) -> float:
    v = sum((row.get(f) or 0.0) for f in fields)
    return v / row["AREA"] * 1000 if per_area else v


@lru_cache(maxsize=None)
def _national_values(fields: tuple[str, ...], per_area: bool) -> list[float]:
    return sorted(_nri_raw(list(fields), r, per_area) for r in read_snapshot().values())


def national_percentile(fields: list[str], value: float, per_area: bool = False) -> float:
    """Share of US counties (3,232, NRI snapshot) with a lower value, 0-100."""
    xs = _national_values(tuple(fields), per_area)
    return round(sum(x < value for x in xs) / len(xs) * 100, 1)


def nri_metric(conn: sqlite3.Connection, hub_id: str, key: str, per_area: bool = False) -> MetricValue:
    fields, label = NRI_METRICS[key]
    rows = dict(conn.execute("SELECT field, value FROM nri WHERE hub_id = ?", (hub_id,)).fetchall())
    if not rows:  # county not loaded at all -> unknown, not 0
        return MetricValue(key=key, value=None, unit="events/yr", period="NRI long-term",
                           source=SOURCE_NRI, note="no NRI data for this hub's county")
    value = round(_nri_raw(fields, rows, per_area), 4)
    not_applicable = [f for f in fields if rows.get(f) is None]
    note = f"{label}: {value:.3g} events/yr in the county"
    note += f"{' per 1,000 sq mi' if per_area else ''}; US county percentile {national_percentile(fields, value, per_area)}"
    if not_applicable:
        note += f"; {', '.join(not_applicable)} not applicable in this county (counted as 0)"
    if key == "nri_tornado_freq":
        note += ("; county-wide count: larger counties record more tornadoes (US rank correlation with "
                 "county area +0.34), so compare hubs with care")
    return MetricValue(key=key, value=value, unit="events/yr per 1,000 sq mi" if per_area else "events/yr",
                       period="NRI long-term", source=SOURCE_NRI, note=note)
