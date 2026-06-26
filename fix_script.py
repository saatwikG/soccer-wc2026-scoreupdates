import sys

with open('FifaScore.py', 'r', encoding='utf-8') as f:
    content = f.read()

start_idx = content.find("async def process_live_match_summary")
end_idx = content.find("async def track_world_cup_scores(session):")

new_funcs = """async def process_live_match_summary(session, match_id, summary_data, snapshot, periodic_update_time):
    now = time.time()
    commentary = summary_data.get('commentary', [])
    latest_plays = commentary[:5]
    first_seen = match_id not in notified_commentary_keys
    seen_keys = notified_commentary_keys.setdefault(match_id, set())
    last_periodic_update.setdefault(match_id, now)
    
    if first_seen:
        for play in commentary:
            seen_keys.add(get_commentary_key(match_id, play))
        if should_send_kickoff_alert(snapshot, now):
            alert_msg, alert_logo = build_kickoff_alert(snapshot)
            await send_telegram_message(session, alert_msg, photo_url=alert_logo)
            last_periodic_update[match_id] = now
            logger.info(f"Kickoff alert sent for match {match_id}")
        return
        
    new_plays = []
    for play in reversed(commentary):
        event_key = get_commentary_key(match_id, play)
        if event_key not in seen_keys:
            seen_keys.add(event_key)
            new_plays.append(play)

    alert_sent = False
    for play in new_plays:
        event_type = classify_commentary_event(play)
        if event_type == "update":
            continue
        
        alert_msg, alert_logo = await build_commentary_alert(snapshot, play, latest_plays)
        if alert_msg:
            await send_telegram_message(session, alert_msg, photo_url=alert_logo)
            alert_sent = True
            
    if alert_sent:
        last_periodic_update[match_id] = now
        return
    
    if now - last_periodic_update.get(match_id, now) >= periodic_update_time:
        if await send_periodic_match_update(session, match_id, snapshot, latest_plays):
            last_periodic_update[match_id] = now

async def track_world_cup_scores_from_summary(session):
    periodic_update_time = APP_CONFIG["settings"]["periodic_update_seconds"]
    
    try:
        events = await fetch_scoreboard_events(session)
        for event in events:
            match_id = str(event.get('id', ''))
            scoreboard_state = _get_event_status_state(event)
            if not match_id or scoreboard_state not in ("in", "post"):
                continue
            
            summary_data = await fetch_match_summary(session, match_id)
            if not summary_data:
                continue
            
            snapshot = build_match_snapshot(summary_data, fallback_event=event)
            if not snapshot.get('status_state'):
                snapshot['status_state'] = scoreboard_state
            
            # --- Kickoff Detection via State Transition ---
            current_period = snapshot.get('period')
            current_status_name = snapshot.get('status_name', '').upper()
            
            old_phase = match_phases.get(match_id)
            if old_phase:
                old_period, old_status_name = old_phase
                
                # Detect Second Half Kickoff (Halftime -> In Progress)
                if old_status_name == "STATUS_HALFTIME" and current_status_name == "STATUS_IN_PROGRESS":
                    alert_msg = f"⚽ **SECOND HALF KICKOFF!**\\n{format_match_context(snapshot)}\\n\\n{format_scoreline(snapshot)}\\n\\nThe second half is underway!"
                    teams = snapshot.get('teams', [])
                    alert_logo = teams[0].get('logo', '') if teams else ''
                    await send_telegram_message(session, alert_msg, photo_url=alert_logo)
                    logger.info(f"Second half kickoff alert sent for match {match_id}")
            
            match_phases[match_id] = (current_period, current_status_name)
            
            if snapshot['status_state'] == "in":
                await process_live_match_summary(session, match_id, summary_data, snapshot, periodic_update_time)
            elif snapshot['status_state'] == "post":
                await send_post_match_recap(session, match_id, summary_data, snapshot)

    except Exception as e:
        logger.error(f"Error checking API in background tracker: {e}")

"""

content = content[:start_idx] + new_funcs + content[end_idx:]
with open('FifaScore.py', 'w', encoding='utf-8') as f:
    f.write(content)
