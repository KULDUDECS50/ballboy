"""Player intel CLI — orchestrates the whole pre-match intel pipeline.

USAGE
-----
    # Build dossiers + manager profiles for a match:
    python -m prophet.sports.soccer.player_intel.cli build \\
        --home "Arsenal" --home-manager "Mikel Arteta" \\
        --home-starters "Saka,Ødegaard,Saliba,Gabriel,White" \\
        --away "Manchester City" --away-manager "Pep Guardiola" \\
        --away-starters "Haaland,De Bruyne,Foden,Rodri,Gvardiol"

    # Quick test (skip web search + matchups for speed):
    python -m prophet.sports.soccer.player_intel.cli build \\
        --home "Arsenal" --home-manager "Mikel Arteta" \\
        --home-starters "Saka,Ødegaard" \\
        --away "Manchester City" --away-manager "Pep Guardiola" \\
        --away-starters "Haaland,De Bruyne" \\
        --skip-web-search --skip-matchups

    # Run only the manager fingerprint step:
    python -m prophet.sports.soccer.player_intel.cli managers \\
        --home "Arsenal" --home-manager "Mikel Arteta" \\
        --away "Manchester City" --away-manager "Pep Guardiola"

    # Test FPL lookup (no LLM calls):
    python -m prophet.sports.soccer.player_intel.cli fpl-test --player "Saka"
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import fpl_client
from .dossier_builder import build_match_dossiers
from .manager_agent import build_match_context


def cmd_build(args):
    home_starters = [s.strip() for s in args.home_starters.split(",") if s.strip()]
    away_starters = [s.strip() for s in args.away_starters.split(",") if s.strip()]

    print(f"\n=== Building dossiers ===")
    print(f"Home ({args.home}): {home_starters}")
    print(f"Away ({args.away}): {away_starters}")
    print(f"Web search: {'skipped' if args.skip_web_search else 'enabled'}")
    print(f"Matchups: {'skipped' if args.skip_matchups else 'enabled'}")
    print()

    written = build_match_dossiers(
        home_team=args.home,
        home_starters=home_starters,
        away_team=args.away,
        away_starters=away_starters,
        skip_web_search=args.skip_web_search,
        skip_matchups=args.skip_matchups,
        output_dir=args.output_dir,
    )
    print(f"\n✓ Wrote {len(written)} dossiers to {args.output_dir}/")

    if not args.skip_managers:
        print(f"\n=== Building manager profiles ===")
        ctx = build_match_context(
            home_manager=args.home_manager,
            home_team=args.home,
            away_manager=args.away_manager,
            away_team=args.away,
            output_path="manager_context.json",
        )
        print(f"✓ Wrote manager_context.json")
        print(f"  {ctx.home_manager.name}: {ctx.home_manager.style_summary[:120]}...")
        print(f"  {ctx.away_manager.name}: {ctx.away_manager.style_summary[:120]}...")


def cmd_managers(args):
    print(f"\n=== Building manager profiles only ===")
    ctx = build_match_context(
        home_manager=args.home_manager,
        home_team=args.home,
        away_manager=args.away_manager,
        away_team=args.away,
        output_path="manager_context.json",
    )
    print(f"✓ Wrote manager_context.json")
    print()
    print(f"--- {ctx.home_manager.name} ({ctx.home_manager.team}) ---")
    print(f"  Style: {ctx.home_manager.style_summary}")
    print(f"  Expected today: {ctx.home_manager.expected_today}")
    print()
    print(f"--- {ctx.away_manager.name} ({ctx.away_manager.team}) ---")
    print(f"  Style: {ctx.away_manager.style_summary}")
    print(f"  Expected today: {ctx.away_manager.expected_today}")


def cmd_fpl_test(args):
    """Quick smoke test — no LLM calls, just FPL API."""
    print(f"\n=== FPL lookup: {args.player} ===")
    el = fpl_client.find_player(args.player)
    if not el:
        print(f"✗ Not found in FPL roster")
        sys.exit(1)
    print(f"FPL ID: {el['id']}")
    print(f"Web name: {el['web_name']}")
    print(f"Position: {fpl_client.get_player_position(args.player)}")
    print()
    fitness = fpl_client.get_player_fitness(args.player)
    print(f"Fitness: status={fitness.status}, chance={fitness.chance_of_playing}%")
    if fitness.news:
        print(f"  News: {fitness.news}")
    print()
    form = fpl_client.get_player_form(args.player)
    print(f"Form (last 5 GW):")
    print(f"  Minutes: {form.minutes_played_last_5}")
    print(f"  Goals: {form.goals_last_5}")
    print(f"  Assists: {form.assists_last_5}")
    print(f"  xG: {form.xg_last_5}")
    print(f"  xA: {form.xa_last_5}")
    print(f"  FPL form rating: {form.fpl_form_rating}")


def main():
    p = argparse.ArgumentParser(prog="player_intel.cli")
    sub = p.add_subparsers(dest="cmd", required=True)

    # build
    pb = sub.add_parser("build", help="Build all dossiers + manager profiles")
    pb.add_argument("--home", required=True)
    pb.add_argument("--home-manager", required=True)
    pb.add_argument("--home-starters", required=True,
                    help="Comma-separated player names")
    pb.add_argument("--away", required=True)
    pb.add_argument("--away-manager", required=True)
    pb.add_argument("--away-starters", required=True)
    pb.add_argument("--output-dir", default="player_dossiers")
    pb.add_argument("--skip-web-search", action="store_true",
                    help="Skip Anthropic web_search calls (faster + free)")
    pb.add_argument("--skip-matchups", action="store_true",
                    help="Skip GLM matchup analysis")
    pb.add_argument("--skip-managers", action="store_true")
    pb.add_argument("--verbose", "-v", action="store_true")
    pb.set_defaults(func=cmd_build)

    # managers only
    pm = sub.add_parser("managers", help="Build only manager profiles")
    pm.add_argument("--home", required=True)
    pm.add_argument("--home-manager", required=True)
    pm.add_argument("--away", required=True)
    pm.add_argument("--away-manager", required=True)
    pm.add_argument("--verbose", "-v", action="store_true")
    pm.set_defaults(func=cmd_managers)

    # fpl-test
    pf = sub.add_parser("fpl-test", help="Smoke test FPL lookup (no LLM calls)")
    pf.add_argument("--player", required=True)
    pf.add_argument("--verbose", "-v", action="store_true")
    pf.set_defaults(func=cmd_fpl_test)

    args = p.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    args.func(args)


if __name__ == "__main__":
    main()
