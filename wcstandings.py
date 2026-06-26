import requests

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X",
    "Accept": "application/json"
}
children = requests.get("https://site.api.espn.com/apis/v2/sports/soccer/fifa.world/standings", headers=headers).json().get('children', [])
for child in children:
    print(f"{child['name']}")
    for detail in child['standings']['entries']:
        print(f" {detail['team']['displayName']} - Group standing: {detail['note']['rank']} - {detail['note']['description']}")
