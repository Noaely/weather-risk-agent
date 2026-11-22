"""Access code, rate limits, spend cap, production config, sessions, static UI. No API calls."""

import pytest
from fastapi.testclient import TestClient

from backend.agent.loop import Grounding, SessionStore, TurnResult
from backend.agent.schema import AgentAnswer
from backend.security import ConfigError, Guard, SlidingWindow
from backend.settings import DB_PATH, FRONTEND_DIST

CODE = "reviewer-code-123"


# --------------------------------------------------------------------------- unit: limits and config
def test_sliding_window_limits_and_expires():
    w = SlidingWindow(limit=2, window=3600)
    assert w.allow("a", now=0) and w.allow("a", now=1)
    assert not w.allow("a", now=2)              # third within the hour is refused
    assert w.allow("b", now=2)                  # other keys are independent
    assert w.allow("a", now=3601)               # first event has left the window


def test_production_refuses_to_start_without_a_code():
    with pytest.raises(ConfigError):
        Guard.from_env({"APP_ENV": "production"})
    with pytest.raises(ConfigError):
        Guard.from_env({"APP_ENV": "production", "ACCESS_CODE": "short"})
    assert Guard.from_env({"APP_ENV": "production", "ACCESS_CODE": CODE}).code_required


def test_local_development_without_code_is_open():
    g = Guard.from_env({})
    assert not g.code_required and g.check("1.2.3.4", None).ok


def test_defaults_match_the_agreed_limits():
    g = Guard.from_env({"ACCESS_CODE": CODE})
    assert (g.per_ip_hour, g.global_hour, g.failed_codes_per_ip_hour, g.daily_cap_usd) == (40, 60, 10, 1.00)


def test_wrong_codes_block_the_ip_after_the_limit():
    g = Guard(access_code=CODE, failed_codes_per_ip_hour=3)
    assert [g.check("9.9.9.9", "nope").status for _ in range(3)] == [401, 401, 401]
    blocked = g.check("9.9.9.9", CODE)          # even the right code is refused now, for an hour
    assert blocked.status == 429 and "wrong access codes" in blocked.message
    assert g.check("8.8.8.8", CODE).ok          # other addresses unaffected


def test_per_ip_and_global_limits():
    g = Guard(access_code=CODE, per_ip_hour=2, global_hour=3)
    assert g.check("1.1.1.1", CODE).ok and g.check("1.1.1.1", CODE).ok
    assert g.check("1.1.1.1", CODE).status == 429          # per-IP
    assert g.check("2.2.2.2", CODE).ok                      # 3rd overall
    over = g.check("3.3.3.3", CODE)
    assert over.status == 429 and "all users" in over.message   # global


def test_daily_spend_cap():
    g = Guard(access_code=CODE, daily_cap_usd=0.05)
    g.record_spend(0.03)
    assert g.check("1.1.1.1", CODE).ok
    g.record_spend(0.03)
    capped = g.check("1.1.1.1", CODE)
    assert capped.status == 429 and "budget" in capped.message


def test_sessions_only_resume_server_issued_ids_and_are_capped():
    store = SessionStore(max_sessions=2)
    a = store.get(None)
    assert store.get(a.id) is a                              # follow-up resumes
    invented = store.get("attacker-chosen-id")
    assert invented.id != "attacker-chosen-id"               # unknown id -> fresh random id
    store.get(None)                                          # 3rd session evicts the oldest (a)
    assert store.get(a.id).id != a.id


# --------------------------------------------------------------------------- API: enforced on /chat itself
needs_db = pytest.mark.skipif(not DB_PATH.exists(), reason="run backend.ingestion.run first")


@pytest.fixture
def client(monkeypatch):
    import backend.app as app_module

    def fake_turn(conn, session, question):
        return TurnResult(model="claude-sonnet-5-5", answer=AgentAnswer(
            answer="ok", hubs_referenced=[], scores=[], reasoning="r", assumptions=[],
            confidence="high", data_sources=[]), grounding=Grounding(ok=True),
            usage={"input_tokens": 0, "output_tokens": 1000, "cache_read_input_tokens": 0,
                   "cache_creation_input_tokens": 0})          # = $0.01 on Sonnet 5.5

    monkeypatch.setattr(app_module.agent, "run_turn", fake_turn)
    monkeypatch.setattr(app_module, "guard", Guard(access_code=CODE, per_ip_hour=40, global_hour=60,
                                                   daily_cap_usd=0.025))
    return TestClient(app_module.app)


@needs_db
def test_chat_requires_the_code_even_without_the_ui(client):
    assert client.post("/chat", json={"message": "hi"}).status_code == 401
    assert client.post("/chat", json={"message": "hi"}, headers={"X-Access-Code": "wrong"}).status_code == 401
    ok = client.post("/chat", json={"message": "hi"}, headers={"X-Access-Code": CODE})
    assert ok.status_code == 200 and ok.json()["answer"]["answer"] == "ok"


@needs_db
def test_spend_is_recorded_from_real_usage_and_cap_applies(client):
    h = {"X-Access-Code": CODE}
    assert client.post("/chat", json={"message": "1"}, headers=h).status_code == 200   # $0.01
    assert client.post("/chat", json={"message": "2"}, headers=h).status_code == 200   # $0.02
    assert client.post("/chat", json={"message": "3"}, headers=h).status_code == 200   # $0.03 >= cap
    r = client.post("/chat", json={"message": "4"}, headers=h)
    assert r.status_code == 429 and "budget" in r.json()["detail"]


@needs_db
def test_access_endpoint_checks_the_code_without_a_model_call(client):
    assert client.post("/access", headers={"X-Access-Code": CODE}).status_code == 200
    assert client.post("/access", headers={"X-Access-Code": "nope"}).status_code == 401
    for _ in range(9):                                  # 10 wrong codes in total -> blocked
        client.post("/access", headers={"X-Access-Code": "nope"})
    assert client.post("/access", headers={"X-Access-Code": CODE}).status_code == 429
    assert client.post("/chat", json={"message": "hi"}, headers={"X-Access-Code": CODE}).status_code == 429


@needs_db
def test_health_is_public_and_says_a_code_is_needed(client):
    body = client.get("/health").json()
    assert body["access_code_required"] is True and body["client_ip_source"] in {"socket", "true-client-ip"}
    assert "sk-ant" not in str(body)


def test_true_client_ip_is_used_and_x_forwarded_for_is_ignored():
    from starlette.requests import Request
    from backend.security import client_ip

    def req(headers):
        return Request({"type": "http", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
                        "client": ("10.0.0.1", 1234)})

    assert client_ip(req({"True-Client-IP": "203.0.113.7"})) == ("203.0.113.7", "true-client-ip")
    assert client_ip(req({"X-Forwarded-For": "1.2.3.4"})) == ("10.0.0.1", "socket")   # forgeable, not trusted


@pytest.mark.skipif(not (FRONTEND_DIST / "index.html").exists(), reason="run npm run build first")
@needs_db
def test_built_ui_is_served_on_the_same_origin(client):
    r = client.get("/")
    assert r.status_code == 200 and '<div id="root">' in r.text
