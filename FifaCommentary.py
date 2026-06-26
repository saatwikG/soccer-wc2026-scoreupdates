import requests

game_id = "760437"
# Switching to the stable Summary API
url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary?event={game_id}"

# Browsers send many headers; mimicking them helps bypass bot detection
headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json"
}

def fetch_commentary():
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        # If it fails again, printing the raw text will show us exactly why (e.g., an HTML error page)
        try:
            data = response.json()
        except requests.exceptions.JSONDecodeError:
            print("Failed to decode JSON. Here is the raw response:")
            print(response.text[:500]) # Print first 500 chars to diagnose
            return

        # In the Summary API, the play-by-play is housed under the 'commentary' key
        commentary_data = data.get('commentary', [])
        
        if not commentary_data:
            print("No commentary data found for this match. It may not have started or ESPN didn't log play-by-play.")
            return
            
        print(f"Found {len(commentary_data)} commentary events:\n")
        
        # ESPN orders this list with the newest plays first.
        # We'll reverse it so it reads chronologically from kickoff to final whistle.
        for play in reversed(commentary_data):
            # ESPN's time object looks like: {'displayValue': '45', 'addedTime': '+2'}
            time_data = play.get('time', {})
            minute = time_data.get('displayValue', '')
            added_time = time_data.get('addedTime', '')
            
            # Combine minute and added time (e.g., "45" + "+2" = "45+2'")
            time_string = f"{minute}{added_time}'" if minute else "N/A"
            
            text = play.get('text', 'No text provided')
            print(f"[{time_string}] {text}")

    except requests.exceptions.RequestException as e:
        print(f"Error fetching data: {e}")

fetch_commentary()