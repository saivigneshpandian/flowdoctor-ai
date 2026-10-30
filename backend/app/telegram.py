import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

TIMEOUT = 10.0
MAX_FIELD_LENGTH = 500
WEBHOOK_PATH = "/telegram/webhook"
APPROVE_ACTION = "approve"
REJECT_ACTION = "reject"

# Tests replace this. Real calls leave it empty and use the network.
_transport: httpx.AsyncBaseTransport | None = None


async def _call(
    method: str,
    payload: dict[str, Any],
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any] | None:
    """Calls the Bot API and returns its result, or None if it failed."""
    token = get_settings().telegram_bot_token
    if not token:
        return None
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=transport or _transport) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return response.json().get("result") or {}
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


async def register_webhook() -> None:
    """Points Telegram at this server. Failures are logged and never stop the server starting."""
    settings = get_settings()
    base = settings.public_api_url.rstrip("/")
    # Only a public HTTPS address counts. A laptop sharing the bot token must not take over
    # the webhook from production, so an empty PUBLIC_API_URL means leave it alone.
    if not settings.telegram_bot_token or not settings.telegram_webhook_secret:
        logger.info("Telegram replies are off: TELEGRAM_BOT_TOKEN or TELEGRAM_WEBHOOK_SECRET is empty.")
        return
    if not base.startswith("https://"):
        logger.info("Telegram webhook not registered: PUBLIC_API_URL isn't a public HTTPS address.")
        return
    result = await _call(
        "setWebhook",
        {
            "url": f"{base}{WEBHOOK_PATH}",
            "secret_token": settings.telegram_webhook_secret,
            "allowed_updates": ["message", "callback_query"],
        },
    )
    if result is None:
        logger.warning("Telegram webhook registration failed, so buttons and replies won't work.")
    else:
        logger.info("Telegram webhook registered at %s%s", base, WEBHOOK_PATH)
