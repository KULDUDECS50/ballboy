"""Pipeline orchestrator — runs a game (replay or live) through the full stack.

For each play:
  1. Update game state
  2. Compute new win probability (classical, fast)
  3. Decide if this is a high-leverage moment
  4. Generate commentary (deep if high-leverage, fast otherwise)
  5. Yield a structured event for the frontend / WebSocket / audio pipeline

Designed to be consumed as an async generator. The WebSocket endpoint and
the CLI demo both subscribe to this.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import AsyncIterator

from .commentary import commentate
from .game_source import GameDataSource
from .win_probability import (
    GameState,
    is_high_leverage,
    leverage,
    nba_win_probability,
    state_from_play,
)

logger = logging.getLogger(__name__)


@dataclass
class GameEvent:
    """One step in the live broadcast. Sent as a JSON message to the frontend."""

    seq: int
    period: int
    clock: str
    home_team: str
    home_score: int
    away_team: str
    away_score: int
    play_text: str
    play_type: str
    wp_home: float                  # P(home wins) AFTER this play
    wp_home_prev: float             # P(home wins) BEFORE
    leverage: float                 # |wp swing|
    is_high_leverage: bool
    commentary: str
    deep_commentary: bool
    leverage_note: str = ""


def _format_clock(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


async def run_game_pipeline(
    source: GameDataSource,
    *,
    leverage_threshold: float = 0.05,
) -> AsyncIterator[GameEvent]:
    """Yield GameEvent objects as the game progresses."""
    source.load_meta()
    meta = source.game_meta
    home_team = meta["home_team"]
    away_team = meta["away_team"]
    logger.info("Pipeline started: %s @ %s (mode=%s)",
                away_team, home_team, source.mode)

    prev_wp_home = 0.5    # Start of game: roughly neutral with home edge
    recent_plays: list[dict] = []
    seq = 0

    async for play in source.stream():
        seq += 1
        state = state_from_play(play, home_team=home_team, away_team=away_team)
        new_wp = nba_win_probability(state)
        lev = leverage(prev_wp_home, new_wp)
        high = is_high_leverage(state, lev, threshold_swing=leverage_threshold)

        comm = commentate(
            play=play,
            state=state,
            prev_wp_home=prev_wp_home,
            new_wp_home=new_wp,
            recent_plays=recent_plays,
            deep=high,
        )

        event = GameEvent(
            seq=seq,
            period=state.period,
            clock=_format_clock(state.clock_seconds),
            home_team=home_team,
            home_score=state.home_score,
            away_team=away_team,
            away_score=state.away_score,
            play_text=play.get("text", ""),
            play_type=play.get("type", ""),
            wp_home=round(new_wp, 4),
            wp_home_prev=round(prev_wp_home, 4),
            leverage=round(lev, 4),
            is_high_leverage=high,
            commentary=comm.line,
            deep_commentary=comm.deep,
            leverage_note=comm.leverage_note,
        )
        yield event

        # Maintain rolling context.
        prev_wp_home = new_wp
        recent_plays.append(play)
        if len(recent_plays) > 10:
            recent_plays = recent_plays[-10:]

    logger.info("Pipeline finished after %d plays.", seq)
