"""The demo account's "Simulate a failure" button. Every n8n call here is fake, and nothing is approved."""

import copy
import json
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import get_settings
from app.main import app
from app.services import demo
from tests.test_isolation import ALICE, BOB, WEBHOOK_BODY, world  # noqa: F401

LEAD_ID = "cPNkhlaKXAQtA3tt"
ORDER_ID = "D9SPvXRwpEsBzgVc"
ROUTE = f"/demo/simulate-failure/{LEAD_ID}"
ORDER_ROUTE = f"/demo/simulate-failure/{ORDER_ID}"
LEAD = demo.SCENARIOS["Lead Capture"]
ORDER = demo.SCENARIOS["Order Processing"]
# From the saved backup of the real workflow, before it was fixed.
ORIGINAL_ORDER_CODE = """return $input.all().map(item => {
  const order = item.json.body.order;
  const total = order.items.reduce((sum, i) => sum + i.price * i.qty, 0);
  return { json: { customer: order.customer, total } };
});"""
FIXED_CODE = "return $input.all(); // already fixed"


@pytest.fixture(autouse=True)
def fresh_cooldown(monkeypatch):
    monkeypatch.setattr(demo, "_last_runs", {})
    monkeypatch.setattr(demo, "WEBHOOK_RETRY_SECONDS", 0)


@pytest.fixture
def demo_world(world, monkeypatch):  # noqa: F811
    """Alice is the demo account. Her Normalize Lead has already been fixed."""
    monkeypatch.setenv("DEMO_EMAIL", ALICE["email"].upper())
    get_settings.cache_clear()
    alice, bob, alice_n8n, bob_n8n = world
    node(alice_n8n.workflow)["parameters"]["jsCode"] = FIXED_CODE
    return alice, bob, alice_n8n, bob_n8n


def node(workflow, name=LEAD.node_name):
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
    assert node(execution["workflowData"])["parameters"]["jsCode"] == LEAD.code
    assert "item.json.body.email.toLowerCase()" in LEAD.code


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
    assert node(n8n.workflow)["parameters"]["jsCode"] == LEAD.code
    assert [json.loads(hook["body"]) for hook in n8n.webhooks] == [
        {"Email": "test.lead@example.com", "name": "Test Lead"}
    ]
    assert n8n.workflow["active"] is True


def test_changes_only_the_code_of_the_one_node(demo_world):
    alice, _, n8n, _ = demo_world
    before = copy.deepcopy(n8n.workflow)

    alice.post(ROUTE)

    node(before)["parameters"]["jsCode"] = LEAD.code
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


def test_runs_again_after_two_minutes(demo_world):
    alice, _, n8n, _ = demo_world
    alice.post(ROUTE)
    demo._last_runs[LEAD_ID] -= 121

    assert alice.post(ROUTE).status_code == 200

    assert len(n8n.webhooks) == 2


# When it can't run


def test_workflow_without_a_known_bug_changes_nothing(demo_world):
    alice, _, n8n, _ = demo_world
    n8n.workflow["name"] = "Error Handler"

    response = alice.post(ROUTE)

    assert response.status_code == 409
    assert "no demo failure" in response.json()["detail"]
    assert n8n.writes() == []
    assert LEAD_ID not in demo._last_runs


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
    assert LEAD_ID not in demo._last_runs


def test_a_webhook_reporting_the_failure_counts_as_sent(demo_world):
    alice, _, n8n, _ = demo_world
    n8n.webhook_statuses = [500]

    assert alice.post(ROUTE).status_code == 200

    assert len(n8n.webhooks) == 1


# Order Processing and the per-workflow rules


def become_order_processing(n8n):
    """Turns the fake n8n's one workflow into Order Processing, as it really is once fixed."""
    n8n.workflow_id = ORDER_ID
    n8n.workflow["id"] = ORDER_ID
    n8n.workflow["name"] = "Order Processing"
    node(n8n.workflow)["name"] = ORDER.node_name
    node(n8n.workflow, ORDER.node_name)["parameters"]["jsCode"] = "return $input.all(); // fixed"
    node(n8n.workflow, "Lead Form Webhook")["parameters"]["path"] = "order-intake"


def test_order_processing_restores_its_own_bug_and_sends_the_unwrapped_order(demo_world):
    alice, _, n8n, _ = demo_world
    become_order_processing(n8n)

    response = alice.post(ORDER_ROUTE)

    assert response.status_code == 200
    assert response.json()["workflow_name"] == "Order Processing"
    assert n8n.writes() == [
        ("PUT", f"/workflows/{ORDER_ID}"),
        ("POST", f"/workflows/{ORDER_ID}/activate"),
        ("POST", "/webhook/order-intake"),
    ]
    assert node(n8n.workflow, ORDER.node_name)["parameters"]["jsCode"] == ORIGINAL_ORDER_CODE
    assert json.loads(n8n.webhooks[0]["body"]) == {
        "customer": "Meena",
        "items": [{"price": 299, "qty": 1}],
    }
    assert n8n.webhooks[0]["api_key"] is None


def test_order_processing_only_rejects_its_own_open_fixes(demo_world):
    alice, _, n8n, _ = demo_world
    user_id = fail_for_real(alice)
    lead_fix = db.list_open_proposals(user_id)[0]
    become_order_processing(n8n)

    assert alice.post(ORDER_ROUTE).status_code == 200

    assert db.get_proposal_by_id(lead_fix["id"])["status"] == "open"


def test_order_processing_needs_its_node(demo_world):
    alice, _, n8n, _ = demo_world
    become_order_processing(n8n)
    node(n8n.workflow, ORDER.node_name)["name"] = "Renamed"

    response = alice.post(ORDER_ROUTE)

    assert response.status_code == 409
    assert "Calculate Total" in response.json()["detail"]
    assert n8n.writes() == []


def test_limit_is_per_workflow(demo_world):
    alice, _, n8n, _ = demo_world
    assert alice.post(ROUTE).status_code == 200

    n8n_lead = copy.deepcopy(n8n.workflow)
    become_order_processing(n8n)
    assert alice.post(ORDER_ROUTE).status_code == 200
    assert alice.post(ORDER_ROUTE).status_code == 429

    n8n.workflow = n8n_lead
    n8n.workflow_id = LEAD_ID
    assert alice.post(ROUTE).status_code == 429
    assert len(n8n.webhooks) == 2


@pytest.mark.parametrize(
    ("name", "expected"),
    [("Lead Capture", True), ("Order Processing", True), ("Error Handler", False)],
)
def test_demo_workflow_list_flags_only_known_bugs(demo_world, name, expected):
    alice, _, n8n, _ = demo_world
    n8n.workflow["name"] = name

    assert alice.get("/workflows").json()[0]["can_simulate"] is expected


def test_other_accounts_never_see_the_button(demo_world):
    _, bob, _, _ = demo_world

    assert bob.get("/workflows").json()[0]["can_simulate"] is False


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
@pytest.mark.parametrize("name", list(demo.SCENARIOS))
def test_each_test_request_really_breaks_its_buggy_code(name):
    scenario = demo.SCENARIOS[name]
    # The way n8n runs a Code node: a function body, with $input.
    script = (
        f"const $input = {{ all: () => [{{ json: {{ body: {json.dumps(scenario.payload)} }} }}] }};\n"
        f"(function () {{\n{scenario.code}\n}})();"
    )

    result = subprocess.run(
        [shutil.which("node"), "-e", script], capture_output=True, text=True, timeout=10
    )

    assert result.returncode != 0
    assert "TypeError" in result.stderr
