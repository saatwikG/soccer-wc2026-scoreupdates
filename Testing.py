from zoneinfo import ZoneInfo
import requests
from datetime import date, datetime, timedelta

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X",
    "Accept": "application/json"
}

teams = requests.get("https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary?event=760459", headers=headers).json().get('boxscore').get('teams', [])
for team in teams:
    print(f"{team['team']['name']}")
    for detail in team['statistics']:
        print(f" {detail['label']} - {detail['displayValue']}")

# todaysDate = date.today()  # Get today's date
# tomorrowDate = todaysDate + timedelta(days=1)  # Get tomorrow's date
# print(todaysDate)
# print(tomorrowDate)
# events = requests.get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard?dates={todaysDate.strftime('%Y%m%d')}-{tomorrowDate.strftime('%Y%m%d')}", headers=headers).json().get("events", [])
# for event in events:
#     for competition in event['competitions']:
#         matchtime = datetime.fromisoformat(competition['date']).astimezone(ZoneInfo("America/Chicago"))
#         # 2. Extract components cleanly to avoid cross-platform padding bugs
#         hour = matchtime.strftime("%I").lstrip("0")   # 12-hour clock (removes leading zero if any)
#         am_pm = matchtime.strftime("%p")
#         tz_abbrev = matchtime.strftime("%Z")          # Unlocks dynamic 'CDT' or 'CST' string!   
#         month = matchtime.strftime("%B")              # Full month text name
#         day = str(matchtime.day)                      # Day of the month as a plain integer string
#         year = matchtime.strftime("%Y")               # 4-digit year

#         # 3. Assemble into your exact custom layout
#         readable_time = f"{hour}{am_pm} {tz_abbrev} on {month}, {day}, {year}"
#           # Convert to datetime object
#         print(f"{competition['altGameNote']} - {event['name']} in {competition['venue']['fullName']} {competition['venue']['address']['city']}, {competition['venue']['address']['country']} @ {readable_time}")
#         for detail in competition['details']:
#             if detail['type']['text'] in "Goal":
#                 print(f"  {detail}")