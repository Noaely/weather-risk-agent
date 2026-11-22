"""Score tools: rank_hubs, compare_hubs, explain_score.

Scores come from backend.scoring (deterministic). They are cached per process and recomputed
whenever a new ingestion run or a scoring-config change appears.
"""

from __future__ import annotations

import sqlite3
import threading

from backend.scoring import config as cfg
from backend.scoring.score import HubScore, config_hash, score_all
from backend.tools.hubs import not_found, resolve_hub

def sig3(v: float | None) -> float | None:
    """Display rounding to 3 significant figures (2.6786 -> 2.68, 0.0115 -> 0.0115). Scores keep full precision."""
    return None if v is None else float(f"{v:.3g}")


_cache: dict[tuple, dict[str, HubScore]] = {}
_lock = threading.Lock()

SCALE = "0-100 (low < 33, moderate 33-66, high >= 66)"
METHOD = ("overall = the worst single hazard score (a hub closes when one hazard hits it); "
          "multi_hazard = equal-weight average of the three hazards (breadth of exposure). "
          "Scores use 10-year averages; anchors are fixed at US extremes, not relative to these hubs.")


def all_scores(conn: sqlite3.Connection) -> dict[str, HubScore]:
    version = (conn.execute("SELECT MAX(id) FROM ingestion_runs").fetchone()[0], config_hash())
    with _lock:
        if version not in _cache:
            _cache.clear()
            _cache[version] = {s.hub_id: s for s in score_all(conn)}
        return _cache[version]


def _hazard_value(s: HubScore, hazard: str) -> float | None:
    if hazard == "overall":
        return s.overall
    if hazard == "multi_hazard":
        return s.multi_hazard
    return s.hazards[hazard].score


def _level(s: HubScore, hazard: str) -> str | None:
    if hazard == "overall":
        return s.overall_level
    if hazard == "multi_hazard":
        return s.multi_hazard_level
    return s.hazards[hazard].level


def rank_hubs(conn: sqlite3.Connection, hazard: str, region: str | None = None) -> dict:
    scores = [s for s in all_scores(conn).values() if region is None or s.region == region]
    known = sorted((s for s in scores if _hazard_value(s, hazard) is not None),
                   key=lambda s: -_hazard_value(s, hazard))  # type: ignore[operator]
    ranking, prev, rank = [], None, 0
    for i, s in enumerate(known, 1):
        v = _hazard_value(s, hazard)
        if v != prev:          # equal scores share a rank (1, 2, 2, 4)
            rank, prev = i, v
        row = {"rank": rank, "hub_id": s.hub_id, "name": s.name, "region": s.region,
               "score": v, "level": _level(s, hazard)}
        if hazard == "overall":
            row["driven_by"] = s.worst_hazard
            row["multi_hazard"] = s.multi_hazard
        ranking.append(row)
    ties = sorted({r["rank"] for r in ranking if sum(x["rank"] == r["rank"] for x in ranking) > 1})
    return {
        "status": "ok", "hazard": hazard, "region": region or "all", "period": known[0].period if known else None,
        "scale": SCALE, "method": METHOD, "ranking": ranking,
        "tied_ranks": ties,
        "unknown": [s.name for s in scores if _hazard_value(s, hazard) is None],
    }


def _metric_rows(s: HubScore, hazard: str) -> list[dict]:
    return [{"metric": m.label, "raw_value": sig3(m.raw), "unit": m.unit, "score_0_100": m.normalized,
             "weight": m.weight, "coverage": m.coverage, "source": m.source}
            for m in s.hazards[hazard].metrics]


def _caveats(s: HubScore) -> list[str]:
    """Data-driven caveats the agent should mention when relevant."""
    out = []
    known = {k: h.score for k, h in s.hazards.items() if h.score is not None}
    if s.worst_hazard:
        close = [k for k, v in known.items() if k != s.worst_hazard and known[s.worst_hazard] - v <= 3]
        if close:
            out.append(f"{s.worst_hazard} and {', '.join(close)} are within 3 points; do not describe "
                       f"the hub as exposed to {s.worst_hazard} alone.")
    for k, h in s.hazards.items():
        station = [m.normalized for m in h.metrics
                   if m.normalized is not None and not m.source.startswith("FEMA")]
        nri = [m for m in h.metrics if m.source.startswith("FEMA") and m.normalized is not None]
        if station and nri and nri[0].normalized - sum(station) / len(station) > 50:
            out.append(f"{h.label}: the FEMA NRI county frequency ({sig3(nri[0].raw)} events/yr, score "
                       f"{nri[0].normalized}) is far higher than the measured station data. NRI is "
                       f"county-wide; for a large county it may not reflect the hub location. The NRI "
                       f"part has a low weight ({nri[0].weight}).")
        for m in h.metrics:
            if m.coverage is not None and m.coverage < 0.99:
                out.append(f"{m.label}: data coverage {m.coverage:.1%} (missing days are excluded, not counted as zero).")
    return out


def compare_hubs(conn: sqlite3.Connection, hubs: list[str], hazards: list[str] | None = None) -> dict:
    hazards = hazards or list(cfg.HAZARDS)
    ids, unknown = [], []
    for name in hubs:
        hid = resolve_hub(conn, name)
        (ids.append(hid) if hid else unknown.append(name))
    scores = all_scores(conn)
    result = {
        "status": "ok" if not unknown else "partial", "scale": SCALE, "method": METHOD,
        "hubs": [{
            "hub_id": scores[h].hub_id, "name": scores[h].name, "region": scores[h].region,
            "period": scores[h].period,
            "overall": scores[h].overall, "overall_level": scores[h].overall_level,
            "overall_driven_by": scores[h].worst_hazard,
            "multi_hazard": scores[h].multi_hazard,
            "hazards": {k: {"score": scores[h].hazards[k].score, "level": scores[h].hazards[k].level,
                            "metrics": _metric_rows(scores[h], k)} for k in hazards},
            "caveats": _caveats(scores[h]),
        } for h in dict.fromkeys(ids)],
    }
    if unknown:
        result["not_found"] = not_found(conn, unknown)
    return result


def explain_score(conn: sqlite3.Connection, hub: str, hazard: str | None = None) -> dict:
    hid = resolve_hub(conn, hub)
    if hid is None:
        return not_found(conn, [hub])
    s = all_scores(conn)[hid]
    hazards = [hazard] if hazard else list(cfg.HAZARDS)
    ranks = {k: next(r["rank"] for r in rank_hubs(conn, k)["ranking"] if r["hub_id"] == hid)
             for k in ["overall", *cfg.HAZARDS]}
    return {
        "status": "ok", "hub_id": s.hub_id, "name": s.name, "region": s.region, "period": s.period,
        "scale": SCALE, "method": METHOD,
        "overall": s.overall, "overall_level": s.overall_level, "overall_driven_by": s.worst_hazard,
        "multi_hazard": s.multi_hazard, "multi_hazard_level": s.multi_hazard_level,
        "rank_among_all_hubs": {k: f"{v} of {len(all_scores(conn))}" for k, v in ranks.items()},
        "hazards": {k: {
            "score": s.hazards[k].score, "level": s.hazards[k].level,
            "formula": "weighted average of the metric scores below",
            "metrics": [{"metric": m.label, "raw_value": sig3(m.raw), "unit": m.unit, "score_0_100": m.normalized,
                         "weight": m.weight, "anchors_raw_0_and_100": list(m.anchors),
                         "anchor_reference": m.anchor_rationale, "coverage": m.coverage,
                         "source": m.source, "detail": m.note} for m in s.hazards[k].metrics],
        } for k in hazards},
        "not_scored_context": {
            "note": "Outside the scored hazards. Use these to explain other risks; they do not change the score.",
            "metrics": [{"metric": c.key, "value": sig3(c.value), "unit": c.unit, "source": c.source, "detail": c.note}
                        for c in s.context],
        },
        "caveats": _caveats(s),
    }
