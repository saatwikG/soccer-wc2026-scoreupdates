import os
import json
import logging
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("fifabot")

class Settings:
    def __init__(self):
        self.APP_CONFIG = {}
        self.BOT_TOKEN = ""
        self.CHAT_ID = ""
        self.GEMINI_API_KEY = ""

    def load(self):
        """Loads configuration and secrets securely at boot."""
        try:
            with open("config.json", "r", encoding="utf-8") as f:
                self.APP_CONFIG = json.load(f)
        except Exception as e:
            logger.critical(f"FATAL: Could not load config.json: {e}")
            exit(1)

        KEY_VAULT_URL = os.environ.get("KEY_VAULT_URL")
        if KEY_VAULT_URL:
            try:
                logger.info("Initializing Azure Credentials and fetching secrets...")
                credential = DefaultAzureCredential()
                client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)
                self.BOT_TOKEN = client.get_secret("FifaBotToken").value
                self.CHAT_ID = client.get_secret("FifaBotChatId").value
                self.GEMINI_API_KEY = client.get_secret("googlegenaikey").value
                logger.info("Azure secrets fetched successfully.")
            except Exception as e:
                logger.error(f"Failed to fetch secrets from Azure Key Vault: {e}")

# Global singleton to inject into other modules
settings = Settings()