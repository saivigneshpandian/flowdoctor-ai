"""Approving and rejecting fixes from Telegram. Every n8n and Telegram call here is fake."""

import asyncio
import json
import logging
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db, telegram
from app.config import get_settings
from app.main import app
from app.services import telegram_bot
from tests.test_isolation import ALICE, WEBHOOK_BODY, world  # noqa: F401

SECRET = "telegram-test-secret"
TOKEN = "123:bot-token-value"
ALICE_CHAT = "1001"
BOB_CHAT = "2002"
ALERT_ID = 555
HEADERS = {"X-Telegram-Bot-Api-Secret-Token": SECRET}


class FakeTelegram:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.failing = set()
        self.updates = []

    async def handler(self, request):
        method = request.url.path.rsplit("/", 1)[-1]
        self.calls.append((method, json.loads(request.content)))
        if self.fail or method in self.failing:
            return httpx.Response(500)
        if method == "getUpdates":
            if self.updates:
                return httpx.Response(200, json={"ok": True, "result": [self.updates.pop(0)]})
            await asyncio.sleep(0.01)
            return httpx.Response(200, json={"ok": True, "result": []})
        if method == "deleteWebhook":
            return httpx.Response(200, json={"ok": True, "result": True})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": ALERT_ID}})

    def of(self, method):
        return [payload for name, payload in self.calls if name == method]

    def texts(self):
        return [payload["text"] for payload in self.of("sendMessage")]


@pytest.fixture
def bot(monkeypatch):
    fake = FakeTelegram()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", SECRET)
    get_settings.cache_clear()
    monkeypatch.setattr(telegram, "_transport", httpx.MockTransport(fake.handler))
    return fake


def connect_chat(client, key, chat_id):
    response = client.post(
        "/connect", json={"n8n_url": "http://n8n:5678", "api_key": key, "telegram_chat_id": chat_id}
    )
    assert response.json() == {"ok": True}


@pytest.fixture
def alert(world, bot):  # noqa: F811
    """A real failure webhook for Alice, which leaves an open proposal and a sent alert."""
    alice, bob, alice_n8n, bob_n8n = world
    connect_chat(alice, "alice-key", ALICE_CHAT)
    connect_chat(bob, "bob-key", BOB_CHAT)
    token = db.get_user_by_email(ALICE["email"])["webhook_token"]
    assert alice.post(f"/webhook/n8n-error/{token}", json=WEBHOOK_BODY).status_code == 200
    user_id = db.get_user_by_email(ALICE["email"])["id"]
    proposal = db.list_open_proposals(user_id)[0]
    bot.calls.clear()
    return alice, proposal, alice_n8n


def post(client, update, headers=HEADERS):
    return client.post("/telegram/webhook", json=update, headers=headers)


def tap(client, proposal_id, action="approve", chat=ALICE_CHAT):
    return post(
        client,
        {
            "update_id": 1,
            "callback_query": {
                "id": "cb1",
                "data": f"{action}:{proposal_id}",
                "message": {"message_id": ALERT_ID, "chat": {"id": int(chat)}},
            },
        },
    )


def reply(client, text, chat=ALICE_CHAT, reply_to=ALERT_ID):
    message = {"message_id": 9, "chat": {"id": int(chat)}, "text": text}
    if reply_to is not None:
        message["reply_to_message"] = {"message_id": reply_to}
    return post(client, {"update_id": 2, "message": message})


def status(proposal):
    return db.get_proposal_by_id(proposal["id"])["status"]


# The webhook's secret


@pytest.mark.parametrize(
    "headers", [{}, {"X-Telegram-Bot-Api-Secret-Token": "wrong"}], ids=["missing", "wrong"]
)
def test_webhook_rejects_a_bad_secret(alert, headers):
    client, proposal, n8n = alert

    response = tap_with_headers(client, proposal, headers)

    assert response.status_code == 401
    assert status(proposal) == "open"
    assert n8n.writes() == []


def tap_with_headers(client, proposal, headers):
    update = {"callback_query": {"id": "x", "data": f"approve:{proposal['id']}", "message": {"chat": {"id": int(ALICE_CHAT)}}}}
    return post(client, update, headers)


def test_webhook_is_off_without_a_configured_secret(alert, monkeypatch):
    client, proposal, n8n = alert
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "")
    get_settings.cache_clear()

    for headers in ({}, {"X-Telegram-Bot-Api-Secret-Token": ""}):
        assert tap_with_headers(client, proposal, headers).status_code == 503
    assert status(proposal) == "open"
    assert n8n.writes() == []


def test_webhook_needs_no_login_but_the_secret_is_enough(bot):
    with TestClient(app) as anonymous:
        assert post(anonymous, {"update_id": 1}).status_code == 200


# Alerts


def test_alert_has_both_buttons_and_stores_its_message(world, bot):  # noqa: F811
    alice, _, _, _ = world
    connect_chat(alice, "alice-key", ALICE_CHAT)
    token = db.get_user_by_email(ALICE["email"])["webhook_token"]

    alice.post(f"/webhook/n8n-error/{token}", json=WEBHOOK_BODY)

    sent = bot.of("sendMessage")[0]
    assert sent["chat_id"] == ALICE_CHAT
    assert "Lead Capture failed" in sent["text"]
    assert "Proposed fix:" in sent["text"]
    buttons = sent["reply_markup"]["inline_keyboard"][0]
    assert [b["text"] for b in buttons] == ["Approve fix", "Reject"]
    for button in buttons:
        assert len(button["callback_data"].encode()) <= 64

    user_id = db.get_user_by_email(ALICE["email"])["id"]
    proposal = db.list_open_proposals(user_id)[0]
    assert proposal["telegram_chat_id"] == ALICE_CHAT
    assert proposal["telegram_message_id"] == ALERT_ID


def test_manual_fix_alert_has_no_approve_button(world, bot, monkeypatch):  # noqa: F811
    alice, _, alice_n8n, _ = world
    connect_chat(alice, "alice-key", ALICE_CHAT)
    run = alice_n8n.executions["4"]["data"]["resultData"]
    run["runData"]["Normalize Lead"][0]["error"]["message"] = "The credentials have expired"
    token = db.get_user_by_email(ALICE["email"])["webhook_token"]

    alice.post(f"/webhook/n8n-error/{token}", json=WEBHOOK_BODY)

    sent = bot.of("sendMessage")[0]
    assert "reply_markup" not in sent
    assert "Open FlowDoctor for the steps" in sent["text"]


def test_failed_alert_send_doesnt_lose_the_proposal(world, bot):  # noqa: F811
    alice, _, _, _ = world
    connect_chat(alice, "alice-key", ALICE_CHAT)
    bot.fail = True
    token = db.get_user_by_email(ALICE["email"])["webhook_token"]

    assert alice.post(f"/webhook/n8n-error/{token}", json=WEBHOOK_BODY).status_code == 200

    user_id = db.get_user_by_email(ALICE["email"])["id"]
    proposal = db.list_open_proposals(user_id)[0]
    assert proposal["telegram_message_id"] is None


# Buttons


def test_approve_button_applies_reports_the_real_result_and_removes_buttons(alert):
    client, proposal, n8n = alert
    bot = telegram._transport.handler.__self__

    assert tap(client, proposal["id"]).status_code == 200

    assert len(n8n.puts) == 1
    assert status(proposal) == "approved"
    assert bot.of("answerCallbackQuery")[0]["callback_query_id"] == "cb1"
    text = bot.texts()[0]
    assert "Recovered: 1" in text
    assert "Verified: yes" in text
    edit = bot.of("editMessageReplyMarkup")[0]
    assert edit == {"chat_id": ALICE_CHAT, "message_id": ALERT_ID}


def test_failed_repair_is_reported_honestly(alert):
    client, proposal, n8n = alert
    bot = telegram._transport.handler.__self__
    n8n.retry_outcome = "error"

    tap(client, proposal["id"])

    text = bot.texts()[0]
    assert "Recovered: 0" in text
    assert "Verified: no" in text
    assert "Execution status: error" in text
    assert "restored" in text
    assert status(proposal) == "failed"
    assert len(bot.of("editMessageReplyMarkup")) == 1


def test_buttons_stay_when_nothing_happened_and_it_can_be_retried(alert):
    client, proposal, n8n = alert
    bot = telegram._transport.handler.__self__
    n8n.fail_on.add(f"GET /workflows/{n8n.workflow_id}")

    tap(client, proposal["id"])

    assert status(proposal) == "open"
    assert "Nothing was changed" in bot.texts()[0]
    assert bot.of("editMessageReplyMarkup") == []
    assert n8n.puts == []


def test_reject_button_changes_nothing_in_n8n(alert):
    client, proposal, n8n = alert
    bot = telegram._transport.handler.__self__

    tap(client, proposal["id"], "reject")

    assert status(proposal) == "rejected"
    assert n8n.writes() == []
    assert bot.texts() == ["Fix rejected. Nothing was changed."]
    assert len(bot.of("editMessageReplyMarkup")) == 1
    user_id = proposal["user_id"]
    assert db.list_incidents(user_id)[0]["fix_status"] == "rejected"


def test_double_tap_applies_once(alert):
    client, proposal, n8n = alert
    bot = telegram._transport.handler.__self__

    tap(client, proposal["id"])
    tap(client, proposal["id"])

    assert len(n8n.puts) == 1
    assert "already approved" in bot.texts()[1]


def test_approved_in_the_app_first_then_tapped(alert):
    client, proposal, n8n = alert
    bot = telegram._transport.handler.__self__
    client.post(f"/fix/{proposal['id']}/reject")

    tap(client, proposal["id"])

    assert n8n.writes() == []
    assert "already rejected" in bot.texts()[0]
    assert len(bot.of("editMessageReplyMarkup")) == 1


def test_result_is_saved_in_the_proposals_thread(alert):
    client, proposal, _ = alert

    tap(client, proposal["id"])

    thread = db.get_proposal_by_id(proposal["id"])["thread_id"]
    detail = client.get(f"/threads/{thread}").json()
    assert detail["messages"][-1]["message"]["type"] == "result"
    assert detail["proposal_states"][proposal["id"]] == "approved"


@pytest.mark.parametrize("data", ["approve:prop_missing", "delete:x", "approve", ""])
def test_unknown_buttons_do_nothing(alert, data):
    client, proposal, n8n = alert
    bot = telegram._transport.handler.__self__

    update = {"callback_query": {"id": "c", "data": data, "message": {"chat": {"id": int(ALICE_CHAT)}}}}
    assert post(client, update).status_code == 200

    assert status(proposal) == "open"
    assert n8n.writes() == []
    assert bot.of("answerCallbackQuery")


# Text replies


@pytest.mark.parametrize("text", ["yes, fix it", "approve", "go ahead"])
def test_reply_approves(alert, text):
    client, proposal, n8n = alert

    reply(client, text)

    assert status(proposal) == "approved"
    assert len(n8n.puts) == 1


@pytest.mark.parametrize("text", ["no", "reject it", "No thanks"])
def test_reply_rejects(alert, text):
    client, proposal, n8n = alert

    reply(client, text)

    assert status(proposal) == "rejected"
    assert n8n.writes() == []


@pytest.mark.parametrize("text", ["no idea why this failed", "yes but what does it change?", "maybe"])
def test_unclear_reply_does_nothing_and_says_so(alert, text):
    client, proposal, n8n = alert
    bot = telegram._transport.handler.__self__

    reply(client, text)

    assert status(proposal) == "open"
    assert n8n.writes() == []
    assert "approve" in bot.texts()[0]


def test_message_that_isnt_a_reply_is_ignored(alert):
    client, proposal, n8n = alert
    bot = telegram._transport.handler.__self__

    reply(client, "yes, fix it", reply_to=None)

    assert status(proposal) == "open"
    assert n8n.writes() == []
    assert bot.calls == []


def test_reply_to_a_different_message_is_ignored(alert):
    client, proposal, n8n = alert
    bot = telegram._transport.handler.__self__

    reply(client, "yes, fix it", reply_to=ALERT_ID + 1)

    assert status(proposal) == "open"
    assert bot.calls == []


def test_reply_without_text_is_ignored(alert):
    client, proposal, _ = alert
    message = {"chat": {"id": int(ALICE_CHAT)}, "reply_to_message": {"message_id": ALERT_ID}, "photo": []}

    assert post(client, {"message": message}).status_code == 200

    assert status(proposal) == "open"


def test_approve_reply_on_a_manual_fix_doesnt_apply_anything(world, bot):  # noqa: F811
    alice, _, alice_n8n, _ = world
    connect_chat(alice, "alice-key", ALICE_CHAT)
    run = alice_n8n.executions["4"]["data"]["resultData"]
    run["runData"]["Normalize Lead"][0]["error"]["message"] = "The credentials have expired"
    token = db.get_user_by_email(ALICE["email"])["webhook_token"]
    alice.post(f"/webhook/n8n-error/{token}", json=WEBHOOK_BODY)
    bot.calls.clear()

    reply(alice, "approve")

    user_id = db.get_user_by_email(ALICE["email"])["id"]
    assert db.list_open_proposals(user_id)[0]["kind"] == "manual"
    assert alice_n8n.writes() == []
    assert "needs your action" in bot.texts()[0]


# Chat ownership


def test_another_chat_cant_tap_a_button(alert):
    client, proposal, n8n = alert
    bot = telegram._transport.handler.__self__

    tap(client, proposal["id"], chat=BOB_CHAT)

    assert status(proposal) == "open"
    assert n8n.writes() == []
    assert bot.of("sendMessage") == []
    assert "can't decide" in bot.of("answerCallbackQuery")[0]["text"]


def test_another_chat_cant_reply(alert):
    client, proposal, n8n = alert
    bot = telegram._transport.handler.__self__

    reply(client, "approve", chat=BOB_CHAT)

    assert status(proposal) == "open"
    assert n8n.writes() == []
    assert bot.calls == []


def test_changing_the_saved_chat_revokes_the_old_one(alert):
    client, proposal, n8n = alert
    connect_chat(client, "alice-key", "3003")

    tap(client, proposal["id"], chat=ALICE_CHAT)

    assert status(proposal) == "open"
    assert n8n.writes() == []


def test_owner_with_no_saved_chat_cant_be_decided_from_telegram(alert):
    client, proposal, n8n = alert
    client.post("/connect", json={"n8n_url": "http://n8n:5678", "api_key": "alice-key"})

    tap(client, proposal["id"])

    assert status(proposal) == "open"
    assert n8n.writes() == []


# Polling


@pytest.fixture
def polling(bot, monkeypatch):
    monkeypatch.setenv("PUBLIC_API_URL", "https://app.example.com/api")
    monkeypatch.setattr(telegram_bot, "RETRY_SECONDS", 0.01)
    monkeypatch.setattr(telegram_bot, "MAX_RETRY_SECONDS", 0.01)
    get_settings.cache_clear()
    return bot


def wait_until(condition):
    deadline = time.monotonic() + 5
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def tap_update(update_id, proposal_id, action="approve", chat=ALICE_CHAT):
    return {
        "update_id": update_id,
        "callback_query": {
            "id": "cb1",
            "data": f"{action}:{proposal_id}",
            "message": {"message_id": ALERT_ID, "chat": {"id": int(chat)}},
        },
    }


def reply_update(update_id, text, chat=ALICE_CHAT):
    return {
        "update_id": update_id,
        "message": {
            "message_id": 9,
            "chat": {"id": int(chat)},
            "text": text,
            "reply_to_message": {"message_id": ALERT_ID},
        },
    }


def test_startup_clears_the_webhook_then_polls_without_a_secret(polling, monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "")
    get_settings.cache_clear()

    with TestClient(app):
        wait_until(lambda: polling.of("getUpdates"))

    methods = [name for name, _ in polling.calls]
    assert methods[0] == "deleteWebhook"
    assert "setWebhook" not in methods
    assert polling.of("deleteWebhook") == [{"drop_pending_updates": True}]
    assert polling.of("getUpdates")[0] == {
        "timeout": telegram.LONG_POLL_SECONDS,
        "allowed_updates": ["message", "callback_query"],
    }


@pytest.mark.parametrize(
    ("token", "public"),
    [("", "https://app.example.com/api"), (TOKEN, ""), (TOKEN, "http://localhost:8000")],
    ids=["no-token", "no-public-url", "not-https"],
)
def test_startup_doesnt_touch_telegram_when_not_set_up(polling, monkeypatch, token, public):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    monkeypatch.setenv("PUBLIC_API_URL", public)
    get_settings.cache_clear()

    with TestClient(app):
        time.sleep(0.05)

    assert polling.calls == []


def test_polled_button_applies_the_fix_and_reports_it(alert, polling):
    _, proposal, n8n = alert
    polling.updates.append(tap_update(70, proposal["id"]))

    with TestClient(app):
        wait_until(lambda: polling.of("editMessageReplyMarkup"))

    assert status(proposal) == "approved"
    assert len(n8n.puts) == 1
    assert "Recovered: 1" in polling.texts()[0]


def test_polled_reply_uses_the_same_decision_parser(alert, polling):
    _, proposal, n8n = alert
    polling.updates.append(reply_update(70, "no idea why this failed"))
    polling.updates.append(reply_update(71, "no"))

    with TestClient(app):
        wait_until(lambda: status(proposal) == "rejected")

    assert n8n.writes() == []


def test_polled_updates_from_another_chat_are_refused(alert, polling):
    _, proposal, n8n = alert
    polling.updates.append(tap_update(70, proposal["id"], chat=BOB_CHAT))
    polling.updates.append(reply_update(71, "approve", chat=BOB_CHAT))

    with TestClient(app):
        wait_until(lambda: polling.of("answerCallbackQuery"))
        time.sleep(0.05)

    assert status(proposal) == "open"
    assert n8n.writes() == []
    assert polling.of("sendMessage") == []


def test_polling_moves_past_each_update(polling):
    polling.updates.append({"update_id": 70})
    polling.updates.append({"update_id": 71})

    with TestClient(app):
        wait_until(lambda: any(call.get("offset") == 72 for call in polling.of("getUpdates")))

    assert [call.get("offset") for call in polling.of("getUpdates")][:3] == [None, 71, 72]


def test_polling_survives_failures_without_leaking_the_token(polling, caplog):
    polling.failing = {"deleteWebhook", "getUpdates"}

    with caplog.at_level(logging.INFO), TestClient(app) as client:
        wait_until(lambda: len(polling.of("deleteWebhook")) >= 2)
        assert polling.of("getUpdates") == []

        polling.failing = {"getUpdates"}
        wait_until(lambda: len(polling.of("getUpdates")) >= 2)
        assert client.get("/health").status_code == 200

        polling.failing = set()
        polling.updates.append({"update_id": 70})
        wait_until(lambda: any(call.get("offset") == 71 for call in polling.of("getUpdates")))

    assert "getUpdates failed" in caplog.text
    assert "bot-token-value" not in caplog.text


def run_poller(process, body):
    """Runs the poller against the fake Telegram for as long as body takes."""

    async def main():
        poller = asyncio.create_task(telegram_bot.poll_updates(process))
        await body()
        poller.cancel()
        await asyncio.gather(poller, return_exceptions=True)

    asyncio.run(main())


async def wait_for_list(items):
    while not items:
        await asyncio.sleep(0.01)


def test_a_slow_repair_doesnt_hold_up_the_next_update(polling):
    polling.updates.extend([{"update_id": 1}, {"update_id": 2}])
    release = []
    finished = []

    async def process(update):
        if update["update_id"] == 1:
            await wait_for_list(release)
            finished.append(1)
        else:
            release.append(True)

    async def body():
        await asyncio.wait_for(wait_for_list(finished), 5)

    run_poller(process, body)

    assert finished == [1]


def test_shutdown_lets_a_running_repair_finish(polling):
    polling.updates.append({"update_id": 1})
    started = []
    finished = []

    async def process(update):
        started.append(update)
        await asyncio.sleep(0.05)
        finished.append(update)

    async def body():
        await asyncio.wait_for(wait_for_list(started), 5)

    run_poller(process, body)

    assert finished


def test_a_malformed_update_doesnt_stop_polling(polling):
    polling.updates.extend([{"no_update_id": True}, {"update_id": 5}])
    seen = []

    async def process(update):
        seen.append(update)

    async def body():
        await asyncio.wait_for(wait_for_list(seen), 5)

    run_poller(process, body)

    assert seen == [{"update_id": 5}]
