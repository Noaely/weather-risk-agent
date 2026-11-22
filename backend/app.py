"""FastAPI app: POST /chat and GET /health.

Run:  .venv/Scripts/python -m uvicorn backend.app:app --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from backend.agent.config import load_config
from backend.agent.loop import Agent, SessionStore, TurnResult
from backend.db import connect
from backend.settings import DB_PATH, FIRST_YEAR, LAST_FULL_YEAR

app = FastAPI(title="Weather Risk Intelligence Agent")
config = load_config()
agent = Agent(config)
sessions = SessionStore()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None


class ChatResponse(TurnResult):
    session_id: str


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
            "thinking": config.thinking}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    # Sync endpoint: FastAPI runs it in a worker thread, so each request opens its own SQLite connection.
    if not DB_PATH.exists():
        raise HTTPException(503, "Database not built. Run: python -m backend.ingestion.run")
    session = sessions.get(req.session_id)
    conn = connect()
    try:
        result = agent.run_turn(conn, session, req.message)
    finally:
        conn.close()
    return ChatResponse(session_id=session.id, **result.model_dump())
