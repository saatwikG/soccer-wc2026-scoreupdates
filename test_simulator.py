import unittest.mock as mock
import FifaScore as tracker # Ensure this matches your actual filename

# --- MOCK DATA ---
# This mimics the ESPN API structure with the 'logos' array
# We use this to verify your extraction logic: 
# team1_obj.get('logos')[0].get('href')
MOCK_EVENT = {
    "id": "12345",
    "status": {"type": {"state": "in"}, "displayClock": "10"},
    "competitions": [{
        "altGameNote": "Test Match",
        "venue": {"fullName": "Test Stadium", "address": {"city": "Test City", "country": "Test"}},
        "competitors": [
            {
                "team": {
                    "id": "1", 
                    "name": "Japan", 
                    "logos": [{"href": "https://a.espncdn.com/i/teamlogos/soccer/500/217.png"}]
                }, 
                "score": "0"
            },
            {
                "team": {
                    "id": "2", 
                    "name": "Tunisia", 
                    "logos": [{"href": "https://a.espncdn.com/i/teamlogos/soccer/500/218.png"}]
                }, 
                "score": "0"
            }
        ],
        "details": []
    }]
}

def mock_get(url, **kwargs):
    """Intercepts requests and returns our mock event data."""
    if "scoreboard" in url:
        return mock.Mock(status_code=200, json=lambda: {"events": [MOCK_EVENT]})
    elif "summary" in url:
        # Return an empty list for commentary so the script doesn't crash
        return mock.Mock(status_code=200, json=lambda: {"commentary": []})
    return mock.Mock(status_code=404)

def test_logo_logic():
    print("🧪 Running Logo/Flag Logic Simulation...")
    
    # 1. Patch requests.get to use our mock
    with mock.patch('requests.get', side_effect=mock_get):
        # 2. Patch the telegram sender so it doesn't try to send to real Telegram
        with mock.patch('FifaScore.send_telegram_message') as mock_send:
            
            # Initialize match state (pre-match)
            print("⏳ Initializing match state...")
            tracker.track_world_cup_scores()
            
            # 3. Simulate a goal (Change score from 0 to 1)
            print("⚽ Simulating a goal for Japan...")
            MOCK_EVENT['competitions'][0]['competitors'][0]['score'] = "1"
            # Add a detail so it detects an event
            MOCK_EVENT['competitions'][0]['details'] = [{"scoreValue": 1, "team": {"id": "1"}}]
            
            # Run the tracker
            tracker.track_world_cup_scores()
            
            # Check if an alert was sent
            if mock_send.called:
                alert_text = mock_send.call_args[0][0]
                print("\n--- GENERATED TELEGRAM MESSAGE ---")
                print(alert_text)
                print("----------------------------------")
                
                # Check for logo URL presence
                if "https://a.espncdn.com/i/teamlogos" in alert_text:
                    print("✅ SUCCESS: Logo URL successfully embedded in text!")
                else:
                    print("❌ FAILURE: Logo URL missing from message.")
            else:
                print("❌ FAILURE: No message was triggered.")

if __name__ == "__main__":
    test_logo_logic()