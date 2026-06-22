import requests
import time
import os
import random
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from google import genai
from google.genai import types

# --- AZURE KEY VAULT CONFIGURATION ---
KEY_VAULT_URL = os.environ.get("KEY_VAULT_URL")
BOT_TOKEN_SECRET_NAME = "FifaBotToken"
CHAT_ID_SECRET_NAME = "FifaBotChatId"
GEMINI_API_KEY_SECRET_NAME = "googlegenaikey"

try:
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)
    BOT_TOKEN = client.get_secret(BOT_TOKEN_SECRET_NAME).value
    CHAT_ID = client.get_secret(CHAT_ID_SECRET_NAME).value
    GEMINI_API_KEY = client.get_secret(GEMINI_API_KEY_SECRET_NAME).value

    # Configure the new Gemini client
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)

except Exception as e:
    print(f"Failed to fetch secrets from Azure Key Vault: {e}")
    BOT_TOKEN = ""
    CHAT_ID = ""
    gemini_client = None

# The script's memory dictionaries
saved_match_state = {}
last_notified = {} # Tracks the exact time a message was last sent for each match
sent_headlines = set() # Tracks which matches have had their headlines sent
seen_commentaries = {} # Tracks seen commentary lines per match to avoid duplicates

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    max_length = 4000  # Safe margin below 4096
    
    while len(message) > max_length:
        split_index = message.rfind('\n', 0, max_length)
        if split_index == -1:
            split_index = max_length
            
        chunk = message[:split_index]
        _dispatch_to_telegram(url, chunk)
        message = message[split_index:].lstrip('\n')
        
    if message.strip():
        _dispatch_to_telegram(url, message)

def _dispatch_to_telegram(url, text):
    """Helper function to execute the POST request with a Plain Text fallback for Markdown errors."""
    payload = {"chat_id": CHAT_ID, "text": text.strip(), "parse_mode": "Markdown"}
    
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        
    except requests.exceptions.HTTPError as e:
        # If Telegram throws a 400 Bad Request, it's almost always a Markdown syntax error from the AI.
        if response.status_code == 400:
            print("⚠️ Telegram rejected the Markdown formatting. Retrying as plain text...")
            safe_payload = {"chat_id": CHAT_ID, "text": text.strip()}
            try:
                safe_response = requests.post(url, json=safe_payload)
                safe_response.raise_for_status()
            except Exception as fallback_e:
                print(f"❌ Fallback also failed: {fallback_e}")
        else:
            print(f"❌ Failed to send message (HTTP {response.status_code}): {e}")
            
    except Exception as e:
        print(f"❌ Network error while sending message: {e}")
    
    # Telegram rate limits bots to ~1 message per second inside a specific chat
    time.sleep(1)

def get_team_stats(team_data, details):
    """Safely extracts score, red cards, AND yellow cards from the team data and match details."""
    score = int(team_data.get('score', 0))
    team_id = team_data.get('team', {}).get('id')
    red_cards = 0
    yellow_cards = 0
    
    for detail in details:
        if detail.get('team', {}).get('id') == team_id:
            if detail.get('redCard', False):
                red_cards += 1
            if detail.get('yellowCard', False):
                yellow_cards += 1
            
    return score, red_cards, yellow_cards

def fetch_recent_commentary(match_id):
    """Fetches all new play-by-play commentary since the last time it was called."""
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary?event={match_id}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json"
    }
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        commentary_data = data.get('commentary', [])
        
        if not commentary_data:
            return []
            
        is_first_run = match_id not in seen_commentaries
        if is_first_run:
            seen_commentaries[match_id] = set()
            
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
                if not is_first_run:
                    new_events.insert(0, event_str)
                    
        return new_events
    except Exception as e:
        print(f"Error fetching commentary: {e}")
        return []

def fetch_full_match_commentary(match_id):
    """Fetches the complete play-by-play commentary for a given match."""
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary?event={match_id}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json"
    }
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        commentary_data = data.get('commentary', [])
        
        if not commentary_data:
            return []
            
        all_events = []
        for play in reversed(commentary_data):
            time_string = f"{play.get('time', {}).get('displayValue', '')}{play.get('time', {}).get('addedTime', '')}'"
            all_events.append(f"[{time_string}] {play.get('text', '')}")
        return all_events
    except Exception as e:
        print(f"Error fetching full commentary: {e}")
        return []

def summarize_events_with_gemini(prompt, max_retries=3):
    """Generates a narrative summary of match events using Gemini Flash with a Persona."""
    if not prompt or not gemini_client:
        return ""

    safety_settings = [
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
    ]
    
    commentator_persona = (
        "You are a legendary, highly passionate television football (soccer) commentator. "
        "Your job is to read raw play-by-play data and translate it into thrilling, vivid, "
        "and dramatic broadcast commentary. Use advanced tactical terminology, express "
        "excitement during goals or red cards, but keep your responses incredibly concise "
        "so they fit in a mobile push notification."
    )
    
    base_delay = 2 

    # --- DEBUG: Print the outbound prompt ---
    print("\n" + "="*50)
    print("🤖 [DEBUG] SENDING PROMPT TO GEMINI:")
    print("-" * 50)
    print(prompt)
    print("="*50 + "\n")

    for attempt in range(max_retries):
        try:
            response = gemini_client.models.generate_content(
                model='gemini-2.5-flash', 
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=commentator_persona, 
                    temperature=0.7, 
                    safety_settings=safety_settings,
                )
            )
            
            result_text = response.text.strip()

            # --- DEBUG: Print the inbound response ---
            print("\n" + "="*50)
            print("✅ [DEBUG] RECEIVED RESPONSE FROM GEMINI:")
            print("-" * 50)
            print(result_text)
            print("="*50 + "\n")

            return result_text
            
        except Exception as e:
            print(f"⚠️ Gemini API Error (Attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                sleep_time = (base_delay ** attempt) + random.uniform(0, 1)
                print(f"⏳ Retrying Gemini in {sleep_time:.2f} seconds...")
                time.sleep(sleep_time)
            else:
                print("❌ Max retries reached. Falling back to raw data.")
                return ""

def track_world_cup_scores():
    url = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            return
            
        data = response.json()
        events = data.get('events', [])

        for event in events:
            match_id = event['id']
            status_state = event['status']['type']['state']
            clock = event['status']['displayClock']
            
            competitions = event['competitions']
            details = competitions[0].get('details', [])
            
            # --- EXTRACT MATCH CONTEXT (Group, Stadium, Location) ---
            comp_data = competitions[0]
            group_info = comp_data.get('altGameNote', 'World Cup Match')
            
            venue_data = comp_data.get('venue', {})
            stadium = venue_data.get('fullName', 'Unknown Stadium')
            address_data = venue_data.get('address', {})
            city = address_data.get('city', '')
            country = address_data.get('country', '')
            
            location_parts = [p for p in [stadium, city, country] if p]
            location_str = ", ".join(location_parts)
            match_context = f"🏆 {group_info}\n🏟️ {location_str}"
            
            # Re-defining competitors to prevent KeyError
            competitors = comp_data.get('competitors', [])
            if not competitors or len(competitors) < 2:
                continue 
            
            # Team 1 (Home)
            name1 = competitors[0]['team']['name']
            score1, red1, yellow1 = get_team_stats(competitors[0], details)
            
            # Team 2 (Away)
            name2 = competitors[1]['team']['name']
            score2, red2, yellow2 = get_team_stats(competitors[1], details)
            
            current_state = f"{score1}-{score2}-{red1}-{red2}-{yellow1}-{yellow2}-{status_state}"

            if match_id not in saved_match_state:
                saved_match_state[match_id] = current_state
                last_notified[match_id] = time.time()

            alert_msg = ""
            event_triggered = False

            if current_state != saved_match_state[match_id]:
                old_state = saved_match_state[match_id].split('-')
                old_score1, old_score2 = int(old_state[0]), int(old_state[1])
                old_red1, old_red2 = int(old_state[2]), int(old_state[3])
                old_yellow1, old_yellow2 = int(old_state[4]), int(old_state[5])
                old_status_state = old_state[6] if len(old_state) > 6 else 'pre'

                # 1. MATCH FINISHED
                if status_state == 'post' and old_status_state != 'post':
                    alert_msg = f"🏁 **FULL TIME**\n{match_context}\n\n{name1} {score1} - {score2} {name2}"
                    
                    full_commentary = fetch_full_match_commentary(match_id)
                    if full_commentary:
                        commentary_str = "\n".join(full_commentary)
                        prompt = f"The final whistle has blown! Read this data for the entire 90-minute match. Write a brief post-match tactical review. Mention the overarching story of the match and standout performances.\n\nDATA:\n\"\"\"\n{commentary_str}\n\"\"\""
                        summary = summarize_events_with_gemini(prompt)
                        if summary:
                            alert_msg += f"\n\n**Match Highlights:**\n{summary}"
                    
                    event_triggered = True

                # 2. LIVE EVENTS
                elif status_state == 'in':
                    # --- Goals ---
                    if score1 > old_score1 or score2 > old_score2:
                        scoring_team_id = competitors[0]['team']['id'] if score1 > old_score1 else competitors[1]['team']['id']

                        goal_desc = "A brilliant finish"
                        for detail in reversed(details):
                            if detail.get('scoreValue') == 1 and detail.get('team', {}).get('id') == scoring_team_id:
                                goal_desc = detail.get('type', {}).get('text', 'A brilliant finish')
                                if detail.get('penaltyKick', False):
                                    goal_desc = "Penalty Kick"
                                elif detail.get('ownGoal', False):
                                    goal_desc = "Own Goal"
                                break

                        goal_context = f"**Event:** {goal_desc}"
                        recent_comments = fetch_recent_commentary(match_id)
                        if recent_comments:
                            commentary_str = "\n".join(recent_comments)
                            prompt = f"A goal was just scored! Read this play-by-play data and describe the build-up. Mention who provided the assist, the type of shot, and react with absolute broadcast elation in 2 sentences.\n\nDATA:\n\"\"\"\n{commentary_str}\n\"\"\""
                            summary = summarize_events_with_gemini(prompt)
                            if summary:
                                goal_context = f"**The Goal:** {summary}"
                            else:
                                goal_context += "\n" + "\n".join([f"• {c}" for c in recent_comments])

                        alert_msg = f"⚽ **GOAL!**\n{match_context}\n\n{name1} {score1} - {score2} {name2}\n⏰ Clock: {clock}'\n\n{goal_context}"
                        event_triggered = True

                    # --- Red Cards ---
                    elif red1 > old_red1 or red2 > old_red2:
                        card_team = name1 if red1 > old_red1 else name2
                        card_team_id = competitors[0]['team']['id'] if red1 > old_red1 else competitors[1]['team']['id']
                        men_down_msg = f"{card_team} is down to 10 men."
                        
                        player_name = "A player"
                        offense_desc = "Foul"
                        
                        for detail in reversed(details):
                            if detail.get('redCard', False) and detail.get('team', {}).get('id') == card_team_id:
                                athletes = detail.get('athletesInvolved', [])
                                if athletes:
                                    player_name = athletes[0].get('shortName', athletes[0].get('displayName', player_name))
                                offense_desc = detail.get('type', {}).get('text', offense_desc)
                                break
                                
                        foul_context = f"Reason: {offense_desc}"
                        recent_comments = fetch_recent_commentary(match_id)
                        if recent_comments:
                            commentary_str = "\n".join(recent_comments)
                            prompt = f"A straight RED card was just issued to {player_name} ({card_team}) for a '{offense_desc}'. Based on the following recent play-by-play data, write a dramatic 1-2 sentence explanation detailing exactly what happened to cause this card.\n\nDATA:\n\"\"\"\n{commentary_str}\n\"\"\""
                            summary = summarize_events_with_gemini(prompt)
                            if summary:
                                foul_context = f"**What Happened:**\n{summary}"

                        alert_msg = f"🟥 **RED CARD!**\n{match_context}\n\n{player_name} ({card_team}) has been sent off!\n\n{foul_context}\n\n⏰ Clock: {clock}'\n{men_down_msg}"
                        event_triggered = True

                    # --- Yellow Cards ---
                    elif yellow1 > old_yellow1 or yellow2 > old_yellow2:
                        card_team = name1 if yellow1 > old_yellow1 else name2
                        card_team_id = competitors[0]['team']['id'] if yellow1 > old_yellow1 else competitors[1]['team']['id']
                        
                        player_name = "A player"
                        offense_desc = "Foul"
                        
                        for detail in reversed(details):
                            if detail.get('yellowCard', False) and detail.get('team', {}).get('id') == card_team_id:
                                athletes = detail.get('athletesInvolved', [])
                                if athletes:
                                    player_name = athletes[0].get('shortName', athletes[0].get('displayName', player_name))
                                offense_desc = detail.get('type', {}).get('text', offense_desc)
                                break
                                
                        foul_context = f"Reason: {offense_desc}"
                        recent_comments = fetch_recent_commentary(match_id)
                        if recent_comments:
                            commentary_str = "\n".join(recent_comments)
                            prompt = f"A YELLOW card was just issued to {player_name} ({card_team}) for a '{offense_desc}'. Based on the following recent play-by-play data, write a brief, exciting 1-sentence explanation of the foul or incident that led to this booking.\n\nDATA:\n\"\"\"\n{commentary_str}\n\"\"\""
                            summary = summarize_events_with_gemini(prompt)
                            if summary:
                                foul_context = f"**The Foul:** {summary}"

                        alert_msg = f"🟨 **YELLOW CARD!**\n{match_context}\n\nBooking for {player_name} ({card_team})!\n\n{foul_context}\n\n⏰ Clock: {clock}'\nScore remains: {name1} {score1} - {score2} {name2}"
                        event_triggered = True

                saved_match_state[match_id] = current_state

            # 3. 5-MINUTE PERIODIC UPDATES
            if status_state == 'in' and not event_triggered:
                seconds_since_last_alert = time.time() - last_notified.get(match_id, time.time())
                
                if seconds_since_last_alert >= 300:
                    alert_msg = f"⏱️ **MATCH UPDATE**\n{match_context}\n\n{name1} {score1} - {score2} {name2}\n⏰ Clock: {clock}'"
                    
                    recent_comments = fetch_recent_commentary(match_id)
                    if recent_comments:
                        commentary_str = "\n".join(recent_comments)
                        prompt = f"Analyze this recent play-by-play data. Write a 2-sentence dramatic update on the flow of the match. Which team is dominating possession? Are they pressing hard or defending deep?\n\nDATA:\n\"\"\"\n{commentary_str}\n\"\"\""
                        summary = summarize_events_with_gemini(prompt)
                        
                        if summary:
                            alert_msg += f"\n\n**Summary:**\n{summary}"
                        else:
                            alert_msg += "\n\n**Latest Action:**\n" + "\n".join([f"• {c}" for c in recent_comments])
                    else:
                        if details:
                            recent_events = []
                            for detail in reversed(details[-3:]):
                                event_text = detail.get('type', {}).get('text', 'Event')
                                event_clock = detail.get('clock', {}).get('displayValue')
                                athletes = detail.get('athletesInvolved', [])
                                player_name = athletes[0].get('shortName', '') if athletes else ''
                                
                                time_prefix = f"{event_clock}' " if event_clock else ""
                                event_str = f"• {time_prefix}{event_text}"
                                if player_name:
                                    event_str += f" ({player_name})"
                                recent_events.append(event_str)
                            if recent_events:
                                alert_msg += "\n\n**Latest Action:**\n" + "\n".join(recent_events)

                    event_triggered = True

            # 4. POST MATCH RECAP (Replaces Headlines)
            if status_state == 'post' and match_id not in sent_headlines and not event_triggered:
                full_commentary = fetch_full_match_commentary(match_id)
                ai_recap = ""
                
                if full_commentary:
                    commentary_str = "\n".join(full_commentary)
                    # The updated, strict prompt specifically asking for NO conversational filler
                    prompt = (
                        "You are a professional sports journalist. Based on the following play-by-play data "
                        "for a completed soccer match, write a single, cohesive paragraph (3-4 sentences) "
                        "providing a factual but creative recap of the game. Highlight the final score, key "
                        "scorers, and the overall momentum. "
                        "IMPORTANT: DO NOT include bullet points. DO NOT include introductory phrases like "
                        "'Here is the summary' or 'Here is a recap'. Output ONLY the final summary paragraph."
                        f"\n\nDATA:\n\"\"\"\n{commentary_str}\n\"\"\""
                    )
                    ai_recap = summarize_events_with_gemini(prompt)
                
                if ai_recap:
                    alert_msg = f"📰 **MATCH RECAP**\n{match_context}\n\n{name1} {score1} - {score2} {name2}\n\n{ai_recap}"
                    event_triggered = True
                    sent_headlines.add(match_id)
                else:
                    # Safe fallback to standard data
                    headlines = event.get('headlines', [])
                    if not headlines and event.get('competitions'):
                        headlines = event['competitions'][0].get('headlines', [])

                    if headlines:
                        headline_texts = []
                        for hl in headlines:
                            hl_text = hl.get('shortLinkText') or hl.get('headline') or hl.get('description')
                            if hl_text:
                                headline_texts.append(hl_text)
                                
                        if headline_texts:
                            headlines_joined = "\n".join([f"• {hl}" for hl in headline_texts])
                            alert_msg = f"📰 **MATCH RECAP**\n{match_context}\n\n{name1} {score1} - {score2} {name2}\n\n**ESPN Headlines:**\n{headlines_joined}"
                            event_triggered = True
                            
                    sent_headlines.add(match_id)

            # --- FIRE DISPATCH ---
            if event_triggered and alert_msg:
                send_telegram_message(alert_msg)
                print(f"Update sent for {name1} vs {name2}")
                last_notified[match_id] = time.time()

    except Exception as e:
        print(f"Error checking API: {e}")

if __name__ == "__main__":
    print("🚀 Fun World Cup Tracker Booted Up...")
    while True:
        track_world_cup_scores()
        time.sleep(30)