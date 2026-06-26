import os
import json
import re
import html
import time
import logging
import asyncio
import aiohttp
from datetime import date, datetime, timedelta
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from google import genai
from google.genai import types
from zoneinfo import ZoneInfo

# ==========================================
# LOGGING CONFIGURATION
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("fifabot")

# ==========================================
# APP CONFIGURATION LOADER
# ==========================================
def load_app_config():
    """Loads external configurations to keep code clean and maintainable."""
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.critical(f"FATAL: Could not load config.json: {e}")
        exit(1)

APP_CONFIG = load_app_config()

# ==========================================
# AZURE KEY VAULT CONFIGURATION
# ==========================================
KEY_VAULT_URL = os.environ.get("KEY_VAULT_URL")
BOT_TOKEN_SECRET_NAME = "FifaBotToken"
CHAT_ID_SECRET_NAME = "FifaBotChatId"
GEMINI_API_KEY_SECRET_NAME = "googlegenaikey"

try:
    logger.info("Initializing Azure Credentials and fetching secrets...")
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)
    BOT_TOKEN = client.get_secret(BOT_TOKEN_SECRET_NAME).value
    CHAT_ID = client.get_secret(CHAT_ID_SECRET_NAME).value
    GEMINI_API_KEY = client.get_secret(GEMINI_API_KEY_SECRET_NAME).value
    
    # Configure the new Gemini client
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    logger.info("Azure secrets fetched and Gemini client initialized successfully.")
except Exception as e:
    logger.error(f"Failed to fetch secrets from Azure Key Vault: {e}")
    BOT_TOKEN = ""
    CHAT_ID = ""
    gemini_client = None

# ==========================================
# GLOBAL MEMORY DICTIONARIES
# ==========================================
saved_match_state = {}
team_names_memory = {}   
last_notified = {}       
seen_commentaries = {}   
update_offset = None     

# ==========================================
# FLAG ENGINE (OFFICIAL 2026 QUALIFIED TEAMS)
# ==========================================
TEAM_FLAGS = {
    # Hosts (3)
    "United States": "🇺🇸", "USA": "🇺🇸", "Canada": "🇨🇦", "Mexico": "🇲🇽",
    
    # UEFA / Europe (16)
    "Austria": "🇦🇹", "Belgium": "🇧🇪", "Bosnia and Herzegovina": "🇧🇦", 
    "Croatia": "🇭🇷", "Czechia": "🇨🇿", "Czech Republic": "🇨🇿", "England": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", 
    "France": "🇫🇷", "Germany": "🇩🇪", "Netherlands": "🇳🇱", "Norway": "🇳🇴", 
    "Portugal": "🇵🇹", "Scotland": "🏴󠁧󠁢󠁳󠁣󠁴󠁿", "Spain": "🇪🇸", "Sweden": "🇸🇪", 
    "Switzerland": "🇨🇭", "Turkey": "🇹🇷", "Türkiye": "🇹🇷",
    
    # CONMEBOL / South America (6)
    "Argentina": "🇦🇷", "Brazil": "🇧🇷", "Colombia": "🇨🇴", 
    "Ecuador": "🇪🇨", "Paraguay": "🇵🇾", "Uruguay": "🇺🇾",
    
    # CAF / Africa (10)
    "Algeria": "🇩🇿", "Cabo Verde": "🇨🇻", "Cape Verde": "🇨🇻", "Congo DR": "🇨🇩", "DR Congo": "🇨🇩", 
    "Côte d'Ivoire": "🇨🇮", "Ivory Coast": "🇨🇮", "Egypt": "🇪🇬", "Ghana": "🇬🇭", 
    "Morocco": "🇲🇦", "Senegal": "🇸🇳", "South Africa": "🇿🇦", "Tunisia": "🇹🇳",
    
    # AFC / Asia (9)
    "Australia": "🇦🇺", "Iran": "🇮🇷", "IR Iran": "🇮🇷", "Iraq": "🇮🇶", 
    "Japan": "🇯🇵", "Jordan": "🇯🇴", "South Korea": "🇰🇷", "Korea Republic": "🇰🇷", 
    "Qatar": "🇶🇦", "Saudi Arabia": "🇸🇦", "Uzbekistan": "🇺🇿",
    
    # CONCACAF / North & Central America (3 Non-Hosts)
    "Curaçao": "🇨🇼", "Haiti": "🇭🇹", "Panama": "🇵🇦",
    
    # OFC / Oceania (1)
    "New Zealand": "🇳🇿"
}

def get_flag(team_name):
    """Returns the flag emoji for a team, or a generic white flag if not found."""
    return TEAM_FLAGS.get(team_name, "🏳️")

# ==========================================
# PERSISTENT SUBSCRIBER STORAGE
# ==========================================
SUBSCRIBERS_FILE = APP_CONFIG["files"]["subscribers"]

def load_subscribers():
    """Loads subscribers from a local file, ensuring the Master ID is always included."""
    subs = {str(CHAT_ID)} if CHAT_ID else set()
    if os.path.exists(SUBSCRIBERS_FILE):
        try:
            with open(SUBSCRIBERS_FILE, "r") as f:
                saved_subs = json.load(f)
                subs.update(saved_subs)
                logger.info(f"Loaded {len(subs)} subscribers from disk.")
        except Exception as e:
            logger.error(f"Error loading subscribers file: {e}")
    return subs

def save_subscriber(new_id):
    """Saves a new subscriber to RAM and the local file."""
    if str(new_id) not in subscribers:
        subscribers.add(str(new_id))
        try:
            with open(SUBSCRIBERS_FILE, "w") as f:
                json.dump(list(subscribers), f)
            logger.info(f"💾 New user {new_id} saved to disk.")
        except Exception as e:
            logger.error(f"Error saving to subscribers file: {e}")

subscribers = load_subscribers()

# ==========================================
# ASYNC TELEGRAM DISPATCH HELPERS
# ==========================================

async def send_telegram_message(session, message, photo_url=None):
    """Broadcasts automated push updates to all subscribed users asynchronously."""
    url_base = f"{APP_CONFIG['api_urls']['telegram_base']}{BOT_TOKEN}/sendMessage"
    max_length = 1000 if photo_url else 4000 
    
    for sub_id in subscribers:
        temp_msg = message 
        
        if len(temp_msg) > max_length:
            split_index = temp_msg.rfind('\n', 0, max_length)
            if split_index == -1:
                split_index = max_length
            
            chunk = temp_msg[:split_index]
            await _dispatch_to_telegram(session, url_base, sub_id, chunk, photo_url)
            temp_msg = temp_msg[split_index:].lstrip('\n')
            
            while len(temp_msg) > 4000:
                split_index = temp_msg.rfind('\n', 0, 4000)
                if split_index == -1:
                    split_index = 4000
                chunk = temp_msg[:split_index]
                await _dispatch_to_telegram(session, url_base, sub_id, chunk, photo_url=None)
                temp_msg = temp_msg[split_index:].lstrip('\n')
                
            if temp_msg.strip():
                await _dispatch_to_telegram(session, url_base, sub_id, temp_msg, photo_url=None)
        else:
            if temp_msg.strip():
                await _dispatch_to_telegram(session, url_base, sub_id, temp_msg, photo_url)

async def _dispatch_to_telegram(session, url_base, target_chat_id, text, photo_url=None):
    """Helper function to execute async POST requests, safely swapping to sendPhoto if needed."""
    network_timeout = APP_CONFIG["settings"]["network_timeout"]
    
    if photo_url:
        method_url = url_base.replace("sendMessage", "sendPhoto")
        payload = {"chat_id": target_chat_id, "photo": photo_url, "caption": text.strip(), "parse_mode": "HTML"}
    else:
        method_url = url_base
        payload = {
            "chat_id": target_chat_id, 
            "text": text.strip(), 
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True}  
        }
        
    try:
        async with session.post(method_url, json=payload, timeout=network_timeout) as response:
            if response.status == 400:
                logger.warning(f"Telegram rejected HTML formatting for {target_chat_id}. Retrying as plain text...")
                
                safe_payload = payload.copy()
                if not photo_url:
                    safe_payload.pop("parse_mode", None)
                else:
                    safe_payload.pop("parse_mode", None)

                async with session.post(method_url, json=safe_payload, timeout=network_timeout) as safe_response:
                    safe_response.raise_for_status()
                    
            else:
                response.raise_for_status()
                logger.debug(f"Message successfully delivered to {target_chat_id}")
                
    except asyncio.TimeoutError:
        logger.error(f"Timeout while sending message to {target_chat_id}")
    except Exception as e:
        logger.error(f"Network error while sending message to {target_chat_id}: {e}")

# ==========================================
# ASYNC DATA & SCHEDULE HELPERS
# ==========================================

def get_team_stats(team_data, details):
    score = int(team_data.get('score', 0))
    team_id = team_data.get('team', {}).get('id')
    red_cards = 0
    yellow_cards = 0
    for detail in details:
        if detail.get('team', {}).get('id') == team_id:
            if detail.get('redCard', False): red_cards += 1
            if detail.get('yellowCard', False): yellow_cards += 1
    return score, red_cards, yellow_cards

async def fetch_recent_commentary(session, match_id):
    url = f"{APP_CONFIG['api_urls']['espn_summary']}?event={match_id}"
    try:
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=APP_CONFIG["settings"]["network_timeout"]) as response:
            response.raise_for_status()
            data = await response.json()
            commentary_data = data.get('commentary', [])
        
        if not commentary_data: return []
            
        is_first_run = match_id not in seen_commentaries
        if is_first_run: seen_commentaries[match_id] = set()
            
        new_events = []
        for play in commentary_data:
            time_data = play.get('time', {})
            minute = time_data.get('displayValue', '')
            added_time = time_data.get('addedTime', '')
            time_string = f"{minute}{added_time}'" if minute else "N/A"
            text = play.get('text', 'No text provided')
            event_str = f"[{time_string}] {text}"
            
            if event_str not in seen_commentaries[match_id]:
                seen_commentaries[match_id].add(event_str)
                if not is_first_run: new_events.insert(0, event_str)
        return new_events
    except Exception as e:
        logger.error(f"Error fetching commentary for match {match_id}: {e}")
        return []

async def fetch_full_match_commentary(session, match_id):
    url = f"{APP_CONFIG['api_urls']['espn_summary']}?event={match_id}"
    try:
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=APP_CONFIG["settings"]["network_timeout"]) as response:
            response.raise_for_status()
            data = await response.json()
            commentary_data = data.get('commentary', [])
            
        if not commentary_data: return []
        
        all_events = []
        for play in reversed(commentary_data):
            time_string = f"{play.get('time', {}).get('displayValue', '')}{play.get('time', {}).get('addedTime', '')}'"
            all_events.append(f"[{time_string}] {play.get('text', '')}")
        return all_events
    except Exception as e:
        logger.error(f"Error fetching full commentary for match {match_id}: {e}")
        return []

async def get_upcoming_schedule(session, limit=None):
    """Fetches World Cup matches asynchronously, optionally limiting to the next N games."""
    base_url = APP_CONFIG['api_urls']['espn_scoreboard']
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        todaysDate = date.today()  
        tomorrowDate = todaysDate + timedelta(days=2) 
        date_range = f"{todaysDate.strftime('%Y%m%d')}-{tomorrowDate.strftime('%Y%m%d')}"
        
        async with session.get(f"{base_url}?dates={date_range}", headers=headers, timeout=APP_CONFIG["settings"]["network_timeout"]) as response:
            response.raise_for_status()
            data = await response.json()
            events = data.get('events', [])
        
        upcoming_events = [e for e in events if e.get('status', {}).get('type', {}).get('state') == 'pre']
        
        if not upcoming_events: 
            return "No World Cup matches scheduled in the near future."
            
        if limit:
            upcoming_events = upcoming_events[:limit]
            schedule_msg = f"📅 <b>Next {limit} Upcoming Match{'es' if limit > 1 else ''}</b>\n\n"
        else:
            schedule_msg = "📅 <b>Upcoming Matches (Next 48 Hours)</b>\n\n"
        
        for event in upcoming_events:
            for competition in event.get('competitions', []):
                group_info = competition.get('altGameNote', 'World Cup Match')
                stadium = competition.get('venue', {}).get('fullName', 'Unknown')
                city = competition.get('venue', {}).get('address', {}).get('city', '')
                country = competition.get('venue', {}).get('address', {}).get('country', '')
                
                location_parts = [p for p in [stadium, city, country] if p]
                location_str = ", ".join(location_parts)
                
                competitors = competition.get('competitors', [])
                if len(competitors) >= 2:
                    raw_name1 = competitors[0].get('team', {}).get('name', 'Unknown')
                    display_name1 = f"{get_flag(raw_name1)} {raw_name1}"
                    
                    raw_name2 = competitors[1].get('team', {}).get('name', 'Unknown')
                    display_name2 = f"{raw_name2} {get_flag(raw_name2)}"
                    
                    matchup_str = f"{display_name1} vs {display_name2}"
                else:
                    matchup_str = event.get('name', 'Unknown Matchup')

                matchtime = datetime.fromisoformat(competition['date']).astimezone(ZoneInfo(APP_CONFIG["settings"]["timezone"]))
                
                hour = matchtime.strftime("%I").lstrip("0")   
                am_pm = matchtime.strftime("%p")
                tz_abbrev = matchtime.strftime("%Z")          
                month = matchtime.strftime("%B")              
                day = str(matchtime.day)                      
                year = matchtime.strftime("%Y")               
                
                readable_time = f"{hour}{am_pm} {tz_abbrev} on {month} {day}, {year}"
                
                schedule_msg += f"⚽ {matchup_str}\n🏆 {group_info}\n🏟️ {location_str}\n⏰ {readable_time}\n\n"
                
        return schedule_msg
        
    except Exception as e:
        logger.error(f"Error checking schedule: {e}")
        return f"⚠️ Error fetching schedule: {e}"

async def get_match_stats(session, match_id):
    """Fetches and formats a clean, mirrored stats table for Telegram."""
    url = f"{APP_CONFIG['api_urls']['espn_summary']}?event={match_id}"
    try:
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=APP_CONFIG["settings"]["network_timeout"]) as response:
            response.raise_for_status()
            data = await response.json()
        
        boxscore = data.get('boxscore', {}).get('teams', [])
        if len(boxscore) < 2:
            return None
            
        t1 = boxscore[0]
        t2 = boxscore[1]
        
        t1_name = t1.get('team', {}).get('name', 'Team 1')
        t2_name = t2.get('team', {}).get('name', 'Team 2')
        display_t1 = f"{get_flag(t1_name)} {t1_name}"
        display_t2 = f"{t2_name} {get_flag(t2_name)}"
        
        def extract_stat(stats_list, target_names, target_labels):
            for s in stats_list:
                if s.get('name') in target_names or s.get('label', '').lower() in target_labels:
                    return s.get('displayValue', '0')
            return "0"
            
        s1_data = t1.get('statistics', [])
        s2_data = t2.get('statistics', [])

        stat_blueprint = [
            ("📊", "Possession", ['possessionPct'], ['possession']),
            ("🎯", "Shots", ['totalShots', 'shotsSummary', 'shots'], ['shots']),
            ("🥅", "On Goal", ['shotsOnGoal', 'onGoal'], ['on goal']),
            ("👟", "Passes", ['totalPasses', 'passes'], ['passes']),
            ("⛳", "Corners", ['wonCorners', 'corners', 'cornerKicks'], ['corner kicks', 'corners']),
            ("🛑", "Fouls", ['foulsCommitted', 'fouls'], ['fouls']),
            ("🟨", "Yellows", ['yellowCards'], ['yellow cards']),
            ("🟥", "Reds", ['redCards'], ['red cards']),
            ("🧤", "Saves", ['saves'], ['saves']),
            ("🚩", "Offsides", ['totalOffsides', 'offsides'], ['offsides'])
        ]

        lines = [
            f"📊 <b>LIVE MATCH STATS</b>",
            f"{display_t1} vs {display_t2}",
            "━━━━━━━━━━━━━━━━━━━━"
        ]
        
        for emoji, label, names, labels in stat_blueprint:
            val1 = extract_stat(s1_data, names, labels)
            val2 = extract_stat(s2_data, names, labels)
            
            if label == "Possession" and "%" not in val1:
                val1 += "%"
                val2 += "%"
                
            lines.append(f"{val1}  {emoji} <b>{label}</b> {emoji}  {val2}")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Error fetching stats for {match_id}: {e}")
        return None

# ==========================================
# ASYNC GEMINI INTEGRATION
# ==========================================

PROMPTS_FILE = APP_CONFIG["files"]["prompts"]

def load_prompts():
    """Safely loads the latest prompts from the JSON file at runtime."""
    if os.path.exists(PROMPTS_FILE):
        try:
            with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading {PROMPTS_FILE}: {e}")
    return {}

async def summarize_events_with_gemini(event_type, raw_data):
    """Generates rich summaries asynchronously using prompts hot-loaded from an external JSON."""
    if not raw_data or not gemini_client:
        return "" 
        
    max_retries = APP_CONFIG["gemini"]["max_retries"]

    prompt_registry = load_prompts()
    system_instruction = prompt_registry.get(
        event_type, 
        "You are a helpful sports assistant. Summarize this match data accurately."
    )

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=APP_CONFIG["gemini"]["temperature"], 
    )

    for attempt in range(max_retries):
        try:
            # Native async SDK call pulling model directly from config
            response = await gemini_client.aio.models.generate_content(
                model=APP_CONFIG["gemini"]["model"], 
                contents=str(raw_data),
                config=config
            )
            
            # --- LOGGING TOKEN CONSUMPTION ---
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                in_tokens = response.usage_metadata.prompt_token_count
                out_tokens = response.usage_metadata.candidates_token_count
                logger.info(f"Gemini Tokens Used [{event_type}] - In: {in_tokens} | Out: {out_tokens}")
            
            text = response.text.strip()
            
            # 1. Secure the output for Telegram HTML parsing
            text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            
            # 2. Convert standard Markdown bullet points to safe Unicode bullets
            text = re.sub(r'^[\*\-]\s+', '• ', text, flags=re.MULTILINE)
            
            # 3. Convert Markdown Headers (###, ##, #) to Bold HTML
            text = re.sub(r'^#+\s+(.*)', r'<b>\1</b>', text, flags=re.MULTILINE)
            
            # 4. Convert Bold-Italic (***text***) to HTML
            text = re.sub(r'\*\*\*(.*?)\*\*\*', r'<b><i>\1</i></b>', text)
            
            # 5. Convert standard Bold (**text**) to HTML
            text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
            
            # 6. Convert standard Italic (*text*) to HTML
            text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
            
            return text
            
        except Exception as e:
            logger.warning(f"Gemini API attempt {attempt + 1} failed: {e}")
            if attempt == max_retries - 1:
                logger.error(f"Gemini API completely failed after {max_retries} attempts.")
                return ""
            await asyncio.sleep(2)

async def get_world_cup_standings(session):
    """Fetches, sorts, and formats the group standings asynchronously for Telegram."""
    url = APP_CONFIG["api_urls"]["espn_standings"]
    try:
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=APP_CONFIG["settings"]["network_timeout"]) as response:
            response.raise_for_status()
            data = await response.json()
            children = data.get('children', [])

        if not children:
            return "⚠️ Standings are currently unavailable."

        standings_msg = "🏆 <b>2026 WORLD Cup STANDINGS</b>\n\n"

        for group in children:
            group_name = group.get('name', 'Unknown Group')
            standings_msg += f"📊 <b>{group_name}</b>\n"

            entries = group.get('standings', {}).get('entries', [])

            # CRITICAL: Sort the entries by rank!
            sorted_entries = sorted(entries, key=lambda x: int(x.get('note', {}).get('rank', 99)))

            for detail in sorted_entries:
                team_name = detail.get('team', {}).get('displayName', 'Unknown')
                rank = detail.get('note', {}).get('rank', '-')
                desc = detail.get('note', {}).get('description', '')

                flag = get_flag(team_name)

                if "Advance" in desc:
                    status_icon = "🟢" 
                elif "Best 8" in desc:
                    status_icon = "🟡" 
                else:
                    status_icon = "🔴" 

                standings_msg += f"<code>{rank}.</code> {flag} <b>{team_name}</b> {status_icon}\n"

            standings_msg += "\n" 

        return standings_msg

    except Exception as e:
        logger.error(f"Error fetching standings: {e}")
        return "⚠️ Could not load tournament standings at this time."

# ==========================================
# BACKGROUND TRACKER (PUSH NOTIFICATIONS)
# ==========================================

async def track_world_cup_scores(session):
    # Fetch the high-level scoreboard to find active matches
    url = APP_CONFIG["api_urls"]["espn_scoreboard"]
    periodic_update_time = APP_CONFIG["settings"]["periodic_update_seconds"]
    
    try:
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=APP_CONFIG["settings"]["network_timeout"]) as response:
            response.raise_for_status()
            data = await response.json()
            
            # Loop through events to find matches that are currently LIVE or JUST STARTED
            for event in data.get('events', []):
                match_id = event['id']
                status_dict = event.get('status', {})
                status_type = status_dict.get('type', {}).get('name', '')
                
                # If the match is in progress, jump straight into the Summary API
                if status_type == "STATUS_IN_PROGRESS":
                    await process_live_match(session, match_id, periodic_update_time)
                    
    except Exception as e:
        logger.error(f"Error checking scoreboard: {e}")

async def process_live_match(session, match_id, sleep_interval):
    """Stateless function that relies entirely on the Summary API for match events"""
    
    # 1. Always use summary API for latest events [cite: 5]
    url = f"{APP_CONFIG['api_urls']['espn_summary']}?event={match_id}"
    try:
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=APP_CONFIG["settings"]["network_timeout"]) as response:
            response.raise_for_status()
            data = await response.json()
            
            commentary_data = data.get('commentary', [])
            
            # 2. Extract current match state and team stats directly from API payload
            boxscore = data.get('boxscore', {})
            teams = boxscore.get('teams', [])
            
            # Use your existing helper to get live stats statelessly
            # (Assuming you pass the correct teams payload and details)
            # score, red_cards, yellow_cards = get_team_stats(team_data, details) 
            
            # 3. Process Kickoffs and Latest Events
            for comment in commentary_data:
                # Assuming the API provides a timestamp or clock for the commentary
                # If the event happened within our last tracker_sleep_interval, trigger the alert!
                # Note: You'll need to parse the API's specific time format here
                
                is_kickoff = comment.get('playType', {}).get('text') == "Kickoff"
                is_recent = check_if_event_is_recent(comment, sleep_interval) # Custom helper to compare timestamps
                
                if is_kickoff and is_recent:
                     # Send Kickoff Alert directly!
                     # await send_inline_menu(session, CHAT_ID, custom_text="⚽ KICKOFF! The match has started!")
                     pass
                     
                elif is_recent:
                     # Send other live events using Gemini summaries [cite: 6]
                     # summary = await summarize_events_with_gemini("live_event", comment) [cite: 6]
                     pass
                     
    except Exception as e:
        logger.error(f"Error processing live match {match_id}: {e}")

def check_if_event_is_recent(comment, sleep_interval):
    """
    Evaluates if an event occurred within the bot's polling window.
    This replaces the need for `seen_commentaries`.
    """
    # Pseudo-code for timestamp comparison:
    # event_time = parse_time(comment['time'])
    # current_time = get_current_time()
    # return (current_time - event_time).total_seconds() <= sleep_interval
    return True

# ==========================================
# ASYNC INTERACTIVE INLINE MENU UI
# ==========================================
async def send_inline_menu(session, chat_id, custom_text=None):
    """Sends the sleek inline menu, adapting the text based on context."""
    url = f"{APP_CONFIG['api_urls']['telegram_base']}{BOT_TOKEN}/sendMessage"
    text = custom_text if custom_text else "🏆 <b>World Cup Tracker</b>\n\nSelect an option below:"
    
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "⚽ Live Scores", "callback_data": "/score"}, {"text": "📊 Live Stats", "callback_data": "/livestats"}],
                [{"text": "📅 Schedule", "callback_data": "/schedule"}, {"text": "🏆 Standings", "callback_data": "/standings"}],
                [{"text": "❓ Help", "callback_data": "/help"}]
            ]
        }
    }
    
    try:
        async with session.post(url, json=payload, timeout=APP_CONFIG["settings"]["network_timeout"]) as response:
            response.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to send inline menu to {chat_id}: {e}")

# ==========================================
# ASYNC INTERACTIVE LISTENER (PULL COMMANDS)
# ==========================================

async def handle_user_commands(session, chat_id, text):
    """Processes predefined interactive commands from Telegram users."""
    command = text.strip().lower()
    is_new_user = str(chat_id) not in subscribers
    save_subscriber(chat_id) 

    url_base = f"{APP_CONFIG['api_urls']['telegram_base']}{BOT_TOKEN}/sendMessage"

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
        live_matches = [mid for mid, state in saved_match_state.items() if state.split('-')[-1] == 'in']
        
        if not live_matches:
            fallback_msg = "⚠️ There are no live World Cup matches playing right now.\n\n"
            upcoming_two = await get_upcoming_schedule(session, limit=2)
            await _dispatch_to_telegram(session, url_base, chat_id, fallback_msg + upcoming_two)
            return
            
        score_msg = "🏆 <b>LIVE SCORES</b>\n\n"
        for match_id, state in saved_match_state.items():
            parts = state.split('-')
            if parts[-1] == 'in':  
                score1, score2 = parts[0], parts[1]
                name1, name2 = team_names_memory.get(match_id, ("Team 1", "Team 2"))
                score_msg += f"⚽ {name1} {score1} - {score2} {name2}\n"
                
        await _dispatch_to_telegram(session, url_base, chat_id, score_msg)

    elif command == '/livestats':
        live_matches = [mid for mid, state in saved_match_state.items() if state.split('-')[-1] == 'in']
        
        if not live_matches:
            await _dispatch_to_telegram(session, url_base, chat_id, "⚠️ No live matches currently in progress.")
            return
            
        for match_id in live_matches:
            stats_text = await get_match_stats(session, match_id)
            if stats_text:
                await _dispatch_to_telegram(session, url_base, chat_id, stats_text)
            else:
                await _dispatch_to_telegram(session, url_base, chat_id, "⚠️ Could not load stats for a current match.")
    
    elif command == '/standings':
        standings_text = await get_world_cup_standings(session)
        await _dispatch_to_telegram(session, url_base, chat_id, standings_text)

    elif command == '/schedule':
        schedule_text = await get_upcoming_schedule(session)
        await _dispatch_to_telegram(session, url_base, chat_id, schedule_text)

    elif command == '/help':
        help_text = (
            "🤖 <b>Available Commands:</b>\n"
            "/start - Subscribe to bot updates\n"
            "/score - Get live status & commentary of current matches\n"
            "/livestats - View detailed data & stats for live matches\n"
            "/standings - View the current group stage tables\n" 
            "/schedule - View games for the next 2 days\n"
            "/help - Show this menu"
        )
        await _dispatch_to_telegram(session, url_base, chat_id, help_text)

    else:
        warning_msg = "⚠️ I only understand specific commands. Please use the Menu button or type /help to see my available commands!"
        await _dispatch_to_telegram(session, url_base, chat_id, warning_msg)

async def check_telegram_updates(session):
    """Polls Telegram for new user messages and button clicks rapidly."""
    global update_offset
    url = f"{APP_CONFIG['api_urls']['telegram_base']}{BOT_TOKEN}/getUpdates"
    
    # Safely construct the parameters dictionary
    params = {"timeout": 1}
    if update_offset is not None:
        params["offset"] = update_offset
    
    try:
        async with session.get(url, params=params, timeout=APP_CONFIG["settings"]["network_timeout"]) as response:
            if response.status == 200:
                data = await response.json()
                for result in data.get("result", []):
                    update_offset = result["update_id"] + 1
                    
                    if "message" in result and "text" in result["message"]:
                        chat_id = result["message"]["chat"]["id"]
                        text = result["message"]["text"]
                        await handle_user_commands(session, chat_id, text)
                        
                    elif "callback_query" in result:
                        chat_id = result["callback_query"]["message"]["chat"]["id"]
                        button_data = result["callback_query"]["data"]
                        callback_id = result["callback_query"]["id"]
                        
                        try:
                            # Acknowledge the callback quickly to stop loading spinner on mobile
                            cb_url = f"{APP_CONFIG['api_urls']['telegram_base']}{BOT_TOKEN}/answerCallbackQuery"
                            async with session.post(cb_url, json={"callback_query_id": callback_id}, timeout=5) as cb_response:
                                cb_response.raise_for_status()
                        except Exception as e:
                            logger.debug(f"Failed to answer callback query: {e}")
                            
                        # Treat button press like text input
                        await handle_user_commands(session, chat_id, button_data)
    except asyncio.TimeoutError:
        pass # Expected on long poll timeout
    except Exception as e:
        logger.error(f"Error checking Telegram updates: {e}")

# ==========================================
# MAIN EXECUTION THREAD (ASYNC)
# ==========================================

async def async_telegram_listener(session):
    """Continuously polls Telegram for new commands and button clicks."""
    logger.info("Started Telegram Async Interactive Listener.")
    listener_sleep = APP_CONFIG["settings"]["listener_sleep_interval"]
    while True:
        await check_telegram_updates(session)
        await asyncio.sleep(listener_sleep)

async def async_background_tracker(session):
    """Runs the heavy API tracking and Gemini logic continuously."""
    logger.info("Started ESPN Background Async Tracker.")
    tracker_sleep = APP_CONFIG["settings"]["tracker_sleep_interval"]
    while True:
        await track_world_cup_scores(session)
        await asyncio.sleep(tracker_sleep)

async def main():
    logger.info("🚀 Pro World Cup Tracker Booted Up (Fully Asyncio Mode)...")
    
    # Establish a single AIOHTTP session for the entire application lifecycle
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