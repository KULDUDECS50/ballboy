"""Pydantic schemas for player intel outputs.

Every player intel agent writes JSON conforming to one of these shapes.
Downstream consumers (synthesis agent, frontend) trust the schema.

Files this module produces (in working directory):
    player_dossiers/<player_name>.json  -- one per player
    manager_context.json                -- both managers' tactical profiles
    matchup_analyses.json               -- key 1v1 matchups for the match
    polymarket_state.json               -- live market odds (separate agent)
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Player form (FPL + FBref)
# ---------------------------------------------------------------------------

class PlayerForm(BaseModel):
    """Last 5 games stats + FPL form rating."""
    minutes_played_last_5: int = 0
    goals_last_5: int = 0
    assists_last_5: int = 0
    xg_last_5: float = 0.0
    xa_last_5: float = 0.0
    shots_last_5: int = 0
    key_passes_last_5: int = 0
    fpl_form_rating: float = 0.0      # FPL's own form score (0-10)
    fpl_points_per_game: float = 0.0
    notes: str = ""                    # Free-text observations


# ---------------------------------------------------------------------------
# Fitness status (FPL)
# ---------------------------------------------------------------------------

class PlayerFitness(BaseModel):
    status: Literal["available", "doubtful", "injured", "suspended", "unknown"] = "unknown"
    chance_of_playing: int = 100       # 0-100, FPL's own estimate
    news: str = ""                     # FPL news field (raw)
    news_added: str = ""               # When the news was posted


# ---------------------------------------------------------------------------
# Off-field intelligence (web_search agent output)
# ---------------------------------------------------------------------------

class OffFieldIntel(BaseModel):
    sentiment: Literal["positive", "neutral", "negative", "unknown"] = "unknown"
    summary: str = ""                  # 2-3 sentences, factual
    performance_risk: Literal["low", "medium", "high", "unknown"] = "unknown"
    key_signals: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    confidence: float = 0.0            # 0.0-1.0


# ---------------------------------------------------------------------------
# Tactical matchup (matchup_agent output)
# ---------------------------------------------------------------------------

class TacticalMatchup(BaseModel):
    expected_marker: str = ""          # Who's likely to mark this player
    marker_team: str = ""
    marker_position: str = ""
    edge: Literal["player", "marker", "neutral"] = "neutral"
    tactical_summary: str = ""         # 1-2 sentence analysis
    exploit_pattern: str = ""          # Specific pattern to look for
    historical_h2h: str = ""           # Past 1v1 data if available


# ---------------------------------------------------------------------------
# Full player dossier (composes the above)
# ---------------------------------------------------------------------------

class PlayerDossier(BaseModel):
    player: str
    team: str
    position: str = "unknown"          # GK, CB, FB, DM, CM, AM, W, ST, etc.
    fpl_id: int | None = None

    form: PlayerForm = Field(default_factory=PlayerForm)
    fitness: PlayerFitness = Field(default_factory=PlayerFitness)
    off_field: OffFieldIntel = Field(default_factory=OffFieldIntel)
    matchup: TacticalMatchup | None = None

    built_at: str = ""                 # ISO timestamp when dossier was built
    sources: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Manager tactical fingerprint
# ---------------------------------------------------------------------------

class ManagerProfile(BaseModel):
    name: str
    team: str
    season: str = ""

    # Tactical patterns
    avg_formation: str = ""            # "4-3-3", "3-5-2", etc.
    avg_possession_pct: float = 0.0
    ppda: float = 0.0                  # Passes per defensive action — press intensity
    avg_first_sub_minute: int = 0      # When they typically make first sub
    avg_subs_per_match: float = 0.0

    # Performance
    matches_in_season: int = 0
    win_rate: float = 0.0
    home_vs_away_split: str = ""       # "Home: 2.1 GF/match; Away: 1.4 GF/match"

    # Behavioral fingerprint (LLM-generated narrative)
    style_summary: str = ""            # 2-3 sentences on tactical identity
    sub_pattern: str = ""              # When/how they typically sub
    vs_opponent_history: str = ""      # Pattern vs this specific opponent
    expected_today: str = ""           # What they'll likely do today

    built_at: str = ""


class MatchManagerContext(BaseModel):
    """Both managers in one file for easy synthesis agent consumption."""
    home_manager: ManagerProfile
    away_manager: ManagerProfile
    built_at: str = ""


# ---------------------------------------------------------------------------
# Polymarket overlay
# ---------------------------------------------------------------------------

class PolymarketMarket(BaseModel):
    market_id: str
    question: str                      # Human-readable question
    yes_price: float                   # 0.0-1.0 implied probability
    no_price: float
    volume_24h: float = 0.0
    related_to: str = ""               # "home_win", "away_win", "next_goal", etc.


class PolymarketSnapshot(BaseModel):
    match_id: str
    markets: list[PolymarketMarket] = Field(default_factory=list)
    fetched_at: str = ""               # ISO timestamp
    source: str = "polymarket"
    note: str = ""                     # Any caveat (e.g., "no markets found for this match")
