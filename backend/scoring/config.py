"""All scoring thresholds, anchors and weights in one place. These are assumptions the client can tune.

How a score is built
1. Raw metric per hub over the scoring window (10 years, decision D1), e.g. "13.3 days/yr with >= 1 in snow".
2. Normalize to 0-100 with FIXED anchors: value <= lo -> 0, value >= hi -> 100, linear in between.
   Anchors are fixed (not min-max across our hubs) so a score means the same thing over time and an
   alert on a score change is meaningful. Anchors come from reference points OUTSIDE our 12 hubs:
   - station metrics: the same metric at well-known extreme US stations (ACIS 2016-2025, Stage 3 check)
   - NRI metrics: one rule, 100 = the US county 99th percentile (all 3,232 counties,
     data/raw/nri_counties.csv, NRI null counted as 0). Hardcoded, so a refreshed NRI snapshot
     cannot silently move the scale.
3. Hazard score = weighted average of its metric scores.
   OVERALL score = the WORST single hazard score (decision D15): a hub closes when one hazard hits
   it, not on an average. Hubs are ranked by it. The weighted average of hazard scores
   (HAZARD_WEIGHTS) is reported next to it as a secondary "multi-hazard" score.

Missing data: a station metric needs at least MIN_COVERAGE of its days observed, otherwise it is
unknown (None). If any metric of a hazard is unknown, the hazard is unknown; if any hazard is
unknown, the overall (worst) and multi-hazard scores are unknown, because the unknown hazard could be
the worst one. Nothing is guessed or filled with 0.
"""

from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------- thresholds (per day)
MEASURABLE_SNOW_CM = 0.254   # 0.1 in, the NWS definition of measurable snowfall. Factual questions.
HEAVY_SNOW_CM = 2.5          # ~1 in: disruption starts with real accumulation. Scoring.
EXTREME_COLD_C = -15.0       # 5 F. No national standard (NWS cold criteria are local): an assumption.
HEAVY_RAIN_MM = 25.0         # ~1 in/day, close to the "very heavy precipitation day" index (>= 20 mm).
STRONG_GUST_KMH = 75.0       # ~46 mph, lower bound of the NWS Wind Advisory gust range. Context only.

# A station metric is computed only if at least this share of its days has data. Below that, the
# metric is unknown rather than estimated from too few days.
MIN_COVERAGE = 0.90


@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    unit: str
    lo: float        # raw value that maps to 0
    hi: float        # raw value that maps to 100
    weight: float    # weight inside its hazard
    source: str      # 'station' | 'nri'
    why: str         # anchor rationale, shown in the breakdown and docs


# --------------------------------------------------------------------------- hazards
HAZARDS: dict[str, list[MetricSpec]] = {
    "winter": [
        MetricSpec("heavy_snow_days", "Days with snowfall >= 1 in (2.5 cm)", "days/yr", 0, 25, 0.4, "station",
                   "100 = Great Lakes snowbelt (Buffalo 24.6, Duluth 24.2, Syracuse 29.4 days/yr)"),
        MetricSpec("extreme_cold_days", "Days with min temperature <= -15 C (5 F)", "days/yr", 0, 45, 0.3, "station",
                   "100 = northern-tier cold (Duluth 46, Fargo 51.7 days/yr)"),
        MetricSpec("nri_winter_freq", "FEMA NRI winter weather frequency (WNTW_AFREQ)", "events/yr", 0, 41, 0.3, "nri",
                   "100 = US county 99th percentile (41.0 events/yr)"),
    ],
    # Wind gusts are NOT part of the hurricane score: Open-Meteo model gusts peak in the Midwest
    # (Columbus 6.2 days/yr >= 75 km/h vs Miami 1.1) and do not measure hurricane exposure.
    # They are kept as a context metric. Hurricane exposure is the long-term NRI layer.
    "hurricane": [
        MetricSpec("nri_hurricane_freq", "FEMA NRI hurricane frequency (HRCN_AFREQ)", "events/yr", 0, 0.306, 1.0, "nri",
                   "100 = US county 99th percentile (0.306 events/yr; most exposed: NC Outer Banks 0.46, South Florida)"),
    ],
    "flood": [
        MetricSpec("heavy_rain_days", "Days with precipitation >= 25 mm (~1 in)", "days/yr", 0, 25, 0.4, "station",
                   "100 = wettest Gulf Coast (New Orleans 22.5, Mobile 20.9 days/yr)"),
        MetricSpec("avg_annual_max_3day_precip", "Average of each year's maximum 3-day precipitation", "mm", 25, 200, 0.4, "station",
                   "0 = ~1 in (nuisance rain only); 100 = ~8 in in 3 days (major flooding). Yearly maxima are "
                   "averaged so one event (Harvey 2017: 722 mm) cannot set the metric alone"),
        MetricSpec("nri_flood_freq", "FEMA NRI inland + coastal flood frequency (IFLD_AFREQ + CFLD_AFREQ)", "events/yr", 0, 9.08, 0.2, "nri",
                   "100 = US county 99th percentile of inland + coastal (9.08 events/yr). Low weight: county-level and "
                   "Maricopa (Phoenix) is the top US county although measured heavy rain there is the lowest of our hubs"),
    ],
}

# Weights for the secondary multi-hazard average only (the overall score is the worst hazard).
HAZARD_WEIGHTS: dict[str, float] = {"winter": 1 / 3, "hurricane": 1 / 3, "flood": 1 / 3}  # equal: no client data to prefer one

HAZARD_LABELS = {"winter": "Winter", "hurricane": "Hurricane", "flood": "Flood / heavy rain"}

# If True, NRI flood frequency is divided by county area (per 1,000 sq mi) before normalizing.
# Default False: across all US counties inland-flood AFREQ does not grow with area (Spearman -0.01),
# and dividing makes small urban counties (Denver, Clayton) look flood-prone. See ARCHITECTURE.md.
FLOOD_NRI_PER_AREA = False

# --------------------------------------------------------------------------- risk levels
# Fixed equal thirds of the 0-100 scale. A label is a reading aid; the number is what is computed.
LEVELS = [(66.0, "high"), (33.0, "moderate"), (0.0, "low")]


def level_for(score: float | None) -> str | None:
    if score is None:
        return None
    for lower, name in LEVELS:
        if score >= lower:
            return name
    return "low"


# --------------------------------------------------------------------------- context only (not scored)
# NRI hazards stored so the agent can back up answers with data (decision D3), plus model gust days.
CONTEXT_NRI = {"TRND": "Tornado", "HAIL": "Hail", "ISTM": "Ice storm"}
