"""Workflows tagged to be ignored. Every n8n, LLM and Telegram call here is fake."""

import pytest

from app import db
from app.config import get_settings
from app.services import demo
from app.services.workflows import is_ignored
from tests.test_isolation import ALICE, WEBHOOK_BODY, alerts, world  # noqa: F401

IGNORED = {"id": "1", "name": "Error Handler", "tags": [{"id": "t1", "name": "flowdoctor-ignore"}]}


def tag(workflow, *names):
    workflow["tags"] = [{"id": f"t{i}", "name": name} for i, name in enumerate(names)]


def names(client):
    return [workflow["name"] for workflow in client.get("/workflows").json()]


def post_failure(client):
    token = db.get_user_by_email(ALICE["email"])["webhook_token"]
    assert client.post(f"/webhook/n8n-error/{token}", json=WEBHOOK_BODY).status_code == 200
    return db.get_user_by_email(ALICE["email"])["id"]


def set_ignore_tag(monkeypatch, value):
    monkeypatch.setenv("IGNORE_TAG", value)
    get_settings.cache_clear()


# Which workflows count as ignored


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ([{"name": "flowdoctor-ignore"}], True),
        ([{"name": "other"}, {"name": "flowdoctor-ignore"}], True),
        ([{"name": "Flowdoctor-Ignore"}], True),
        ([{"name": "other"}], False),
        ([{"name": "flowdoctor-ignore-not"}], False),
        ([], False),
        (None, False),
        (["flowdoctor-ignore"], False),
    ],
)
def test_is_ignored_reads_tag_names(tags, expected):
    assert is_ignored({"tags": tags}) is expected


def test_workflow_with_no_tags_field_is_not_ignored():
    assert is_ignored({"name": "Old n8n"}) is False


def test_tag_name_is_configurable(monkeypatch):
    set_ignore_tag(monkeypatch, "hide-me")

    assert is_ignored({"tags": [{"name": "hide-me"}]}) is True
    assert is_ignored({"tags": [{"name": "flowdoctor-ignore"}]}) is False


def test_empty_tag_name_ignores_nothing(monkeypatch):
    set_ignore_tag(monkeypatch, "")

    assert is_ignored({"tags": [{"name": ""}]}) is False
    assert is_ignored({"tags": [{"name": "flowdoctor-ignore"}]}) is False


def test_default_tag_name(monkeypatch):
    monkeypatch.delenv("IGNORE_TAG")
    get_settings.cache_clear()

    assert get_settings().ignore_tag == "flowdoctor-ignore"


# GET /workflows and the agent's counts


def test_tagged_workflow_is_left_out_of_the_list(world):  # noqa: F811
    alice, _, n8n, _ = world
    n8n.extra_workflows = [IGNORED, {"id": "2", "name": "Kept", "tags": [{"name": "billing"}]}]

    assert names(alice) == ["Lead Capture", "Kept"]


def test_ignored_workflow_costs_no_execution_lookups(world):  # noqa: F811
    alice, _, n8n, _ = world
    tag(n8n.workflow, "flowdoctor-ignore")

    assert names(alice) == []

    assert not [call for call in n8n.calls if call[1] == "/executions"]


def test_only_the_tagging_instance_is_affected(world):  # noqa: F811
    alice, bob, alice_n8n, _ = world
    tag(alice_n8n.workflow, "flowdoctor-ignore")

    assert names(alice) == []
    assert names(bob) == ["Lead Capture"]


def test_status_question_doesnt_count_ignored_workflows(world):  # noqa: F811
    alice, _, n8n, _ = world
    n8n.extra_workflows = [IGNORED]

    reply = alice.post("/chat", json={"message": "Give me an overview"}).json()

    assert reply["messages"][0]["content"].startswith("1 workflows:")


def test_asking_what_failed_only_looks_at_visible_workflows(world):  # noqa: F811
    # The fake's failures appear under every workflow, so counting the ignored one would be ambiguous.
    alice, _, n8n, _ = world
    n8n.extra_workflows = [IGNORED]

    reply = alice.post("/chat", json={"message": "Why did it fail?"}).json()

    assert [message["type"] for message in reply["messages"]] == ["diagnosis", "proposal"]


def test_naming_an_ignored_workflow_in_chat_doesnt_diagnose_it(world):  # noqa: F811
    alice, _, n8n, _ = world
    tag(n8n.workflow, "flowdoctor-ignore")

    reply = alice.post("/chat", json={"message": "Why did Lead Capture fail?"}).json()

    assert "proposal" not in [message["type"] for message in reply["messages"]]
    assert "Lead Capture" not in str(reply["messages"])
    assert db.list_incidents(db.get_user_by_email(ALICE["email"])["id"]) == []


def test_a_chat_aimed_at_an_ignored_workflow_doesnt_diagnose_it(world):  # noqa: F811
    alice, _, n8n, _ = world
    tag(n8n.workflow, "flowdoctor-ignore")

    reply = alice.post(
        "/chat", json={"message": "Why did this fail?", "workflow_id": n8n.workflow_id}
    ).json()

    assert reply["messages"][0]["type"] == "error"
    assert db.list_incidents(db.get_user_by_email(ALICE["email"])["id"]) == []


# Webhook failures


def test_webhook_failure_from_an_ignored_workflow_is_skipped(world, alerts):  # noqa: F811
    alice, _, n8n, _ = world
    tag(n8n.workflow, "other", "flowdoctor-ignore")

    user_id = post_failure(alice)

    assert alerts == []
    assert db.list_incidents(user_id) == []
    assert db.list_open_proposals(user_id) == []
    assert alice.get("/threads").json() == []
    assert not [call for call in n8n.calls if call[1].startswith("/executions")]


def test_webhook_failure_uses_the_configured_tag(world, alerts, monkeypatch):  # noqa: F811
    alice, _, n8n, _ = world
    set_ignore_tag(monkeypatch, "hide-me")
    tag(n8n.workflow, "flowdoctor-ignore")

    user_id = post_failure(alice)

    assert len(db.list_incidents(user_id)) == 1


def test_webhook_failure_from_a_normal_workflow_still_alerts(world, alerts):  # noqa: F811
    alice, _, n8n, _ = world
    alice.post(
        "/connect",
        json={"n8n_url": "http://n8n:5678", "api_key": "alice-key", "telegram_chat_id": "chat"},
    )
    tag(n8n.workflow, "billing")

    user_id = post_failure(alice)

    assert len(db.list_incidents(user_id)) == 1
    assert [chat for _, chat in alerts] == ["chat"]


def test_failure_is_not_dropped_when_the_tags_cant_be_read(world, alerts):  # noqa: F811
    alice, _, n8n, _ = world
    alice.post(
        "/connect",
        json={"n8n_url": "http://n8n:5678", "api_key": "alice-key", "telegram_chat_id": "chat"},
    )
    n8n.fail_on.add(f"GET /workflows/{n8n.workflow_id}")

    post_failure(alice)

    assert len(alerts) == 1
    assert "could not diagnose" in alerts[0][0]


# Demo button


def test_ignored_demo_workflow_cant_be_simulated(world, monkeypatch):  # noqa: F811
    alice, _, n8n, _ = world
    monkeypatch.setattr(demo, "_last_runs", {})
    monkeypatch.setenv("DEMO_EMAIL", ALICE["email"])
    get_settings.cache_clear()
    tag(n8n.workflow, "flowdoctor-ignore")

    response = alice.post(f"/demo/simulate-failure/{n8n.workflow_id}")

    assert response.status_code == 409
    assert n8n.writes() == []
