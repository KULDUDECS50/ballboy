"""Off-field intelligence agent — web_search per player.

Uses Anthropic's web_search tool to surface recent news about a player that
might affect their performance. Costs ~$0.05-0.10 per player.

If ANTHROPIC_API_KEY isn't set, returns a neutral placeholder so the dossier
builder doesn't crash.

OUTPUT
------
OffFieldIntel object with:
    sentiment (positive/neutral/negative)
    summary (2-3 factual sentences)
    performance_risk (low/medium/high)
    key_signals (bullet points)
    sources (URLs)
    confidence (0-1)
"""

from __future__ import annotations

import json
import logging
import os
import re

from .schema import OffFieldIntel

logger = logging.getLogger(__name__)


WEB_SEARCH_PROMPT = """Search for any news from the last 7 days about {player_name} ({team}) that could affect their performance in an upcoming match.

Focus on:
- Injuries, knocks, fitness concerns
- Off-field issues (controversy, family events, social media posts)
- Recent interview quotes suggesting mood, motivation, or focus
- Transfer or contract speculation
- Personal milestones (birth, marriage, bereavement)

Be skeptical of tabloid sources. Weight verified outlets (BBC, The Athletic, Sky Sports, ESPN, club official sites) higher than tabloids (The Sun, Daily Mail).

If you find NOTHING notable in the last 7 days, respond honestly with sentiment=neutral and confidence=0.9.

Return ONLY this JSON (no markdown fences, no preamble):
{{
  "sentiment": "positive" | "neutral" | "negative",
  "summary": "<2-3 sentences, factual, no speculation>",
  "performance_risk": "low" | "medium" | "high",
  "key_signals": ["<short bullet>", "<short bullet>"],
  "sources": ["<url1>", "<url2>"],
  "confidence": <0.0-1.0>
}}"""


def _extract_json(text: str) -> dict:
    """Extract a JSON object from possibly-fenced/preamble-wrapped text."""
    text = text.strip()
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = text.replace("```", "").strip()
    # Find first balanced { ... }
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                return json.loads(text[start:i+1])
    return json.loads(text)


def fetch_off_field_intel(player_name: str, team: str = "") -> OffFieldIntel:
    """Run a web_search query for off-field news on a player."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.info("ANTHROPIC_API_KEY not set — returning neutral placeholder for %s",
                    player_name)
        return OffFieldIntel(
            sentiment="unknown",
            summary="Off-field intel disabled (no ANTHROPIC_API_KEY).",
            performance_risk="unknown",
            confidence=0.0,
        )

    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package not available — skipping off-field intel")
        return OffFieldIntel(summary="Anthropic package not installed.")

    client = anthropic.Anthropic(api_key=api_key)
    prompt = WEB_SEARCH_PROMPT.format(player_name=player_name, team=team or "their team")

    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=800,
            tools=[{"type": "web_search_20250305", "name": "web_search",
                    "max_uses": 3}],
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        logger.warning("Web search call failed for %s: %s", player_name, exc)
        return OffFieldIntel(
            summary=f"Web search failed: {exc}",
            confidence=0.0,
        )

    # Extract the final text response from the (possibly tool-using) message
    text_parts = []
    for block in response.content:
        if hasattr(block, "text"):
            text_parts.append(block.text)
    raw = "\n".join(text_parts).strip()
    if not raw:
        return OffFieldIntel(summary="Web search returned no text.", confidence=0.0)

    try:
        parsed = _extract_json(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("JSON parse failed for %s off-field intel: %s",
                       player_name, exc)
        return OffFieldIntel(
            summary=f"Could not parse web search output: {raw[:200]}",
            confidence=0.0,
        )

    # Validate via Pydantic
    try:
        return OffFieldIntel(**parsed)
    except Exception as exc:
        logger.warning("OffFieldIntel validation failed: %s. Raw: %s", exc, parsed)
        return OffFieldIntel(
            summary=str(parsed.get("summary", ""))[:500] or "Validation failed",
            confidence=0.0,
        )
