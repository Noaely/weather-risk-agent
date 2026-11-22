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
        # the question text picks which hubs the fake answer references
        hubs = {"single": ["denver"], "none": []}.get(question, ["miami", "houston", "miami", "seattle"])
        session.messages.append({"role": "user", "content": question})
        return TurnResult(model="fake", answer=AgentAnswer(
            answer="a", hubs_referenced=hubs,
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


def test_table_shown_by_default_only_for_two_or_more_hubs(client):
    comparison = client.post("/chat", json={"message": "Compare Miami and Houston"}).json()
    assert comparison["show_table"] is True                      # 2 hubs (duplicate + non-hub dropped)
    single = client.post("/chat", json={"message": "single"}).json()
    assert single["show_table"] is False and len(single["hub_scores"]) == 1   # data still sent for the toggle
    none = client.post("/chat", json={"message": "none"}).json()
    assert none["show_table"] is False and none["hub_scores"] == []


def test_prompt_states_the_same_table_rule():
    from backend.agent.prompt import build_system_prompt
    from backend.agent.schema import TABLE_MIN_HUBS
    assert f"lists {TABLE_MIN_HUBS} or more hubs" in build_system_prompt()


def test_session_id_is_reused_for_follow_ups(client):
    first = client.post("/chat", json={"message": "q1"}).json()
    second = client.post("/chat", json={"message": "q2", "session_id": first["session_id"]}).json()
    assert second["session_id"] == first["session_id"]


def test_empty_message_is_rejected(client):
    assert client.post("/chat", json={"message": ""}).status_code == 422
