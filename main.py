import asyncio
import aiohttp
import logging
from core.config import settings
from core.state import state
from services.gemini_service import init_gemini
from bot.tracker import track_world_cup_scores
from bot.handlers import check_telegram_updates

logger = logging.getLogger("fifabot")

async def async_telegram_listener(session):
    """Continuously polls Telegram for new commands and button clicks."""
    logger.info("Started Telegram Async Interactive Listener.")
    listener_sleep = settings.APP_CONFIG["settings"]["listener_sleep_interval"]
    while True:
        await check_telegram_updates(session)
        await asyncio.sleep(listener_sleep)

async def async_background_tracker(session):
    """Runs the heavy API tracking and Gemini logic continuously."""
    logger.info("Started ESPN Background Async Tracker.")
    tracker_sleep = settings.APP_CONFIG["settings"]["tracker_sleep_interval"]
    while True:
        await track_world_cup_scores(session)
        await asyncio.sleep(tracker_sleep)

async def main():
    # 1. Initialize core dependencies BEFORE accepting web requests
    logger.info("🚀 Pro World Cup Tracker Booting Up (Modular Mode)...")
    settings.load()
    state.load_subscribers()
    init_gemini()

    # 2. Establish a single AIOHTTP session for the application lifecycle
    async with aiohttp.ClientSession() as session:
        await asyncio.gather(
            async_telegram_listener(session),
            async_background_tracker(session)
        )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Tracker safely shut down via keyboard interrupt.")