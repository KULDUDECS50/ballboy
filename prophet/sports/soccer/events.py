"""Soccer event fetchers.

Three sources supported:
  - 'espn'           : Free, no auth, what we already use
  - 'football-data'  : Free tier with key (set FOOTBALL_DATA_KEY in .env)
  - 'mock'           : Reads from a local JSON file (for offline dev)

Each fetcher returns RAW source-specific data. The normalizer.py module
converts these into the canonical MatchEvent schema.

Adding a new source: implement a fetch_{source}_events(match_id) function
that returns whatever shape the source gives you, then add normalization
rules in normalizer.py.

TODOs for you (Ruth):
  - [ ] Pick your primary source (recommend ESPN for now)
  - [ ] If using football-data.org, get a key and add to .env as FOOTBALL_DATA_KEY
  - [ ] Test against a recent EPL match — verify event coverage looks good
  - [ ] Decide if you need set-piece location detail; if so, add API-Football
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ESPN
# ---------------------------------------------------------------------------

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"


def fetch_espn_match(league: str, event_id: str) -> dict:
    """Fetch a single soccer match's full data from ESPN.

    league: 'eng.1' (EPL), 'uefa.champions', 'usa.1' (MLS), etc.
    event_id: ESPN's event identifier for the match.

    Returns the raw summary payload — includes commentary (event timeline),
    box score, lineups, statistics.
    """
    url = f"{ESPN_BASE}/{league}/summary"
    resp = requests.get(
        url, params={"event": event_id}, timeout=15,
        headers={"User-Agent": "prophet-agent/0.1"},
    )
    resp.raise_for_status()
    return resp.json()


def fetch_espn_scoreboard(league: str = "eng.1", target_date: str | None = None) -> dict:
    """List matches for a league/date. target_date in YYYYMMDD format."""
    url = f"{ESPN_BASE}/{league}/scoreboard"
    params = {}
    if target_date:
        params["dates"] = target_date
    resp = requests.get(url, params=params, timeout=15,
                        headers={"User-Agent": "prophet-agent/0.1"})
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# football-data.org
# ---------------------------------------------------------------------------

FOOTBALL_DATA_BASE = "https://api.football-data.org/v4"


def fetch_football_data_match(match_id: int | str) -> dict:
    """Fetch a single match from football-data.org. Requires FOOTBALL_DATA_KEY."""
    key = os.environ.get("FOOTBALL_DATA_KEY")
    if not key:
        raise RuntimeError("Set FOOTBALL_DATA_KEY in .env to use football-data.org")

    url = f"{FOOTBALL_DATA_BASE}/matches/{match_id}"
    resp = requests.get(url, headers={"X-Auth-Token": key}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_football_data_competition_matches(
    competition: str = "PL",
    status: str | None = None,
) -> dict:
    """List matches in a competition. PL=Premier League, CL=Champions League."""
    key = os.environ.get("FOOTBALL_DATA_KEY")
    if not key:
        raise RuntimeError("Set FOOTBALL_DATA_KEY in .env to use football-data.org")

    url = f"{FOOTBALL_DATA_BASE}/competitions/{competition}/matches"
    params = {}
    if status:
        params["status"] = status
    resp = requests.get(url, headers={"X-Auth-Token": key}, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Mock (for offline dev — your friend during the hackathon)
# ---------------------------------------------------------------------------

def fetch_mock_match(path: str | Path) -> dict:
    """Load a mock match JSON. Use this when no network or for repeatable tests."""
    return json.loads(Path(path).read_text())


# ---------------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------------

def fetch_match(source: str, **kwargs) -> dict:
    """Dispatch to the right fetcher by source name."""
    if source == "espn":
        return fetch_espn_match(
            league=kwargs.get("league", "eng.1"),
            event_id=kwargs["event_id"],
        )
    if source == "football-data":
        return fetch_football_data_match(match_id=kwargs["match_id"])
    if source == "mock":
        return fetch_mock_match(kwargs["path"])
    raise ValueError(f"Unknown source: {source}")
