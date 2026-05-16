"""SidelineIQ — Synthesis Agent (Role 4).

Reads current_state.json (Role 3 output) every 30s, calls GLM-5.1 via Wafer,
writes current_insight.json, and logs each insight to stdout.

USAGE
-----
    # Make sure WAFER_API_KEY is exported (source .env first) and Role 3 is running.
    python -m prophet.sports.soccer.synthesis_agent
    python -m prophet.sports.soccer.synthesis_agent --poll-interval 30

OUTPUT CONTRACT
---------------
Writes current_insight.json with this shape (the Insight card on the frontend reads it):
{
  "headline": str,         # Short title (< 60 chars)
  "body": str,             # 1-2 sentences of analysis
  "urgency": int,          # 1 (informational) .. 5 (act now)
  "action": str,           # One of ALLOWED_ACTIONS (enum); off-list -> "no_action"
  "minute": int,           # Match minute the insight was synthesized at
  "timestamp": float,      # Unix time the insight was written
  "stale_seconds": int,    # Age of the underlying state when we read it
}

Staleness gate: if Role 3's current_state.json is older than STALE_THRESHOLD_SECONDS,
we skip the LLM call and write a placeholder "Stale state" insight instead, so the
frontend can show "upstream lagging" rather than a confidently-wrong insight.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

from ...config import PRIMARY_MODEL
from ...llm import call_json

logger = logging.getLogger("synthesis")

CURRENT_STATE_PATH = Path("current_state.json")
CURRENT_INSIGHT_PATH = Path("current_insight.json")
PLAYER_DOSSIERS_DIR = Path("player_dossiers")
MANAGER_CONTEXT_PATH = Path("manager_context.json")
POLYMARKET_STATE_PATH = Path("polymarket_state.json")
STALE_THRESHOLD_SECONDS = 90    # Beyond this, Role 3 is lagging — skip LLM call.

ALLOWED_ACTIONS = {
    "substitution",
    "formation_change",
    "press_higher",
    "press_lower",
    "tempo_up",
    "tempo_down",
    "defensive_focus",
    "attacking_focus",
    "set_piece_alert",
    "no_action",
}


SYNTHESIS_SYSTEM = """You are a soccer tactical co-analyst sitting beside the manager.
Every 30 seconds you read live match state and produce ONE concise tactical insight.

Be specific: cite the actual minute, score, cards, and patterns you see in the data.
Don't invent players or facts that aren't in the state.
Don't repeat your previous insight — find a new angle, or honestly say "Steady state, no action."

The "action" field MUST be exactly one of these values:
  "substitution"          — bring on a fresh player
  "formation_change"      — shift shape (e.g. 4-3-3 -> 4-2-3-1)
  "press_higher"          — push the press line up
  "press_lower"           — drop the press line / mid-block
  "tempo_up"              — speed up build-up / be more direct
  "tempo_down"            — slow down, manage the game
  "defensive_focus"       — prioritize keeping the clean sheet
  "attacking_focus"       — commit numbers forward
  "set_piece_alert"       — corner/free-kick threat to manage
  "no_action"             — informational only, nothing to do

If none fits, use "no_action".

You may also see optional ## Player intel, ## Manager profiles, and ## Polymarket odds sections. Use them to ground your insight when present, but never invent data that isn't there.

Respond with ONLY valid JSON in this shape:
{
  "headline": "<short title, < 60 chars>",
  "body": "<1-2 sentences of analysis grounded in the state>",
  "urgency": <int 1-5, where 1 is informational and 5 is act now>,
  "action": "<one of the allowed values above>"
}"""


def read_state() -> dict | None:
    """Read current_state.json. Returns None if missing or unreadable."""
    if not CURRENT_STATE_PATH.exists():
        return None
    try:
        return json.loads(CURRENT_STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("current_state.json unreadable: %s", exc)
        return None


def _read_optional_json(path: Path) -> dict | None:
    """Read a JSON file if it exists. Silent on missing; warns on parse error."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("%s unreadable: %s", path, exc)
        return None


def read_player_dossiers() -> dict[str, dict] | None:
    """Load every player_dossiers/*.json. Returns {file_stem: dossier}, or None."""
    if not PLAYER_DOSSIERS_DIR.is_dir():
        return None
    out: dict[str, dict] = {}
    for f in sorted(PLAYER_DOSSIERS_DIR.glob("*.json")):
        data = _read_optional_json(f)
        if data is not None:
            out[f.stem] = data
    return out or None


def format_state_for_prompt(
    state: dict,
    last_insight: dict | None,
    *,
    dossiers: dict[str, dict] | None = None,
    manager: dict | None = None,
    polymarket: dict | None = None,
) -> str:
    """Render Role 3's state dict as a markdown block for the LLM."""
    teams = state.get("teams") or {"home": "Home", "away": "Away"}
    score = state.get("score") or {"home": 0, "away": 0}
    minute = state.get("minute", 0)
    added = state.get("added_time", 0)
    stale = state.get("stale_seconds", -1)
    stale_str = f"{stale}s" if stale >= 0 else "unknown"
    sources = ",".join(state.get("data_sources") or []) or "none"

    lines = [
        f"## Match state @ {minute}'{f'+{added}' if added else ''}",
        f"**{teams['home']} {score['home']} - {score['away']} {teams['away']}**",
        f"Data sources: {sources} (state freshness: {stale_str})",
        "",
    ]

    poss = state.get("possession_pct")
    if poss:
        lines.append(
            f"Possession: {teams['home']} {poss.get('home','?')}% — "
            f"{teams['away']} {poss.get('away','?')}%"
        )
    press = state.get("press_intensity")
    if press:
        lines.append(
            f"Press intensity: {teams['home']} {press.get('home','?')} — "
            f"{teams['away']} {press.get('away','?')}"
        )
    zone = state.get("ball_zone")
    if zone:
        lines.append(f"Ball zone: {zone}")
    shape = state.get("team_shape")
    if shape:
        lines.append(
            f"Shapes: {teams['home']} {shape.get('home','?')} — "
            f"{teams['away']} {shape.get('away','?')}"
        )

    cards = state.get("cards") or {}
    subs = state.get("subs_used") or {"home": 0, "away": 0}
    h_c = cards.get("home") or {"yellow": 0, "red": 0}
    a_c = cards.get("away") or {"yellow": 0, "red": 0}
    lines += [
        "",
        f"Cards/Subs: "
        f"{teams['home']} {h_c.get('yellow',0)}Y {h_c.get('red',0)}R subs {subs.get('home',0)}/5 — "
        f"{teams['away']} {a_c.get('yellow',0)}Y {a_c.get('red',0)}R subs {subs.get('away',0)}/5",
        "",
        "### Last 6 events",
    ]
    for ev in (state.get("recent_events") or [])[-6:]:
        lines.append(
            f"- {ev.get('minute','?')}' [{ev.get('side','?')}/{ev.get('kind','?')}] "
            f"{(ev.get('text') or '')[:120]}"
        )

    if dossiers:
        lines += ["", "## Player intel"]
        for name, dossier in dossiers.items():
            compact = json.dumps(dossier, default=str)
            if len(compact) > 400:
                compact = compact[:397] + "..."
            lines.append(f"- {name}: {compact}")

    if manager:
        blob = json.dumps(manager, indent=2, default=str)
        if len(blob) > 1500:
            blob = blob[:1497] + "..."
        lines += ["", "## Manager profiles", "```json", blob, "```"]

    if polymarket:
        blob = json.dumps(polymarket, indent=2, default=str)
        if len(blob) > 1500:
            blob = blob[:1497] + "..."
        lines += ["", "## Polymarket odds", "```json", blob, "```"]

    if last_insight:
        lines += [
            "",
            "### Your previous insight (do not repeat)",
            f"- \"{last_insight.get('headline','')}\" — {last_insight.get('body','')}",
        ]

    return "\n".join(lines)


def _normalize_action(raw: object) -> str:
    """Clamp the model's action to ALLOWED_ACTIONS; fall back to 'no_action'."""
    if isinstance(raw, str) and raw in ALLOWED_ACTIONS:
        return raw
    return "no_action"


def _stale_state_insight(state: dict) -> dict:
    """Placeholder insight when Role 3 has fallen behind — no LLM call."""
    stale = state.get("stale_seconds", -1)
    return {
        "headline": "Stale state — Role 3 lagging",
        "body": f"Live data is {stale} seconds old. Synthesis paused until fresh state arrives.",
        "urgency": 1,
        "action": "no_action",
        "minute": state.get("minute", 0),
        "timestamp": time.time(),
        "stale_seconds": stale,
    }


def synthesize(state: dict, last_insight: dict | None) -> dict:
    """Call GLM-5.1 with the formatted state, return a normalized insight dict.

    Honors the staleness gate: if state is older than STALE_THRESHOLD_SECONDS,
    returns a placeholder without calling the LLM.
    """
    stale = state.get("stale_seconds", -1)
    if stale > STALE_THRESHOLD_SECONDS:
        return _stale_state_insight(state)

    user_prompt = format_state_for_prompt(
        state, last_insight,
        dossiers=read_player_dossiers(),
        manager=_read_optional_json(MANAGER_CONTEXT_PATH),
        polymarket=_read_optional_json(POLYMARKET_STATE_PATH),
    )
    result = call_json(
        SYNTHESIS_SYSTEM,
        user_prompt,
        model=PRIMARY_MODEL,        # GLM-5.1 per .env / config.py
        temperature=0.4,
        max_tokens=2500,
    )
    return {
        "headline": str(result.get("headline", ""))[:120],
        "body": str(result.get("body", "")),
        "urgency": int(result.get("urgency", 1)),
        "action": _normalize_action(result.get("action")),
        "minute": state.get("minute", 0),
        "timestamp": time.time(),
        "stale_seconds": stale,
    }


def atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON via a .tmp file + os.replace — frontend never sees partial writes."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def run_loop(poll_interval: float = 30.0) -> None:
    """Main loop: read state -> synthesize -> write insight -> log -> sleep.

    Updates last_insight only on real LLM outputs (not on stale-state placeholders),
    so the "do not repeat" prompt context tracks the most recent real insight.
    """
    logger.info("SYNTHESIS agent starting (poll=%.1fs, model=%s)",
                poll_interval, PRIMARY_MODEL)
    last_insight: dict | None = None
    while True:
        t0 = time.time()
        state = read_state()
        if state is None:
            logger.info("[wait] no %s yet", CURRENT_STATE_PATH)
        else:
            try:
                insight = synthesize(state, last_insight)
                atomic_write_json(CURRENT_INSIGHT_PATH, insight)
                logger.info(
                    "[%d'] U%d %s — %s (action=%s)",
                    insight["minute"], insight["urgency"],
                    insight["headline"], insight["body"], insight["action"],
                )
                # Only carry forward "real" insights so dedup context isn't polluted.
                if insight["action"] != "no_action" or "Stale state" not in insight["headline"]:
                    last_insight = insight
            except Exception as exc:
                logger.warning("synthesis call failed: %s", exc)

        elapsed = time.time() - t0
        time.sleep(max(0.0, poll_interval - elapsed))


def main() -> None:
    p = argparse.ArgumentParser(prog="synthesis_agent")
    p.add_argument("--poll-interval", type=float, default=30.0,
                   help="Seconds between LLM calls (default 30)")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    def _bye(_sig, _frame):
        logger.info("SYNTHESIS stopping")
        sys.exit(0)
    signal.signal(signal.SIGINT, _bye)

    run_loop(poll_interval=args.poll_interval)


if __name__ == "__main__":
    main()
