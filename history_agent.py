import sqlite3
import json

conn = sqlite3.connect("ballboy.db")
cursor = conn.cursor()

def query_history(game_state: dict) -> dict:
    team_home = game_state.get("team_home", "Arsenal FC")
    minute = game_state.get("minute", 60)
    score_home = game_state.get("score", {}).get("home", 0)
    score_away = game_state.get("score", {}).get("away", 0)
    
    # Is team winning, drawing or losing?
    if score_home > score_away:
        score_filter = "score_home > score_away"
        situation = "leading"
    elif score_home == score_away:
        score_filter = "score_home = score_away"
        situation = "drawing"
    else:
        score_filter = "score_home < score_away"
        situation = "losing"

    cursor.execute(f"""
        SELECT 
            COUNT(*) as situations,
            SUM(score_home) as total_scored,
            AVG(score_home) as avg_scored,
            AVG(score_away) as avg_conceded
        FROM match_states
        WHERE team_home LIKE ?
        AND minute BETWEEN ? AND ?
        AND {score_filter}
    """, (f"%{team_home.split()[0]}%", minute - 10, minute + 10))
    
    row = cursor.fetchone()
    situations, total_scored, avg_scored, avg_conceded = row
    
    if not situations or situations < 2:
        return {
            "pattern": "Not enough historical data for this situation",
            "situations": 0
        }
    
    result = {
        "team": team_home,
        "situations": situations,
        "situation_type": situation,
        "minute_range": f"{minute-10}-{minute+10}",
        "avg_goals_scored": round(avg_scored or 0, 2),
        "avg_goals_conceded": round(avg_conceded or 0, 2),
        "pattern": f"{team_home} when {situation} around minute {minute}: "
                   f"avg {round(avg_scored or 0,1)} scored, "
                   f"{round(avg_conceded or 0,1)} conceded "
                   f"across {situations} similar situations"
    }
    
    with open("history_context.json", "w") as f:
        json.dump(result, f, indent=2)
    
    print(f"[history] {result['pattern']}")
    return result

if __name__ == "__main__":
    # Test with a fake game state
    test_state = {
        "team_home": "Arsenal FC",
        "minute": 67,
        "score": {"home": 1, "away": 0}
    }
    query_history(test_state)