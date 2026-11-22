"""API tests with the agent replaced by a stub: checks the HTTP contract the UI relies on."""

import pytest
from fastapi.testclient import TestClient

from backend.agent.loop import Grounding, TurnResult
from backend.agent.schema import AgentAnswer
from backend.settings import DB_PATH

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="run backend.ingestion.run first")


@pytest.fixture
def client(monkeypatch):
    import backend.app as app_module

    def fake_turn(conn, session, question):
        session.messages.append({"role": "user", "content": question})
        return TurnResult(model="fake", answer=AgentAnswer(
            answer="Miami and Houston.", hubs_referenced=["miami", "houston", "miami", "seattle"],
            scores=[], reasoning="r", assumptions=[], confidence="high", data_sources=[]),
            grounding=Grounding(ok=True))

    monkeypatch.setattr(app_module.agent, "run_turn", fake_turn)
    return TestClient(app_module.app)


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["hubs"] == 12


def test_chat_returns_deterministic_hub_scores_in_answer_order(client):
    body = client.post("/chat", json={"message": "Compare Miami and Houston"}).json()
    hubs = body["hub_scores"]
    assert [h["hub_id"] for h in hubs] == ["miami", "houston"]  # duplicate and non-hub dropped
    miami = hubs[0]
    assert miami["worst_hazard"] == "hurricane"
    assert miami["overall"] == miami["hazards"]["hurricane"]["score"]
    assert set(miami["hazards"]) == {"winter", "hurricane", "flood"}


def test_session_id_is_reused_for_follow_ups(client):
    first = client.post("/chat", json={"message": "q1"}).json()
    second = client.post("/chat", json={"message": "q2", "session_id": first["session_id"]}).json()
    assert second["session_id"] == first["session_id"]


def test_empty_message_is_rejected(client):
    assert client.post("/chat", json={"message": ""}).status_code == 422
