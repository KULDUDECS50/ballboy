"""Cache games from ESPN for replay.

Usage:
    # List today's NBA games:
    python -m prophet.sports.cache_games list --sport nba

    # Cache a specific game by event ID:
    python -m prophet.sports.cache_games fetch --sport nba --event 401705034

    # Cache all the FINAL games for today:
    python -m prophet.sports.cache_games fetch_today --sport nba
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from . import espn

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "games"


def cmd_list(args):
    target = date.today() if not args.date else datetime.strptime(args.date, "%Y-%m-%d").date()
    games = espn.get_scoreboard(sport=args.sport, target_date=target)
    if not games:
        print(f"No games found for {args.sport} on {target}.")
        return
    print(f"\n{args.sport.upper()} games for {target}:\n")
    for g in games:
        status = g["status"].upper()
        print(f"  {g['id']:14s}  {g['away_team']:4s} {g['away_score']:3d}  @  "
              f"{g['home_team']:4s} {g['home_score']:3d}    [{status}]  {g['status_detail']}")
    print()


def cmd_fetch(args):
    cache_dir = Path(args.cache_dir)
    out = espn.cache_game(args.sport, args.event, cache_dir)
    print(f"Cached → {out}")


def cmd_fetch_today(args):
    cache_dir = Path(args.cache_dir)
    target = date.today() if not args.date else datetime.strptime(args.date, "%Y-%m-%d").date()
    games = espn.get_scoreboard(sport=args.sport, target_date=target)
    final = [g for g in games if g["status"] == "post"]
    if not final:
        print(f"No completed games for {args.sport} on {target} — nothing to cache.")
        return
    print(f"Caching {len(final)} completed {args.sport.upper()} games from {target}...")
    for g in final:
        try:
            out = espn.cache_game(args.sport, g["id"], cache_dir)
            print(f"  ✓ {g['away_team']} @ {g['home_team']} → {out.name}")
        except Exception as exc:
            print(f"  ✗ {g['away_team']} @ {g['home_team']}: {exc}")


def cmd_fetch_recent(args):
    """Walk back N days, cache all completed games. Good before-demo prep."""
    cache_dir = Path(args.cache_dir)
    today = date.today()
    total = 0
    for d in range(args.days):
        target = today - timedelta(days=d)
        try:
            games = espn.get_scoreboard(sport=args.sport, target_date=target)
        except Exception as exc:
            print(f"  ✗ {target}: {exc}")
            continue
        final = [g for g in games if g["status"] == "post"]
        if not final:
            continue
        print(f"{target}: {len(final)} completed {args.sport} games")
        for g in final:
            try:
                espn.cache_game(args.sport, g["id"], cache_dir)
                print(f"  ✓ {g['away_team']} {g['away_score']} @ {g['home_team']} {g['home_score']}")
                total += 1
            except Exception as exc:
                print(f"  ✗ {g['away_team']} @ {g['home_team']}: {exc}")
    print(f"\nDone. {total} games cached.")


def main():
    logging.basicConfig(level=logging.WARNING)
    p = argparse.ArgumentParser(prog="cache_games")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="List games for a date")
    p_list.add_argument("--sport", default="nba")
    p_list.add_argument("--date", help="YYYY-MM-DD (default: today)")
    p_list.set_defaults(func=cmd_list)

    p_fetch = sub.add_parser("fetch", help="Cache one game by event ID")
    p_fetch.add_argument("--sport", default="nba")
    p_fetch.add_argument("--event", required=True)
    p_fetch.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    p_fetch.set_defaults(func=cmd_fetch)

    p_today = sub.add_parser("fetch_today", help="Cache all completed games for a day")
    p_today.add_argument("--sport", default="nba")
    p_today.add_argument("--date", help="YYYY-MM-DD (default: today)")
    p_today.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    p_today.set_defaults(func=cmd_fetch_today)

    p_recent = sub.add_parser("fetch_recent", help="Cache last N days of completed games")
    p_recent.add_argument("--sport", default="nba")
    p_recent.add_argument("--days", type=int, default=7)
    p_recent.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    p_recent.set_defaults(func=cmd_fetch_recent)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
