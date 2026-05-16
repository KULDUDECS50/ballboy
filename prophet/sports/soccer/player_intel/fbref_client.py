"""FBref client via the soccerdata package.

FBref has the deepest free public soccer stats — xG, xA, progressive passes,
defensive actions, manager career stats. We use the `soccerdata` Python
package which scrapes and caches FBref data.

GRACEFUL DEGRADATION
--------------------
If soccerdata isn't installed (it has heavy pandas dependencies), this module
returns empty dicts. FPL data alone is still usable for the dossier.

To install:  pip install soccerdata
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    import soccerdata as sd
    _HAS_SOCCERDATA = True
except ImportError:
    logger.info("soccerdata not installed — FBref features will be unavailable. "
                "Install with: pip install soccerdata")
    _HAS_SOCCERDATA = False


def is_available() -> bool:
    return _HAS_SOCCERDATA


_fbref_cache: dict[str, Any] = {}


def _get_fbref(season: str = "2024-25", league: str = "ENG-Premier League"):
    if not _HAS_SOCCERDATA:
        return None
    key = f"{league}_{season}"
    if key not in _fbref_cache:
        try:
            _fbref_cache[key] = sd.FBref(leagues=league, seasons=season)
        except Exception as exc:
            logger.warning("FBref init failed: %s", exc)
            return None
    return _fbref_cache[key]


def get_team_season_stats(team_name: str, season: str = "2024-25") -> dict:
    """Pull aggregate stats for a team's season — possession, PPDA, xG, etc."""
    fbref = _get_fbref(season=season)
    if not fbref:
        return {}
    try:
        df = fbref.read_team_season_stats()
        # Match on team name (case-insensitive, contains)
        match = df.index.get_level_values("team").str.lower().str.contains(team_name.lower())
        if not match.any():
            return {}
        row = df[match].iloc[0]
        return {
            "team": team_name,
            "possession_pct": float(row.get("Poss", 0) or 0),
            "goals_for": int(row.get("GF", 0) or 0),
            "goals_against": int(row.get("GA", 0) or 0),
            "xg_for": float(row.get("xG", 0) or 0),
            "xg_against": float(row.get("xGA", 0) or 0),
            "wins": int(row.get("W", 0) or 0),
            "draws": int(row.get("D", 0) or 0),
            "losses": int(row.get("L", 0) or 0),
            "matches_played": int(row.get("MP", 0) or 0),
        }
    except Exception as exc:
        logger.warning("FBref team stats failed for %s: %s", team_name, exc)
        return {}


def get_player_season_stats(player_name: str, team_name: str = "",
                             season: str = "2024-25") -> dict:
    """Pull a player's aggregate season stats from FBref standard table."""
    fbref = _get_fbref(season=season)
    if not fbref:
        return {}
    try:
        df = fbref.read_player_season_stats(stat_type="standard")
        # Match by player name; team filter helps disambiguate
        df = df.reset_index()
        mask = df["player"].str.lower().str.contains(player_name.lower(), na=False)
        if team_name:
            mask &= df["team"].str.lower().str.contains(team_name.lower(), na=False)
        rows = df[mask]
        if rows.empty:
            return {}
        row = rows.iloc[0]
        return {
            "player": str(row.get("player", "")),
            "team": str(row.get("team", "")),
            "position": str(row.get("position", "")),
            "minutes": int(row.get("minutes", 0) or 0),
            "goals": int(row.get("goals", 0) or 0),
            "assists": int(row.get("assists", 0) or 0),
            "xg": float(row.get("xg", 0) or 0),
            "xa": float(row.get("xa", 0) or 0),
        }
    except Exception as exc:
        logger.warning("FBref player stats failed for %s: %s", player_name, exc)
        return {}
