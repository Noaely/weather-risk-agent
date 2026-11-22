"""Run the evaluation set against the agent and print pass/fail per case, latency and cost.

    python eval/run_eval.py --dry-run                       # free: show expected values, check preconditions
    python eval/run_eval.py                                 # all cases, model/effort from .env
    python eval/run_eval.py --effort low                    # same cases, another setting
    python eval/run_eval.py --model claude-opus-5-5 --cases dallas_false_premise,business_top3
    python eval/run_eval.py --budget 0.5                    # stop before exceeding $0.50 for this run

All checks are code, no LLM judge: answer structure, numbers against values computed from the DB by the
same tool functions the agent uses, required wording via regexes, and the grounding checks. The agent
is called in-process (same code path as POST /chat, minus HTTP). Results are saved to eval/results/.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from backend.agent.config import load_config  # noqa: E402
from backend.agent.loop import Agent, Session, TurnResult, numbers_in  # noqa: E402
from backend.agent.pricing import cost_usd as cost  # noqa: E402
from backend.agent.tool_defs import HANDLERS  # noqa: E402
from backend.db import connect  # noqa: E402
from backend.tools.metric import get_hub_metric  # noqa: E402
from backend.tools.scores import all_scores, rank_hubs  # noqa: E402

CASES_FILE = ROOT / "eval" / "cases.yaml"
RESULTS_DIR = ROOT / "eval" / "results"

# Pre-run estimate per question, measured in the Stage 6 runs (Sonnet medium: $0.23 for 21 questions;
# Opus medium: $0.27 for 6 hard ones), rounded up so the budget guard errs on the safe side.
EST_PER_TURN = {"claude-sonnet-5-5": 0.015, "claude-opus-5-5": 0.05}

SNAKE_CASE = re.compile(r"\b[a-z]+(?:_[a-z0-9]+)+\b")   # tool names, field names, hub ids



# --------------------------------------------------------------------------- expected values
class Expect:
    """Computes expected values from the DB with the agent's own tool functions."""

    def __init__(self, conn) -> None:
        self.conn = conn
        self.scores = all_scores(conn)
        self.hub_names = {h: s.name for h, s in self.scores.items()}

    def value(self, spec: dict) -> Any:
        if "fixed" in spec:
            return spec["fixed"]
        if "rank" in spec:                         # ordered list of hub names
            hazard, region = spec["rank"]
            return [r["name"] for r in rank_hubs(self.conn, hazard, region)["ranking"]]
        if "hazard_score" in spec:
            hub, hazard = spec["hazard_score"]
            return self.scores[hub].hazards[hazard].score
        if "overall" in spec:
            return self.scores[spec["overall"]].overall
        if "metric" in spec:
            hub, metric, year, field = spec["metric"]
            return get_hub_metric(self.conn, hub, metric, year)[field]
        raise ValueError(f"unknown spec {spec}")

    def precondition(self, pre: dict | None) -> str | None:
        """None if the data still supports the case, otherwise why not."""
        if not pre:
            return None
        if "equal_scores" in pre:
            hubs, hazard = pre["equal_scores"]
            vals = {self.scores[h].hazards[hazard].score for h in hubs}
            return None if len(vals) == 1 else f"{hubs} no longer tied on {hazard}: {vals}"
        if "near_tie" in pre:
            hub, points = pre["near_tie"]
            s = sorted((h.score for h in self.scores[hub].hazards.values()), reverse=True)
            return None if s[0] - s[1] <= points else f"{hub} top two hazards {s[0]} vs {s[1]} not within {points}"
        if "lowest_overall" in pre:
            low = min(self.scores.values(), key=lambda s: s.overall).hub_id
            return None if low == pre["lowest_overall"] else f"lowest overall is {low}"
        if "level_not" in pre:
            hub, which, level = pre["level_not"]
            got = self.scores[hub].overall_level if which == "overall" else self.scores[hub].hazards[which].level
            return None if got != level else f"{hub} {which} is now {level}"
        raise ValueError(f"unknown precondition {pre}")


# --------------------------------------------------------------------------- checks
def hubs_in_order(text: str, names: list[str]) -> list[str]:
    found = []
    for n in names:
        m = re.search(rf"\b{re.escape(n)}\b", text, re.I)
        if m:
            found.append((m.start(), n))
    return [n for _, n in sorted(found)]


def near(expected: float, pool: list[float]) -> bool:
    return any(abs(expected - x) <= 0.05 + 0.005 * abs(expected) for x in pool)


def run_checks(case: dict, res: TurnResult, ex: Expect) -> list[tuple[str, bool, str]]:
    """Returns (check name, passed, detail) for every check that applies to this case."""
    out: list[tuple[str, bool, str]] = []
    a = res.answer
    out.append(("ok", a is not None and res.error is None, res.error or ""))
    if a is None:
        return out
    text = a.answer
    g = res.grounding
    out.append(("grounded", bool(g and g.ok and not g.text_unverified),
                "" if not g else f"ungrounded={g.ungrounded} text_unverified={g.text_unverified}"))
    leaks = sorted(set(SNAKE_CASE.findall(text)))
    out.append(("no_internal_names", not leaks, f"found {leaks}" if leaks else ""))

    called = {c.name for c in res.tool_calls}
    if "tools_any" in case:
        out.append(("tools_any", bool(called & set(case["tools_any"])), f"called {sorted(called)}"))
    if "hubs_include" in case:
        missing = set(case["hubs_include"]) - set(a.hubs_referenced)
        out.append(("hubs_include", not missing, f"missing {sorted(missing)}" if missing else ""))
    names = list(ex.hub_names.values())
    order = hubs_in_order(text, names)
    if "first_hub" in case:
        want = ex.value(case["first_hub"])
        want = want[0] if isinstance(want, list) else want
        out.append(("first_hub", bool(order) and order[0] == want, f"expected {want}, text order {order[:4]}"))
    if "first_hubs" in case:
        k = case["first_hubs"]["k"]
        want = set(ex.value(case["first_hubs"]["spec"])[:k])
        out.append(("first_hubs", set(order[:k]) == want, f"expected {sorted(want)}, text order {order[:k + 1]}"))
    if "numbers" in case:
        pool = [s.value for s in a.scores] + numbers_in(text)
        for spec in case["numbers"]:
            want = ex.value(spec)
            out.append((f"number {want}", want is not None and near(float(want), pool), f"spec {spec}"))
    for rx in case.get("mentions", []):
        out.append((f"mentions /{rx}/", bool(re.search(rx, text, re.I)), ""))
    if "mentions_any" in case:
        out.append(("mentions_any", any(re.search(rx, text, re.I) for rx in case["mentions_any"]),
                    f"none of {case['mentions_any']}"))
    for rx in case.get("not_mentions", []):
        m = re.search(rx, text, re.I)
        out.append((f"not_mentions /{rx}/", m is None, f"found {m.group(0)!r}" if m else ""))
    if case.get("no_scores"):
        out.append(("no_scores", not a.scores, f"scores {[(s.hub_id, s.value) for s in a.scores]}"))
    if "confidence_in" in case:
        out.append(("confidence_in", a.confidence in case["confidence_in"], f"got {a.confidence}"))
    return out


# --------------------------------------------------------------------------- main
def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model")
    p.add_argument("--effort")
    p.add_argument("--thinking")
    p.add_argument("--cases", help="comma-separated case ids (default: all)")
    p.add_argument("--budget", type=float, default=1.00, help="stop before a case if this run's spend would exceed it (USD)")
    p.add_argument("--dry-run", action="store_true", help="no API calls: print expected values and preconditions")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    cases = yaml.safe_load(CASES_FILE.read_text(encoding="utf-8"))["cases"]
    if args.cases:
        wanted = args.cases.split(",")
        unknown = set(wanted) - {c["id"] for c in cases}
        if unknown:
            sys.exit(f"unknown case ids: {sorted(unknown)}")
        cases = [c for c in cases if c["id"] in wanted]
    overrides = {k: v for k, v in {"model": args.model, "effort": args.effort, "thinking": args.thinking}.items() if v}
    cfg = load_config(**overrides)
    conn = connect()
    ex = Expect(conn)
    turns = sum(len(c["turns"]) for c in cases)
    est = turns * EST_PER_TURN.get(cfg.model, 0.06)
    print(f"{len(cases)} cases, {turns} questions | model {cfg.model}, effort {cfg.effort}, thinking {cfg.thinking}")
    print(f"estimated cost ~${est:.2f} (budget guard ${args.budget:.2f})")

    if args.dry_run:
        for c in cases:
            pre = ex.precondition(c.get("precondition"))
            specs = [c[k] for k in ("first_hub",) if k in c] + c.get("numbers", [])
            if "first_hubs" in c:
                specs.append(c["first_hubs"]["spec"])
            vals = [ex.value(s) for s in specs]
            print(f"  {c['id']:34s} precondition: {'OK' if pre is None else 'FAILS - ' + pre:10s} expected: {vals}")
        return

    agent = Agent(cfg)
    rows, spent = [], 0.0
    for c in cases:
        pre = ex.precondition(c.get("precondition"))
        if pre:
            rows.append({"id": c["id"], "group": c["group"], "status": "SKIP", "detail": f"precondition: {pre}"})
            continue
        if spent + EST_PER_TURN.get(cfg.model, 0.06) * len(c["turns"]) > args.budget:
            rows.append({"id": c["id"], "group": c["group"], "status": "NOT RUN", "detail": "budget"})
            continue
        session = Session(id=f"eval-{c['id']}")
        usage: dict[str, int] = {}
        t0 = time.perf_counter()
        res = None
        for q in c["turns"]:
            res = agent.run_turn(conn, session, q)
            for k, v in res.usage.items():
                usage[k] = usage.get(k, 0) + v
            if res.error:
                break
        case_cost = cost(usage, cfg.model)
        spent += case_cost
        checks = run_checks(c, res, ex)
        failed = [(n, d) for n, ok, d in checks if not ok]
        rows.append({
            "id": c["id"], "group": c["group"], "status": "PASS" if not failed else "FAIL",
            "failed": failed, "checks": len(checks), "seconds": round(time.perf_counter() - t0, 1),
            "last_turn_seconds": round(res.latency_ms / 1000, 1), "rounds": res.rounds, "cost": round(case_cost, 4),
            "tools": [x.name for x in res.tool_calls if x.name != "submit_answer"],
            "answer": res.answer.model_dump() if res.answer else None, "error": res.error,
            "grounding": res.grounding.model_dump() if res.grounding else None,
        })
        mark = "PASS" if not failed else "FAIL"
        print(f"  {mark}  {c['id']:34s} {rows[-1]['last_turn_seconds']:5.1f}s  ${case_cost:.3f}"
              + ("" if not failed else "  <- " + "; ".join(f"{n}: {d}" for n, d in failed)))

    ran = [r for r in rows if r["status"] in ("PASS", "FAIL")]
    passed = sum(r["status"] == "PASS" for r in ran)
    lat = [r["last_turn_seconds"] for r in ran]
    print("\n" + "=" * 78)
    print(f"{passed}/{len(ran)} passed | model {cfg.model} effort {cfg.effort} thinking {cfg.thinking}")
    if lat:
        print(f"latency per question: median {statistics.median(lat):.1f}s, max {max(lat):.1f}s | "
              f"total cost ${spent:.2f} (est. ${est:.2f})")
    for g in dict.fromkeys(r["group"] for r in rows):
        grp = [r for r in ran if r["group"] == g]
        print(f"  {g:13s} {sum(r['status'] == 'PASS' for r in grp)}/{len(grp)}")
    for r in rows:
        if r["status"] in ("SKIP", "NOT RUN"):
            print(f"  {r['status']}: {r['id']} ({r['detail']})")

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = RESULTS_DIR / f"{stamp}_{cfg.model}_{cfg.effort}_{cfg.thinking}.json"
    out.write_text(json.dumps({"config": cfg.__dict__, "passed": passed, "ran": len(ran), "cost_usd": round(spent, 4),
                               "median_s": statistics.median(lat) if lat else None, "rows": rows},
                              indent=1, default=str), encoding="utf-8")
    print(f"saved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
