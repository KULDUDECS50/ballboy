"""Central configuration for the Prophet agent.

All tunable knobs live here. Override via environment variables.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------
# Primary model for the Judge agent and single-pass forecasts.
PRIMARY_MODEL = os.environ.get("PROPHET_PRIMARY_MODEL", "claude-sonnet-4-5")
# Lighter model for Bull/Bear agents and difficulty classification.
# Using the same model is fine; using a smaller one saves cost.
FAST_MODEL = os.environ.get("PROPHET_FAST_MODEL", "claude-haiku-4-5")

# Wafer override — if set, all LLM calls go through Wafer's compatible API.
# Their API mirrors OpenAI's, so we use the anthropic client with a custom
# base URL only if WAFER_BASE_URL is set, else default Anthropic.
WAFER_BASE_URL = os.environ.get("WAFER_BASE_URL")  # e.g. https://api.wafer.ai/v1
WAFER_API_KEY = os.environ.get("WAFER_API_KEY")

# ---------------------------------------------------------------------------
# Inference budgets — how much compute per difficulty tier
# ---------------------------------------------------------------------------
BUDGET_EASY_SAMPLES = 1
BUDGET_MEDIUM_SAMPLES = 1
BUDGET_HARD_SAMPLES = 5            # Self-consistency sampling on hard questions
DEBATE_ROUNDS_HARD = 2             # Two-round Bull/Bear exchange before Judge

# ---------------------------------------------------------------------------
# Category → routing decisions
# ---------------------------------------------------------------------------
CATEGORY_ROUTING = {
    "Economics": "QUANTITATIVE",
    "Financials": "QUANTITATIVE",
    "Sports": "RECURRING",
    "Politics": "EVENT_DRIVEN",
    "World": "EVENT_DRIVEN",
    "Entertainment": "EVENT_DRIVEN",
    "Science and Technology": "SCIENTIFIC",
    "Climate and Weather": "SCIENTIFIC",
}

# Aggregator weights when combining sub-system outputs. These are STARTING
# points — once we have backtest data, the live-learning loop tunes them.
DEFAULT_WEIGHTS = {
    "time_series": 0.4,
    "council": 0.5,
    "base_rate": 0.6,           # When base_rate is confident, lean on it heavily
}

# Clipping — Prophet Arena rejects p_yes outside [0.01, 0.99].
MIN_P = 0.01
MAX_P = 0.99


def clip(p: float) -> float:
    """Clip a probability to the allowed range."""
    return max(MIN_P, min(MAX_P, p))
