"""Manager tactical fingerprint agent.

Produces a ManagerProfile per manager. Combines:
1. Hard stats from FBref (possession, PPDA, formation, sub timing) if available
2. GLM-5.1 narrative via Wafer for tactical style summary

Output: writes manager_context.json with both managers' profiles.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from ....llm import call_json
from . import fbref_client
from .schema import ManagerProfile, MatchManagerContext

logger = logging.getLogger(__name__)


MANAGER_PROMPT = """You are a soccer tactical analyst. Build a concise tactical fingerprint for the following manager.

Manager: {manager_name}
Team: {team}
Opponent today: {opponent}
Season: {season}

Team aggregate stats this season (from FBref):
{team_stats}

Based on what you know about this manager's tactical identity, sub patterns, and history vs this specific opponent, return ONLY this JSON:

{{
  "avg_formation": "<typical formation, e.g. '4-3-3'>",
  "avg_first_sub_minute": <integer minute, typically 55-75>,
  "avg_subs_per_match": <typical number, usually 3.0-5.0>,
  "style_summary": "<2-3 sentences describing tactical identity (e.g. high press, possession, counter-attack, low block, etc.)>",
  "sub_pattern": "<1 sentence on when/how they typically use substitutions>",
  "vs_opponent_history": "<1 sentence on patterns vs this specific opponent, or 'limited prior data' if unknown>",
  "expected_today": "<1 sentence prediction of approach today given opponent>"
}}

Be specific. Reference actual known patterns. If you have no information about a manager, say so honestly — do not invent."""


def build_manager_profile(
    manager_name: str,
    team: str,
    opponent: str,
    season: str = "2024-25",
) -> ManagerProfile:
    """Build a ManagerProfile by combining FBref stats + LLM narrative."""

    # Pull team stats from FBref
    team_stats = fbref_client.get_team_season_stats(team, season=season)
    if team_stats:
        team_stats_str = json.dumps(team_stats, indent=2)
    else:
        team_stats_str = "(FBref data unavailable — analyze from general knowledge)"

    # Call GLM-5.1 for the tactical narrative
    try:
        result = call_json(
            "You are a soccer tactical analyst. Output only valid JSON.",
            MANAGER_PROMPT.format(
                manager_name=manager_name, team=team, opponent=opponent,
                season=season, team_stats=team_stats_str,
            ),
            temperature=0.3,
            max_tokens=1500,
        )
    except Exception as exc:
        logger.warning("Manager profile LLM call failed for %s: %s", manager_name, exc)
        return ManagerProfile(
            name=manager_name, team=team, season=season,
            style_summary=f"LLM call failed: {exc}",
            built_at=datetime.now(timezone.utc).isoformat(),
        )

    return ManagerProfile(
        name=manager_name,
        team=team,
        season=season,
        avg_formation=str(result.get("avg_formation", "")),
        avg_possession_pct=float(team_stats.get("possession_pct", 0)),
        ppda=float(team_stats.get("ppda", 0)) if "ppda" in team_stats else 0.0,
        avg_first_sub_minute=int(result.get("avg_first_sub_minute", 0)),
        avg_subs_per_match=float(result.get("avg_subs_per_match", 0)),
        matches_in_season=int(team_stats.get("matches_played", 0)),
        win_rate=(team_stats.get("wins", 0) / max(team_stats.get("matches_played", 1), 1))
                 if team_stats else 0.0,
        style_summary=str(result.get("style_summary", "")),
        sub_pattern=str(result.get("sub_pattern", "")),
        vs_opponent_history=str(result.get("vs_opponent_history", "")),
        expected_today=str(result.get("expected_today", "")),
        built_at=datetime.now(timezone.utc).isoformat(),
    )


def build_match_context(
    home_manager: str, home_team: str,
    away_manager: str, away_team: str,
    season: str = "2024-25",
    output_path: str = "manager_context.json",
) -> MatchManagerContext:
    """Build profiles for both managers and write to disk."""
    logger.info("Building manager profiles: %s (%s) vs %s (%s)",
                home_manager, home_team, away_manager, away_team)

    home = build_manager_profile(home_manager, home_team, away_team, season)
    away = build_manager_profile(away_manager, away_team, home_team, season)

    ctx = MatchManagerContext(
        home_manager=home,
        away_manager=away,
        built_at=datetime.now(timezone.utc).isoformat(),
    )

    # Atomic write
    tmp = output_path + ".tmp"
    with open(tmp, "w") as f:
        f.write(ctx.model_dump_json(indent=2))
    os.replace(tmp, output_path)
    logger.info("Wrote %s", output_path)

    return ctx
