"""FastAPI app: POST /chat and GET /health, and (when built) the chat UI from frontend/dist.

Run locally:  python -m uvicorn backend.app:app --port 8000   (virtualenv active)
Production:   one service serves the API and the built UI on the same origin (see Dockerfile).
              APP_ENV=production requires ACCESS_CODE; see backend/security.py for the limits.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.agent.config import load_config
from backend.agent.loop import Agent, SessionStore, TurnResult
from backend.agent.pricing import cost_usd
from backend.agent.schema import TABLE_MIN_HUBS
from backend.db import connect
from backend.security import Guard, client_ip
from backend.settings import DB_PATH, FIRST_YEAR, FRONTEND_DIST, LAST_FULL_YEAR
from backend.tools.scores import all_scores

guard = Guard.from_env()          # raises at startup if production is misconfigured
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
def health(request: Request) -> dict:
    """Public: no secrets, no client IPs. Tells the UI whether an access code is needed."""
    base = {"access_code_required": guard.code_required,
            # which header the rate limiter keys on for THIS request (not the IP itself)
            "client_ip_source": client_ip(request)[1]}
    if not DB_PATH.exists():
        return {"status": "no_database", "hint": "run python -m backend.ingestion.run", **base}
    conn = connect()
    try:
        hubs = conn.execute("SELECT COUNT(*) FROM hubs").fetchone()[0]
    finally:
        conn.close()
    return {"status": "ok", "hubs": hubs, "period": f"{FIRST_YEAR}-{LAST_FULL_YEAR}",
            "model": config.model, "effort": config.effort, "thinking": config.thinking,
            "api_key_set": bool(os.getenv("ANTHROPIC_API_KEY")),   # True/False only, never the key
            **base}


@app.post("/access")
def check_access(request: Request):
    """Lets the UI check a code before the first question. No model call; wrong codes still count."""
    decision = guard.check_code(client_ip(request)[0], request.headers.get("x-access-code"))
    return JSONResponse({"ok": decision.ok, "detail": decision.message},
                        status_code=200 if decision.ok else decision.status)


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request):
    # Checked on every request, whether it comes from the UI or straight from curl.
    ip, _ = client_ip(request)
    decision = guard.check(ip, request.headers.get("x-access-code"))
    if not decision.ok:
        return JSONResponse({"detail": decision.message}, status_code=decision.status)

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
    guard.record_spend(cost_usd(result.usage, result.model or config.model))
    return ChatResponse(session_id=session.id, hub_scores=summaries,
                        show_table=len(summaries) >= TABLE_MIN_HUBS, **result.model_dump())


# The built UI (npm run build -> frontend/dist), served on the same origin as the API. Mounted last so
# /chat and /health take precedence. Absent in local development, where Vite serves the UI instead.
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="ui")
