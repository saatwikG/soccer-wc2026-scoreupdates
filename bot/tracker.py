import time
import logging
from core.config import settings
from core.state import state
from utils.helpers import get_flag, get_team_stats
from services.espn_service import fetch_recent_commentary, fetch_full_match_commentary
from services.gemini_service import summarize_events_with_gemini
from services.telegram_service import send_telegram_message

logger = logging.getLogger("fifabot")

async def track_world_cup_scores(session):
    url = settings.APP_CONFIG["api_urls"]["espn_scoreboard"]
    periodic_update_time = settings.APP_CONFIG["settings"]["periodic_update_seconds"]

    try:
        async with session.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=settings.APP_CONFIG["settings"]["network_timeout"]) as response:
            if response.status != 200:
                logger.warning(f"Tracker received non-200 status: {response.status}")
                return
            data = await response.json()

        events = data.get('events', [])

        for event in events:
            match_id = event['id']
            status_state = event['status']['type']['state']

            # Skip fully processed matches
            if status_state == 'post' and match_id in state.saved_match_state and state.saved_match_state[match_id].split('-')[-1] == 'post':
                continue

            clock = event['status']['displayClock']
            competitions = event['competitions']
            details = competitions[0].get('details', [])

            comp_data = competitions[0]
            group_info = comp_data.get('altGameNote', 'World Cup Match')
            venue_data = comp_data.get('venue', {})
            stadium = venue_data.get('fullName', 'Unknown Stadium')
            city = venue_data.get('address', {}).get('city', '')
            country = venue_data.get('address', {}).get('country', '')

            location_str = ", ".join([p for p in [stadium, city, country] if p])
            match_context = f"🏆 {group_info}\n🏟️ {location_str}"

            competitors = comp_data.get('competitors', [])
            if not competitors or len(competitors) < 2: continue

            team1_obj = competitors[0].get('team', {})
            raw_name1 = team1_obj.get('name', 'Unknown')
            name1 = f"{get_flag(raw_name1)} {raw_name1}"
            score1, red1, yellow1 = get_team_stats(competitors[0], details)

            team2_obj = competitors[1].get('team', {})
            raw_name2 = team2_obj.get('name', 'Unknown')
            name2 = f"{raw_name2} {get_flag(raw_name2)}"
            score2, red2, yellow2 = get_team_stats(competitors[1], details)

            current_state = f"{score1}-{score2}-{red1}-{red2}-{yellow1}-{yellow2}-{status_state}"

            state.team_names_memory[match_id] = (name1, name2)

            if match_id not in state.saved_match_state:
                state.saved_match_state[match_id] = current_state
                state.last_notified[match_id] = time.time()
                continue

            alert_msg = ""
            event_triggered = False

            if current_state != state.saved_match_state[match_id]:
                old_state = state.saved_match_state[match_id].split('-')
                old_score1, old_score2 = int(old_state[0]), int(old_state[1])
                old_red1, old_red2 = int(old_state[2]), int(old_state[3])
                old_yellow1, old_yellow2 = int(old_state[4]), int(old_state[5])
                old_status_state = old_state[6] if len(old_state) > 6 else 'pre'

                # 1. MATCH FINISHED
                if status_state == 'post' and old_status_state != 'post':
                    alert_msg = f"🏁 FULL TIME \n{match_context}\n\n{name1} {score1} - {score2} {name2}"

                    full_commentary = await fetch_full_match_commentary(session, match_id)
                    if full_commentary:
                        commentary_str = "\n".join(full_commentary)
                        raw_data = f"Match Data (Full Match):\n{commentary_str}"
                        summary = await summarize_events_with_gemini("post_match", raw_data)
                        if summary:
                            alert_msg += f"\n\n🏆 Tactical Review: \n{summary}"
                    else:
                        headlines = event.get('headlines', [])
                        if not headlines and event.get('competitions'):
                            headlines = event['competitions'][0].get('headlines', [])
                        if headlines:
                            headline_texts = [hl.get('shortLinkText') or hl.get('headline') or hl.get('description') for hl in headlines if hl]
                            if headline_texts:
                                headlines_joined = "\n".join([f"• {hl}" for hl in headline_texts])
                                alert_msg += f"\n\n📰 <b> Headlines:</b> \n{headlines_joined}"

                    event_triggered = True
                    state.seen_commentaries.pop(match_id, None)
                    logger.info(f"🧹 Cleared commentary memory for completed match: {name1} vs {name2}")

                # 2. LIVE EVENTS
                elif status_state == 'in':
                    # Goals
                    if score1 > old_score1 or score2 > old_score2:
                        scoring_team_id = competitors[0]['team']['id'] if score1 > old_score1 else competitors[1]['team']['id']
                        scoring_team = name1 if score1 > old_score1 else name2
                        player_name = "A player"
                        goal_desc = "A brilliant finish"

                        for detail in reversed(details):
                            if detail.get('scoreValue') == 1 and detail.get('team', {}).get('id') == scoring_team_id:
                                goal_desc = detail.get('type', {}).get('text', 'A brilliant finish')
                                if detail.get('penaltyKick', False): goal_desc = "Penalty Kick"
                                elif detail.get('ownGoal', False): goal_desc = "Own Goal"
                                athletes = detail.get('athletesInvolved', [])
                                if athletes:
                                    player_name = athletes[0].get('shortName', athletes[0].get('displayName', player_name))
                                break

                        goal_context = f"Event: {goal_desc}"
                        recent_comments = await fetch_recent_commentary(session, match_id)
                        if recent_comments:
                            commentary_str = "\n".join(recent_comments)
                            raw_data = f"Goal Scored by {player_name} ({goal_desc})\nRecent Play-by-Play:\n{commentary_str}"
                            summary = await summarize_events_with_gemini("goal", raw_data)
                            if summary: goal_context = f"The Goal: {summary}"
                            else: goal_context += "\n" + "\n".join([f"• {c}" for c in recent_comments])

                        alert_msg = f"⚽ GOAL! \n{match_context}\n\n⚡ <b>{player_name}</b> has scored for {scoring_team}!\n\n{name1} {score1} - {score2} {name2}\n⏰ Clock: {clock}'\n\n{goal_context}"
                        event_triggered = True

                    # Red Cards
                    elif red1 > old_red1 or red2 > old_red2:
                        card_team = name1 if red1 > old_red1 else name2
                        card_team_id = competitors[0]['team']['id'] if red1 > old_red1 else competitors[1]['team']['id']
                        men_down_msg = f"{card_team} is down to 10 men."

                        player_name = "A player"
                        offense_desc = "Foul"
                        for detail in reversed(details):
                            if detail.get('redCard', False) and detail.get('team', {}).get('id') == card_team_id:
                                athletes = detail.get('athletesInvolved', [])
                                if athletes: player_name = athletes[0].get('shortName', athletes[0].get('displayName', player_name))
                                offense_desc = detail.get('type', {}).get('text', offense_desc)
                                break

                        foul_context = f"Reason: {offense_desc}"
                        recent_comments = await fetch_recent_commentary(session, match_id)
                        if recent_comments:
                            commentary_str = "\n".join(recent_comments)
                            raw_data = f"Event: RED CARD issued to {player_name} ({card_team}) for '{offense_desc}'.\nRecent Play-by-Play:\n{commentary_str}"
                            summary = await summarize_events_with_gemini("foul", raw_data)
                            if summary: foul_context = f"What Happened: \n{summary}"

                        alert_msg = f"🟥 RED CARD! \n{match_context}\n\n{player_name} ({card_team}) has been sent off!\n\n{foul_context}\n\n⏰ Clock: {clock}'\n{men_down_msg}"
                        event_triggered = True

                    # Yellow Cards
                    elif yellow1 > old_yellow1 or yellow2 > old_yellow2:
                        card_team = name1 if yellow1 > old_yellow1 else name2
                        card_team_id = competitors[0]['team']['id'] if yellow1 > old_yellow1 else competitors[1]['team']['id']

                        player_name = "A player"
                        offense_desc = "Foul"
                        for detail in reversed(details):
                            if detail.get('yellowCard', False) and detail.get('team', {}).get('id') == card_team_id:
                                athletes = detail.get('athletesInvolved', [])
                                if athletes: player_name = athletes[0].get('shortName', athletes[0].get('displayName', player_name))
                                offense_desc = detail.get('type', {}).get('text', offense_desc)
                                break

                        foul_context = f"Reason: {offense_desc}"
                        recent_comments = await fetch_recent_commentary(session, match_id)
                        if recent_comments:
                            commentary_str = "\n".join(recent_comments)
                            raw_data = f"Event: YELLOW CARD issued to {player_name} ({card_team}) for '{offense_desc}'.\nRecent Play-by-Play:\n{commentary_str}"
                            summary = await summarize_events_with_gemini("foul", raw_data)
                            if summary: foul_context = f"The Foul: {summary}"

                        alert_msg = f"🟨 YELLOW CARD! \n{match_context}\n\nBooking for {player_name} ({card_team})!\n\n{foul_context}\n\n⏰ Clock: {clock}'\nScore remains: {name1} {score1} - {score2} {name2}"
                        event_triggered = True

            state.saved_match_state[match_id] = current_state

            # 3. PERIODIC UPDATES
            if status_state == 'in' and not event_triggered:
                seconds_since_last_alert = time.time() - state.last_notified.get(match_id, time.time())
                if seconds_since_last_alert >= periodic_update_time:
                    recent_comments = await fetch_recent_commentary(session, match_id)
                    if recent_comments:
                        alert_msg = f"⏱️ MATCH UPDATE \n{match_context}\n\n{name1} {score1} - {score2} {name2}\n⏰ Clock: {clock}'"
                        commentary_str = "\n".join(recent_comments)
                        raw_data = f"Recent Play-by-Play:\n{commentary_str}"
                        summary = await summarize_events_with_gemini("match_update", raw_data)

                        if summary:
                            alert_msg += f"\n\nSummary: \n{summary}"
                        else:
                            alert_msg += "\n\nLatest Action: \n" + "\n".join([f"• {c}" for c in recent_comments])
                        event_triggered = True
                    else:
                        state.last_notified[match_id] = time.time()

            # FIRE DISPATCH
            if event_triggered and alert_msg:
                await send_telegram_message(session, alert_msg)
                logger.info(f"Update successfully sent for {name1} vs {name2}")
                state.last_notified[match_id] = time.time()

    except Exception as e:
        logger.error(f"Error checking API in background tracker: {e}")