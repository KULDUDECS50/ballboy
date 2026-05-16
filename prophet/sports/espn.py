"""ESPN API client.

Uses ESPN's hidden but public APIs — no key required.
Same endpoint serves past, in-progress, and future games. The plays array
just grows as the game progresses.

Endpoints:
    /scoreboard?dates=YYYYMMDD     → list of games for a date
    /summary?event=<id>            → full game detail + play-by-play

NBA is the default sport but the code generalizes to nfl, mlb, nhl, etc.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)


SPORT_PATHS = {
    "nba": "basketball/nba",
    "wnba": "basketball/wnba",
    "ncaamb": "basketball/mens-college-basketball",
    "nfl": "football/nfl",
    "mlb": "baseball/mlb",
    "nhl": "hockey/nhl",
    "soccer": "soccer/eng.1",  # EPL default
}

BASE = "https://site.api.espn.com/apis/site/v2/sports"


def _fetch(path: str, params: dict | None = None) -> dict:
    """GET an ESPN endpoint with sensible defaults."""
    url = f"{BASE}/{path}"
    resp = requests.get(url, params=params, timeout=15,
                        headers={"User-Agent": "prophet-agent/0.1"})
    resp.raise_for_status()
    return resp.json()


def get_scoreboard(sport: str = "nba", target_date: date | None = None) -> list[dict]:
    """Return a list of simplified game records for a sport on a date.

    Each record has: id, name, status, home, away, scores, broadcast.
    """
    sport_path = SPORT_PATHS.get(sport, sport)
    params = {}
    if target_date:
        params["dates"] = target_date.strftime("%Y%m%d")
    data = _fetch(f"{sport_path}/scoreboard", params)

    games = []
    for event in data.get("events", []):
        try:
            comp = event["competitions"][0]
            home = next(c for c in comp["competitors"] if c["homeAway"] == "home")
            away = next(c for c in comp["competitors"] if c["homeAway"] == "away")
            games.append({
                "id": event["id"],
                "name": event.get("name", ""),
                "short_name": event.get("shortName", ""),
                "status": comp["status"]["type"]["state"],   # 'pre', 'in', 'post'
                "status_detail": comp["status"]["type"].get("shortDetail", ""),
                "clock": comp["status"].get("displayClock", ""),
                "period": comp["status"].get("period", 0),
                "home_team": home["team"]["abbreviation"],
                "home_score": int(home.get("score", 0)),
                "away_team": away["team"]["abbreviation"],
                "away_score": int(away.get("score", 0)),
                "start_time": event.get("date", ""),
            })
        except (KeyError, IndexError, StopIteration) as exc:
            logger.warning("Could not parse event %s: %s", event.get("id"), exc)

    return games


def get_summary(sport: str, event_id: str) -> dict:
    """Fetch the full game summary (includes play-by-play)."""
    sport_path = SPORT_PATHS.get(sport, sport)
    return _fetch(f"{sport_path}/summary", {"event": event_id})


def get_plays(summary: dict) -> list[dict]:
    """Extract a normalized list of plays from a summary payload.

    Returns plays in chronological order with the fields the rest of the
    pipeline expects: id, period, clock, home_score, away_score, text,
    team_abbrev.
    """
    plays_raw = summary.get("plays", [])
    normalized: list[dict] = []
    for p in plays_raw:
        normalized.append({
            "id": p.get("id", ""),
            "sequence": p.get("sequenceNumber") or len(normalized),
            "period": (p.get("period") or {}).get("number", 0),
            "clock": (p.get("clock") or {}).get("displayValue", ""),
            "clock_seconds": _parse_clock_seconds(
                (p.get("clock") or {}).get("displayValue", "")
            ),
            "home_score": int(p.get("homeScore", 0)),
            "away_score": int(p.get("awayScore", 0)),
            "text": p.get("text", ""),
            "type": (p.get("type") or {}).get("text", ""),
            "scoring": p.get("scoringPlay", False),
            "team": (p.get("team") or {}).get("displayName", ""),
        })
    return normalized


def _parse_clock_seconds(clock_str: str) -> float:
    """Convert '11:23' or '0:42.3' to seconds elapsed in period."""
    if not clock_str:
        return 0.0
    try:
        parts = clock_str.split(":")
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (ValueError, IndexError):
        return 0.0


def cache_game(sport: str, event_id: str, cache_dir: str | Path) -> Path:
    """Fetch and save a game's full summary + plays to disk."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    summary = get_summary(sport, event_id)
    plays = get_plays(summary)

    # Extract teams + final score for the index.
    header = summary.get("header", {})
    competitions = header.get("competitions", [{}])
    comp = competitions[0] if competitions else {}
    competitors = comp.get("competitors", [])
    home = next((c for c in competitors if c.get("homeAway") == "home"), {})
    away = next((c for c in competitors if c.get("homeAway") == "away"), {})

    out = {
        "sport": sport,
        "event_id": event_id,
        "name": header.get("competitions", [{}])[0].get("name", ""),
        "home_team": (home.get("team") or {}).get("abbreviation", ""),
        "home_team_name": (home.get("team") or {}).get("displayName", ""),
        "away_team": (away.get("team") or {}).get("abbreviation", ""),
        "away_team_name": (away.get("team") or {}).get("displayName", ""),
        "final_home_score": int(home.get("score", 0)),
        "final_away_score": int(away.get("score", 0)),
        "n_plays": len(plays),
        "plays": plays,
        "raw_summary_preserved": False,   # We dropped raw to keep file small
        "fetched_at": datetime.utcnow().isoformat() + "Z",
    }

    out_path = cache_dir / f"{sport}_{event_id}.json"
    out_path.write_text(json.dumps(out, indent=2))
    logger.info("Cached %s game %s → %s (%d plays)",
                sport, event_id, out_path, len(plays))
    return out_path


def load_cached_game(path: str | Path) -> dict:
    """Load a cached game file."""
    return json.loads(Path(path).read_text())
