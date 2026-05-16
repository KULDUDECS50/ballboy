import sqlite3

conn = sqlite3.connect("ballboy.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS match_states (
    match_id        TEXT,
    team_home       TEXT,
    team_away       TEXT,
    season          TEXT,
    minute          INTEGER,
    score_home      INTEGER,
    score_away      INTEGER,
    possession_home REAL,
    possession_away REAL,
    press_intensity TEXT,
    ball_zone       TEXT,
    conceded_next_15 INTEGER,
    scored_next_15   INTEGER,
    substitution_next_10 INTEGER,
    match_date      TEXT
)
""")

conn.commit()
print("DB ready")