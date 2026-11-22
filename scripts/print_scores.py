"""Compute scores for all hubs, save a timestamped snapshot, and print the score table.

Run:  .venv/Scripts/python scripts/print_scores.py [--breakdown HUB] [--no-save]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.db import connect, init_db  # noqa: E402
from backend.scoring import config as cfg  # noqa: E402
from backend.scoring.score import config_hash, save_scores, score_all, score_hub  # noqa: E402


def fmt(v: float | None, w: int = 7) -> str:
    return f"{'?':>{w}s}" if v is None else f"{v:{w}.1f}"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--breakdown", help="hub id to print the full breakdown for")
    p.add_argument("--no-save", action="store_true")
    args = p.parse_args()

    conn = connect()
    init_db(conn)
    scores = score_all(conn)  # ranked by overall = worst hazard
    print(f"Scores {scores[0].period}  |  config {config_hash()}  |  flood NRI per area: {cfg.FLOOD_NRI_PER_AREA}")
    print("Overall = worst single hazard (ranking). Multi-hazard = equal-weight average (secondary).")
    print(f"{'#':>2s} {'hub':12s} {'region':8s} {'OVERALL':>8s} {'level':9s} {'driver':10s}"
          f"{'winter':>7s} {'hurric.':>7s} {'flood':>7s} {'multi-hz':>9s}")
    for i, s in enumerate(scores, 1):
        h = s.hazards
        print(f"{i:2d} {s.name:12s} {s.region:8s} {fmt(s.overall, 8)} {s.overall_level or '?':9s} "
              f"{s.worst_hazard or '?':10s}{fmt(h['winter'].score)} {fmt(h['hurricane'].score)} "
              f"{fmt(h['flood'].score)} {fmt(s.multi_hazard, 9)}")

    print("\nRaw metric -> normalized (per hazard)")
    for hazard, specs in cfg.HAZARDS.items():
        print(f"  {cfg.HAZARD_LABELS[hazard]}")
        for spec in specs:
            vals = "  ".join(
                f"{s.name[:7]:7s}{fmt(m.raw, 6)}->{fmt(m.normalized, 5)}"
                for s in scores for m in s.hazards[hazard].metrics if m.key == spec.key)
            print(f"    {spec.key} [{spec.lo:g}..{spec.hi:g} {spec.unit}, w={spec.weight}]")
            print(f"      {vals}")

    if args.breakdown:
        print("\n" + score_hub(conn, args.breakdown).model_dump_json(indent=1))
    if not args.no_save:
        print(f"\nsaved as score run {save_scores(conn, scores)}")


if __name__ == "__main__":
    main()
