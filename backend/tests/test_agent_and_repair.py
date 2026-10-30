import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import auth, db, telegram
from app.agent import graph as agent_graph
from app.agent.graph import LLMError, match_workflows, parse_decision, run_agent
from app.api import routes
from app.main import app
from app.n8n.client import N8nClient
from app.schemas import Diagnosis, ManualSteps, PatchProposal
from app.services import repair
from tests.conftest import TEST_USER, TEST_WEBHOOK_TOKEN, log_in
from tests.fake_n8n import FakeN8n

FIXED_CODE = (
    "return $input.all().map(item => ({\n  json: {\n"
    "    email: (item.json.body.email ?? item.json.body.Email).toLowerCase(),\n"
    "    name: item.json.body.name\n  }\n}));"
)


class FakeLLM:
    def __init__(self, category="auto_fixable", patches=None):
        self.category = category
        self.patches = patches
        self.patch_calls = []
        self.diagnose_calls = 0

    def __call__(self):
        return self

    async def diagnose(self, context):
        self.diagnose_calls += 1
        return Diagnosis(
            root_cause="The code reads email but the form sends Email.",
            category=self.category,
            impact=f"{context.affected_count} leads not saved.",
            confidence=1.5,
            recommendation="Read both field names.",
        )

    async def propose_patch(self, context, diagnosis, validation_error):
        self.patch_calls.append(validation_error)
        if self.patches:
            text = self.patches[len(self.patch_calls) - 1]
        else:
            text = json.dumps({**context.parameters, "jsCode": FIXED_CODE})
        return PatchProposal(parameters_json=text, explanation="Accepts both spellings.")

    async def propose_steps(self, context, diagnosis):
        return ManualSteps(title="Reconnect the credential", steps=["Open n8n.", "Reconnect it."])


@pytest.fixture
def n8n(execution, workflow):
    return FakeN8n(execution, workflow)


@pytest.fixture
def client(n8n):
    return N8nClient("http://n8n:5678", "key", transport=n8n.transport)


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setattr(repair, "POLL_INTERVAL", 0.01)
    monkeypatch.setattr(repair, "POLL_TIMEOUT", 0.05)


async def diagnose(client, llm=None, message="Why did my lead capture workflow fail last night?"):
    state = await run_agent(client, TEST_USER, message, llm_factory=llm or FakeLLM())
    return state["messages"]


def proposal_id(messages):
    return next(m["proposal_id"] for m in messages if "proposal_id" in m)


async def test_diagnosis_returns_cards_and_changes_nothing(client, n8n):
    messages = await diagnose(client)

    assert [m["type"] for m in messages] == ["diagnosis", "proposal"]
    assert messages[0]["failed_node"] == "Normalize Lead"
    assert messages[0]["confidence"] == 1.0
    assert "Email" in messages[1]["after"]["parameters"]["jsCode"]
    assert "Email" not in messages[1]["before"]["parameters"]["jsCode"]
    assert n8n.writes() == []
    assert db.list_incidents(TEST_USER)[0]["fix_status"] == "open"


async def test_repeat_question_reuses_the_open_proposal(client):
    llm = FakeLLM()
    first = await diagnose(client, llm)
    second = await diagnose(client, llm)

    assert proposal_id(first) == proposal_id(second)
    assert llm.diagnose_calls == 1
    assert len(db.list_incidents(TEST_USER)) == 1


async def test_invalid_patch_retries_once_with_the_error(client):
    llm = FakeLLM(patches=["not json", json.dumps({"jsCode": FIXED_CODE})])

    messages = await diagnose(client, llm)

    assert messages[1]["type"] == "proposal"
    assert llm.patch_calls[0] is None
    assert "not valid JSON" in llm.patch_calls[1]


async def test_two_invalid_patches_return_an_error_and_save_nothing(client):
    llm = FakeLLM(patches=["not json", "still not json"])

    messages = await diagnose(client, llm)

    assert [m["type"] for m in messages] == ["diagnosis", "error"]
    assert db.list_open_proposals(TEST_USER) == []


async def test_credential_errors_are_forced_to_needs_user(client, n8n):
    error = n8n.executions["4"]["data"]["resultData"]
    error["runData"]["Normalize Lead"][0]["error"]["message"] = "Request failed with status code 401"

    messages = await diagnose(client, FakeLLM(category="auto_fixable"))

    assert [m["type"] for m in messages] == ["diagnosis", "instructions"]
    assert messages[0]["category"] == "needs_user"


async def test_missing_api_key_is_reported(client):
    messages = await diagnose(client, agent_graph.GeminiAgent)

    assert messages[0]["type"] == "error"
    assert "GEMINI_API_KEY" in messages[0]["message"]


async def test_approve_patches_activates_retries_and_verifies(client, n8n):
    n8n.add_failure("5")
    pid = proposal_id(await diagnose(client))

    result = await repair.approve(pid, TEST_USER, client)

    assert result.repair_status == "success"
    assert result.verified and result.recovered_count == 2
    assert result.execution_status == "success"
    assert set(n8n.puts[0]) == {"name", "nodes", "connections", "settings"}
    assert "Email" in n8n.workflow["nodes"][1]["parameters"]["jsCode"]
    assert ("POST", f"/workflows/{n8n.workflow_id}/activate") in n8n.calls
    assert db.list_incidents(TEST_USER)[0]["fix_status"] == "approved"
    assert db.list_incidents(TEST_USER)[0]["resolved_at"]


async def test_failed_retry_rolls_back_and_reports_honestly(client, n8n, workflow):
    pid = proposal_id(await diagnose(client))
    n8n.retry_outcome = "error"

    result = await repair.approve(pid, TEST_USER, client)

    assert result.repair_status == "failed"
    assert not result.verified and result.recovered_count == 0
    assert result.execution_status == "error"
    assert "restored" in result.message
    assert n8n.workflow["nodes"] == workflow["nodes"]
    assert db.list_incidents(TEST_USER)[0]["fix_status"] == "open"


async def test_activate_failure_rolls_back(client, n8n, workflow):
    pid = proposal_id(await diagnose(client))
    n8n.fail_on.add(f"POST /workflows/{n8n.workflow_id}/activate")

    result = await repair.approve(pid, TEST_USER, client)

    assert result.repair_status == "failed"
    assert "ROLLBACK FAILED" in result.message  # Reactivating the backup fails too.
    assert n8n.workflow["nodes"] == workflow["nodes"]


async def test_workflow_edited_after_proposal_is_not_overwritten(client, n8n):
    pid = proposal_id(await diagnose(client))
    n8n.workflow["nodes"][2]["parameters"] = {"edited": True}

    result = await repair.approve(pid, TEST_USER, client)

    assert result.repair_status == "failed"
    assert "edited" in result.message
    assert n8n.puts == []


async def test_inactive_workflow_stays_inactive(client, n8n):
    n8n.workflow["active"] = False
    pid = proposal_id(await diagnose(client))

    result = await repair.approve(pid, TEST_USER, client)

    assert result.repair_status == "success"
    assert ("POST", f"/workflows/{n8n.workflow_id}/activate") not in n8n.calls


async def test_double_approve_applies_once(client, n8n):
    pid = proposal_id(await diagnose(client))
    await repair.approve(pid, TEST_USER, client)

    with pytest.raises(repair.ProposalStateError):
        await repair.approve(pid, TEST_USER, client)
    assert len(n8n.puts) == 1


async def test_reject_changes_nothing(client, n8n):
    pid = proposal_id(await diagnose(client))

    repair.reject(pid, TEST_USER)

    assert n8n.writes() == []
    assert db.get_proposal(pid, TEST_USER)["status"] == "rejected"
    assert db.list_incidents(TEST_USER)[0]["fix_status"] == "rejected"


async def test_chat_approval_and_rejection_route_to_the_same_functions(client, n8n):
    await diagnose(client)

    state = await run_agent(client, TEST_USER, "Yes, fix it", llm_factory=FakeLLM())

    assert state["messages"][0]["type"] == "result"
    assert state["messages"][0]["verified"] is True
    assert len(n8n.puts) == 1


async def test_sidebar_diagnose_message_uses_the_focused_workflow(client, n8n):
    state = await run_agent(
        client, TEST_USER, "Why did this fail?", workflow_id=n8n.workflow_id, llm_factory=FakeLLM()
    )

    assert [m["type"] for m in state["messages"]] == ["diagnosis", "proposal"]
    assert n8n.writes() == []


async def test_chat_approval_with_nothing_open_changes_nothing(client, n8n):
    state = await run_agent(client, TEST_USER, "approve", llm_factory=FakeLLM())

    assert state["messages"][0]["type"] == "text"
    assert n8n.writes() == []


async def test_manual_done_retries_and_marks_manual(client, n8n):
    error = n8n.executions["4"]["data"]["resultData"]
    error["runData"]["Normalize Lead"][0]["error"]["message"] = "The credentials have expired"
    pid = proposal_id(await diagnose(client))

    with pytest.raises(repair.ProposalStateError):
        await repair.approve(pid, TEST_USER, client)
    db.set_proposal_status(pid, "open")

    n8n.retry_outcome = "error"
    failed = await repair.complete_manual(pid, TEST_USER, client)
    assert failed.repair_status == "failed"
    assert db.get_proposal(pid, TEST_USER)["status"] == "open"

    # The failed retry doesn't mark the original recovered, so "done" can be tried again.
    n8n.retry_outcome = "success"
    result = await repair.complete_manual(pid, TEST_USER, client)
    assert result.verified
    assert n8n.puts == []
    assert db.list_incidents(TEST_USER)[0]["fix_status"] == "manual"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Yes, fix it", "approve"),
        ("approve", "approve"),
        ("go ahead", "approve"),
        ("No thanks", "reject"),
        ("reject it", "reject"),
        ("I've completed this", "done"),
        ("done", "done"),
        ("no idea why it failed", None),
        ("yes but what does it change?", None),
        ("Why did my lead capture workflow fail?", None),
    ],
)
def test_parse_decision(message, expected):
    assert parse_decision(message) == expected


def test_match_workflows():
    lead = {"id": "1", "name": "Lead Capture"}
    scoring = {"id": "2", "name": "Lead Scoring"}
    slack = {"id": "3", "name": "Slack Digest"}
    every = [lead, scoring, slack]

    assert match_workflows("why did my lead capture workflow fail", every) == [lead]
    assert match_workflows("whats wrong with the slack digest", every) == [slack]
    assert match_workflows("lead capure failed", every) == [lead]
    assert match_workflows("the lead workflow", every) == [lead, scoring]
    assert match_workflows("hello there", every) == []


# HTTP layer


@pytest.fixture
def api(n8n, monkeypatch):
    monkeypatch.setattr(
        routes,
        "build_client",
        lambda url, key: N8nClient(url, key, transport=n8n.transport),
    )
    with TestClient(app) as test_client:
        log_in(test_client)
        yield test_client


def connect(api):
    return api.post("/connect", json={"n8n_url": "http://n8n:5678", "api_key": "top-secret"})


def test_health(api):
    assert api.get("/health").json() == {"ok": True}


def test_connect_stores_settings_and_never_returns_the_key(api):
    response = connect(api)

    assert response.json() == {"ok": True}
    assert "top-secret" not in response.text
    assert auth.load_connection(TEST_USER)["api_key"] == "top-secret"


def test_connect_failure_is_reported_and_not_stored(api, n8n):
    n8n.fail_on.add("GET /workflows")

    body = connect(api).json()

    assert body["ok"] is False
    assert "500" in body["error"]
    assert auth.load_connection(TEST_USER) is None


def test_workflows_requires_a_connection(api):
    response = api.get("/workflows")

    assert response.status_code == 400
    assert "not connected" in response.json()["detail"]


def test_workflow_status_moves_from_failing_to_pending_to_healthy(api, n8n, monkeypatch):
    monkeypatch.setattr(agent_graph.GeminiAgent, "__new__", lambda cls: FakeLLM())
    connect(api)

    first = api.get("/workflows").json()[0]
    assert (first["status"], first["failed_count"]) == ("failing", 1)
    assert first["name"] == "Lead Capture"

    messages = api.post("/chat", json={"message": "why did lead capture fail"}).json()["messages"]
    assert api.get("/workflows").json()[0]["status"] == "pending"

    result = api.post(f"/fix/{proposal_id(messages)}/approve").json()
    assert result["repair_status"] == "success"
    assert result["verified"] is True

    last = api.get("/workflows").json()[0]
    assert (last["status"], last["failed_count"]) == ("healthy", 0)
    assert last["last_execution_at"]

    incident = api.get("/incidents").json()[0]
    assert incident["fix_status"] == "approved"
    assert "signature" not in incident


def test_fix_endpoints_report_missing_and_repeated_proposals(api, n8n, monkeypatch):
    monkeypatch.setattr(agent_graph.GeminiAgent, "__new__", lambda cls: FakeLLM())
    connect(api)
    assert api.post("/fix/nope/reject").status_code == 404

    messages = api.post("/chat", json={"message": "why did lead capture fail"}).json()["messages"]
    pid = proposal_id(messages)
    assert api.post(f"/fix/{pid}/reject").json() == {"ok": True}
    assert api.post(f"/fix/{pid}/approve").status_code == 409
    assert n8n.writes() == []


# Webhook


WEBHOOK_BODY = {
    "execution": {"id": "4"},
    "workflow": {"id": "cPNkhlaKXAQtA3tt", "name": "Lead Capture"},
    "error": {"message": "boom"},
}


@pytest.fixture
def alerts(monkeypatch):
    sent = []

    async def fake_send(text, chat_id=None):
        sent.append(text)
        return True

    async def fake_alert(text, chat_id=None, proposal_id=None):
        await fake_send(text, chat_id)
        return None

    monkeypatch.setattr(telegram, "send_message", fake_send)
    monkeypatch.setattr(telegram, "send_alert", fake_alert)
    return sent


def post_webhook(api, body=WEBHOOK_BODY):
    return api.post(f"/webhook/n8n-error/{TEST_WEBHOOK_TOKEN}", json=body)


def test_webhook_stores_incident_and_alerts_once(api, n8n, alerts, monkeypatch):
    monkeypatch.setattr(agent_graph.GeminiAgent, "__new__", lambda cls: FakeLLM())
    connect(api)

    assert post_webhook(api).json() == {"ok": True}
    assert len(db.list_incidents(TEST_USER)) == 1
    assert "Lead Capture failed" in alerts[0]
    assert "Normalize Lead" in alerts[0]
    assert "Tap a button or reply to this message" in alerts[0]
    assert n8n.writes() == []

    post_webhook(api)
    assert len(alerts) == 1
    assert len(db.list_incidents(TEST_USER)) == 1


def test_webhook_ignores_failed_retries(api, n8n, alerts, monkeypatch):
    monkeypatch.setattr(agent_graph.GeminiAgent, "__new__", lambda cls: FakeLLM())
    connect(api)
    n8n.executions["4"]["retryOf"] = "1"

    post_webhook(api)

    assert alerts == []
    assert db.list_incidents(TEST_USER) == []


def test_webhook_alerts_when_diagnosis_fails(api, alerts, monkeypatch):
    def unavailable(cls):
        raise LLMError("down")

    monkeypatch.setattr(agent_graph.GeminiAgent, "__new__", unavailable)
    connect(api)

    post_webhook(api)

    assert "could not diagnose" in alerts[0]
    assert db.list_incidents(TEST_USER) == []


async def test_telegram_failure_does_not_leak_the_token(monkeypatch, caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token-value")
    from app.config import get_settings

    get_settings.cache_clear()
    transport = httpx.MockTransport(lambda request: httpx.Response(500))

    assert await telegram.send_message("hi", "123", transport=transport) is False
    assert "bot-token-value" not in caplog.text
