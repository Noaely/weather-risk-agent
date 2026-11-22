"""Hub scores with a full breakdown, so the agent can answer "why".

score_hub() returns, per hazard, every metric's raw value, unit, anchors, normalized value, weight,
coverage and source; the overall score (= the worst single hazard, decision D15); the secondary
multi-hazard score (weighted average of hazards); and context-only data (NRI tornado / hail /
ice storm, model gust days).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone

from pydantic import BaseModel

from backend.scoring import config as cfg
from backend.scoring.metrics import (
    MetricValue, avg_annual_max_3day_precip, day_count_metric, load_daily, nri_metric,
)
from backend.scoring.normalize import normalize, weighted_average
from backend.settings import FIRST_YEAR, LAST_FULL_YEAR


class MetricScore(BaseModel):
    key: str
    label: str
    raw: float | None
    unit: str
    anchors: tuple[float, float]     # (raw value -> 0, raw value -> 100)
    anchor_rationale: str
    normalized: float | None
    weight: float
    coverage: float | None
    source: str
    note: str | None


class HazardScore(BaseModel):
    hazard: str
    label: str
    score: float | None
    level: str | None
    metrics: list[MetricScore]
    unknown_metrics: list[str]       # metrics with no value -> hazard score unknown


class HubScore(BaseModel):
    hub_id: str
    name: str
    region: str
    period: str
    overall: float | None            # MAIN score = worst single hazard (decision D15); used for ranking
    overall_level: str | None
    worst_hazard: str | None         # the hazard that sets the overall score
    multi_hazard: float | None       # secondary: weighted average of all hazards
    multi_hazard_level: str | None
    hazards: dict[str, HazardScore]
    context: list[MetricValue]       # not scored: tornado, hail, ice storm, strong-gust days


def config_hash() -> str:
    """Fingerprint of every scoring setting; stored with saved scores."""
    blob = json.dumps({
        "hazards": {h: [asdict(m) for m in ms] for h, ms in cfg.HAZARDS.items()},
        "overall": "worst_hazard", "weights": cfg.HAZARD_WEIGHTS, "levels": cfg.LEVELS, "per_area": cfg.FLOOD_NRI_PER_AREA,
        "min_coverage": cfg.MIN_COVERAGE,
        "thresholds": [cfg.MEASURABLE_SNOW_CM, cfg.HEAVY_SNOW_CM, cfg.EXTREME_COLD_C,
                       cfg.HEAVY_RAIN_MM, cfg.STRONG_GUST_KMH],
    }, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def raw_metric(conn: sqlite3.Connection, hub_id: str, key: str, rows, start: int, end: int) -> MetricValue:
    if key == "avg_annual_max_3day_precip":
        return avg_annual_max_3day_precip(rows, start, end)
    if key.startswith("nri_"):
        return nri_metric(conn, hub_id, key, per_area=(key == "nri_flood_freq" and cfg.FLOOD_NRI_PER_AREA))
    return day_count_metric(rows, key, start, end)


def score_hub(conn: sqlite3.Connection, hub_id: str,
              start: int = FIRST_YEAR, end: int = LAST_FULL_YEAR) -> HubScore:
    hub = conn.execute("SELECT id, name, region FROM hubs WHERE id = ?", (hub_id,)).fetchone()
    if hub is None:
        raise KeyError(hub_id)
    rows = load_daily(conn, hub_id, start, end)

    hazards: dict[str, HazardScore] = {}
    for hazard, specs in cfg.HAZARDS.items():
        metrics = []
        for spec in specs:
            mv = raw_metric(conn, hub_id, spec.key, rows, start, end)
            metrics.append(MetricScore(
                key=spec.key, label=spec.label, raw=mv.value, unit=mv.unit, anchors=(spec.lo, spec.hi),
                anchor_rationale=spec.why, normalized=normalize(mv.value, spec.lo, spec.hi),
                weight=spec.weight, coverage=mv.coverage, source=mv.source, note=mv.note,
            ))
        score = weighted_average([(m.normalized, m.weight) for m in metrics])
        hazards[hazard] = HazardScore(
            hazard=hazard, label=cfg.HAZARD_LABELS[hazard], score=score, level=cfg.level_for(score),
            metrics=metrics, unknown_metrics=[m.key for m in metrics if m.normalized is None],
        )

    # Overall = worst single hazard: a hub closes when one hazard hits it, not on an average.
    # If any hazard is unknown, the worst is unknown too (the unknown one could be the worst).
    multi = weighted_average([(h.score, cfg.HAZARD_WEIGHTS[k]) for k, h in hazards.items()])
    if any(h.score is None for h in hazards.values()):
        worst, overall = None, None
    else:
        worst = max(hazards, key=lambda k: hazards[k].score)  # type: ignore[arg-type,return-value]
        overall = hazards[worst].score

    context = [nri_metric(conn, hub_id, k) for k in ("nri_tornado_freq", "nri_hail_freq", "nri_ice_storm_freq")]
    context.append(day_count_metric(rows, "strong_gust_days", start, end))

    return HubScore(
        hub_id=hub["id"], name=hub["name"], region=hub["region"],
        period=f"{start}-{end}", overall=overall, overall_level=cfg.level_for(overall),
        worst_hazard=worst, multi_hazard=multi, multi_hazard_level=cfg.level_for(multi),
        hazards=hazards, context=context,
    )


def score_all(conn: sqlite3.Connection) -> list[HubScore]:
    """All hubs, ranked by the overall (worst-hazard) score, highest first; unknown last."""
    ids = [r[0] for r in conn.execute("SELECT id FROM hubs ORDER BY id")]
    scores = [score_hub(conn, i) for i in ids]
    return sorted(scores, key=lambda s: (s.overall is None, -(s.overall or 0), -(s.multi_hazard or 0)))


def save_scores(conn: sqlite3.Connection, scores: list[HubScore]) -> int:
    cur = conn.execute(
        "INSERT INTO score_runs (computed_at, period, config_hash) VALUES (?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), scores[0].period, config_hash()),
    )
    run_id = int(cur.lastrowid)
    records = []
    for s in scores:
        records += [(run_id, s.hub_id, k, h.score, h.level) for k, h in s.hazards.items()]
        records.append((run_id, s.hub_id, "overall", s.overall, s.overall_level))
        records.append((run_id, s.hub_id, "multi_hazard", s.multi_hazard, s.multi_hazard_level))
    conn.executemany("INSERT INTO hub_scores VALUES (?, ?, ?, ?, ?)", records)
    conn.commit()
    return run_id
