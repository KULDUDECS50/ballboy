"""Dossier builder — orchestrates all the player intel agents.

For each player:
  1. FPL form (last 5 games)
  2. FPL fitness status (injuries, news)
  3. Off-field intel via web_search (Anthropic)
  4. Tactical matchup vs expected marker (GLM-5.1 via Wafer)

Writes player_dossiers/<player_name>.json per player.

Run this once before the match starts. The synthesis agent reads the dossiers
as additional context every cycle.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from . import fpl_client
from .matchup_agent import analyze_matchup, find_likely_marker
from .schema import PlayerDossier, TacticalMatchup
from .web_intel_agent import fetch_off_field_intel

logger = logging.getLogger(__name__)

DOSSIER_DIR = Path("player_dossiers")


def _safe_filename(name: str) -> str:
    """Convert a player name into a filesystem-safe filename."""
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", name.strip()) + ".json"


def build_dossier(
    player_name: str,
    team: str,
    *,
    skip_web_search: bool = False,
    marker_name: str = "",
    marker_team: str = "",
    marker_position: str = "",
) -> PlayerDossier:
    """Build a single PlayerDossier."""
    logger.info("Building dossier: %s (%s)", player_name, team)

    # 1. Form
    form = fpl_client.get_player_form(player_name)

    # 2. Fitness
    fitness = fpl_client.get_player_fitness(player_name)

    # 3. Position
    position = fpl_client.get_player_position(player_name)

    # 4. Off-field intel
    if skip_web_search:
        from .schema import OffFieldIntel
        off_field = OffFieldIntel(
            summary="Skipped (skip_web_search=True)",
            confidence=0.0,
        )
    else:
        off_field = fetch_off_field_intel(player_name, team=team)

    # 5. Matchup (if marker info provided)
    matchup: TacticalMatchup | None = None
    if marker_name:
        form_summary = (
            f"{form.goals_last_5}G {form.assists_last_5}A in last 5 GW, "
            f"xG {form.xg_last_5}, FPL form {form.fpl_form_rating}"
        )
        matchup = analyze_matchup(
            player_name=player_name,
            player_team=team,
            player_position=position,
            player_form_summary=form_summary,
            marker_name=marker_name,
            marker_team=marker_team,
            marker_position=marker_position,
        )

    fpl_id = fpl_client.find_player_id(player_name)
    return PlayerDossier(
        player=player_name,
        team=team,
        position=position,
        fpl_id=fpl_id,
        form=form,
        fitness=fitness,
        off_field=off_field,
        matchup=matchup,
        built_at=datetime.now(timezone.utc).isoformat(),
        sources=["FPL API", "Anthropic web_search" if not skip_web_search else "",
                 "GLM-5.1 via Wafer (matchup)" if matchup else ""],
    )


def build_match_dossiers(
    home_team: str, home_starters: list[str],
    away_team: str, away_starters: list[str],
    *,
    skip_web_search: bool = False,
    skip_matchups: bool = False,
    output_dir: str | Path = DOSSIER_DIR,
) -> dict[str, str]:
    """Build dossiers for both teams. Returns map of player → output path."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Building %d home + %d away dossiers → %s",
                len(home_starters), len(away_starters), output_dir)

    # Build basic FPL-lookup info on every player up front so matchups can
    # reference positions without re-querying.
    home_player_meta = []
    for name in home_starters:
        home_player_meta.append({
            "name": name,
            "position": fpl_client.get_player_position(name),
        })
    away_player_meta = []
    for name in away_starters:
        away_player_meta.append({
            "name": name,
            "position": fpl_client.get_player_position(name),
        })

    written: dict[str, str] = {}

    for meta in home_player_meta:
        marker = find_likely_marker(meta["position"], away_player_meta) if not skip_matchups else None
        dossier = build_dossier(
            meta["name"], home_team,
            skip_web_search=skip_web_search,
            marker_name=marker["name"] if marker else "",
            marker_team=away_team if marker else "",
            marker_position=marker["position"] if marker else "",
        )
        path = output_dir / _safe_filename(meta["name"])
        path.write_text(dossier.model_dump_json(indent=2))
        written[meta["name"]] = str(path)
        logger.info("✓ %s → %s", meta["name"], path)

    for meta in away_player_meta:
        marker = find_likely_marker(meta["position"], home_player_meta) if not skip_matchups else None
        dossier = build_dossier(
            meta["name"], away_team,
            skip_web_search=skip_web_search,
            marker_name=marker["name"] if marker else "",
            marker_team=home_team if marker else "",
            marker_position=marker["position"] if marker else "",
        )
        path = output_dir / _safe_filename(meta["name"])
        path.write_text(dossier.model_dump_json(indent=2))
        written[meta["name"]] = str(path)
        logger.info("✓ %s → %s", meta["name"], path)

    logger.info("Built %d dossiers in %s", len(written), output_dir)
    return written
