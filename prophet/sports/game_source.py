"""GameDataSource — unified interface for replay and live game data.

Both modes yield plays one at a time so the analysis pipeline doesn't
know which is which.

Replay mode: reads a cached game file, paces plays by game clock × speedup.
Live mode: polls ESPN's summary endpoint every poll_interval and yields
           new plays as they appear.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import AsyncIterator, Literal

from . import espn

logger = logging.getLogger(__name__)


class GameDataSource:
    """Unified async iterator over game plays."""

    def __init__(
        self,
        *,
        mode: Literal["replay", "live"],
        sport: str = "nba",
        cached_path: str | Path | None = None,
        event_id: str | None = None,
        speedup: float = 30.0,            # Replay only
        poll_interval_s: float = 15.0,    # Live only
        max_play_gap_s: float = 5.0,      # Cap replay sleep to keep it snappy
    ):
        self.mode = mode
        self.sport = sport
        self.cached_path = Path(cached_path) if cached_path else None
        self.event_id = event_id
        self.speedup = max(1.0, speedup)
        self.poll_interval_s = poll_interval_s
        self.max_play_gap_s = max_play_gap_s

        if mode == "replay" and self.cached_path is None:
            raise ValueError("Replay mode needs cached_path")
        if mode == "live" and self.event_id is None:
            raise ValueError("Live mode needs event_id")

        self._seen_play_ids: set[str] = set()
        self.game_meta: dict = {}

    # ---------------------------------------------------------------- public

    def load_meta(self) -> dict:
        """Load game metadata (teams, expected length, etc.) before streaming."""
        if self.mode == "replay":
            data = espn.load_cached_game(self.cached_path)
            self.game_meta = {
                "sport": data["sport"],
                "event_id": data["event_id"],
                "name": data.get("name", ""),
                "home_team": data["home_team"],
                "home_team_name": data.get("home_team_name", data["home_team"]),
                "away_team": data["away_team"],
                "away_team_name": data.get("away_team_name", data["away_team"]),
                "n_plays": data.get("n_plays", len(data.get("plays", []))),
            }
            self._cached_data = data
            return self.game_meta

        # Live mode — fetch once to populate meta.
        summary = espn.get_summary(self.sport, self.event_id)
        header = summary.get("header", {})
        comp = (header.get("competitions") or [{}])[0]
        competitors = comp.get("competitors", [])
        home = next((c for c in competitors if c.get("homeAway") == "home"), {})
        away = next((c for c in competitors if c.get("homeAway") == "away"), {})
        self.game_meta = {
            "sport": self.sport,
            "event_id": self.event_id,
            "name": comp.get("name", ""),
            "home_team": (home.get("team") or {}).get("abbreviation", ""),
            "home_team_name": (home.get("team") or {}).get("displayName", ""),
            "away_team": (away.get("team") or {}).get("abbreviation", ""),
            "away_team_name": (away.get("team") or {}).get("displayName", ""),
            "n_plays": 0,
        }
        return self.game_meta

    async def stream(self) -> AsyncIterator[dict]:
        """Yield plays one at a time."""
        if not self.game_meta:
            self.load_meta()

        if self.mode == "replay":
            async for play in self._stream_replay():
                yield play
        else:
            async for play in self._stream_live():
                yield play

    # --------------------------------------------------------------- private

    async def _stream_replay(self) -> AsyncIterator[dict]:
        plays = self._cached_data["plays"]
        prev_play: dict | None = None

        for play in plays:
            # Pace by game clock differential.
            if prev_play is not None:
                gap = self._game_time_gap(prev_play, play) / self.speedup
                gap = min(gap, self.max_play_gap_s)
                if gap > 0:
                    await asyncio.sleep(gap)
            prev_play = play
            yield play

    async def _stream_live(self) -> AsyncIterator[dict]:
        """Poll ESPN, yield only newly-appeared plays."""
        while True:
            try:
                summary = espn.get_summary(self.sport, self.event_id)
            except Exception as exc:
                logger.warning("Live fetch failed: %s — will retry", exc)
                await asyncio.sleep(self.poll_interval_s)
                continue

            plays = espn.get_plays(summary)
            new_plays = [p for p in plays if p["id"] not in self._seen_play_ids]
            for p in new_plays:
                self._seen_play_ids.add(p["id"])
                yield p

            # Detect game end via status.
            status = (summary.get("header", {})
                      .get("competitions", [{}])[0]
                      .get("status", {})
                      .get("type", {})
                      .get("state", ""))
            if status == "post":
                logger.info("Live game complete.")
                return

            await asyncio.sleep(self.poll_interval_s)

    def _game_time_gap(self, prev: dict, curr: dict) -> float:
        """Seconds of game clock between two plays (positive)."""
        prev_period = prev.get("period", 1)
        curr_period = curr.get("period", 1)
        # If same period, just take clock difference (clock counts DOWN).
        if prev_period == curr_period:
            return max(0.0, prev["clock_seconds"] - curr["clock_seconds"])
        # Otherwise: time left in prev's period + a full period for each
        # intervening period + (period length - curr.clock_seconds).
        from .win_probability import NBA_PERIOD_LENGTH
        per_len = NBA_PERIOD_LENGTH
        gap = prev["clock_seconds"]                          # finish prev period
        gap += (curr_period - prev_period - 1) * per_len     # intervening periods
        gap += per_len - curr["clock_seconds"]               # into current period
        return max(0.0, gap)
