"""FastAPI app: POST /chat and GET /health.

Run:  python -m uvicorn backend.app:app --port 8000   (virtualenv active)
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from backend.agent.config import load_config
from backend.agent.loop import Agent, SessionStore, TurnResult
from backend.agent.schema import TABLE_MIN_HUBS
from backend.db import connect
from backend.settings import DB_PATH, FIRST_YEAR, LAST_FULL_YEAR
from backend.tools.scores import all_scores

app = FastAPI(title="Weather Risk Intelligence Agent")
config = load_config()
agent = Agent(config)
sessions = SessionStore()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None


class HazardCell(BaseModel):
    score: float | None
    level: str | None


class HubSummary(BaseModel):
    """Deterministic scores for a hub the answer references (from the scoring code, not the model)."""
    hub_id: str
    name: str
    region: str
    hazards: dict[str, HazardCell]   # winter / hurricane / flood
    overall: float | None            # = worst single hazard
    overall_level: str | None
    worst_hazard: str | None
    multi_hazard: float | None


class ChatResponse(TurnResult):
    session_id: str
    hub_scores: list[HubSummary] = []
    # show the table by default only for rankings / comparisons; the user can still toggle it
    show_table: bool = False


def hub_summaries(conn, hub_ids: list[str]) -> list[HubSummary]:
    scores = all_scores(conn)
    out = []
    for hid in dict.fromkeys(hub_ids):           # keep the answer's order, drop duplicates
        s = scores.get(hid)
        if s is None:                            # model referenced something that is not a hub
            continue
        out.append(HubSummary(
            hub_id=s.hub_id, name=s.name, region=s.region,
            hazards={k: HazardCell(score=h.score, level=h.level) for k, h in s.hazards.items()},
            overall=s.overall, overall_level=s.overall_level, worst_hazard=s.worst_hazard,
            multi_hazard=s.multi_hazard))
    return out


@app.get("/health")
def health() -> dict:
    if not DB_PATH.exists():
        return {"status": "no_database", "hint": "run python -m backend.ingestion.run"}
    conn = connect()
    try:
        hubs = conn.execute("SELECT COUNT(*) FROM hubs").fetchone()[0]
        runs = {r[0]: r[1] for r in conn.execute(
            "SELECT source, MAX(finished_at) FROM ingestion_runs WHERE status='ok' GROUP BY source")}
    finally:
        conn.close()
    return {"status": "ok", "hubs": hubs, "period": f"{FIRST_YEAR}-{LAST_FULL_YEAR}",
            "last_ingestion": runs, "model": config.model, "effort": config.effort,
            "thinking": config.thinking,
            # True/False only - the key itself is never returned
            "api_key_set": bool(os.getenv("ANTHROPIC_API_KEY"))}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    # Sync endpoint: FastAPI runs it in a worker thread, so each request opens its own SQLite connection.
    if not DB_PATH.exists():
        raise HTTPException(503, "Database not built. Run: python -m backend.ingestion.run")
    session = sessions.get(req.session_id)
    conn = connect()
    try:
        result = agent.run_turn(conn, session, req.message)
        summaries = hub_summaries(conn, result.answer.hubs_referenced) if result.answer else []
    finally:
        conn.close()
    return ChatResponse(session_id=session.id, hub_scores=summaries,
                        show_table=len(summaries) >= TABLE_MIN_HUBS, **result.model_dump())
