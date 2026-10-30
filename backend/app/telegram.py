import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

TIMEOUT = 10.0


async def send_message(
    text: str,
    chat_id: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool:
    settings = get_settings()
    # No fallback to TELEGRAM_CHAT_ID: that would send one user's alerts to someone else's chat.
    target = chat_id
    if not settings.telegram_bot_token or not target:
        logger.info("Telegram is not configured, so no alert was sent.")
        return False

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=transport) as client:
            response = await client.post(url, json={"chat_id": target, "text": text})
            response.raise_for_status()
    except httpx.HTTPError as exc:
        # The exception text contains the URL, and the URL contains the bot token.
        logger.warning("Telegram alert failed: %s", type(exc).__name__)
        return False
    return True
