TEAM_FLAGS = {
    "United States": "🇺🇸", "USA": "🇺🇸", "Canada": "🇨🇦", "Mexico": "🇲🇽",
    "Austria": "🇦🇹", "Belgium": "🇧🇪", "Bosnia and Herzegovina": "🇧🇦",
    "Croatia": "🇭🇷", "Czechia": "🇨🇿", "Czech Republic": "🇨🇿", "England": "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "France": "🇫🇷", "Germany": "🇩🇪", "Netherlands": "🇳🇱", "Norway": "🇳🇴",
    "Portugal": "🇵🇹", "Scotland": "🏴󠁧󠁢󠁳󠁣󠁴󠁿", "Spain": "🇪🇸", "Sweden": "🇸🇪",
    "Switzerland": "🇨🇭", "Turkey": "🇹🇷", "Türkiye": "🇹🇷",
    "Argentina": "🇦🇷", "Brazil": "🇧🇷", "Colombia": "🇨🇴",
    "Ecuador": "🇪🇨", "Paraguay": "🇵🇾", "Uruguay": "🇺🇾",
    "Algeria": "🇩🇿", "Cabo Verde": "🇨🇻", "Cape Verde": "🇨🇻", "Congo DR": "🇨🇩", "DR Congo": "🇨🇩",
    "Côte d'Ivoire": "🇨🇮", "Ivory Coast": "🇨🇮", "Egypt": "🇪🇬", "Ghana": "🇬🇭",
    "Morocco": "🇲🇦", "Senegal": "🇸🇳", "South Africa": "🇿🇦", "Tunisia": "🇹🇳",
    "Australia": "🇦🇺", "Iran": "🇮🇷", "IR Iran": "🇮🇷", "Iraq": "🇮🇶",
    "Japan": "🇯🇵", "Jordan": "🇯🇴", "South Korea": "🇰🇷", "Korea Republic": "🇰🇷",
    "Qatar": "🇶🇦", "Saudi Arabia": "🇸🇦", "Uzbekistan": "🇺🇿",
    "Curaçao": "🇨🇼", "Haiti": "🇭🇹", "Panama": "🇵🇦",
    "New Zealand": "🇳🇿"
}

def get_flag(team_name):
    """Returns the flag emoji for a team, or a generic white flag if not found."""
    return TEAM_FLAGS.get(team_name, "🏳️")

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