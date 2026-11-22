"""Agent loop tests with a scripted fake Claude client: no API calls, no cost, deterministic.

Each test scripts what "Claude" returns per round and checks what the loop does with it.
"""

from types import SimpleNamespace as NS

import pytest

from backend.agent.config import load_config
from backend.agent.loop import REMINDER, Agent, SessionStore
from backend.agent.schema import submit_answer_schema
from backend.agent.tool_defs import TOOLS
from backend.settings import DB_PATH

needs_db = pytest.mark.skipif(not DB_PATH.exists(), reason="run backend.ingestion.run first")


def tool_use(name, input, id="t1"):
    return NS(type="tool_use", id=id, name=name, input=input)


def text(t):
    return NS(type="text", text=t)


def reply(*blocks, stop="tool_use"):
    usage = NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return NS(content=list(blocks), stop_reason=stop, usage=usage, model="fake-model")


def answer(value=7.14, hub="denver", confidence="high", **kw):
    return {"answer": "7.14% of observed days in 2025 had measurable snowfall.", "hubs_referenced": [hub],
            "scores": [{"hub_id": hub, "name": "measurable snow days 2025", "value": value, "unit": "% of days"}],
            "reasoning": "From get_hub_metric.", "assumptions": ["NWS measurable snowfall >= 0.1 in"],
            "confidence": confidence, "data_sources": ["NOAA ACIS station data"], **kw}


class FakeClient:
    """Returns scripted replies in order and records every request."""

    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        self.messages = NS(create=self._create)
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.replies.pop(0)


def run(replies, question="What % of days in Denver last year had snowfall?", session=None):
    from backend.db import connect
    client = FakeClient(replies)
    agent = Agent(load_config(fallbacks=True), client=client)
    session = session or SessionStore().get(None)
    return agent.run_turn(connect(), session, question), session, client


METRIC_CALL = tool_use("get_hub_metric", {"hub": "Denver", "metric": "measurable_snow_days", "year": 2025})


# --------------------------------------------------------------------------- happy path
@needs_db
def test_tool_then_submit_returns_grounded_answer():
    res, session, client = run([reply(METRIC_CALL), reply(tool_use("submit_answer", answer(), id="s1"))])
    assert res.error is None and res.answer.scores[0].value == 7.14
    assert res.grounding.ok
    assert [c.name for c in res.tool_calls] == ["get_hub_metric", "submit_answer"]
    # history: user, assistant(tool_use), user(tool_result), assistant(submit), user(tool_result "Delivered.")
    assert [m["role"] for m in session.messages] == ["user", "assistant", "user", "assistant", "user"]
    assert session.messages[-1]["content"][0]["content"] == "Delivered."


@needs_db
def test_request_uses_strict_tools_caching_effort_and_fallback():
    _, _, client = run([reply(METRIC_CALL), reply(tool_use("submit_answer", answer(), id="s1"))])
    req = client.requests[0]
    assert all(t["strict"] for t in req["tools"])
    assert req["cache_control"] == {"type": "ephemeral"}
    assert req["output_config"] == {"effort": load_config().effort}
    assert req["fallbacks"] == "default" and req["betas"] == ["server-side-fallback-2026-07-01"]
    assert "tool_choice" not in req  # forced tool choice is a 400 on current models


# --------------------------------------------------------------------------- no submit_answer
@needs_db
def test_plain_text_gets_one_reminder():
    res, session, client = run([reply(text("Denver had snow on 7% of days."), stop="end_turn"),
                                reply(tool_use("get_hub_metric", METRIC_CALL.input)),
                                reply(tool_use("submit_answer", answer(), id="s1"))])
    assert res.answer is not None
    assert client.requests[1]["messages"][-1] == {"role": "user", "content": REMINDER}


@needs_db
def test_two_plain_text_replies_is_an_error_and_rolls_back():
    res, session, _ = run([reply(text("a"), stop="end_turn"), reply(text("b"), stop="end_turn")])
    assert res.answer is None and "submit_answer" in res.error
    assert session.messages == []  # failed turn removed; next question starts clean


# --------------------------------------------------------------------------- validation
@needs_db
def test_invalid_schema_is_retried_once():
    bad = answer(confidence="very sure")
    res, _, client = run([reply(METRIC_CALL), reply(tool_use("submit_answer", bad, id="s1")),
                          reply(tool_use("submit_answer", answer(), id="s2"))])
    assert res.answer is not None
    feedback = client.requests[2]["messages"][-1]["content"][0]
    assert feedback["is_error"] and "invalid" in feedback["content"]


@needs_db
def test_invalid_schema_twice_is_a_clear_error():
    bad = answer(confidence="very sure")
    res, session, _ = run([reply(METRIC_CALL), reply(tool_use("submit_answer", bad, id="s1")),
                           reply(tool_use("submit_answer", bad, id="s2"))])
    assert res.answer is None and "validation twice" in res.error and session.messages == []


@needs_db
def test_ungrounded_number_is_sent_back_and_corrected():
    res, _, client = run([reply(METRIC_CALL), reply(tool_use("submit_answer", answer(value=9.99), id="s1")),
                          reply(tool_use("submit_answer", answer(value=7.14), id="s2"))])
    assert res.grounding.ok and res.answer.scores[0].value == 7.14
    assert "9.99" in client.requests[2]["messages"][-1]["content"][0]["content"]


@needs_db
def test_still_ungrounded_after_retry_is_delivered_flagged():
    res, _, _ = run([reply(METRIC_CALL), reply(tool_use("submit_answer", answer(value=9.99), id="s1")),
                     reply(tool_use("submit_answer", answer(value=9.99), id="s2"))])
    assert res.answer is not None and not res.grounding.ok
    assert res.grounding.ungrounded[0]["value"] == 9.99


@needs_db
def test_number_for_the_wrong_hub_is_ungrounded():
    res, _, _ = run([reply(METRIC_CALL), reply(tool_use("submit_answer", answer(hub="miami"), id="s1")),
                     reply(tool_use("submit_answer", answer(hub="miami"), id="s2"))])
    assert not res.grounding.ok


@needs_db
def test_follow_up_can_reuse_numbers_from_an_earlier_turn():
    _, session, _ = run([reply(METRIC_CALL), reply(tool_use("submit_answer", answer(), id="s1"))])
    res, _, client = run([reply(tool_use("submit_answer", answer(), id="s2"))],
                         question="Repeat that number?", session=session)
    assert res.grounding.ok
    assert client.requests[0]["messages"][0]["content"].startswith("What %")  # history was sent


# --------------------------------------------------------------------------- numbers in the answer text
def with_text(t):
    return {**answer(), "answer": t}


@needs_db
def test_text_numbers_from_tools_prompt_and_question_are_not_flagged():
    t = ("In 2025, Denver had measurable snowfall (>= 0.1 in) on 7.14% of observed days, about 7%: "
         "26 of 364 days (coverage 99.73%). That is one of 12 hubs; data covers 2016-2025; "
         "scores below 33 are low. You asked about last year: 2,025 is not a number I made up either.")
    res, _, _ = run([reply(METRIC_CALL), reply(tool_use("submit_answer", with_text(t), id="s1"))])
    assert res.grounding.ok and res.grounding.text_unverified == []


@needs_db
def test_invented_number_in_text_is_reported_but_not_blocked():
    t = "Denver had snow on 7.14% of days, roughly 9.5% more than Chicago and about 41 days in total."
    res, _, client = run([reply(METRIC_CALL), reply(tool_use("submit_answer", with_text(t), id="s1"))])
    assert res.answer is not None and res.answer.answer == t     # delivered, not blocked
    assert res.grounding.ok                                     # scores[] were fine
    assert res.grounding.text_unverified == [9.5, 41.0]
    assert len(client.requests) == 2                            # no retry was triggered by the text


@needs_db
def test_number_from_the_users_question_is_not_flagged():
    t = "No data for 2010; the system covers 2016-2025. Your threshold of 3 inches is not one we use."
    res, _, _ = run([reply(tool_use("submit_answer", {**with_text(t), "scores": [], "confidence": "low"}, id="s1"))],
                    question="How many days with 3 inches of snow did Houston have in 2010?")
    assert res.grounding.text_unverified == []


def test_ranges_and_thousands_are_parsed_correctly():
    from backend.agent.loop import numbers_in
    assert numbers_in("2016-2025") == [2016.0, 2025.0]
    assert numbers_in("min -15 C, 2,024.1 mm, 7.14%") == [-15.0, 2024.1, 7.14]


# --------------------------------------------------------------------------- stop reasons and tool errors
@needs_db
def test_refusal_is_an_error_and_rolls_back():
    res, session, _ = run([reply(text(""), stop="refusal")])
    assert res.answer is None and "declined" in res.error and session.messages == []


@needs_db
def test_tool_errors_go_back_to_the_model():
    res, _, client = run([reply(tool_use("get_hub_metric", {"hub": "Denver", "metric": "nope"})),
                          reply(tool_use("submit_answer", answer(value=1, confidence="low",
                                                                  scores=[]), id="s1"))])
    tool_result = client.requests[1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] and res.answer is not None


@needs_db
def test_unknown_hub_is_a_normal_result_not_an_error():
    res, _, client = run([reply(tool_use("explain_score", {"hub": "Seattle"})),
                          reply(tool_use("submit_answer", answer(scores=[], confidence="low"), id="s1"))])
    tool_result = client.requests[1]["messages"][-1]["content"][0]
    assert "is_error" not in tool_result and '"not_found"' in tool_result["content"]


# --------------------------------------------------------------------------- definitions and config
def test_every_tool_is_strict_with_closed_objects():
    def closed(node):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
        for v in [*node.get("properties", {}).values(), *node.get("$defs", {}).values()]:
            closed(v)
        if isinstance(node.get("items"), dict):
            closed(node["items"])

    for t in TOOLS:
        assert t["strict"] is True
        closed(t["input_schema"])
    assert set(submit_answer_schema()["required"]) == {
        "answer", "hubs_referenced", "scores", "reasoning", "assumptions", "confidence", "data_sources"}


def test_between_tools_rejected_for_opus_and_high_effort():
    with pytest.raises(ValueError):
        load_config(model="claude-opus-5-5", thinking="between_tools")
    with pytest.raises(ValueError):
        load_config(thinking="between_tools", effort="max")
    assert load_config(thinking="between_tools", effort="low").thinking == "between_tools"
