"""Commentary engine — the AI sportscaster.

For each play, produces a 1-2 sentence broadcast-style commentary explaining
what happened and why the win probability moved. High-leverage plays get
deeper analysis (multi-round reasoning); routine plays get a fast pass.

This is the differentiator vs. classical win-probability models: a number
plus a *story*. Judges hear the agent thinking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..config import clip
from ..llm import call_fast, call_json
from .win_probability import GameState

logger = logging.getLogger(__name__)


FAST_SYSTEM = """You are a basketball sports analyst providing live in-game commentary.

For each play, write ONE short commentary line in broadcast-style voice:
- 15-25 words
- Active voice, present tense
- Reference the score / time / momentum if relevant
- Optional: 1 brief insight (a stat, a pattern, a strategic note)

Respond with ONLY JSON: {"line": "<your commentary>"}"""


DEEP_SYSTEM = """You are an experienced NBA broadcast analyst. A high-leverage play just happened.

Provide a richer 2-3 sentence breakdown:
- What happened on the play
- Why this matters tactically or for win probability
- 1 historical / statistical anchor (e.g., "teams trailing by 5 with 2 minutes left win ~22% of the time")

Tone: confident, conversational, slightly dramatic. Think Mark Jackson on TNT.

Respond with ONLY JSON:
{
  "line": "<your 2-3 sentence breakdown>",
  "leverage_note": "<one-phrase summary of why this moment matters>"
}"""


@dataclass
class CommentaryOutput:
    line: str                # The broadcast line
    leverage_note: str = ""  # Optional flag for high-leverage moments
    deep: bool = False       # True if produced via DEEP path


def _format_play_context(
    play: dict,
    state: GameState,
    prev_wp_home: float,
    new_wp_home: float,
    recent_plays: list[dict],
) -> str:
    """Build the user prompt context for one play."""
    swing = (new_wp_home - prev_wp_home) * 100
    swing_str = (f"+{swing:.1f}" if swing >= 0 else f"{swing:.1f}") + " pts toward home"
    parts = [
        f"Sport: NBA",
        f"Teams: {state.away_team} @ {state.home_team} (home)",
        f"Score: {state.away_team} {state.away_score} - {state.home_team} {state.home_score}",
        f"Period: Q{state.period}    Clock: {_fmt_clock(state.clock_seconds)}",
        f"Home win probability moved: {prev_wp_home:.2f} → {new_wp_home:.2f}  ({swing_str})",
        "",
        f"Latest play:",
        f"  [{play.get('type', '')}] {play.get('text', '')}",
    ]
    if recent_plays:
        parts.append("")
        parts.append("Recent plays for context:")
        for rp in recent_plays[-3:]:
            parts.append(f"  - Q{rp.get('period')} {rp.get('clock')} : {rp.get('text')}")
    return "\n".join(parts)


def _fmt_clock(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def commentate(
    play: dict,
    state: GameState,
    prev_wp_home: float,
    new_wp_home: float,
    recent_plays: list[dict] | None = None,
    *,
    deep: bool = False,
) -> CommentaryOutput:
    """Produce a commentary line for one play.

    Args:
        play: The current play dict.
        state: Game state after this play.
        prev_wp_home: P(home wins) BEFORE this play.
        new_wp_home: P(home wins) AFTER this play.
        recent_plays: Last few plays for context (optional).
        deep: If True, use the heavier DEEP_SYSTEM path.

    Returns:
        CommentaryOutput with broadcast line.
    """
    recent_plays = recent_plays or []
    context = _format_play_context(play, state, prev_wp_home, new_wp_home, recent_plays)

    try:
        if deep:
            result = call_json(DEEP_SYSTEM, context, temperature=0.7, max_tokens=300)
            return CommentaryOutput(
                line=result["line"],
                leverage_note=result.get("leverage_note", ""),
                deep=True,
            )
        else:
            result = call_fast(FAST_SYSTEM, context, temperature=0.5, max_tokens=120)
            return CommentaryOutput(line=result["line"], deep=False)
    except Exception as exc:
        logger.warning("Commentary call failed: %s", exc)
        # Fallback: just describe the play factually.
        return CommentaryOutput(
            line=f"{play.get('text', 'Play registered.')}  "
                 f"Score now {state.away_team} {state.away_score} - "
                 f"{state.home_team} {state.home_score}.",
            deep=False,
        )
