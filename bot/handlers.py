import logging
import asyncio
from core.config import settings
from core.state import state
from services.telegram_service import _dispatch_to_telegram, send_inline_menu
from services.espn_service import get_upcoming_schedule, get_match_stats

logger = logging.getLogger("fifabot")

async def handle_user_commands(session, chat_id, text):
    command = text.strip().lower()
    is_new_user = str(chat_id) not in state.subscribers
    state.save_subscriber(chat_id)

    url_base = f"{settings.APP_CONFIG['api_urls']['telegram_base']}{settings.BOT_TOKEN}/sendMessage"

    if command == '/start':
        if is_new_user:
            welcome_msg = "🏆 <b>World Cup Tracker</b>\n\n✅ You are now subscribed to live alerts!\n\nChoose an option below:"
            await send_inline_menu(session, chat_id, custom_text=welcome_msg)
        else:
            welcome_back_msg = "🏆 <b>World Cup Tracker</b>\n\nWelcome back! You are already subscribed to live alerts.\n\nChoose an option below:"
            await send_inline_menu(session, chat_id, custom_text=welcome_back_msg)

    elif command in ('/help', '/menu'):
        await send_inline_menu(session, chat_id)

    elif command == '/score':
        live_matches = [mid for mid, st in state.saved_match_state.items() if st.split('-')[-1] == 'in']

        if not live_matches:
            fallback_msg = "⚠️ There are no live World Cup matches playing right now.\n\n"
            upcoming_two = await get_upcoming_schedule(session, limit=2)
            await _dispatch_to_telegram(session, url_base, chat_id, fallback_msg + upcoming_two)
            return

        score_msg = "🏆 <b>LIVE SCORES</b>\n\n"
        for match_id, st in state.saved_match_state.items():
            parts = st.split('-')
            if parts[-1] == 'in':
                score1, score2 = parts[0], parts[1]
                name1, name2 = state.team_names_memory.get(match_id, ("Team 1", "Team 2"))
                score_msg += f"⚽ {name1} {score1} - {score2} {name2}\n"

        await _dispatch_to_telegram(session, url_base, chat_id, score_msg)

    elif command == '/livestats':
        live_matches = [mid for mid, st in state.saved_match_state.items() if st.split('-')[-1] == 'in']

        if not live_matches:
            await _dispatch_to_telegram(session, url_base, chat_id, "⚠️ No live matches currently in progress.")
            return

        for match_id in live_matches:
            stats_text = await get_match_stats(session, match_id)
            if stats_text:
                await _dispatch_to_telegram(session, url_base, chat_id, stats_text)
            else:
                await _dispatch_to_telegram(session, url_base, chat_id, "⚠️ Could not load stats for a current match.")

    elif command == '/schedule':
        schedule_text = await get_upcoming_schedule(session)
        await _dispatch_to_telegram(session, url_base, chat_id, schedule_text)

    elif command == '/help':
        help_text = (
            "🤖 <b>Available Commands:</b>\n"
            "/start - Subscribe to bot updates\n"
            "/score - Get live status & commentary of current matches\n"
            "/livestats - View detailed data & stats for live matches\n"
            "/schedule - View games for the next 2 days\n"
            "/help - Show this menu"
        )
        await _dispatch_to_telegram(session, url_base, chat_id, help_text)

    else:
        warning_msg = "⚠️ I only understand specific commands. Please use the Menu button or type /help to see my available commands!"
        await _dispatch_to_telegram(session, url_base, chat_id, warning_msg)

async def check_telegram_updates(session):
    url = f"{settings.APP_CONFIG['api_urls']['telegram_base']}{settings.BOT_TOKEN}/getUpdates"

    params = {"timeout": 1}
    if state.update_offset is not None:
        params["offset"] = state.update_offset

    try:
        async with session.get(url, params=params, timeout=settings.APP_CONFIG["settings"]["network_timeout"]) as response:
            if response.status == 200:
                data = await response.json()
                for result in data.get("result", []):
                    state.update_offset = result["update_id"] + 1

                    if "message" in result and "text" in result["message"]:
                        chat_id = result["message"]["chat"]["id"]
                        text = result["message"]["text"]
                        await handle_user_commands(session, chat_id, text)

                    elif "callback_query" in result:
                        chat_id = result["callback_query"]["message"]["chat"]["id"]
                        button_data = result["callback_query"]["data"]
                        callback_id = result["callback_query"]["id"]

                        try:
                            cb_url = f"{settings.APP_CONFIG['api_urls']['telegram_base']}{settings.BOT_TOKEN}/answerCallbackQuery"
                            async with session.post(cb_url, json={"callback_query_id": callback_id}, timeout=5) as cb_response:
                                cb_response.raise_for_status()
                        except Exception as e:
                            logger.debug(f"Failed to answer callback query: {e}")

                        await handle_user_commands(session, chat_id, button_data)
    except asyncio.TimeoutError:
        pass 
    except Exception as e:
        logger.error(f"Error checking Telegram updates: {e}")