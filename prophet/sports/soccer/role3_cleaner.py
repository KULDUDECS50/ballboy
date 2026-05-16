"""Ballboy — Role 3: Soccer API poller + Data Cleaner.

Reads vision_state.json (from Role 1, Qwen2.5-VL output).
Polls ESPN (free) and optionally API-Sports for live match events + stats.
Merges into a single canonical current_state.json that Role 2 (history agent)
and the Synthesis agent consume.

USAGE
-----
    # Mock mode — use a cached match JSON, fast for development
    python -m prophet.sports.soccer.role3_cleaner --mock data/games/mock_LIV_ARS_2026.json

    # Live mode — poll ESPN for a real match
    python -m prophet.sports.soccer.role3_cleaner --source espn --match-id 704495

    # Replay a cached game at a custom speed (good for demo dress rehearsals)
    python -m prophet.sports.soccer.role3_cleaner --replay data/games/mock_LIV_ARS_2026.json --speedup 30

DESIGN NOTES
------------
- Atomic writes: write to .tmp, then os.replace. No partial reads possible.
- Defensive reads: vision_state.json may not exist, be stale, or be invalid JSON.
- API-Sports optional: enable with --use-api-sports if you have a key.
- ESPN as primary source: free, no quota, what we already have wired.
- Run as a long-lived process. Ctrl-C is safe.

OUTPUT CONTRACT
---------------
Writes current_state.json with this shape (your synthesis agent's input):
{
  "minute": int,
  "added_time": int,
  "period": int,
  "score": {"home": int, "away": int},
  "teams": {"home": str, "away": str},
  "possession_pct": {"home": int, "away": int} | null,
  "press_intensity": {"home": "low|medium|high", "away": ...} | null,
  "ball_zone": str | null,
  "team_shape": {"home": str, "away": str} | null,
  "recent_events": [{"minute": int, "kind": str, "text": str, "side": "home|away"}],
  "subs_used": {"home": int, "away": int},
  "cards": {"home": {"yellow": int, "red": int}, "away": {...}},
  "data_sources": ["espn", "vision"],   # what fed this snapshot
  "vision_confidence": float | null,    # last vision read confidence
  "timestamp": float,                   # wall-clock unix time
  "stale_seconds": int                  # how old the freshest input is
}
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger("role3")

# ---------------------------------------------------------------------------
# File contract
# ---------------------------------------------------------------------------

VISION_STATE_PATH = Path("vision_state.json")        # Written by Role 1
CURRENT_STATE_PATH = Path("current_state.json")      # Written by Role 3 (us)
VISION_STALE_SECONDS = 60                            # Older than this = ignore vision

# ---------------------------------------------------------------------------
# ESPN client (primary, free, no quota)
# ---------------------------------------------------------------------------

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"


def fetch_espn_summary(league: str, match_id: str) -> dict | None:
    """Pull the full ESPN summary for a match. Returns None on failure."""
    try:
        url = f"{ESPN_BASE}/{league}/summary"
        resp = requests.get(
            url, params={"event": match_id}, timeout=10,
            headers={"User-Agent": "ballboy-role3/0.1"},
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("ESPN fetch failed: %s", exc)
        return None


def parse_espn_state(payload: dict) -> dict:
    """Extract the fields we care about from ESPN's nested summary payload."""
    header = payload.get("header", {})
    comp = (header.get("competitions") or [{}])[0]
    competitors = comp.get("competitors", [])
    home = next((c for c in competitors if c.get("homeAway") == "home"), {}) or {}
    away = next((c for c in competitors if c.get("homeAway") == "away"), {}) or {}

    home_team = (home.get("team") or {}).get("displayName", "Home")
    away_team = (away.get("team") or {}).get("displayName", "Away")

    # Status — ESPN's clock + period
    status = comp.get("status", {})
    display_clock = (status.get("type") or {}).get("shortDetail", "") or status.get("displayClock", "")
    period = int(status.get("period", 0)) if isinstance(status.get("period"), (int, str)) else 0
    minute, added = _parse_clock(display_clock)

    # Events (commentary timeline)
    raw_events = payload.get("commentary") or payload.get("plays") or []
    recent_events = []
    home_yellow = home_red = away_yellow = away_red = 0
    home_subs = away_subs = 0
    for raw in raw_events[-50:]:  # last 50 plays max
        kind_text = (raw.get("type") or {}).get("text", "") or ""
        team_name = (raw.get("team") or {}).get("displayName", "")
        side = "home" if team_name == home_team else ("away" if team_name == away_team else "neutral")
        ev_minute, ev_added = _parse_clock((raw.get("clock") or {}).get("displayValue", ""))
        recent_events.append({
            "minute": ev_minute,
            "added_time": ev_added,
            "kind": _normalize_kind(kind_text),
            "text": (raw.get("text") or "")[:200],
            "side": side,
        })
        # Tally cards + subs
        k = _normalize_kind(kind_text)
        if k == "yellow_card":
            if side == "home": home_yellow += 1
            elif side == "away": away_yellow += 1
        elif k in ("red_card", "second_yellow"):
            if side == "home": home_red += 1
            elif side == "away": away_red += 1
        elif k == "substitution":
            if side == "home": home_subs += 1
            elif side == "away": away_subs += 1

    score_home = int(home.get("score", 0))
    score_away = int(away.get("score", 0))

    return {
        "teams": {"home": home_team, "away": away_team},
        "minute": minute,
        "added_time": added,
        "period": period,
        "score": {"home": score_home, "away": score_away},
        "recent_events": recent_events[-15:],   # keep last 15 for the prompt
        "subs_used": {"home": home_subs, "away": away_subs},
        "cards": {
            "home": {"yellow": home_yellow, "red": home_red},
            "away": {"yellow": away_yellow, "red": away_red},
        },
        "match_status": (status.get("type") or {}).get("state", "unknown"),
    }


# ---------------------------------------------------------------------------
# API-Sports client (optional — only if user supplies a key)
# ---------------------------------------------------------------------------

def fetch_api_sports_stats(fixture_id: int, api_key: str) -> dict | None:
    """Fetch live stats from API-Sports. Returns possession data ESPN lacks."""
    try:
        url = "https://v3.football.api-sports.io/fixtures/statistics"
        resp = requests.get(
            url,
            headers={"x-apisports-key": api_key},
            params={"fixture": fixture_id},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("API-Sports fetch failed: %s", exc)
        return None


def parse_api_sports_possession(payload: dict) -> dict[str, int] | None:
    """Pull possession % from API-Sports stats response."""
    if not payload:
        return None
    out = {}
    for team_block in payload.get("response", []):
        team_name = (team_block.get("team") or {}).get("name", "")
        for stat in team_block.get("statistics", []):
            if stat.get("type", "").lower() in ("ball possession", "possession"):
                v = stat.get("value", "0%")
                if isinstance(v, str):
                    v = int(v.replace("%", "").strip() or 0)
                out[team_name] = int(v)
    return out or None


# ---------------------------------------------------------------------------
# Vision state reader
# ---------------------------------------------------------------------------

def read_vision_state() -> dict | None:
    """Safely read Role 1's vision_state.json. Returns None if missing/stale/broken."""
    if not VISION_STATE_PATH.exists():
        return None
    try:
        data = json.loads(VISION_STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("vision_state.json unreadable: %s", exc)
        return None

    ts = data.get("timestamp", 0)
    age = time.time() - ts
    if age > VISION_STALE_SECONDS:
        logger.debug("vision_state.json is stale (%.0fs old) — ignoring", age)
        return None
    return data


# ---------------------------------------------------------------------------
# Merger — the actual contract logic
# ---------------------------------------------------------------------------

# Priority rules: who wins when both sources have a field?
# Truth source for each field, in order of preference:
#   ESPN  : minute, score, period, recent_events, cards, subs, match_status
#   API-Sports : possession_pct (if available)
#   Vision: press_intensity, ball_zone, team_shape, possession_pct (fallback)

def merge_states(
    api_state: dict | None,
    vision_state: dict | None,
    possession_override: dict | None = None,
) -> dict:
    """Merge with priority rules. Always returns a valid state dict."""
    merged: dict[str, Any] = {
        "timestamp": time.time(),
        "data_sources": [],
    }
    stale_inputs = []

    # ESPN trunk — these are ground truth when present
    if api_state:
        merged["minute"] = api_state.get("minute", 0)
        merged["added_time"] = api_state.get("added_time", 0)
        merged["period"] = api_state.get("period", 0)
        merged["score"] = api_state.get("score", {"home": 0, "away": 0})
        merged["teams"] = api_state.get("teams", {"home": "Home", "away": "Away"})
        merged["recent_events"] = api_state.get("recent_events", [])
        merged["subs_used"] = api_state.get("subs_used", {"home": 0, "away": 0})
        merged["cards"] = api_state.get("cards", {
            "home": {"yellow": 0, "red": 0},
            "away": {"yellow": 0, "red": 0},
        })
        merged["match_status"] = api_state.get("match_status", "unknown")
        merged["data_sources"].append("espn")
    else:
        # Sane defaults if ESPN fetch failed
        merged.update({
            "minute": 0, "added_time": 0, "period": 0,
            "score": {"home": 0, "away": 0},
            "teams": {"home": "Home", "away": "Away"},
            "recent_events": [],
            "subs_used": {"home": 0, "away": 0},
            "cards": {"home": {"yellow": 0, "red": 0}, "away": {"yellow": 0, "red": 0}},
            "match_status": "no_data",
        })
        stale_inputs.append("espn")

    # Possession — try API-Sports, fall back to vision
    merged["possession_pct"] = None
    if possession_override:
        # Map team names to home/away
        teams = merged["teams"]
        try:
            merged["possession_pct"] = {
                "home": possession_override.get(teams["home"], 50),
                "away": possession_override.get(teams["away"], 50),
            }
            merged["data_sources"].append("api-sports")
        except (KeyError, TypeError):
            pass
    if merged["possession_pct"] is None and vision_state:
        vpos = vision_state.get("possession_pct")
        if vpos:
            merged["possession_pct"] = vpos

    # Vision-only fields
    if vision_state:
        merged["press_intensity"] = vision_state.get("press_intensity")
        merged["ball_zone"] = vision_state.get("ball_zone")
        merged["team_shape"] = vision_state.get("team_shape")
        merged["vision_confidence"] = vision_state.get("confidence", 0.0)
        merged["data_sources"].append("vision")
    else:
        merged["press_intensity"] = None
        merged["ball_zone"] = None
        merged["team_shape"] = None
        merged["vision_confidence"] = None
        stale_inputs.append("vision")

    # Staleness — how old is the freshest contributing input?
    fresh_ts = max(
        (api_state or {}).get("fetched_at", 0),
        (vision_state or {}).get("timestamp", 0),
    )
    merged["stale_seconds"] = int(time.time() - fresh_ts) if fresh_ts else -1
    merged["missing_sources"] = stale_inputs

    return merged


# ---------------------------------------------------------------------------
# Atomic writer
# ---------------------------------------------------------------------------

def atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON to a tmp file then os.replace — no partial reads possible."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ESPN_KIND_MAP = {
    "Goal": "goal", "Own Goal": "own_goal",
    "Penalty - Scored": "penalty_goal", "Penalty - Missed": "penalty_miss",
    "Penalty - Saved": "penalty_miss",
    "Shot on Goal": "shot_on_target", "Shot Off Goal": "shot_off_target",
    "Shot Blocked": "shot_blocked",
    "Yellow Card": "yellow_card", "Second Yellow Card": "second_yellow",
    "Red Card": "red_card",
    "Substitution": "substitution", "Corner Kick": "corner",
    "Free Kick": "free_kick", "Offside": "offside", "Foul": "foul",
    "Kickoff": "kickoff", "Half Time": "half_time", "Full Time": "full_time",
    "Video Review": "var_decision", "Injury": "injury",
}


def _normalize_kind(s: str) -> str:
    return ESPN_KIND_MAP.get(s.strip(), "other")


def _parse_clock(clock_str: str) -> tuple[int, int]:
    """ESPN's clock comes as '67'' or '45'+2'. Return (minute, added_time)."""
    if not clock_str:
        return 0, 0
    s = clock_str.replace("'", "").strip()
    if "+" in s:
        try:
            base, added = s.split("+", 1)
            return int(base.strip()), int(added.strip())
        except ValueError:
            return 0, 0
    try:
        return int(s), 0
    except ValueError:
        return 0, 0


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_live(
    league: str,
    match_id: str,
    poll_interval: float = 30.0,
    api_sports_key: str | None = None,
    api_sports_fixture: int | None = None,
):
    """Long-running poll loop."""
    logger.info("ROLE 3 starting: league=%s match_id=%s poll=%.1fs",
                league, match_id, poll_interval)
    while True:
        t0 = time.time()
        # 1) ESPN
        raw = fetch_espn_summary(league, match_id)
        api_state = parse_espn_state(raw) if raw else None
        if api_state:
            api_state["fetched_at"] = t0

        # 2) API-Sports possession (optional)
        possession = None
        if api_sports_key and api_sports_fixture:
            apisports = fetch_api_sports_stats(api_sports_fixture, api_sports_key)
            possession = parse_api_sports_possession(apisports) if apisports else None

        # 3) Vision
        vision_state = read_vision_state()

        # 4) Merge + write
        merged = merge_states(api_state, vision_state, possession)
        atomic_write_json(CURRENT_STATE_PATH, merged)

        # 5) Log a tight status line
        teams = merged["teams"]
        score = merged["score"]
        srcs = ",".join(merged["data_sources"]) or "none"
        miss = (",".join(merged.get("missing_sources") or []) or "—")
        logger.info(
            "%s %d-%d %s   min=%d  sources=%s  missing=%s",
            teams["home"][:12], score["home"], score["away"], teams["away"][:12],
            merged["minute"], srcs, miss,
        )

        elapsed = time.time() - t0
        time.sleep(max(0.0, poll_interval - elapsed))


def run_replay(cached_path: str, speedup: float = 30.0):
    """Replay a cached game's events — useful for demo dry-runs without ESPN."""
    logger.info("ROLE 3 starting in REPLAY mode: file=%s speedup=%.1fx", cached_path, speedup)
    data = json.loads(Path(cached_path).read_text())
    events = data.get("events", [])
    meta = data.get("meta", {})

    cumulative_events: list[dict] = []
    prev_minute = 0
    for ev in events:
        cumulative_events.append({
            "minute": ev["minute"],
            "added_time": ev.get("added_time", 0),
            "kind": ev["kind"],
            "text": ev.get("text", ""),
            "side": ev.get("side", "neutral"),
        })
        # Pseudo-API-state from the running events
        score = {"home": ev["home_score"], "away": ev["away_score"]}
        cards = _tally_cards(cumulative_events)
        subs = _tally_subs(cumulative_events)
        api_state = {
            "teams": {"home": meta["home_team"], "away": meta["away_team"]},
            "minute": ev["minute"],
            "added_time": ev.get("added_time", 0),
            "period": ev.get("period", 1),
            "score": score,
            "recent_events": cumulative_events[-15:],
            "subs_used": subs,
            "cards": cards,
            "match_status": "in_progress" if ev["kind"] != "full_time" else "finished",
            "fetched_at": time.time(),
        }

        vision_state = read_vision_state()
        merged = merge_states(api_state, vision_state, None)
        atomic_write_json(CURRENT_STATE_PATH, merged)
        logger.info(
            "[replay] min=%d  %s %d-%d %s  [%s]",
            ev["minute"], meta["home_team"][:8], score["home"], score["away"],
            meta["away_team"][:8], ev["kind"],
        )

        # Pace by game-clock differential
        if prev_minute and ev["minute"] > prev_minute:
            sleep_for = (ev["minute"] - prev_minute) * 60 / speedup
            time.sleep(min(sleep_for, 10.0))   # cap at 10s so replay stays snappy
        prev_minute = ev["minute"]

    logger.info("[replay] done — match finished")


def _tally_cards(events: list[dict]) -> dict:
    h_y = sum(1 for e in events if e["side"] == "home" and e["kind"] == "yellow_card")
    h_r = sum(1 for e in events if e["side"] == "home" and e["kind"] in ("red_card", "second_yellow"))
    a_y = sum(1 for e in events if e["side"] == "away" and e["kind"] == "yellow_card")
    a_r = sum(1 for e in events if e["side"] == "away" and e["kind"] in ("red_card", "second_yellow"))
    return {"home": {"yellow": h_y, "red": h_r}, "away": {"yellow": a_y, "red": a_r}}


def _tally_subs(events: list[dict]) -> dict:
    h = sum(1 for e in events if e["side"] == "home" and e["kind"] == "substitution")
    a = sum(1 for e in events if e["side"] == "away" and e["kind"] == "substitution")
    return {"home": h, "away": a}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(prog="role3_cleaner")
    p.add_argument("--source", default="espn", choices=["espn"])
    p.add_argument("--league", default="eng.1", help="ESPN league code")
    p.add_argument("--match-id", help="ESPN match event ID for live mode")
    p.add_argument("--poll-interval", type=float, default=30.0)
    p.add_argument("--use-api-sports", action="store_true",
                   help="Also fetch possession from API-Sports (needs API_SPORTS_KEY env var)")
    p.add_argument("--api-sports-fixture", type=int,
                   help="API-Sports fixture ID (different from ESPN's)")
    p.add_argument("--replay", help="Path to cached game JSON (offline demo dry-run)")
    p.add_argument("--speedup", type=float, default=30.0, help="Replay speedup")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # Graceful Ctrl-C
    def _bye(_sig, _frame):
        logger.info("ROLE 3 stopping")
        sys.exit(0)
    signal.signal(signal.SIGINT, _bye)

    if args.replay:
        run_replay(args.replay, speedup=args.speedup)
    else:
        if not args.match_id:
            p.error("--match-id required (or use --replay)")
        api_sports_key = os.environ.get("API_SPORTS_KEY") if args.use_api_sports else None
        run_live(
            league=args.league,
            match_id=args.match_id,
            poll_interval=args.poll_interval,
            api_sports_key=api_sports_key,
            api_sports_fixture=args.api_sports_fixture,
        )


if __name__ == "__main__":
    main()
