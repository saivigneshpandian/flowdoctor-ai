"""Chats: one thread per workflow per user, saved history, and proposals tied to their thread."""

import json
import sqlite3

from fastapi.testclient import TestClient

from app import auth, db, telegram
from app.config import get_settings
from app.main import app
from tests.test_isolation import ALICE, WEBHOOK_BODY, alices_proposal, world  # noqa: F401

WORKFLOW = "cPNkhlaKXAQtA3tt"
QUESTION = "why did lead capture fail"


def open_workflow(client, workflow_id=WORKFLOW, name="Lead Capture"):
    return client.post("/threads", json={"workflow_id": workflow_id, "workflow_name": name}).json()


def load(client, thread_id):
    return client.get(f"/threads/{thread_id}").json()


def types(detail):
    return [m["message"]["type"] if m["role"] == "agent" else "user" for m in detail["messages"]]


def ask(client, message, thread_id=None):
    body = {"message": message, **({"thread_id": thread_id} if thread_id else {})}
    return client.post("/chat", json=body).json()


def test_threads_need_login():
    with TestClient(app) as anonymous:
        assert anonymous.get("/threads").status_code == 401
        assert anonymous.post("/threads", json={}).status_code == 401
        assert anonymous.get("/threads/thr_x").status_code == 401


def test_selecting_a_workflow_always_opens_its_one_thread(world):  # noqa: F811
    alice, _, _, _ = world

    first = open_workflow(alice)
    second = open_workflow(alice, name="Lead Capture v2")

    assert second["id"] == first["id"]
    assert second["workflow_id"] == WORKFLOW
    assert second["title"] == "Lead Capture v2"


def test_new_chat_reuses_an_unused_blank_thread(world):  # noqa: F811
    alice, _, _, _ = world

    first = alice.post("/threads", json={}).json()
    assert alice.post("/threads", json={}).json()["id"] == first["id"]
    assert first["workflow_id"] is None

    ask(alice, "hello", first["id"])
    assert alice.post("/threads", json={}).json()["id"] != first["id"]


def test_history_survives_a_reload_in_order(world):  # noqa: F811
    alice, _, _, _ = world
    thread = open_workflow(alice)

    reply = ask(alice, "Why did this fail?", thread["id"])

    assert reply["thread_id"] == thread["id"]
    detail = load(alice, thread["id"])
    assert types(detail) == ["user", "diagnosis", "proposal"]
    assert detail["messages"][0]["content"] == "Why did this fail?"
    proposal_id = detail["messages"][2]["message"]["proposal_id"]
    assert detail["proposal_states"] == {proposal_id: "open"}


def test_cards_stay_in_their_own_thread(world):  # noqa: F811
    alice, _, _, _ = world
    workflow_thread = open_workflow(alice)
    other = open_workflow(alice, "another-workflow", "Another")
    general = alice.post("/threads", json={}).json()
    ask(alice, "hello there", general["id"])

    ask(alice, "Why did this fail?", workflow_thread["id"])

    assert types(load(alice, workflow_thread["id"])) == ["user", "diagnosis", "proposal"]
    assert types(load(alice, other["id"])) == []
    assert types(load(alice, general["id"])) == ["user", "text"]


def test_naming_a_workflow_in_a_general_chat_moves_it_to_that_workflows_thread(world):  # noqa: F811
    alice, _, _, _ = world
    general = alice.post("/threads", json={}).json()

    reply = ask(alice, QUESTION, general["id"])

    workflow_thread = open_workflow(alice)
    assert reply["thread_id"] == workflow_thread["id"]
    assert types(load(alice, workflow_thread["id"])) == ["user", "diagnosis", "proposal"]
    assert types(load(alice, general["id"])) == []


def test_chat_with_no_thread_still_works_and_reports_where_it_was_saved(world):  # noqa: F811
    alice, _, _, _ = world

    general = ask(alice, "hello there")
    named = ask(alice, QUESTION)

    assert types(load(alice, general["thread_id"])) == ["user", "text"]
    assert types(load(alice, named["thread_id"])) == ["user", "diagnosis", "proposal"]


def test_a_thread_cant_be_aimed_at_a_different_workflow(world):  # noqa: F811
    alice, _, _, _ = world
    other = open_workflow(alice, "another-workflow", "Another")

    reply = alice.post(
        "/chat", json={"message": "Why did this fail?", "workflow_id": WORKFLOW, "thread_id": other["id"]}
    ).json()

    # It looked up the thread's own workflow, which this n8n doesn't have, and ignored the request's.
    assert [m["type"] for m in reply["messages"]] == ["error"]
    assert "another-workflow" in reply["messages"][0]["message"]
    assert types(load(alice, other["id"])) == ["user", "error"]


def test_typed_approval_in_another_chat_doesnt_reach_a_pending_proposal(world):  # noqa: F811
    alice, _, alice_n8n, _ = world
    alices_proposal(alice)
    general = alice.post("/threads", json={}).json()
    other = open_workflow(alice, "another-workflow", "Another")

    for thread_id in (general["id"], other["id"]):
        reply = ask(alice, "yes, fix it", thread_id)
        assert reply["messages"][0]["type"] == "text"
        assert "no fix waiting" in reply["messages"][0]["content"]
    assert alice_n8n.writes() == []


def test_typed_approval_in_the_proposals_thread_applies_and_is_saved(world):  # noqa: F811
    alice, _, alice_n8n, _ = world
    thread = open_workflow(alice)
    ask(alice, "Why did this fail?", thread["id"])

    reply = ask(alice, "yes, fix it", thread["id"])

    assert reply["messages"][0]["type"] == "result"
    assert len(alice_n8n.puts) == 1
    detail = load(alice, thread["id"])
    assert types(detail) == ["user", "diagnosis", "proposal", "user", "result"]
    assert list(detail["proposal_states"].values()) == ["approved"]


def test_button_results_and_states_survive_a_reload(world):  # noqa: F811
    alice, _, _, _ = world
    thread = open_workflow(alice)
    ask(alice, "Why did this fail?", thread["id"])
    proposal_id = list(load(alice, thread["id"])["proposal_states"])[0]

    assert alice.post(f"/fix/{proposal_id}/approve").json()["repair_status"] == "success"

    detail = load(alice, thread["id"])
    assert types(detail)[-1] == "result"
    assert detail["proposal_states"] == {proposal_id: "approved"}


def test_rejected_state_survives_a_reload(world):  # noqa: F811
    alice, _, _, _ = world
    thread = open_workflow(alice)
    ask(alice, "Why did this fail?", thread["id"])
    proposal_id = list(load(alice, thread["id"])["proposal_states"])[0]

    alice.post(f"/fix/{proposal_id}/reject")

    assert load(alice, thread["id"])["proposal_states"] == {proposal_id: "rejected"}


def test_asking_again_shows_the_same_proposal_in_the_same_thread(world):  # noqa: F811
    alice, _, _, _ = world
    thread = open_workflow(alice)

    ask(alice, "Why did this fail?", thread["id"])
    ask(alice, "Why did this fail?", thread["id"])

    assert len(load(alice, thread["id"])["proposal_states"]) == 1


def test_chats_list_has_title_workflow_updated_and_pending(world):  # noqa: F811
    alice, _, _, _ = world
    open_workflow(alice, "unused-workflow", "Unused")
    alice.post("/threads", json={})
    assert alice.get("/threads").json() == []

    general = ask(alice, "Hello, can you help me with something long enough to shorten? " * 3)
    workflow_thread = open_workflow(alice)
    ask(alice, "Why did this fail?", workflow_thread["id"])

    listed = alice.get("/threads").json()
    assert [t["id"] for t in listed] == [workflow_thread["id"], general["thread_id"]]
    assert listed[0]["title"] == "Lead Capture"
    assert listed[0]["workflow_name"] == "Lead Capture"
    assert listed[0]["pending"] is True
    assert listed[0]["updated_at"]
    assert listed[1]["workflow_id"] is None
    assert listed[1]["pending"] is False
    assert len(listed[1]["title"]) == 60


def test_pending_clears_once_the_proposal_is_decided(world):  # noqa: F811
    alice, _, _, _ = world
    proposal_id = alices_proposal(alice)

    alice.post(f"/fix/{proposal_id}/reject")

    assert alice.get("/threads").json()[0]["pending"] is False


def test_webhook_diagnosis_lands_in_the_owners_workflow_thread(world, monkeypatch):  # noqa: F811
    alice, bob, _, _ = world

    async def no_send(text, chat_id=None):
        return True

    async def no_alert(text, chat_id=None, proposal_id=None):
        return None

    monkeypatch.setattr(telegram, "send_message", no_send)
    monkeypatch.setattr(telegram, "send_alert", no_alert)
    token = db.get_user_by_email(ALICE["email"])["webhook_token"]

    bob.post(f"/webhook/n8n-error/{token}", json=WEBHOOK_BODY)

    thread = open_workflow(alice)
    assert types(load(alice, thread["id"])) == ["diagnosis", "proposal"]
    assert alice.get("/threads").json()[0]["pending"] is True
    assert bob.get("/threads").json() == []


# Isolation between users


def test_chats_list_is_private(world):  # noqa: F811
    alice, bob, _, _ = world
    alices_proposal(alice)

    assert len(alice.get("/threads").json()) == 1
    assert bob.get("/threads").json() == []


def test_other_users_thread_is_a_404_with_no_content(world):  # noqa: F811
    alice, bob, _, _ = world
    thread = open_workflow(alice)
    ask(alice, "a private question", thread["id"])

    response = bob.get(f"/threads/{thread['id']}")

    assert response.status_code == 404
    assert response.json() == bob.get("/threads/thr_missing").json()
    assert "private" not in response.text


def test_cant_post_into_another_users_thread(world):  # noqa: F811
    alice, bob, alice_n8n, _ = world
    thread = open_workflow(alice)
    ask(alice, "Why did this fail?", thread["id"])
    before = load(alice, thread["id"])

    for message in ("yes, fix it", "injected"):
        response = bob.post("/chat", json={"message": message, "thread_id": thread["id"]})
        assert response.status_code == 404

    assert load(alice, thread["id"]) == before
    assert alice_n8n.writes() == []


def test_same_workflow_id_gives_each_user_a_separate_thread(world):  # noqa: F811
    alice, bob, _, _ = world

    alice_thread = open_workflow(alice)
    bob_thread = open_workflow(bob)
    ask(alice, "Why did this fail?", alice_thread["id"])

    assert alice_thread["id"] != bob_thread["id"]
    assert types(load(bob, bob_thread["id"])) == []
    assert load(bob, bob_thread["id"])["proposal_states"] == {}


def test_blank_threads_are_not_shared(world):  # noqa: F811
    alice, bob, _, _ = world

    assert alice.post("/threads", json={}).json()["id"] != bob.post("/threads", json={}).json()["id"]


def test_results_only_go_to_the_owners_thread(world):  # noqa: F811
    alice, bob, _, _ = world
    proposal_id = alices_proposal(alice)
    bob_thread = open_workflow(bob)

    assert bob.post(f"/fix/{proposal_id}/approve").status_code == 404

    assert types(load(bob, bob_thread["id"])) == []
    assert len(load(alice, alice.get("/threads").json()[0]["id"])["proposal_states"]) == 1


# Existing data


OLD_PROPOSAL = (
    "INSERT INTO proposals (id, user_id, incident_id, workflow_id, workflow_name, node_name, kind, "
    "status, signature, parameters, backup_workflow, affected_execution_ids, messages, created_at) "
    "VALUES (?, ?, 'inc', 'wf1', 'Old Workflow', 'Node', 'patch', ?, 'sig', '{}', '{}', '[]', ?, ?)"
)


def test_existing_proposals_are_filed_into_their_workflow_thread_once(database):
    user = db.create_user("old@example.com", "x")
    cards = json.dumps([{"type": "text", "content": "old card"}])
    connection = sqlite3.connect(get_settings().database_path)
    connection.execute(OLD_PROPOSAL, ("prop_a", user["id"], "approved", cards, "2026-10-01T00:00:00Z"))
    connection.execute(OLD_PROPOSAL, ("prop_b", user["id"], "open", cards, "2026-10-02T00:00:00Z"))
    connection.commit()
    connection.close()

    auth.bootstrap()
    auth.bootstrap()

    listed = db.list_threads(user["id"])
    assert len(listed) == 1
    assert listed[0]["title"] == "Old Workflow"
    assert listed[0]["pending"] is True
    assert listed[0]["updated_at"] == "2026-10-02T00:00:00Z"
    assert len(db.list_thread_messages(listed[0]["id"])) == 2
    assert db.thread_proposal_states(listed[0]["id"], user["id"]) == {
        "prop_a": "approved",
        "prop_b": "open",
    }


def test_two_browser_sessions_see_the_same_history(world):  # noqa: F811
    alice, _, _, _ = world
    thread = open_workflow(alice)
    ask(alice, "hello", thread["id"])

    with TestClient(app) as second_tab:
        second_tab.post("/auth/login", json={"email": ALICE["email"], "password": ALICE["password"]})
        assert second_tab.get("/threads").json()[0]["id"] == thread["id"]
        assert types(load(second_tab, thread["id"]))[0] == "user"
