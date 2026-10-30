"""Two users on the same server must never see or change each other's data."""

import json

import pytest
from fastapi.testclient import TestClient

from app import auth, db, telegram
from app.agent import graph as agent_graph
from app.api import routes
from app.main import app
from app.n8n.client import N8nClient
from app.schemas import Diagnosis, ManualSteps, PatchProposal
from app.services import repair
from tests.fake_n8n import FakeN8n

ALICE = {"email": "alice@example.com", "password": "alice-password", "invite_code": "test-invite"}
BOB = {"email": "bob@example.com", "password": "bob-password", "invite_code": "test-invite"}
FIXED_CODE = (
    "return $input.all().map(item => {\n"
    "  const email = item.json.body.email ?? item.json.body.Email;\n"
    "  if (!email) throw new Error('Lead is missing an email');\n"
    "  return { json: { email: email.toLowerCase() } };\n"
    "});"
)


class FakeLLM:
    def __init__(self, category="auto_fixable"):
        self.category = category

    async def diagnose(self, context):
        return Diagnosis(
            root_cause="cause", category=self.category, impact="i", confidence=0.9, recommendation="r"
        )

    async def propose_patch(self, context, diagnosis, validation_error):
        return PatchProposal(parameters_json=json.dumps({"jsCode": FIXED_CODE}), explanation="e")

    async def propose_steps(self, context, diagnosis):
        return ManualSteps(title="Reconnect", steps=["Do it."])


@pytest.fixture
def world(monkeypatch, execution, workflow):
    """Alice and Bob, each logged in with their own browser session and their own n8n."""
    instances = {"alice-key": FakeN8n(execution, workflow), "bob-key": FakeN8n(execution, workflow)}
    monkeypatch.setattr(
        routes,
        "build_client",
        lambda url, key: N8nClient(url, key, transport=instances[key].transport),
    )
    monkeypatch.setattr(repair, "POLL_INTERVAL", 0.01)
    monkeypatch.setattr(repair, "POLL_TIMEOUT", 0.05)
    monkeypatch.setattr(agent_graph.GeminiAgent, "__new__", lambda cls: FakeLLM())

    with TestClient(app) as alice, TestClient(app) as bob:
        for client, credentials, key in (
            (alice, ALICE, "alice-key"),
            (bob, BOB, "bob-key"),
        ):
            assert client.post("/auth/signup", json=credentials).status_code == 200
            connected = client.post("/connect", json={"n8n_url": "http://n8n:5678", "api_key": key})
            assert connected.json() == {"ok": True}
        yield alice, bob, instances["alice-key"], instances["bob-key"]


def alices_proposal(alice):
    messages = alice.post("/chat", json={"message": "why did lead capture fail"}).json()["messages"]
    return next(m["proposal_id"] for m in messages if "proposal_id" in m)


@pytest.mark.parametrize("action", ["approve", "reject", "done"])
def test_other_users_proposal_is_a_404_and_stays_untouched(world, action):
    alice, bob, alice_n8n, _ = world
    proposal_id = alices_proposal(alice)

    response = bob.post(f"/fix/{proposal_id}/{action}")

    assert response.status_code == 404
    assert response.json() == alice.post("/fix/prop_missing/reject").json() | {
        "detail": f"Proposal {proposal_id} was not found."
    }
    assert alice_n8n.writes() == []
    assert alice.get("/incidents").json()[0]["fix_status"] == "open"
    assert alice.get("/workflows").json()[0]["status"] == "pending"


def test_incidents_are_private(world):
    alice, bob, _, _ = world
    alices_proposal(alice)

    assert len(alice.get("/incidents").json()) == 1
    assert bob.get("/incidents").json() == []


def test_pending_status_is_private_even_on_the_same_workflow_id(world):
    alice, bob, _, _ = world
    alices_proposal(alice)

    assert alice.get("/workflows").json()[0]["status"] == "pending"
    assert bob.get("/workflows").json()[0]["status"] == "failing"


def test_typed_approval_only_reaches_your_own_proposal(world):
    alice, bob, alice_n8n, bob_n8n = world
    alices_proposal(alice)

    reply = bob.post("/chat", json={"message": "yes, fix it"}).json()["messages"]

    assert reply[0]["type"] == "text"
    assert "no fix waiting" in reply[0]["content"]
    assert alice_n8n.writes() == []
    assert bob_n8n.writes() == []


def test_each_user_gets_their_own_proposal_for_the_same_failure(world):
    alice, bob, _, _ = world

    assert alices_proposal(alice) != alices_proposal(bob)
    assert len(alice.get("/incidents").json()) == 1
    assert len(bob.get("/incidents").json()) == 1


def test_a_users_approval_only_edits_their_own_n8n(world):
    alice, bob, alice_n8n, bob_n8n = world
    proposal_id = alices_proposal(alice)

    assert alice.post(f"/fix/{proposal_id}/approve").json()["repair_status"] == "success"

    assert len(alice_n8n.puts) == 1
    assert bob_n8n.puts == []
    assert bob.get("/workflows").json()[0]["status"] == "failing"


def test_connections_are_separate(world):
    alice, bob, _, _ = world
    alice_user = db.get_user_by_email(ALICE["email"])
    bob_user = db.get_user_by_email(BOB["email"])

    assert auth.load_connection(alice_user["id"])["api_key"] == "alice-key"
    assert auth.load_connection(bob_user["id"])["api_key"] == "bob-key"

    alice.post("/auth/logout")
    assert alice.get("/workflows").status_code == 401
    assert bob.get("/workflows").status_code == 200


def test_new_user_without_a_connection_sees_nothing(world):
    alice, _, _, _ = world
    alices_proposal(alice)

    with TestClient(app) as carol:
        carol.post("/auth/signup", json={"email": "carol@example.com", "password": "carol-password", "invite_code": "test-invite"})

        assert carol.get("/workflows").status_code == 400
        assert carol.get("/incidents").json() == []
        assert carol.post("/chat", json={"message": "yes"}).json()["messages"][0]["type"] == "error"


WEBHOOK_BODY = {
    "execution": {"id": "4"},
    "workflow": {"id": "cPNkhlaKXAQtA3tt", "name": "Lead Capture"},
}


@pytest.fixture
def alerts(monkeypatch):
    sent = []

    async def fake_send(text, chat_id=None):
        sent.append((text, chat_id))
        return True

    monkeypatch.setattr(telegram, "send_message", fake_send)
    return sent


def test_webhook_token_files_the_incident_under_its_owner(world, alerts):
    alice, bob, _, _ = world
    token = db.get_user_by_email(ALICE["email"])["webhook_token"]

    assert bob.post(f"/webhook/n8n-error/{token}", json=WEBHOOK_BODY).json() == {"ok": True}

    assert len(alice.get("/incidents").json()) == 1
    assert bob.get("/incidents").json() == []


def test_webhook_alert_goes_to_the_owners_chat_only(world, alerts, monkeypatch):
    alice, _, _, _ = world
    alice.post(
        "/connect",
        json={"n8n_url": "http://n8n:5678", "api_key": "alice-key", "telegram_chat_id": "alice-chat"},
    )
    token = db.get_user_by_email(ALICE["email"])["webhook_token"]

    alice.post(f"/webhook/n8n-error/{token}", json=WEBHOOK_BODY)

    assert [chat for _, chat in alerts] == ["alice-chat"]


async def test_no_chat_id_never_falls_back_to_the_servers_chat(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "owners-chat")
    get_settings.cache_clear()

    assert await telegram.send_message("alert", None) is False


def test_unknown_webhook_token_is_a_404(world, alerts):
    alice, _, _, _ = world

    assert alice.post("/webhook/n8n-error/nope", json=WEBHOOK_BODY).status_code == 404
    assert alerts == []


def test_webhook_token_alone_is_enough(world, alerts):
    alice, _, _, _ = world
    token = db.get_user_by_email(ALICE["email"])["webhook_token"]

    with TestClient(app) as stranger:
        assert stranger.post(f"/webhook/n8n-error/{token}", json=WEBHOOK_BODY).status_code == 200

    assert len(alice.get("/incidents").json()) == 1


def test_the_old_webhook_url_is_gone(world):
    alice, _, _, _ = world

    assert alice.post("/webhook/n8n-error", json=WEBHOOK_BODY).status_code == 404
