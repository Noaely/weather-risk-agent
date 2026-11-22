"""The agent loop: plain Anthropic SDK + our tools, no framework.

One user question = one turn:
  send history -> Claude answers with tool calls -> we run them -> send results back -> ...
  until Claude calls submit_answer with a valid, grounded answer (or a limit is hit).

Guarantees the code enforces (not just the prompt):
- Forced tool choice is not available on current models, so if Claude ends with plain text we send
  one reminder to call submit_answer; a second miss is a clear error.
- submit_answer input is validated with Pydantic; one retry on failure, then a clear error.
- Grounding: every number in scores[] must appear in a tool result for that hub in this session.
  One retry with the offending values listed; if still ungrounded the answer is returned flagged.
- Text check (warning only): every number written in the answer text is matched against all tool
  results, the system prompt and the question; the ones that match nothing are returned in
  grounding.text_unverified so they are visible. Never blocks an answer.
- History is append-only within a turn (preserved thinking + prompt caching). If a turn fails, the
  session is truncated back to where it was before the question, so a broken turn cannot poison the next.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import anthropic
from pydantic import BaseModel, ValidationError

from backend.agent.config import AgentConfig
from backend.agent.prompt import build_system_prompt
from backend.agent.schema import AgentAnswer
from backend.agent.tool_defs import TOOLS, run_tool

FALLBACK_BETA = "server-side-fallback-2026-07-01"
REMINDER = ("Please deliver your reply now by calling submit_answer (also if the answer is that the "
            "data does not cover the question).")
# A minus sign counts only when it does not follow a digit, so "2016-2025" is 2016 and 2025, not -2025.
# Thousands separators ("2,024.1") are removed before matching.
_NUMBER = re.compile(r"(?<![\d.])-?\d+(?:\.\d+)?")
NO_KEY_MESSAGE = ("No Anthropic API key: set ANTHROPIC_API_KEY in .env (copy .env.example) "
                  "and restart the backend.")


def numbers_in(text: str) -> list[float]:
    return [float(m) for m in _NUMBER.findall(re.sub(r"(?<=\d),(?=\d{3})", "", text))]


# --------------------------------------------------------------------------- results
class ToolCall(BaseModel):
    name: str
    input: dict[str, Any]
    is_error: bool
    ms: int


class Grounding(BaseModel):
    ok: bool                                  # scores[] all grounded (enforced: one retry)
    ungrounded: list[dict[str, Any]] = []     # scores[] items no tool returned for that hub
    # Warning only, never blocks: numbers in the answer text that match no tool result, no number in
    # the system prompt (thresholds, years, hub count) and no number in the user's question.
    text_unverified: list[float] = []


class TurnResult(BaseModel):
    answer: AgentAnswer | None = None
    error: str | None = None
    grounding: Grounding | None = None
    tool_calls: list[ToolCall] = []
    rounds: int = 0
    latency_ms: int = 0
    model: str = ""
    usage: dict[str, int] = {}


# --------------------------------------------------------------------------- sessions
@dataclass
class Session:
    id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    # numbers returned by tools in this session, per hub (follow-ups may reuse earlier results)
    facts: dict[str, set[float]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


class SessionStore:
    """In-memory conversation memory per session_id. Lost on restart (fine for v1; see Future work).

    Only server-issued ids are resumed: an unknown id (e.g. after a restart, or invented by a caller)
    starts a fresh session with a new random id. At most `max_sessions` are kept; the least recently
    used is dropped first, so a public deployment cannot be made to grow memory without bound.
    """

    def __init__(self, max_sessions: int = 500) -> None:
        self._sessions: OrderedDict[str, Session] = OrderedDict()
        self._lock = threading.Lock()
        self.max_sessions = max_sessions

    def get(self, session_id: str | None) -> Session:
        with self._lock:
            if session_id in self._sessions:
                self._sessions.move_to_end(session_id)
                return self._sessions[session_id]
            sid = uuid.uuid4().hex
            self._sessions[sid] = Session(id=sid)
            while len(self._sessions) > self.max_sessions:
                self._sessions.popitem(last=False)
            return self._sessions[sid]


# --------------------------------------------------------------------------- grounding
def collect_numbers(node: Any, hub: str | None, out: dict[str, set[float]]) -> None:
    """Map hub_id -> every number in tool results under that hub (incl. numbers inside text)."""
    if isinstance(node, dict):
        hub = node.get("hub_id", hub)
        for v in node.values():
            collect_numbers(v, hub, out)
    elif isinstance(node, list):
        for v in node:
            collect_numbers(v, hub, out)
    elif isinstance(node, bool) or node is None:
        return
    elif isinstance(node, (int, float)):
        out.setdefault(hub or "_", set()).add(float(node))
    elif isinstance(node, str):
        out.setdefault(hub or "_", set()).update(numbers_in(node))


def _matches(v: float, pool: set[float]) -> bool:
    """Same number allowing display rounding: 7.14 ~ 7.1 ~ 7; 0.3055 ~ 0.31. Not 7.14 vs 9.5."""
    return any(abs(v - x) <= 0.05 + 0.005 * abs(x) or abs(v - round(x)) < 1e-9 for x in pool)


def check_grounding(answer: AgentAnswer, facts: dict[str, set[float]],
                    known_context: set[float] = frozenset()) -> Grounding:
    bad = [s.model_dump() for s in answer.scores if not _matches(s.value, facts.get(s.hub_id, set()))]
    # Text check: numbers are not tied to a hub in prose, so match against every tool result.
    pool = set().union(*facts.values(), known_context) if facts else set(known_context)
    unverified = sorted({v for v in numbers_in(answer.answer) if not _matches(v, pool)})
    return Grounding(ok=not bad, ungrounded=bad, text_unverified=unverified)


# --------------------------------------------------------------------------- the loop
class Agent:
    def __init__(self, cfg: AgentConfig, client: Any | None = None) -> None:
        self.cfg = cfg
        self.client = client or anthropic.Anthropic()
        # Breakpoint 1 (here): tools + system, identical for every session -> reused across questions.
        # Breakpoint 2 (top-level cache_control in _call): the growing history within a session.
        self.system = [{"type": "text", "text": build_system_prompt(), "cache_control": {"type": "ephemeral"}}]
        # numbers the answer may legitimately use without a tool: thresholds, level cut-offs, years, hub count
        self.prompt_numbers = set(numbers_in(self.system[0]["text"]))

    def _call(self, messages: list[dict[str, Any]]):
        kwargs: dict[str, Any] = dict(
            model=self.cfg.model, max_tokens=self.cfg.max_tokens, system=self.system, tools=TOOLS,
            messages=messages, output_config={"effort": self.cfg.effort},
            cache_control={"type": "ephemeral"},  # caches tools + system + history; grows each round
        )
        if self.cfg.thinking == "between_tools":
            kwargs["thinking"] = {"type": "between_tools"}
        # (adaptive thinking is the default when `thinking` is omitted)
        if self.cfg.fallbacks:
            return self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
        return self.client.messages.create(**kwargs)

    def run_turn(self, conn: sqlite3.Connection, session: Session, question: str) -> TurnResult:
        with session.lock:
            start_len = len(session.messages)
            result = self._run(conn, session.messages, session.facts, question)
            if result.answer is None:
                del session.messages[start_len:]  # roll back the failed turn (tail only)
            return result

    def _run(self, conn: sqlite3.Connection, messages: list[dict[str, Any]],
             facts: dict[str, set[float]], question: str) -> TurnResult:
        t0 = time.perf_counter()
        res = TurnResult(model=self.cfg.model)
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                 "cache_creation_input_tokens": 0}
        reminded = schema_retry = grounding_retry = False
        known = self.prompt_numbers | set(numbers_in(question))
        messages.append({"role": "user", "content": question})

        def done(**kw) -> TurnResult:
            for k, v in kw.items():
                setattr(res, k, v)
            res.usage, res.latency_ms = usage, int((time.perf_counter() - t0) * 1000)
            return res

        for _ in range(self.cfg.max_rounds):
            res.rounds += 1
            try:
                resp = self._call(messages)
            except anthropic.APIStatusError as e:
                return done(error=f"Claude API error {e.status_code}: {e.message}")
            except anthropic.APIConnectionError:
                return done(error="Could not reach the Claude API (network).")
            except TypeError as e:
                # The SDK raises TypeError (not an API error) when no credentials are configured at all.
                if "authentication" not in str(e).lower():
                    raise
                return done(error=NO_KEY_MESSAGE)
            for k in usage:
                usage[k] += getattr(resp.usage, k, 0) or 0
            res.model = resp.model
            messages.append({"role": "assistant", "content": resp.content})  # unchanged, incl. thinking blocks

            if resp.stop_reason == "refusal":
                return done(error="The model declined to answer this request.")
            if resp.stop_reason == "max_tokens":
                return done(error="The model ran out of output tokens before finishing.")

            calls = [b for b in resp.content if b.type == "tool_use"]
            if not calls:
                if reminded:
                    return done(error="The model did not deliver a structured answer (no submit_answer call).")
                reminded = True
                messages.append({"role": "user", "content": REMINDER})
                continue

            results: list[dict[str, Any]] = []
            submit = None
            for b in calls:
                if b.name == "submit_answer":
                    submit = b
                    continue
                t = time.perf_counter()
                text, is_error = run_tool(conn, b.name, b.input)
                res.tool_calls.append(ToolCall(name=b.name, input=b.input, is_error=is_error,
                                               ms=int((time.perf_counter() - t) * 1000)))
                collect_numbers(json.loads(text), None, facts)
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": text,
                                **({"is_error": True} if is_error else {})})

            if submit is not None:
                res.tool_calls.append(ToolCall(name="submit_answer", input=submit.input, is_error=False, ms=0))
                feedback, answer = self._check_submit(submit.input, facts, len(calls) > 1)
                if answer is not None and feedback is None:
                    results.append({"type": "tool_result", "tool_use_id": submit.id, "content": "Delivered."})
                    messages.append({"role": "user", "content": results})
                    return done(answer=answer, grounding=check_grounding(answer, facts, known))
                kind = feedback["kind"]
                if kind == "schema" and schema_retry or kind == "grounding" and grounding_retry:
                    if kind == "grounding" and answer is not None:
                        # second ungrounded answer: deliver it, clearly flagged, rather than nothing
                        results.append({"type": "tool_result", "tool_use_id": submit.id, "content": "Delivered."})
                        messages.append({"role": "user", "content": results})
                        return done(answer=answer, grounding=check_grounding(answer, facts, known))
                    return done(error=f"Answer failed validation twice: {feedback['message']}")
                schema_retry |= kind == "schema"
                grounding_retry |= kind == "grounding"
                results.append({"type": "tool_result", "tool_use_id": submit.id, "is_error": True,
                                "content": feedback["message"]})
            messages.append({"role": "user", "content": results})

        return done(error=f"No answer after {self.cfg.max_rounds} model calls.")

    @staticmethod
    def _check_submit(raw: dict, facts: dict[str, set[float]], with_other_calls: bool):
        """Returns (feedback or None, parsed answer or None)."""
        if with_other_calls:
            return {"kind": "order", "message": "Read the results of your other tool calls first, "
                                                "then call submit_answer on its own."}, None
        try:
            answer = AgentAnswer.model_validate(raw)
        except ValidationError as e:
            return {"kind": "schema", "message": f"submit_answer input is invalid: {e.errors()}"}, None
        g = check_grounding(answer, facts)
        if not g.ok:
            return {"kind": "grounding", "message": (
                "These scores[] values do not appear in any tool result for that hub in this turn: "
                f"{g.ungrounded}. Use only numbers exactly as tools returned them (call a tool if you "
                "need a number), then call submit_answer again.")}, answer
        return None, answer
