import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from core.config import settings
from core.state import state
from utils.helpers import get_flag

logger = logging.getLogger("fifabot")

async def fetch_recent_commentary(session, match_id):
    url = f"{settings.APP_CONFIG['api_urls']['espn_summary']}?event={match_id}"
    try:
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=settings.APP_CONFIG["settings"]["network_timeout"]) as response:
            response.raise_for_status()
            data = await response.json()
            commentary_data = data.get('commentary', [])

        if not commentary_data: return []

        is_first_run = match_id not in state.seen_commentaries
        if is_first_run: state.seen_commentaries[match_id] = set()

        new_events = []
        for play in commentary_data:
            time_data = play.get('time', {})
            minute = time_data.get('displayValue', '')
            added_time = time_data.get('addedTime', '')
            time_string = f"{minute}{added_time}'" if minute else "N/A"
            text = play.get('text', 'No text provided')
            event_str = f"[{time_string}] {text}"

            if event_str not in state.seen_commentaries[match_id]:
                state.seen_commentaries[match_id].add(event_str)
                if not is_first_run: new_events.insert(0, event_str)
        return new_events
    except Exception as e:
        logger.error(f"Error fetching commentary for match {match_id}: {e}")
        return []

async def fetch_full_match_commentary(session, match_id):
    url = f"{settings.APP_CONFIG['api_urls']['espn_summary']}?event={match_id}"
    try:
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=settings.APP_CONFIG["settings"]["network_timeout"]) as response:
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
    base_url = settings.APP_CONFIG['api_urls']['espn_scoreboard']
    headers = {'User-Agent': 'Mozilla/5.0'}

    try:
        todaysDate = datetime.now(ZoneInfo(settings.APP_CONFIG["settings"]["timezone"])).date()
        finalDate = todaysDate + timedelta(days=3)
        date_range = f"{todaysDate.strftime('%Y%m%d')}-{finalDate.strftime('%Y%m%d')}"

        async with session.get(f"{base_url}?dates={date_range}", headers=headers, timeout=settings.APP_CONFIG["settings"]["network_timeout"]) as response:
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
            schedule_msg = "📅 <b>Upcoming Knockout Matches</b>\n\n"

        for event in upcoming_events:
            for competition in event.get('competitions', []):
                group_info = competition.get('altGameNote', 'World Cup Match')
                stadium = competition.get('venue', {}).get('fullName', 'Unknown')
                city = competition.get('venue', {}).get('address', {}).get('city', '')
                country = competition.get('venue', {}).get('address', {}).get('country', '')

                location_str = ", ".join([p for p in [stadium, city, country] if p])

                competitors = competition.get('competitors', [])
                if len(competitors) >= 2:
                    raw_name1 = competitors[0].get('team', {}).get('name', 'Unknown')
                    display_name1 = f"{get_flag(raw_name1)} {raw_name1}"

                    raw_name2 = competitors[1].get('team', {}).get('name', 'Unknown')
                    display_name2 = f"{raw_name2} {get_flag(raw_name2)}"

                    matchup_str = f"{display_name1} vs {display_name2}"
                else:
                    matchup_str = event.get('name', 'Unknown Matchup')

                matchtime = datetime.fromisoformat(competition['date']).astimezone(ZoneInfo(settings.APP_CONFIG["settings"]["timezone"]))

                hour = matchtime.strftime("%I").lstrip("0")
                minute = matchtime.strftime("%M")
                am_pm = matchtime.strftime("%p")
                tz_abbrev = matchtime.strftime("%Z")
                month = matchtime.strftime("%B")
                day = str(matchtime.day)
                year = matchtime.strftime("%Y")

                readable_time = f"{hour}:{minute} {am_pm} {tz_abbrev} on {month} {day}, {year}"

                schedule_msg += f"⚽ {matchup_str}\n🏆 {group_info}\n🏟️ {location_str}\n⏰ {readable_time}\n\n"

        return schedule_msg

    except Exception as e:
        logger.error(f"Error checking schedule: {e}")
        return f"⚠️ Error fetching schedule: {e}"

async def get_match_stats(session, match_id):
    url = f"{settings.APP_CONFIG['api_urls']['espn_summary']}?event={match_id}"
    try:
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=settings.APP_CONFIG["settings"]["network_timeout"]) as response:
            response.raise_for_status()
            data = await response.json()

        boxscore = data.get('boxscore', {}).get('teams', [])
        if len(boxscore) < 2:
            return None

        t1, t2 = boxscore[0], boxscore[1]
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