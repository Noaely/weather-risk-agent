"""The eval's checks must be able to FAIL: feed them deliberately bad answers (no API calls)."""

import importlib.util
import sys
from pathlib import Path

import pytest

from backend.agent.loop import Grounding, TurnResult
from backend.agent.schema import AgentAnswer
from backend.settings import DB_PATH

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="run backend.ingestion.run first")

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("run_eval", ROOT / "eval" / "run_eval.py")
run_eval = importlib.util.module_from_spec(spec)
sys.modules["run_eval"] = run_eval
spec.loader.exec_module(run_eval)


@pytest.fixture(scope="module")
def ex():
    from backend.db import connect
    return run_eval.Expect(connect())


def turn(text, scores=(), conf="high", hubs=(), unverified=(), tools=()):
    from backend.agent.loop import ToolCall
    return TurnResult(
        answer=AgentAnswer(answer=text, hubs_referenced=list(hubs),
                           scores=[{"hub_id": h, "name": "x", "value": v, "unit": "u"} for h, v in scores],
                           reasoning="r", assumptions=[], confidence=conf, data_sources=[]),
        grounding=Grounding(ok=True, text_unverified=list(unverified)),
        tool_calls=[ToolCall(name=t, input={}, is_error=False, ms=0) for t in tools])


def failed(case, res, ex):
    return {name for name, ok, _ in run_eval.run_checks(case, res, ex) if not ok}


CASE = {"tools_any": ["rank_hubs"], "first_hub": {"rank": ["winter", "Midwest"]},
        "numbers": [{"hazard_score": ["minneapolis", "winter"]}]}


def test_good_answer_passes(ex):
    res = turn("Minneapolis leads at 52.6, then Chicago.", scores=[("minneapolis", 52.6)], tools=["rank_hubs"])
    assert failed(CASE, res, ex) == set()


def test_wrong_top_hub_wrong_number_and_wrong_tool_fail(ex):
    res = turn("Chicago leads at 60, then Minneapolis.", tools=["explain_score"])
    assert failed(CASE, res, ex) == {"tools_any", "first_hub", "number 52.6"}


def test_internal_names_and_unverified_numbers_fail(ex):
    res = turn("Per rank_hubs, kansas_city is 9.9 points behind.", unverified=[9.9])
    assert {"no_internal_names", "grounded"} <= failed({}, res, ex)


def test_out_of_scope_checks_fail_on_made_up_numbers(ex):
    case = {"no_scores": True, "not_mentions": [r"\$\s?\d"], "confidence_in": ["low"]}
    res = turn("Hubs lost about $4 million.", scores=[("miami", 4.0)], conf="high")
    assert failed(case, res, ex) == {"no_scores", r"not_mentions /\$\s?\d/", "confidence_in"}


def test_first_hubs_set_for_top3(ex):
    case = {"first_hubs": {"spec": {"rank": ["overall", None]}, "k": 3}}
    assert failed(case, turn("Invest in Miami, Houston and Memphis."), ex) == set()
    assert failed(case, turn("Invest in Miami, Houston and Denver."), ex) == {"first_hubs"}


def test_error_turn_fails_ok():
    res = TurnResult(error="boom")
    checks = run_eval.run_checks({}, res, None)
    assert checks == [("ok", False, "boom")]
