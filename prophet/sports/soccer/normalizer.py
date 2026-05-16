"""Source-specific normalizers → canonical MatchFeed.

Add a normalize_X function for each source. Keep all source-specific quirks
in here so the rest of the pipeline can trust the schema.

TODOs for you (Ruth):
  - [ ] Test normalize_espn against a real ESPN soccer summary
        (ESPN's soccer payload differs from basketball — verify key names)
  - [ ] Add normalize_football_data if you decide to use that source
  - [ ] Handle edge cases:  own goals, VAR overturns, penalty shootouts
  - [ ] If the synthesis agent gets confused by edge events, add filtering
        here (e.g., drop OFFSIDE events that don't matter tactically)
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .schema import EventKind, MatchEvent, MatchFeed, MatchMeta, Side

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ESPN soccer payload → MatchFeed
# ---------------------------------------------------------------------------

# Mapping from ESPN play type names to our canonical EventKind.
# Verify against real data — names can vary between leagues / over time.
ESPN_TYPE_MAP: dict[str, EventKind] = {
    "Goal": EventKind.GOAL,
    "Own Goal": EventKind.OWN_GOAL,
    "Penalty - Scored": EventKind.PENALTY_GOAL,
    "Penalty - Missed": EventKind.PENALTY_MISS,
    "Penalty - Saved": EventKind.PENALTY_MISS,
    "Shot on Goal": EventKind.SHOT_ON_TARGET,
    "Shot Off Goal": EventKind.SHOT_OFF_TARGET,
    "Shot Blocked": EventKind.SHOT_BLOCKED,
    "Yellow Card": EventKind.YELLOW_CARD,
    "Second Yellow Card": EventKind.SECOND_YELLOW,
    "Red Card": EventKind.RED_CARD,
    "Substitution": EventKind.SUBSTITUTION,
    "Corner Kick": EventKind.CORNER,
    "Free Kick": EventKind.FREE_KICK,
    "Offside": EventKind.OFFSIDE,
    "Foul": EventKind.FOUL,
    "Kickoff": EventKind.KICKOFF,
    "Half Time": EventKind.HALF_TIME,
    "Full Time": EventKind.FULL_TIME,
    "Injury": EventKind.INJURY,
    "Video Review": EventKind.VAR_DECISION,
}


def _parse_espn_clock(clock_str: str) -> tuple[int, int]:
    """Parse ESPN's clock like "45'+2" or "67'" into (minute, added_time)."""
    if not clock_str:
        return 0, 0
    clock_str = clock_str.replace("'", "").strip()
    if "+" in clock_str:
        base, added = clock_str.split("+", 1)
        return int(base.strip()), int(added.strip())
    try:
        return int(clock_str), 0
    except ValueError:
        return 0, 0


def normalize_espn(payload: dict, match_id: str | None = None) -> MatchFeed:
    """Convert an ESPN soccer /summary response into MatchFeed."""
    header = payload.get("header", {})
    comp = (header.get("competitions") or [{}])[0]
    competitors = comp.get("competitors", [])
    home_c = next((c for c in competitors if c.get("homeAway") == "home"), {})
    away_c = next((c for c in competitors if c.get("homeAway") == "away"), {})
    home_team = (home_c.get("team") or {}).get("displayName", "Home")
    away_team = (away_c.get("team") or {}).get("displayName", "Away")

    match_id = match_id or str(comp.get("id", "unknown"))

    meta = MatchMeta(
        match_id=match_id,
        competition=(payload.get("league", {}) or {}).get("name", ""),
        home_team=home_team,
        away_team=away_team,
        venue=(comp.get("venue") or {}).get("fullName", ""),
        status=_map_espn_status(comp.get("status", {})),
    )

    # ESPN puts the event timeline in either 'plays' or 'commentary' depending on sport.
    raw_events = payload.get("commentary") or payload.get("plays") or []

    events: list[MatchEvent] = []
    home_score = away_score = 0
    for i, raw in enumerate(raw_events, start=1):
        kind_str = (raw.get("type") or {}).get("text", "") or raw.get("text", "")
        kind = ESPN_TYPE_MAP.get(kind_str, EventKind.OTHER)

        clock_str = (raw.get("clock") or {}).get("displayValue", "")
        minute, added = _parse_espn_clock(clock_str)

        # Determine side from team field
        team_name = (raw.get("team") or {}).get("displayName", "")
        if team_name == home_team:
            side = Side.HOME
        elif team_name == away_team:
            side = Side.AWAY
        else:
            side = Side.NEUTRAL

        # Track running score from goal events
        if kind in {EventKind.GOAL, EventKind.PENALTY_GOAL}:
            if side == Side.HOME:
                home_score += 1
            elif side == Side.AWAY:
                away_score += 1
        elif kind == EventKind.OWN_GOAL:
            # Own goals credit the OTHER team
            if side == Side.HOME:
                away_score += 1
            elif side == Side.AWAY:
                home_score += 1

        # Sub events have two participants
        participants = raw.get("participants") or []
        player_in = player_out = ""
        if kind == EventKind.SUBSTITUTION and len(participants) >= 2:
            player_in = (participants[0].get("athlete") or {}).get("displayName", "")
            player_out = (participants[1].get("athlete") or {}).get("displayName", "")
        elif participants:
            player_out = (participants[0].get("athlete") or {}).get("displayName", "")

        period = int((raw.get("period") or {}).get("number", 1))

        events.append(MatchEvent(
            match_id=match_id,
            sequence=i,
            source="espn",
            minute=minute,
            added_time=added,
            period=period,
            kind=kind,
            text=raw.get("text", ""),
            side=side,
            team_name=team_name,
            player_in=player_in,
            player_out=player_out,
            home_score=home_score,
            away_score=away_score,
            extras={"raw_type": kind_str},
        ))

    return MatchFeed(meta=meta, events=events)


def _map_espn_status(status: dict) -> str:
    state = (status.get("type") or {}).get("state", "")
    return {
        "pre": "scheduled",
        "in": "in_progress",
        "post": "finished",
    }.get(state, "scheduled")


# ---------------------------------------------------------------------------
# Mock payload → MatchFeed (passthrough since mock is already in our shape)
# ---------------------------------------------------------------------------

def normalize_mock(payload: dict) -> MatchFeed:
    """Mock payloads are already in MatchFeed shape — just validate."""
    return MatchFeed.model_validate(payload)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def normalize(source: str, payload: dict, **kwargs) -> MatchFeed:
    if source == "espn":
        return normalize_espn(payload, match_id=kwargs.get("match_id"))
    if source == "mock":
        return normalize_mock(payload)
    # TODO: football-data, API-Football
    raise ValueError(f"No normalizer for source: {source}")
