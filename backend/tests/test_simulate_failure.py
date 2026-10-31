"""The demo account's "Simulate a failure" button. Every n8n call here is fake, and nothing is approved."""

import copy
import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import get_settings
from app.main import app
from app.services import demo
from tests.test_isolation import ALICE, BOB, WEBHOOK_BODY, world  # noqa: F401

ROUTE = "/demo/simulate-failure"
FIXED_CODE = "return $input.all(); // already fixed"


@pytest.fixture(autouse=True)
def fresh_cooldown(monkeypatch):
    monkeypatch.setattr(demo, "_last_run", None)
    monkeypatch.setattr(demo, "WEBHOOK_RETRY_SECONDS", 0)


@pytest.fixture
def demo_world(world, monkeypatch):  # noqa: F811
    """Alice is the demo account. Her Normalize Lead has already been fixed."""
    monkeypatch.setenv("DEMO_EMAIL", ALICE["email"].upper())
    get_settings.cache_clear()
    alice, bob, alice_n8n, bob_n8n = world
    node(alice_n8n.workflow)["parameters"]["jsCode"] = FIXED_CODE
    return alice, bob, alice_n8n, bob_n8n


def node(workflow, name=demo.NODE_NAME):
    return next(n for n in workflow["nodes"] if n["name"] == name)


def fail_for_real(client, email=ALICE["email"]):
    token = db.get_user_by_email(email)["webhook_token"]
    assert client.post(f"/webhook/n8n-error/{token}", json=WEBHOOK_BODY).status_code == 200
    return db.get_user_by_email(email)["id"]


# Who can use it


def test_me_marks_only_the_demo_account(demo_world):
    alice, bob, _, _ = demo_world

    assert alice.get("/auth/me").json()["is_demo"] is True
    assert bob.get("/auth/me").json()["is_demo"] is False


def test_other_accounts_are_refused_and_change_nothing(demo_world):
    _, bob, _, bob_n8n = demo_world

    assert bob.post(ROUTE).status_code == 403

    assert bob_n8n.writes() == []
    assert bob_n8n.webhooks == []


def test_nobody_is_the_demo_account_when_none_is_set(world):  # noqa: F811
    alice, _, alice_n8n, _ = world

    assert alice.get("/auth/me").json()["is_demo"] is False
    assert alice.post(ROUTE).status_code == 403
    assert alice_n8n.writes() == []


def test_needs_a_login():
    with TestClient(app) as anonymous:
        assert anonymous.post(ROUTE).status_code == 401


# What it does


def test_buggy_code_is_the_original_from_the_real_execution(execution):
    assert node(execution["workflowData"])["parameters"]["jsCode"] == demo.BUGGY_CODE
    assert "item.json.body.email.toLowerCase()" in demo.BUGGY_CODE


def test_restores_the_bug_republishes_then_sends_one_lead(demo_world):
    alice, _, n8n, _ = demo_world
    workflow_id = n8n.workflow_id

    response = alice.post(ROUTE)

    assert response.status_code == 200
    assert response.json() == {
        "workflow_id": workflow_id,
        "workflow_name": "Lead Capture",
        "failed_count": 1,
        "cooldown_seconds": 120,
    }
    assert [call for call in n8n.writes()] == [
        ("PUT", f"/workflows/{workflow_id}"),
        ("POST", f"/workflows/{workflow_id}/activate"),
        ("POST", "/webhook/lead-capture"),
    ]
    assert node(n8n.workflow)["parameters"]["jsCode"] == demo.BUGGY_CODE
    assert [json.loads(hook["body"]) for hook in n8n.webhooks] == [
        {"Email": "test.lead@example.com", "name": "Test Lead"}
    ]


def test_changes_only_the_code_of_the_one_node(demo_world):
    alice, _, n8n, _ = demo_world
    before = copy.deepcopy(n8n.workflow)

    alice.post(ROUTE)

    node(before)["parameters"]["jsCode"] = demo.BUGGY_CODE
    assert n8n.puts[0]["nodes"] == before["nodes"]
    assert n8n.puts[0]["connections"] == before["connections"]


def test_test_lead_doesnt_carry_the_n8n_api_key(demo_world):
    # n8n saves webhook headers in the execution, and that data goes to the LLM.
    alice, _, n8n, _ = demo_world

    alice.post(ROUTE)

    assert n8n.webhooks[0]["api_key"] is None


def test_workflow_list_shows_the_new_failure(demo_world):
    alice, _, _, _ = demo_world
    before = alice.get("/workflows").json()[0]["failed_count"]

    reported = alice.post(ROUTE).json()["failed_count"]

    assert reported == before
    assert alice.get("/workflows").json()[0]["failed_count"] == before + 1


def test_open_proposals_are_rejected_so_the_next_diagnosis_is_fresh(demo_world):
    alice, _, n8n, _ = demo_world
    user_id = fail_for_real(alice)
    old = db.list_open_proposals(user_id)[0]
    n8n.fail_on.clear()
    writes_before = len(n8n.puts)

    alice.post(ROUTE)

    assert db.get_proposal_by_id(old["id"])["status"] == "rejected"
    assert db.list_incidents(user_id)[0]["fix_status"] == "rejected"
    assert len(n8n.puts) == writes_before + 1

    fail_for_real(alice)
    fresh = db.list_open_proposals(user_id)
    assert len(fresh) == 1
    assert fresh[0]["id"] != old["id"]


def test_other_users_proposals_are_left_alone(demo_world):
    alice, bob, _, _ = demo_world
    bob_id = fail_for_real(bob, BOB["email"])
    theirs = db.list_open_proposals(bob_id)[0]

    assert alice.post(ROUTE).status_code == 200

    assert db.get_proposal_by_id(theirs["id"])["status"] == "open"


def test_proposals_on_other_workflows_are_left_alone(demo_world):
    alice, _, _, _ = demo_world
    user_id = fail_for_real(alice)
    other = db.list_open_proposals(user_id)[0]
    with db._connect() as connection:
        connection.execute("UPDATE proposals SET workflow_id = 'other' WHERE id = ?", (other["id"],))

    assert alice.post(ROUTE).status_code == 200

    assert db.get_proposal_by_id(other["id"])["status"] == "open"


def test_nothing_is_ever_approved(demo_world):
    alice, _, n8n, _ = demo_world
    user_id = fail_for_real(alice)

    alice.post(ROUTE)

    assert not [call for call in n8n.calls if call[1].endswith("/retry")]
    assert db.list_incidents(user_id)[0]["fix_status"] != "approved"


# The two-minute limit


def test_second_run_within_two_minutes_is_refused(demo_world):
    alice, _, n8n, _ = demo_world
    alice.post(ROUTE)
    writes = len(n8n.writes())

    response = alice.post(ROUTE)

    assert response.status_code == 429
    assert 0 < int(response.headers["retry-after"]) <= 120
    assert "Try again in" in response.json()["detail"]
    assert len(n8n.writes()) == writes
    assert len(n8n.webhooks) == 1


def test_runs_again_after_two_minutes(demo_world, monkeypatch):
    alice, _, n8n, _ = demo_world
    alice.post(ROUTE)
    monkeypatch.setattr(demo, "_last_run", demo._last_run - 121)

    assert alice.post(ROUTE).status_code == 200

    assert len(n8n.webhooks) == 2


# When it can't run


def test_missing_workflow_changes_nothing_and_can_be_retried(demo_world):
    alice, _, n8n, _ = demo_world
    n8n.workflow["name"] = "Renamed"

    response = alice.post(ROUTE)

    assert response.status_code == 409
    assert "Lead Capture" in response.json()["detail"]
    assert n8n.writes() == []
    assert demo._last_run is None


@pytest.mark.parametrize(
    ("node_name", "field"),
    [("Normalize Lead", "name"), ("Lead Form Webhook", "type")],
    ids=["no-code-node", "no-webhook"],
)
def test_bad_setup_rejects_nothing(demo_world, node_name, field):
    alice, _, n8n, _ = demo_world
    user_id = fail_for_real(alice)
    writes = len(n8n.writes())
    node(n8n.workflow, node_name)[field] = "something-else"

    assert alice.post(ROUTE).status_code == 409

    assert len(db.list_open_proposals(user_id)) == 1
    assert len(n8n.writes()) == writes


def test_n8n_failure_is_reported_and_can_be_retried(demo_world):
    alice, _, n8n, _ = demo_world
    n8n.fail_on.add(f"PUT /workflows/{n8n.workflow_id}")

    assert alice.post(ROUTE).status_code == 502
    assert n8n.webhooks == []

    n8n.fail_on.clear()
    assert alice.post(ROUTE).status_code == 200


def test_webhook_not_ready_yet_is_retried_and_still_sends_one_lead(demo_world):
    alice, _, n8n, _ = demo_world
    n8n.webhook_statuses = [404, 200]

    assert alice.post(ROUTE).status_code == 200

    assert len(n8n.webhooks) == 2
    assert n8n.failures_from_webhooks == 1


def test_webhook_that_never_registers_is_reported(demo_world):
    alice, _, n8n, _ = demo_world
    n8n.webhook_statuses = [404, 404, 404]

    response = alice.post(ROUTE)

    assert response.status_code == 409
    assert "HTTP 404" in response.json()["detail"]
    assert n8n.failures_from_webhooks == 0
    assert demo._last_run is None


def test_a_webhook_reporting_the_failure_counts_as_sent(demo_world):
    alice, _, n8n, _ = demo_world
    n8n.webhook_statuses = [500]

    assert alice.post(ROUTE).status_code == 200

    assert len(n8n.webhooks) == 1
