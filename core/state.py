import os
import json
import logging
from core.config import settings

logger = logging.getLogger("fifabot")

class StateManager:
    def __init__(self):
        self.saved_match_state = {}
        self.team_names_memory = {}
        self.last_notified = {}
        self.seen_commentaries = {}
        self.update_offset = None
        self.subscribers = set()

    def load_subscribers(self):
        subs = {str(settings.CHAT_ID)} if settings.CHAT_ID else set()
        file_path = settings.APP_CONFIG.get("files", {}).get("subscribers", "subscribers.json")
        
        if os.path.exists(file_path):
            try:
                with open(file_path, "r") as f:
                    saved_subs = json.load(f)
                    subs.update(saved_subs)
                    logger.info(f"Loaded {len(subs)} subscribers from disk.")
            except Exception as e:
                logger.error(f"Error loading subscribers file: {e}")
        self.subscribers = subs

    def save_subscriber(self, new_id):
        if str(new_id) not in self.subscribers:
            self.subscribers.add(str(new_id))
            file_path = settings.APP_CONFIG.get("files", {}).get("subscribers", "subscribers.json")
            try:
                with open(file_path, "w") as f:
                    json.dump(list(self.subscribers), f)
                logger.info(f"💾 New user {new_id} saved to disk.")
            except Exception as e:
                logger.error(f"Error saving to subscribers file: {e}")

# Global state manager
state = StateManager()