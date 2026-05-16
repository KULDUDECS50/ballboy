"""Fantasy Premier League API client.

The FPL API is free, no auth required, and surprisingly clean for player data.
It's curated by FPL editors who watch every match and update injury news within
minutes of press conferences.

ENDPOINTS WE USE
----------------
GET https://fantasy.premierleague.com/api/bootstrap-static/
    Master roster: all players, teams, current form, news, injury status

GET https://fantasy.premierleague.com/api/element-summary/{player_id}/
    Detailed gameweek-by-gameweek history for one player

CACHING
-------
We cache the bootstrap response for 10 minutes per process. The master roster
changes once per gameweek + breaking news. No need to hit it every call.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from .schema import PlayerFitness, PlayerForm

logger = logging.getLogger(__name__)

FPL_BOOTSTRAP_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
FPL_ELEMENT_URL = "https://fantasy.premierleague.com/api/element-summary/{id}/"

# Process-local cache
_bootstrap_cache: dict[str, Any] | None = None
_bootstrap_cached_at: float = 0
_CACHE_TTL_SECONDS = 600

# Map FPL status codes → our enum
_STATUS_MAP = {
    "a": "available",
    "d": "doubtful",
    "i": "injured",
    "s": "suspended",
    "u": "unknown",      # Unavailable (loan/transferred/etc)
    "n": "unknown",      # Not in squad
}


def _get_bootstrap(force_refresh: bool = False) -> dict[str, Any]:
    """Fetch and cache the bootstrap-static response."""
    global _bootstrap_cache, _bootstrap_cached_at
    now = time.time()
    if (
        not force_refresh
        and _bootstrap_cache is not None
        and now - _bootstrap_cached_at < _CACHE_TTL_SECONDS
    ):
        return _bootstrap_cache
    resp = requests.get(FPL_BOOTSTRAP_URL, timeout=15,
                        headers={"User-Agent": "sidelineiq/0.1"})
    resp.raise_for_status()
    _bootstrap_cache = resp.json()
    _bootstrap_cached_at = now
    logger.info("Refreshed FPL bootstrap (%d players)",
                len(_bootstrap_cache.get("elements", [])))
    return _bootstrap_cache


def find_player(name: str) -> dict | None:
    """Look up a player by approximate name match.

    Returns the raw FPL element dict, or None if not found.
    Matches against web_name first (last name usually), then first_name + second_name.
    """
    bootstrap = _get_bootstrap()
    name_lower = name.lower().strip()

    # Exact match on web_name first (handles "Saka", "Salah", "Haaland")
    for el in bootstrap["elements"]:
        if el["web_name"].lower() == name_lower:
            return el

    # Contains match
    for el in bootstrap["elements"]:
        full = f"{el['first_name']} {el['second_name']}".lower()
        if name_lower in full or el["web_name"].lower() in name_lower:
            return el

    logger.warning("FPL: no match for '%s'", name)
    return None


def find_player_id(name: str) -> int | None:
    el = find_player(name)
    return el["id"] if el else None


def get_player_fitness(name: str) -> PlayerFitness:
    """Pull current fitness status from FPL."""
    el = find_player(name)
    if not el:
        return PlayerFitness(status="unknown", chance_of_playing=100,
                             news="Player not found in FPL roster")
    return PlayerFitness(
        status=_STATUS_MAP.get(el.get("status", "a"), "unknown"),
        chance_of_playing=int(el.get("chance_of_playing_this_round") or 100),
        news=el.get("news") or "",
        news_added=el.get("news_added") or "",
    )


def _get_player_history(player_id: int) -> list[dict]:
    """Fetch gameweek-by-gameweek history for one player."""
    url = FPL_ELEMENT_URL.format(id=player_id)
    try:
        resp = requests.get(url, timeout=15,
                            headers={"User-Agent": "sidelineiq/0.1"})
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("FPL element fetch failed for %d: %s", player_id, exc)
        return []
    return resp.json().get("history", [])


def get_player_form(name: str) -> PlayerForm:
    """Compute last-5-games rolling stats + FPL form rating."""
    el = find_player(name)
    if not el:
        return PlayerForm(notes=f"Player '{name}' not found in FPL roster")

    history = _get_player_history(el["id"])
    if not history:
        return PlayerForm(
            fpl_form_rating=float(el.get("form") or 0),
            fpl_points_per_game=float(el.get("points_per_game") or 0),
            notes="No gameweek history available yet",
        )

    last_5 = history[-5:]
    return PlayerForm(
        minutes_played_last_5=sum(gw.get("minutes", 0) for gw in last_5),
        goals_last_5=sum(gw.get("goals_scored", 0) for gw in last_5),
        assists_last_5=sum(gw.get("assists", 0) for gw in last_5),
        xg_last_5=round(sum(float(gw.get("expected_goals", 0) or 0)
                            for gw in last_5), 2),
        xa_last_5=round(sum(float(gw.get("expected_assists", 0) or 0)
                            for gw in last_5), 2),
        shots_last_5=sum(gw.get("threat", 0) and 1 or 0 for gw in last_5),
        key_passes_last_5=sum(int(float(gw.get("creativity", 0) or 0) / 10)
                              for gw in last_5),  # FPL doesn't expose KP directly
        fpl_form_rating=float(el.get("form") or 0),
        fpl_points_per_game=float(el.get("points_per_game") or 0),
        notes=f"Last {len(last_5)} gameweeks from FPL history",
    )


def get_player_position(name: str) -> str:
    """Return position string. FPL element_type: 1=GK, 2=DEF, 3=MID, 4=FWD."""
    el = find_player(name)
    if not el:
        return "unknown"
    return {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}.get(el.get("element_type", 0), "unknown")


def get_team_lineup_by_team_name(team_name: str) -> list[dict]:
    """Return all players belonging to a team (by name match)."""
    bootstrap = _get_bootstrap()
    team_name_lower = team_name.lower().strip()
    team = next((t for t in bootstrap["teams"]
                 if t["name"].lower() == team_name_lower
                 or t["short_name"].lower() == team_name_lower), None)
    if not team:
        return []
    return [el for el in bootstrap["elements"] if el["team"] == team["id"]]
