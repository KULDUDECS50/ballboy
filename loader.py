import requests
import sqlite3
import time

API_KEY = "cae0743864ad4d41bd2d2116856a436a"
BASE_URL = "https://api.football-data.org/v4"
HEADERS = {"X-Auth-Token": API_KEY}

conn = sqlite3.connect("ballboy.db")
cursor = conn.cursor()

def api_get(endpoint):
    r = requests.get(f"{BASE_URL}{endpoint}", headers=HEADERS)
    
    # Respect rate limits from headers
    available = int(r.headers.get("X-Requests-Available", 10))
    if available < 3:
        reset = int(r.headers.get("X-RequestCounter-Reset", 60))
        print(f"Rate limit low, waiting {reset}s...")
        time.sleep(reset)
    
    return r.json()

def load_team_history(team_name: str):
    """Load last 2 seasons of matches for a team into SQLite"""
    
    # Step 1: find the team ID
    data = api_get("/teams?limit=100")
    team_id = None
    for team in data.get("teams", []):
        if team_name.lower() in team["name"].lower():
            team_id = team["id"]
            print(f"Found: {team['name']} (id={team_id})")
            break
    
    if not team_id:
        print(f"Team not found: {team_name}")
        return

    # Step 2: fetch matches
    for season in ["2024", "2023"]:
        data = api_get(f"/teams/{team_id}/matches?season={season}&status=FINISHED")
        matches = data.get("matches", [])
        print(f"{team_name} {season}: {len(matches)} matches found")
        
        for m in matches:
            home = m["homeTeam"]["name"]
            away = m["awayTeam"]["name"]
            score_home = m["score"]["fullTime"]["home"]
            score_away = m["score"]["fullTime"]["away"]
            date = m["utcDate"][:10]
            match_id = str(m["id"])
            
            # Insert match-level record
            # We create synthetic minute snapshots at key moments
            for minute in [45, 60, 70, 80, 90]:
                cursor.execute("""
                INSERT OR IGNORE INTO match_states
                (match_id, team_home, team_away, season, minute,
                 score_home, score_away, possession_home, possession_away,
                 press_intensity, ball_zone, conceded_next_15,
                 scored_next_15, substitution_next_10, match_date)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    f"{match_id}_{minute}",
                    home, away, season, minute,
                    score_home or 0, score_away or 0,
                    50.0, 50.0,  # possession not in free tier, default 50/50
                    "unknown", "unknown",
                    0, 0, 0,
                    date
                ))
        
        conn.commit()
        time.sleep(6)  # 10 calls/min = 1 per 6 seconds

    print(f"Done loading {team_name}")

if __name__ == "__main__":
    import sys
    team = sys.argv[1] if len(sys.argv) > 1 else "Arsenal"
    load_team_history(team)