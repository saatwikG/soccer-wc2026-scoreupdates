import logging
import asyncio
from core.config import settings
from core.state import state

logger = logging.getLogger("fifabot")

async def _dispatch_to_telegram(session, url_base, target_chat_id, text):
    network_timeout = settings.APP_CONFIG["settings"]["network_timeout"]
    payload = {
        "chat_id": target_chat_id,
        "text": text.strip(),
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True}
    }

    try:
        async with session.post(url_base, json=payload, timeout=network_timeout) as response:
            if response.status == 400:
                logger.warning(f"Telegram rejected HTML formatting for {target_chat_id}. Retrying as plain text...")
                safe_payload = payload.copy()
                safe_payload.pop("parse_mode", None)
                async with session.post(url_base, json=safe_payload, timeout=network_timeout) as safe_response:
                    safe_response.raise_for_status()
            else:
                response.raise_for_status()
                logger.debug(f"Message successfully delivered to {target_chat_id}")

    except asyncio.TimeoutError:
        logger.error(f"Timeout while sending message to {target_chat_id}")
    except Exception as e:
        logger.error(f"Network error while sending message to {target_chat_id}: {e}")

async def send_telegram_message(session, message):
    url_base = f"{settings.APP_CONFIG['api_urls']['telegram_base']}{settings.BOT_TOKEN}/sendMessage"
    max_length = 4000

    for sub_id in state.subscribers:
        temp_msg = message

        if len(temp_msg) > max_length:
            split_index = temp_msg.rfind('\n', 0, max_length)
            if split_index == -1: split_index = max_length
            chunk = temp_msg[:split_index]
            await _dispatch_to_telegram(session, url_base, sub_id, chunk)
            temp_msg = temp_msg[split_index:].lstrip('\n')

            while len(temp_msg) > 4000:
                split_index = temp_msg.rfind('\n', 0, 4000)
                if split_index == -1: split_index = 4000
                chunk = temp_msg[:split_index]
                await _dispatch_to_telegram(session, url_base, sub_id, chunk)
                temp_msg = temp_msg[split_index:].lstrip('\n')

            if temp_msg.strip():
                await _dispatch_to_telegram(session, url_base, sub_id, temp_msg)
        else:
            if temp_msg.strip():
                await _dispatch_to_telegram(session, url_base, sub_id, temp_msg)

async def send_inline_menu(session, chat_id, custom_text=None):
    url = f"{settings.APP_CONFIG['api_urls']['telegram_base']}{settings.BOT_TOKEN}/sendMessage"
    text = custom_text if custom_text else "🏆 <b>World Cup Tracker</b>\n\nSelect an option below:"

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "⚽ Live Scores", "callback_data": "/score"}, {"text": "📊 Live Stats", "callback_data": "/livestats"}],
                [{"text": "📅 Schedule", "callback_data": "/schedule"}, {"text": "❓ Help", "callback_data": "/help"}]
            ]
        }
    }

    try:
        async with session.post(url, json=payload, timeout=settings.APP_CONFIG["settings"]["network_timeout"]) as response:
            response.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to send inline menu to {chat_id}: {e}")