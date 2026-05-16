"""Tactical matchup analyzer.

Given a player and their expected marker, calls GLM-5.1 to produce a tactical
matchup analysis: who has the edge, what pattern to exploit, historical h2h
context.

Used by dossier_builder.py to attach matchup info to each starter.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ....llm import call_json
from .schema import TacticalMatchup

logger = logging.getLogger(__name__)


# Position → expected marker position
# Used as a heuristic to identify who likely marks whom.
EXPECTED_MARKER = {
    "FWD": ["DEF"],           # Striker marked by CB
    "MID": ["MID"],           # Midfielders matched up
    "DEF": ["FWD"],           # CB marks striker
    "GK": [],                 # GK has no direct marker
}


MATCHUP_PROMPT = """You are a soccer tactical analyst. Analyze the 1v1 matchup between these two players.

PLAYER (offensive intent):
- Name: {player_name}
- Team: {player_team}
- Position: {player_position}
- Recent form (last 5 games): {player_form}

EXPECTED MARKER:
- Name: {marker_name}
- Team: {marker_team}
- Position: {marker_position}
- Recent form: {marker_form}

Return ONLY this JSON (no markdown fences):

{{
  "edge": "player" | "marker" | "neutral",
  "tactical_summary": "<1-2 sentences on who wins this matchup and why>",
  "exploit_pattern": "<1 sentence on a specific pattern to look for that exploits the edge>",
  "historical_h2h": "<1 sentence on past matchups if you have data, or 'limited prior data' if not>"
}}

Be specific. Reference actual known traits (pace, stamina, technical ability, defensive tendencies). Do not invent statistics."""


def analyze_matchup(
    player_name: str, player_team: str, player_position: str,
    player_form_summary: str,
    marker_name: str, marker_team: str, marker_position: str,
    marker_form_summary: str = "limited data",
) -> TacticalMatchup:
    """Run a single matchup analysis. Returns a TacticalMatchup dataclass."""
    try:
        result = call_json(
            "You are a soccer tactical analyst. Output only valid JSON.",
            MATCHUP_PROMPT.format(
                player_name=player_name,
                player_team=player_team,
                player_position=player_position,
                player_form=player_form_summary,
                marker_name=marker_name,
                marker_team=marker_team,
                marker_position=marker_position,
                marker_form=marker_form_summary,
            ),
            temperature=0.3,
            max_tokens=1500,
        )
    except Exception as exc:
        logger.warning("Matchup LLM call failed for %s vs %s: %s",
                       player_name, marker_name, exc)
        return TacticalMatchup(
            expected_marker=marker_name,
            marker_team=marker_team,
            marker_position=marker_position,
            edge="neutral",
            tactical_summary=f"Analysis failed: {exc}",
            exploit_pattern="",
            historical_h2h="",
        )

    return TacticalMatchup(
        expected_marker=marker_name,
        marker_team=marker_team,
        marker_position=marker_position,
        edge=str(result.get("edge", "neutral")) if result.get("edge") in
              ("player", "marker", "neutral") else "neutral",
        tactical_summary=str(result.get("tactical_summary", "")),
        exploit_pattern=str(result.get("exploit_pattern", "")),
        historical_h2h=str(result.get("historical_h2h", "")),
    )


def find_likely_marker(player_position: str, opposition_players: list[dict]) -> dict | None:
    """Heuristic: given a player's position and the opposition lineup,
    pick the most-likely marker.

    Args:
        player_position: 'FWD', 'MID', 'DEF', 'GK'
        opposition_players: list of dicts with 'name', 'position' (FPL format)

    Returns:
        Best matching opposition player dict, or None.
    """
    if not opposition_players:
        return None
    target_positions = EXPECTED_MARKER.get(player_position, [])
    candidates = [p for p in opposition_players
                  if p.get("position", "") in target_positions]
    if not candidates:
        # Fall back: anyone is better than nothing
        candidates = opposition_players
    # Return the first candidate (could improve with ratings/specificity)
    return candidates[0]
