import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

TIMEOUT = 10.0
MAX_FIELD_LENGTH = 500
# Telegram holds getUpdates open this long when nothing is waiting.
LONG_POLL_SECONDS = 30
APPROVE_ACTION = "approve"
REJECT_ACTION = "reject"

# Tests replace this. Real calls leave it empty and use the network.
_transport: httpx.AsyncBaseTransport | None = None


async def _call(
    method: str,
    payload: dict[str, Any],
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = TIMEOUT,
) -> Any:
    """Calls the Bot API and returns its result, or None if it failed."""
    token = get_settings().telegram_bot_token
    if not token:
        return None
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport or _transport) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            result = response.json().get("result")
            return {} if result is None else result
    except (httpx.HTTPError, ValueError) as exc:
        # The exception text contains the URL, and the URL contains the bot token.
        logger.warning("Telegram %s failed: %s", method, type(exc).__name__)
        return None


async def send_message(
    text: str,
    chat_id: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool:
    # No fallback to TELEGRAM_CHAT_ID: that would send one user's alerts to someone else's chat.
    if not chat_id or not get_settings().telegram_bot_token:
        logger.info("Telegram is not configured, so no alert was sent.")
        return False
    return await _call("sendMessage", {"chat_id": chat_id, "text": text}, transport) is not None


def _shorten(text: str) -> str:
    return text if len(text) <= MAX_FIELD_LENGTH else f"{text[:MAX_FIELD_LENGTH]}…"


def format_alert(
    workflow_name: str,
    failed_node: str,
    root_cause: str,
    explanation: str | None,
) -> str:
    lines = [f"{workflow_name} failed", f"Node: {failed_node}", f"Cause: {_shorten(root_cause)}"]
    if explanation:
        # This is all the reader has to go on when approving from here.
        lines.append(f"Proposed fix: {_shorten(explanation)}")
        lines.append("Tap a button or reply to this message. The full diff is in FlowDoctor.")
    else:
        lines.append("This needs your action. Open FlowDoctor for the steps.")
    return "\n".join(lines)


async def send_alert(text: str, chat_id: str | None, proposal_id: str | None) -> int | None:
    """Sends an alert, with decision buttons when a fix is waiting. Returns its message id."""
    if not chat_id or not get_settings().telegram_bot_token:
        logger.info("Telegram is not configured, so no alert was sent.")
        return None
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if proposal_id:
        payload["reply_markup"] = {
            "inline_keyboard": [
                [
                    {"text": "Approve fix", "callback_data": f"{APPROVE_ACTION}:{proposal_id}"},
                    {"text": "Reject", "callback_data": f"{REJECT_ACTION}:{proposal_id}"},
                ]
            ]
        }
    result = await _call("sendMessage", payload)
    return result.get("message_id") if result else None


async def answer_callback(callback_id: str, text: str) -> None:
    await _call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


async def remove_buttons(chat_id: str, message_id: int) -> None:
    await _call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id})


def polling_enabled() -> bool:
    """Only the deployed server polls: Telegram allows one reader per bot, and a laptop sharing
    the token would take updates away from production and answer them from the wrong database."""
    settings = get_settings()
    if not settings.telegram_bot_token:
        logger.info("Telegram replies are off: TELEGRAM_BOT_TOKEN is empty.")
        return False
    if not settings.public_api_url.startswith("https://"):
        logger.info("Telegram polling is off: PUBLIC_API_URL isn't a public HTTPS address.")
        return False
    return True


async def delete_webhook() -> bool:
    # Dropped updates are deliberate: a tap left waiting while the server was down shouldn't
    # edit a workflow hours later. The buttons are still there to tap again.
    return await _call("deleteWebhook", {"drop_pending_updates": True}) is not None


async def get_updates(offset: int | None) -> list[dict[str, Any]] | None:
    """Waits for new updates. Returns None if the call failed."""
    payload: dict[str, Any] = {
        "timeout": LONG_POLL_SECONDS,
        "allowed_updates": ["message", "callback_query"],
    }
    if offset is not None:
        payload["offset"] = offset
    return await _call("getUpdates", payload, timeout=LONG_POLL_SECONDS + TIMEOUT)
