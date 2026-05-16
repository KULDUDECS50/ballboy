"""Canonical soccer event schema.

Every fetcher (ESPN, football-data.org, API-Football, mock) MUST output
events that conform to this schema. The synthesis agent and feature pipeline
trust the schema completely — they do not handle source-specific quirks.

If you add a new data source, write its events through the normalizer in
normalizer.py, not directly.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class EventKind(str, Enum):
    """Standardized event types. Add new ones here, not in source-specific code."""

    GOAL = "goal"
    OWN_GOAL = "own_goal"
    PENALTY_GOAL = "penalty_goal"
    PENALTY_MISS = "penalty_miss"
    SHOT_ON_TARGET = "shot_on_target"
    SHOT_OFF_TARGET = "shot_off_target"
    SHOT_BLOCKED = "shot_blocked"
    YELLOW_CARD = "yellow_card"
    SECOND_YELLOW = "second_yellow"
    RED_CARD = "red_card"
    SUBSTITUTION = "substitution"
    CORNER = "corner"
    FREE_KICK = "free_kick"
    OFFSIDE = "offside"
    FOUL = "foul"
    KICKOFF = "kickoff"
    HALF_TIME = "half_time"
    FULL_TIME = "full_time"
    VAR_DECISION = "var_decision"
    INJURY = "injury"
    OTHER = "other"


class Side(str, Enum):
    HOME = "home"
    AWAY = "away"
    NEUTRAL = "neutral"


class MatchEvent(BaseModel):
    """One normalized event from a soccer match."""

    # Identity
    match_id: str
    sequence: int                     # 1-indexed order across the match
    source: str                       # "espn" | "football-data" | "mock" | ...

    # When
    minute: int                       # 0..120 (regulation + ET)
    added_time: int = 0               # Stoppage time, e.g. minute=45 added_time=2 → "45+2'"
    timestamp: datetime | None = None # Wall-clock if available
    period: int = 1                   # 1 = 1st half, 2 = 2nd half, 3/4 = ET halves, 5 = penalties

    # What
    kind: EventKind
    text: str = ""                    # Human-readable description

    # Who
    side: Side = Side.NEUTRAL         # Which team this affects
    team_name: str = ""
    player_in: str = ""               # For subs: the player coming on
    player_out: str = ""              # For subs (and most other events: the actor)

    # State after this event
    home_score: int = 0
    away_score: int = 0

    # Extra (geometry, xG, etc. — source-dependent, optional)
    extras: dict[str, Any] = Field(default_factory=dict)


class MatchMeta(BaseModel):
    """Header info about a match. Used to bootstrap the feature pipeline."""

    match_id: str
    competition: str = ""
    home_team: str
    away_team: str
    venue: str = ""
    kickoff: datetime | None = None
    status: Literal["scheduled", "in_progress", "finished", "postponed"] = "scheduled"
    home_lineup: list[str] = Field(default_factory=list)
    away_lineup: list[str] = Field(default_factory=list)
    home_formation: str = ""
    away_formation: str = ""


class MatchFeed(BaseModel):
    """Top-level wrapper: meta + ordered events."""

    meta: MatchMeta
    events: list[MatchEvent] = Field(default_factory=list)
