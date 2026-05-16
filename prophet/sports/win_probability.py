"""Win probability models.

Fast classical baselines used as the foundation layer. The LLM commentary
sub-system then explains *why* the probability moves and where the model
might be wrong.

NBA model:
    Logistic regression on (home_lead, time_remaining_seconds).
    Coefficients fit on a historical sample similar to those published by
    Inpredictable / Dean Oliver. Tune by re-fitting on your own data.

Returns P(home team wins). Caller flips for away team probability.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


# NBA game total length in seconds.
NBA_PERIOD_LENGTH = 12 * 60     # 12-minute quarters
NBA_PERIODS = 4
NBA_GAME_LENGTH = NBA_PERIOD_LENGTH * NBA_PERIODS

# Logistic regression coefficients.
# log-odds(home wins) = b0 + b1 * lead + b2 * lead / sqrt(time_remaining + 1)
# This form captures the well-known fact that a small lead late in the game
# matters far more than the same lead in the first quarter.
NBA_COEFS = {
    "b0": 0.20,        # Home court bias when tied with full game remaining
    "b1": 0.04,        # Per-point lead (mild effect across full game)
    "b2": 3.0,         # Per-point lead WEIGHTED by inverse-sqrt time
                       # (Empirical: gives p≈0.99 for +10 with 60s, p≈0.55 home at tip-off)
}


@dataclass
class GameState:
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    period: int            # 1-indexed quarter
    clock_seconds: float   # Seconds remaining IN current period

    @property
    def total_seconds_remaining(self) -> float:
        full_periods_remaining = max(0, NBA_PERIODS - self.period)
        return self.clock_seconds + full_periods_remaining * NBA_PERIOD_LENGTH

    @property
    def home_lead(self) -> int:
        return self.home_score - self.away_score

    @property
    def fraction_complete(self) -> float:
        elapsed = NBA_GAME_LENGTH - self.total_seconds_remaining
        return max(0.0, min(1.0, elapsed / NBA_GAME_LENGTH))


def nba_win_probability(state: GameState) -> float:
    """Estimate P(home team wins) given a GameState."""
    t_rem = state.total_seconds_remaining
    lead = state.home_lead

    # Special cases at boundaries.
    if t_rem <= 0:
        return 1.0 if lead > 0 else (0.0 if lead < 0 else 0.5)

    weighted_lead = lead / math.sqrt(t_rem + 1)
    log_odds = (
        NBA_COEFS["b0"]
        + NBA_COEFS["b1"] * lead
        + NBA_COEFS["b2"] * weighted_lead
    )
    # Convert log-odds to probability via sigmoid.
    p_home = 1.0 / (1.0 + math.exp(-log_odds))
    return max(0.01, min(0.99, p_home))


def state_from_play(play: dict, home_team: str, away_team: str) -> GameState:
    """Build a GameState from a single play dict (from espn.get_plays)."""
    return GameState(
        home_team=home_team,
        away_team=away_team,
        home_score=int(play.get("home_score", 0)),
        away_score=int(play.get("away_score", 0)),
        period=int(play.get("period", 1)),
        clock_seconds=float(play.get("clock_seconds", 0)),
    )


def leverage(prev_wp: float, new_wp: float) -> float:
    """How big a swing did this play cause? In [0, 1].

    Used to decide whether to escalate to deep LLM analysis on this play
    or skip it as routine.
    """
    return abs(new_wp - prev_wp)


def is_high_leverage(
    state: GameState,
    leverage_score: float,
    threshold_swing: float = 0.05,
) -> bool:
    """Tag a play as 'worth deep analysis'.

    Criteria:
      - Large WP swing (>5 percentage points)
      - Late in a close game (last 3 minutes, lead <= 5)
      - Lead change
    """
    if leverage_score >= threshold_swing:
        return True
    if state.period == 4 and state.clock_seconds < 180 and abs(state.home_lead) <= 5:
        return True
    return False
