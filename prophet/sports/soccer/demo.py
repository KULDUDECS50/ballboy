"""Demo CLI for the soccer data pipeline.

Run me to see fetch → normalize → features end-to-end.

Examples:
    # Run against the bundled mock match (no network):
    python3 -m prophet.sports.soccer.demo --mock

    # Run against a real ESPN match (find an event id with cache_games list):
    python3 -m prophet.sports.soccer.demo --source espn --event 704495

    # Stream feature snapshots minute-by-minute (5-minute stride):
    python3 -m prophet.sports.soccer.demo --mock --stream
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from . import events as fetchers
from . import normalizer
from .features import compute_features, iter_snapshots


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mock", action="store_true", help="Use the bundled mock match")
    p.add_argument("--mock-path", default=None,
                   help="Path to mock JSON (default: data/games/mock_LIV_ARS_2026.json)")
    p.add_argument("--source", default="mock", choices=["mock", "espn"],
                   help="Data source")
    p.add_argument("--league", default="eng.1", help="ESPN league code")
    p.add_argument("--event", help="ESPN event ID (required if source=espn)")
    p.add_argument("--stream", action="store_true",
                   help="Print snapshots every 5 game-minutes instead of just final")
    p.add_argument("--stride", type=int, default=5)
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # ----- 1. Fetch -----
    if args.mock or args.source == "mock":
        path = args.mock_path or "data/games/mock_LIV_ARS_2026.json"
        raw = fetchers.fetch_mock_match(path)
        source = "mock"
        match_id = None
    elif args.source == "espn":
        if not args.event:
            raise SystemExit("--event is required with --source espn")
        raw = fetchers.fetch_espn_match(league=args.league, event_id=args.event)
        source = "espn"
        match_id = args.event
    else:
        raise SystemExit(f"Unknown source: {args.source}")

    # ----- 2. Normalize -----
    feed = normalizer.normalize(source, raw, match_id=match_id)
    print(f"\n=== Match: {feed.meta.home_team} vs {feed.meta.away_team} "
          f"({feed.meta.competition}) ===")
    print(f"Events parsed: {len(feed.events)}")
    if feed.events:
        last = feed.events[-1]
        print(f"Final state: {last.home_score} - {last.away_score} at {last.minute}'\n")

    # ----- 3. Features -----
    if args.stream:
        print(f"Snapshot stream (stride={args.stride} min):\n")
        for snap in iter_snapshots(feed, stride=args.stride):
            print(snap.as_prompt_block())
            print("\n" + "-" * 60 + "\n")
    else:
        snap = compute_features(feed)
        print("Final snapshot:\n")
        print(snap.as_prompt_block())


if __name__ == "__main__":
    main()
