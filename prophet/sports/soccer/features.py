"""Feature engineering for soccer match state.

Given a MatchFeed (events so far), produces a rolling MatchSnapshot containing
tactical features. The synthesis agent (Qwen / GLM) reads these snapshots
instead of raw events — features are pre-digested signal.

Design principle: features must be EXPLAINABLE. The synthesis agent will
include them in its reasoning ("Manchester United have generated 3 shots
in the last 10 minutes, but only 0.4 xG..."). Numerical features with clear
meanings beat clever black-box embeddings here.

TODOs for you (Ruth):
  - [ ] Tune the rolling windows (5/10/15 min) — depends on demo pace
  - [ ] Add xG aggregation if your data source provides shot xG values
  - [ ] Add a "set piece pressure" feature using corner/free-kick locations
  - [ ] Add player workload tracking (minutes played per starter)
  - [ ] If you have lineup data, expose 'substitution candidates' (oldest,
        on yellow, lowest activity in last 15 min)
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .schema import EventKind, MatchEvent, MatchFeed, Side


# A 'shot' is anything where the ball went towards goal.
SHOT_EVENTS = {
    EventKind.GOAL,
    EventKind.PENALTY_GOAL,
    EventKind.SHOT_ON_TARGET,
    EventKind.SHOT_OFF_TARGET,
    EventKind.SHOT_BLOCKED,
}

# 'Dangerous events' for momentum: shots, corners, free kicks in dangerous areas.
DANGEROUS_EVENTS = SHOT_EVENTS | {EventKind.CORNER, EventKind.FREE_KICK}


@dataclass
class TeamFeatures:
    """Features for one team. Mirror this for home/away in MatchSnapshot."""

    name: str
    score: int = 0
    shots_total: int = 0
    shots_on_target: int = 0
    shots_last_10: int = 0
    shots_last_5: int = 0
    corners: int = 0
    yellow_cards: int = 0
    red_cards: int = 0
    subs_used: int = 0
    # Players still considered fresh (haven't been on long). For demo: just
    # count subs used. With lineup data, you could track individual minutes.
    momentum_score: float = 0.0  # Derived field, see compute_momentum()
    # Pressure indicator: dangerous events in last 10 minutes
    pressure_last_10: int = 0


@dataclass
class MatchSnapshot:
    """The state at one moment in time. The synthesis agent reads this."""

    match_id: str
    minute: int                       # Current match minute (0..120)
    period: int                       # 1, 2, 3, 4, 5
    home: TeamFeatures
    away: TeamFeatures
    score_diff: int                   # home.score - away.score
    recent_events_text: list[str] = field(default_factory=list)
    # Tactical state flags — derived booleans the synthesis agent can latch onto
    is_late_close_game: bool = False
    is_first_half: bool = True
    is_second_half_window: bool = False  # 60-75', the classic sub window
    home_pressure_advantage: float = 0.0  # Positive = home pressuring

    def as_prompt_block(self) -> str:
        """Render as a markdown-ish block suitable for the synthesis prompt."""
        h, a = self.home, self.away
        lines = [
            f"## Match state @ {self.minute}'",
            f"**{h.name} {h.score} - {a.score} {a.name}**",
            "",
            f"### Last 10 minutes",
            f"- {h.name} shots: {h.shots_last_10}   (on target so far: {h.shots_on_target})",
            f"- {a.name} shots: {a.shots_last_10}   (on target so far: {a.shots_on_target})",
            f"- {h.name} dangerous events: {h.pressure_last_10}",
            f"- {a.name} dangerous events: {a.pressure_last_10}",
            "",
            f"### Cards & subs",
            f"- {h.name}: {h.yellow_cards}Y {h.red_cards}R — subs used {h.subs_used}/5",
            f"- {a.name}: {a.yellow_cards}Y {a.red_cards}R — subs used {a.subs_used}/5",
            "",
            f"### Tactical context",
            f"- Phase: {'1st half' if self.is_first_half else '2nd half'}"
            f"{' — IN SUB WINDOW (60-75)' if self.is_second_half_window else ''}",
            f"- Score state: {'leveled' if self.score_diff == 0 else f'{h.name if self.score_diff > 0 else a.name} +{abs(self.score_diff)}'}",
            f"- Late & close: {'YES' if self.is_late_close_game else 'no'}",
            f"- Pressure advantage: {self.home_pressure_advantage:+.1f} (home positive)",
            "",
            f"### Recent events",
        ]
        for ev in self.recent_events_text[-6:]:
            lines.append(f"- {ev}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Feature computation
# ---------------------------------------------------------------------------

def compute_features(feed: MatchFeed, *, up_to_minute: int | None = None) -> MatchSnapshot:
    """Build a MatchSnapshot from the events seen so far.

    Args:
        feed: All events up to now.
        up_to_minute: If set, ignore events after this minute (useful for
                      replaying historical matches frame-by-frame).
    """
    meta = feed.meta
    events = feed.events
    if up_to_minute is not None:
        events = [e for e in events if e.minute + e.added_time / 60.0 <= up_to_minute]

    if not events:
        # Pre-match — return an empty snapshot
        return MatchSnapshot(
            match_id=meta.match_id,
            minute=0,
            period=1,
            home=TeamFeatures(name=meta.home_team),
            away=TeamFeatures(name=meta.away_team),
            score_diff=0,
        )

    current = events[-1]
    minute = current.minute + (current.added_time or 0) / 60.0
    minute_int = int(minute)
    period = current.period

    home = TeamFeatures(name=meta.home_team, score=current.home_score)
    away = TeamFeatures(name=meta.away_team, score=current.away_score)

    # Aggregate over the whole match for totals
    for ev in events:
        team_f = home if ev.side == Side.HOME else (away if ev.side == Side.AWAY else None)
        if team_f is None:
            continue
        if ev.kind in SHOT_EVENTS:
            team_f.shots_total += 1
            if ev.kind in {EventKind.GOAL, EventKind.PENALTY_GOAL, EventKind.SHOT_ON_TARGET}:
                team_f.shots_on_target += 1
        if ev.kind == EventKind.CORNER:
            team_f.corners += 1
        if ev.kind == EventKind.YELLOW_CARD:
            team_f.yellow_cards += 1
        if ev.kind in {EventKind.RED_CARD, EventKind.SECOND_YELLOW}:
            team_f.red_cards += 1
        if ev.kind == EventKind.SUBSTITUTION:
            team_f.subs_used += 1

    # Rolling windows: last 10 minutes and last 5 minutes
    cutoff_10 = minute - 10
    cutoff_5 = minute - 5
    for ev in events:
        ev_minute = ev.minute + (ev.added_time or 0) / 60.0
        team_f = home if ev.side == Side.HOME else (away if ev.side == Side.AWAY else None)
        if team_f is None:
            continue
        if ev_minute >= cutoff_10:
            if ev.kind in SHOT_EVENTS:
                team_f.shots_last_10 += 1
            if ev.kind in DANGEROUS_EVENTS:
                team_f.pressure_last_10 += 1
        if ev_minute >= cutoff_5 and ev.kind in SHOT_EVENTS:
            team_f.shots_last_5 += 1

    # Derived: momentum score, pressure advantage
    home.momentum_score = _compute_momentum(home)
    away.momentum_score = _compute_momentum(away)
    pressure_advantage = home.pressure_last_10 - away.pressure_last_10

    # Tactical state flags
    is_late_close_game = (minute_int >= 75) and abs(home.score - away.score) <= 1
    is_first_half = period == 1
    is_second_half_window = 60 <= minute_int <= 75

    # Recent events text (last 6, most recent last)
    recent = [_format_event(ev) for ev in events[-10:]]

    return MatchSnapshot(
        match_id=meta.match_id,
        minute=minute_int,
        period=period,
        home=home,
        away=away,
        score_diff=home.score - away.score,
        recent_events_text=recent,
        is_late_close_game=is_late_close_game,
        is_first_half=is_first_half,
        is_second_half_window=is_second_half_window,
        home_pressure_advantage=float(pressure_advantage),
    )


def _compute_momentum(team: TeamFeatures) -> float:
    """Simple momentum: weighted recent shots + pressure events.

    Heuristic — replace with regression on historical data when you have it.
    """
    return team.shots_last_5 * 2.0 + (team.pressure_last_10 - team.shots_last_5) * 1.0


def _format_event(ev: MatchEvent) -> str:
    time = f"{ev.minute}'" + (f"+{ev.added_time}" if ev.added_time else "")
    actor = ev.team_name if ev.team_name else "—"
    return f"{time} [{ev.kind.value}] {actor}: {ev.text}"


# ---------------------------------------------------------------------------
# Streaming helper: replay a MatchFeed minute-by-minute
# ---------------------------------------------------------------------------

def iter_snapshots(feed: MatchFeed, stride: int = 1):
    """Yield MatchSnapshot at each `stride`-minute increment.

    Useful for building the demo where the synthesis agent processes
    the match in time-ordered chunks.
    """
    if not feed.events:
        return
    max_minute = max(
        (e.minute + (e.added_time or 0) / 60.0) for e in feed.events
    )
    cur = 0
    while cur <= max_minute + 1:
        yield compute_features(feed, up_to_minute=cur)
        cur += stride
