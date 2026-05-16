"""Polymarket overlay agent.

Polls Polymarket's CLOB API for active soccer markets. Filters to markets
relevant to a specific match (e.g. by team name). Writes
polymarket_state.json that the synthesis agent and frontend can read.

ENDPOINT
--------
GET https://clob.polymarket.com/markets
GET https://gamma-api.polymarket.com/markets?active=true

No auth required. Returns JSON.

OUTPUT
------
polymarket_state.json:
{
  "match_id": str,
  "markets": [
    {"market_id": str, "question": str, "yes_price": float, "no_price": float,
     "volume_24h": float, "related_to": str}
  ],
  "fetched_at": "<ISO timestamp>",
  "source": "polymarket",
  "note": str
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
from datetime import datetime, timezone
from typing import Any

import requests

from .schema import PolymarketMarket, PolymarketSnapshot

logger = logging.getLogger("polymarket")

GAMMA_API = "https://gamma-api.polymarket.com/markets"
OUTPUT_PATH = "polymarket_state.json"


def fetch_active_soccer_markets(
    team_filters: list[str] | None = None,
    limit: int = 50,
) -> list[dict]:
    """Pull active markets and filter to soccer-related ones."""
    params = {"active": "true", "limit": limit, "closed": "false"}
    try:
        resp = requests.get(GAMMA_API, params=params, timeout=15,
                            headers={"User-Agent": "sidelineiq/0.1"})
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Polymarket fetch failed: %s", exc)
        return []

    markets = resp.json()
    if not isinstance(markets, list):
        return []

    # Filter to soccer-tagged markets
    soccer_markets = []
    for m in markets:
        tags = (m.get("category") or "") + " " + " ".join(m.get("tags", []) or [])
        question = (m.get("question") or "").lower()
        haystack = (tags + " " + question).lower()
        if any(kw in haystack for kw in
               ["soccer", "football", "premier league", "epl", "champions league",
                "la liga", "bundesliga", "serie a", "mls", "world cup"]):
            soccer_markets.append(m)

    # Further filter by team names if provided
    if team_filters:
        filtered = []
        for m in soccer_markets:
            q = (m.get("question") or "").lower()
            if any(t.lower() in q for t in team_filters):
                filtered.append(m)
        return filtered

    return soccer_markets


def parse_market(raw: dict) -> PolymarketMarket | None:
    """Convert a Polymarket gamma response into our schema."""
    try:
        # Gamma API uses outcome prices in different shapes — try a few
        outcome_prices = raw.get("outcomePrices")
        if isinstance(outcome_prices, str):
            outcome_prices = json.loads(outcome_prices)
        if not outcome_prices or len(outcome_prices) < 2:
            return None
        yes = float(outcome_prices[0])
        no = float(outcome_prices[1])
        return PolymarketMarket(
            market_id=str(raw.get("id", "") or raw.get("conditionId", "")),
            question=str(raw.get("question", "")),
            yes_price=yes,
            no_price=no,
            volume_24h=float(raw.get("volume24hr", 0) or 0),
            related_to=_classify_market(raw.get("question", "")),
        )
    except (json.JSONDecodeError, ValueError, KeyError, IndexError):
        return None


def _classify_market(question: str) -> str:
    """Categorize a market question into a coarse type."""
    q = question.lower()
    if "win" in q:
        return "match_winner"
    if "score" in q and ("next" in q or "first" in q):
        return "next_goal"
    if "card" in q:
        return "cards"
    if "corner" in q:
        return "corners"
    if "over" in q or "under" in q:
        return "total_goals"
    return "other"


def fetch_snapshot(match_id: str, team_filters: list[str]) -> PolymarketSnapshot:
    """Build a PolymarketSnapshot for a specific match."""
    raw_markets = fetch_active_soccer_markets(team_filters=team_filters)
    parsed = [m for m in (parse_market(r) for r in raw_markets) if m is not None]

    note = ""
    if not parsed:
        note = (f"No active Polymarket markets matched team filters {team_filters}. "
                f"Try team aliases (e.g., 'Arsenal' instead of 'Arsenal FC').")

    return PolymarketSnapshot(
        match_id=match_id,
        markets=parsed,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        source="polymarket",
        note=note,
    )


def atomic_write(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def run_loop(match_id: str, team_filters: list[str], poll_interval: float = 30.0):
    """Long-running poll loop."""
    logger.info("POLYMARKET starting: match=%s teams=%s poll=%.1fs",
                match_id, team_filters, poll_interval)
    while True:
        t0 = time.time()
        try:
            snap = fetch_snapshot(match_id, team_filters)
            atomic_write(OUTPUT_PATH, snap.model_dump())
            logger.info("Polymarket: %d markets, sample question: %s",
                        len(snap.markets),
                        snap.markets[0].question[:80] if snap.markets else "(none)")
        except Exception as exc:
            logger.warning("Polymarket loop error: %s", exc)
        elapsed = time.time() - t0
        time.sleep(max(0.0, poll_interval - elapsed))


def main():
    p = argparse.ArgumentParser(prog="polymarket_agent")
    p.add_argument("--match-id", required=True, help="Identifier for the match (any string)")
    p.add_argument("--team-filter", action="append", default=[],
                   help="Team name(s) to filter markets by (repeatable)")
    p.add_argument("--poll-interval", type=float, default=30.0)
    p.add_argument("--once", action="store_true",
                   help="Fetch once and exit (for testing)")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    def _bye(_sig, _frame):
        logger.info("POLYMARKET stopping")
        sys.exit(0)
    signal.signal(signal.SIGINT, _bye)

    if args.once:
        snap = fetch_snapshot(args.match_id, args.team_filter)
        atomic_write(OUTPUT_PATH, snap.model_dump())
        print(json.dumps(snap.model_dump(), indent=2))
        return

    run_loop(args.match_id, args.team_filter, args.poll_interval)


if __name__ == "__main__":
    main()
