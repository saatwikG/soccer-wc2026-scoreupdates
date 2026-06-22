import requests
import time
import os
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

# --- AZURE KEY VAULT CONFIGURATION ---
KEY_VAULT_URL = os.environ.get("KEY_VAULT_URL")
BOT_TOKEN_SECRET_NAME = "FifaBotToken"
CHAT_ID_SECRET_NAME = "FifaBotChatId"

try:
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)
    BOT_TOKEN = client.get_secret(BOT_TOKEN_SECRET_NAME).value
    CHAT_ID = client.get_secret(CHAT_ID_SECRET_NAME).value
except Exception as e:
    print(f"Failed to fetch secrets from Azure Key Vault: {e}")
    BOT_TOKEN = ""
    CHAT_ID = ""

# The script's memory dictionaries
saved_match_state = {}
last_notified = {} # Tracks the exact time a message was last sent for each match
sent_headlines = set() # Tracks which matches have had their headlines sent
seen_commentaries = {} # Tracks seen commentary lines per match to avoid duplicates

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    max_length = 4000  # Safe margin below 4096
    
    while len(message) > max_length:
        # Search backwards from the 4000th character to find the nearest newline
        split_index = message.rfind('\n', 0, max_length)
        
        # Fallback: If somehow there are NO newlines in a 4000-char block, force a hard split
        if split_index == -1:
            split_index = max_length
            
        # Extract the chunk up to the split point
        chunk = message[:split_index]
        _dispatch_to_telegram(url, chunk)
        
        # Update the message to be whatever is left over, removing the leading newline
        message = message[split_index:].lstrip('\n')
        
    # Send any remaining text at the end of the loop
    if message.strip():
        _dispatch_to_telegram(url, message)

def _dispatch_to_telegram(url, text):
    """Helper function to actually execute the POST request."""
    payload = {"chat_id": CHAT_ID, "text": text.strip(), "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
    except Exception as e:
        print(f"Failed to send message: {e}")
    
    # Telegram rate limits bots to ~1 message per second inside a specific chat
    time.sleep(1)

def get_team_stats(team_data, details):
    """Safely extracts score, red cards, AND yellow cards from the team data and match details."""
    score = int(team_data.get('score', 0))
    team_id = team_data.get('team', {}).get('id')
    red_cards = 0
    yellow_cards = 0
    
    # The API JSON shows that cards are reliably tracked in 'details', not 'statistics'
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
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
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
        # ESPN orders newest first. Iterate to find unseen ones.
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
                    # Insert at beginning for chronological order (oldest -> newest)
                    new_events.insert(0, event_str)
                    
        return new_events
    except Exception as e:
        print(f"Error fetching commentary: {e}")
        return []

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
            status_state = event['status']['type']['state'] # 'pre', 'in', or 'post'
            clock = event['status']['displayClock']
            
            competitors = event['competitions'][0]['competitors']
            details = event['competitions'][0].get('details', [])
            
            # Team 1 (Home)
            name1 = competitors[0]['team']['name']
            score1, red1, yellow1 = get_team_stats(competitors[0], details)
            
            # Team 2 (Away)
            name2 = competitors[1]['team']['name']
            score2, red2, yellow2 = get_team_stats(competitors[1], details)
            
            # Create a combined state string: "Score1-Score2-Red1-Red2-Yellow1-Yellow2-Status"
            current_state = f"{score1}-{score2}-{red1}-{red2}-{yellow1}-{yellow2}-{status_state}"

            # Initial memory save
            if match_id not in saved_match_state:
                saved_match_state[match_id] = current_state
                last_notified[match_id] = time.time() # Start the 5-minute stopwatch

            alert_msg = ""
            event_triggered = False

            # Check if the state has changed
            if current_state != saved_match_state[match_id]:
                
                # Split the old state to figure out exactly WHAT changed
                old_state = saved_match_state[match_id].split('-')
                old_score1, old_score2 = int(old_state[0]), int(old_state[1])
                old_red1, old_red2 = int(old_state[2]), int(old_state[3])
                old_yellow1, old_yellow2 = int(old_state[4]), int(old_state[5])
                old_status_state = old_state[6] if len(old_state) > 6 else 'pre'

                # 1. Check for MATCH FINISHED (Transitioned to 'post')
                if status_state == 'post' and old_status_state != 'post':
                    alert_msg = f"🏁 **FULL TIME**\n\n{name1} {score1} - {score2} {name2}\n\nThe match has concluded!"
                    
                    # Fetch headlines about the match
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
                            alert_msg += "\n\n**Post-Match Headlines:**\n" + "\n".join([f"📰 {hl}" for hl in headline_texts])
                            sent_headlines.add(match_id)

                    event_triggered = True

                # 2. Check for live events ONLY if the game is still 'in' progress
                elif status_state == 'in':
                    # Check for Goals
                    if score1 > old_score1 or score2 > old_score2:
                        scoring_team_id = competitors[0]['team']['id'] if score1 > old_score1 else competitors[1]['team']['id']

                        # Fetch the commentary
                        recent_comments = fetch_recent_commentary(match_id)
                        if recent_comments:
                            commentary_text = "\n".join([f"• {c}" for c in recent_comments])
                        else:
                            # Fallback if no commentary
                            goal_desc = "A brilliant finish"
                            details = event['competitions'][0].get('details', [])
                            for detail in reversed(details):
                                if detail.get('scoreValue') == 1 and detail.get('team', {}).get('id') == scoring_team_id:
                                    goal_desc = detail.get('type', {}).get('text', 'A brilliant finish')
                                    if detail.get('penaltyKick', False):
                                        goal_desc = "Penalty Kick"
                                    elif detail.get('ownGoal', False):
                                        goal_desc = "Own Goal"
                                    break
                            commentary_text = goal_desc

                        alert_msg = f"⚽ **GOAL!**\n\n{name1} {score1} - {score2} {name2}\n⏰ Clock: {clock}'\n\n{commentary_text}"
                        event_triggered = True

                    # Check for Red Cards
                    elif red1 > old_red1 or red2 > old_red2:
                        card_team = name1 if red1 > old_red1 else name2
                        card_team_id = competitors[0]['team']['id'] if red1 > old_red1 else competitors[1]['team']['id']
                        men_down_msg = f"{card_team} is down to 10 men."
                        
                        player_name = "A player"
                        offense_desc = "Foul"
                        
                        details = event['competitions'][0].get('details', [])
                        for detail in reversed(details):
                            if detail.get('redCard', False) and detail.get('team', {}).get('id') == card_team_id:
                                athletes = detail.get('athletesInvolved', [])
                                if athletes:
                                    player_name = athletes[0].get('shortName', athletes[0].get('displayName', player_name))
                                offense_desc = detail.get('type', {}).get('text', offense_desc)
                                break
                                
                        alert_msg = f"🟥 **RED CARD!**\n\n{player_name} ({card_team}) has been sent off!\nReason: {offense_desc}\n⏰ Clock: {clock}'\n{men_down_msg}"
                        event_triggered = True

                    # Check for Yellow Cards
                    elif yellow1 > old_yellow1 or yellow2 > old_yellow2:
                        card_team = name1 if yellow1 > old_yellow1 else name2
                        card_team_id = competitors[0]['team']['id'] if yellow1 > old_yellow1 else competitors[1]['team']['id']
                        
                        player_name = "A player"
                        offense_desc = "Foul"
                        
                        details = event['competitions'][0].get('details', [])
                        for detail in reversed(details):
                            if detail.get('yellowCard', False) and detail.get('team', {}).get('id') == card_team_id:
                                athletes = detail.get('athletesInvolved', [])
                                if athletes:
                                    player_name = athletes[0].get('shortName', athletes[0].get('displayName', player_name))
                                offense_desc = detail.get('type', {}).get('text', offense_desc)
                                break
                                
                        alert_msg = f"🟨 **YELLOW CARD!**\n\nBooking for {player_name} ({card_team})!\nReason: {offense_desc}\n⏰ Clock: {clock}'\nScore remains: {name1} {score1} - {score2} {name2}"
                        event_triggered = True

                # Update memory
                saved_match_state[match_id] = current_state

            # 3. Check for 5-minute periodic update (300 seconds)
            # Only runs if the game is live, and we didn't just send a goal/card alert
            if status_state == 'in' and not event_triggered:
                # Calculate how long it's been since our last message
                seconds_since_last_alert = time.time() - last_notified.get(match_id, time.time())
                
                if seconds_since_last_alert >= 300:
                    alert_msg = f"⏱️ **MATCH UPDATE**\n\n{name1} {score1} - {score2} {name2}\n⏰ Clock: {clock}'"
                    
                    recent_comments = fetch_recent_commentary(match_id)
                    if recent_comments:
                        alert_msg += "\n\n**Latest Action:**\n" + "\n".join([f"• {c}" for c in recent_comments])
                    else:
                        # Fallback if no commentary
                        details = event['competitions'][0].get('details', [])
                        if details:
                            recent_events = []
                            # Take up to the 3 most recent events
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

            # 4. Check for delayed headlines for finished games
            if status_state == 'post' and match_id not in sent_headlines and not event_triggered:
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
                        headlines_joined = "\n".join([f"📰 {hl}" for hl in headline_texts])
                        alert_msg = f"📰 **POST-MATCH HEADLINES**\n\n{name1} {score1} - {score2} {name2}\n\n{headlines_joined}"
                        event_triggered = True
                        sent_headlines.add(match_id)

            # Send the alert if an event occurred OR 5 minutes passed
            if event_triggered and alert_msg:
                send_telegram_message(alert_msg)
                print(f"Update sent for {name1} vs {name2}")
                # Reset the stopwatch whenever ANY message is sent
                last_notified[match_id] = time.time()

    except Exception as e:
        print(f"Error checking API: {e}")

if __name__ == "__main__":
    print("🚀 Fun World Cup Tracker Booted Up...")
    while True:
        track_world_cup_scores()
        time.sleep(30)