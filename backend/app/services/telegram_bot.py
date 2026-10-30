"""Decisions made from Telegram: inline buttons and text replies to an alert."""

import asyncio
import logging
from typing import Any, Awaitable, Callable

from app import auth, db, telegram
from app.agent.graph import parse_decision
from app.n8n.client import N8nClient
from app.schemas import RepairResult
from app.services import repair

logger = logging.getLogger(__name__)

RETRY_SECONDS = 5.0
MAX_RETRY_SECONDS = 60.0
# Below Docker's 10 seconds before it kills a stopping container.
SHUTDOWN_GRACE_SECONDS = 8.0


def _authorized_chat(proposal: dict[str, Any], chat_id: str) -> dict[str, Any] | None:
    """The owner's connection, only if this chat is the one they saved right now."""
    connection = auth.load_connection(proposal["user_id"])
    if connection and connection.get("telegram_chat_id") == chat_id:
        return connection
    logger.warning("Ignored a Telegram decision from a chat that doesn't own proposal %s", proposal["id"])
    return None


def _format_result(result: RepairResult) -> str:
    return (
        f"{result.message}\n"
        f"Recovered: {result.recovered_count}\n"
        f"Verified: {'yes' if result.verified else 'no'}\n"
        f"Execution status: {result.execution_status}"
    )


async def poll_updates(process: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
    """Reads updates from Telegram until cancelled. Each runs in its own task, so one slow
    repair doesn't hold up other users or leave their buttons spinning."""
    in_flight: set[asyncio.Task[None]] = set()
    offset: int | None = None
    webhook_cleared = False
    delay = RETRY_SECONDS
    try:
        while True:
            try:
                # getUpdates is refused while a webhook is set, so this has to succeed first.
                webhook_cleared = webhook_cleared or await telegram.delete_webhook()
                updates = await telegram.get_updates(offset) if webhook_cleared else None
                if updates is not None:
                    delay = RETRY_SECONDS
                    for update in updates:
                        # Moving past an update tells Telegram not to send it again.
                        offset = update["update_id"] + 1
                        task = asyncio.create_task(process(update))
                        in_flight.add(task)
                        task.add_done_callback(in_flight.discard)
                    continue
            except Exception as exc:
                logger.error("Telegram polling hit an error: %s", type(exc).__name__)
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_RETRY_SECONDS)
    finally:
        # Never cancel these: a repair stopped halfway could leave a workflow half changed.
        if in_flight:
            await asyncio.wait(in_flight, timeout=SHUTDOWN_GRACE_SECONDS)


async def handle_update(update: dict[str, Any], build_client: Callable[[str, str], N8nClient]) -> None:
    """Handles one Telegram update. Anything that isn't a decision on an alert is ignored."""
    if isinstance(update.get("callback_query"), dict):
        await _handle_button(update["callback_query"], build_client)
    elif isinstance(update.get("message"), dict):
        await _handle_reply(update["message"], build_client)


async def _handle_button(callback: dict[str, Any], build_client: Callable[[str, str], N8nClient]) -> None:
    callback_id = str(callback.get("id", ""))
    message = callback.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id", ""))
    action, _, proposal_id = str(callback.get("data", "")).partition(":")

    proposal = db.get_proposal_by_id(proposal_id) if proposal_id else None
    if action not in (telegram.APPROVE_ACTION, telegram.REJECT_ACTION) or proposal is None:
        await telegram.answer_callback(callback_id, "This fix is no longer available.")
        return
    connection = _authorized_chat(proposal, chat_id)
    if connection is None:
        await telegram.answer_callback(callback_id, "This chat can't decide this fix.")
        return

    # Telegram shows a spinner on the button until this is answered, and a repair can take a while.
    working = "Applying the fix…" if action == telegram.APPROVE_ACTION else "Rejecting the fix…"
    await telegram.answer_callback(callback_id, working)
    await _decide(proposal, action, connection, chat_id, build_client)


async def _handle_reply(message: dict[str, Any], build_client: Callable[[str, str], N8nClient]) -> None:
    replied_to = (message.get("reply_to_message") or {}).get("message_id")
    text = message.get("text")
    chat_id = str((message.get("chat") or {}).get("id", ""))
    # Only a reply to an alert counts, so ordinary messages in the chat can never decide a fix.
    if not replied_to or not isinstance(text, str) or not chat_id:
        return
    proposal = db.get_proposal_by_alert(chat_id, int(replied_to))
    if proposal is None:
        return
    connection = _authorized_chat(proposal, chat_id)
    if connection is None:
        return

    decision = parse_decision(text)
    if decision is None:
        await telegram.send_message('Reply "approve" or "reject", or use the buttons.', chat_id)
    elif decision == "done":
        await telegram.send_message("Open FlowDoctor to confirm you've finished the steps.", chat_id)
    else:
        await _decide(proposal, decision, connection, chat_id, build_client)


async def _decide(
    proposal: dict[str, Any],
    decision: str,
    connection: dict[str, Any],
    chat_id: str,
    build_client: Callable[[str, str], N8nClient],
) -> None:
    proposal_id = proposal["id"]
    user_id = proposal["user_id"]

    if decision == telegram.APPROVE_ACTION and proposal["kind"] != "patch":
        await telegram.send_message(
            "This one needs your action rather than an approval. Open FlowDoctor for the steps.", chat_id
        )
        return

    try:
        if decision == telegram.APPROVE_ACTION:
            client = build_client(connection["n8n_url"], connection["api_key"])
            try:
                result = await repair.approve(proposal_id, user_id, client)
            finally:
                await client.aclose()
            repair.record_result(proposal_id, user_id, result)
            reply = _format_result(result)
        else:
            repair.reject(proposal_id, user_id)
            reply = "Fix rejected. Nothing was changed."
    except repair.ProposalStateError as exc:
        reply = str(exc)
    except Exception:
        # The outcome is unknown, so say that rather than guess.
        logger.exception("Telegram decision crashed for proposal %s", proposal_id)
        reply = "Something went wrong and the outcome isn't confirmed. Check FlowDoctor before trying again."

    await telegram.send_message(reply, chat_id)

    # A fix can stay open, for example if n8n couldn't be reached and nothing changed.
    # Its buttons stay so it can be tried again.
    current = db.get_proposal_by_id(proposal_id)
    if current and current["status"] != "open" and current["telegram_message_id"]:
        await telegram.remove_buttons(current["telegram_chat_id"], current["telegram_message_id"])
