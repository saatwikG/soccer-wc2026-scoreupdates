import json
import os
import re
import logging
import asyncio
from google import genai
from google.genai import types
from core.config import settings

logger = logging.getLogger("fifabot")
gemini_client = None

def init_gemini():
    global gemini_client
    if settings.GEMINI_API_KEY:
        gemini_client = genai.Client(api_key=settings.GEMINI_API_KEY)
        logger.info("Gemini client initialized successfully.")

def load_prompts():
    file_path = settings.APP_CONFIG.get("files", {}).get("prompts", "prompts.json")
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading {file_path}: {e}")
    return {}

async def summarize_events_with_gemini(event_type, raw_data):
    if not raw_data or not gemini_client:
        return ""

    max_retries = settings.APP_CONFIG["gemini"]["max_retries"]
    prompt_registry = load_prompts()
    system_instruction = prompt_registry.get(
        event_type,
        "You are a helpful sports assistant. Summarize this match data accurately."
    )

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=settings.APP_CONFIG["gemini"]["temperature"],
    )

    for attempt in range(max_retries):
        try:
            response = await gemini_client.aio.models.generate_content(
                model=settings.APP_CONFIG["gemini"]["model"],
                contents=str(raw_data),
                config=config
            )

            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                in_tokens = response.usage_metadata.prompt_token_count
                out_tokens = response.usage_metadata.candidates_token_count
                logger.info(f"Gemini Tokens Used [{event_type}] - In: {in_tokens} | Out: {out_tokens}")

            text = response.text.strip()
            text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            text = re.sub(r'^[\*\-]\s+', '• ', text, flags=re.MULTILINE)
            text = re.sub(r'^#+\s+(.*)', r'<b>\1</b>', text, flags=re.MULTILINE)
            text = re.sub(r'\*\*\*(.*?)\*\*\*', r'<b><i>\1</i></b>', text)
            text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
            text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)

            return text

        except Exception as e:
            logger.warning(f"Gemini API attempt {attempt + 1} failed: {e}")
            if attempt == max_retries - 1:
                logger.error(f"Gemini API completely failed after {max_retries} attempts.")
                return ""
            await asyncio.sleep(2)