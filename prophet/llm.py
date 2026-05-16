"""Thin wrapper around Wafer's OpenAI-compatible LLM endpoint.

Centralizes:
  - Client creation (Wafer @ pass.wafer.ai/v1, OpenAI-compatible, GLM-5.1)
  - JSON-mode parsing (strips markdown fences, validates floats)
  - Light retry logic for transient errors
  - Hook point for LMCache / Tensormesh integration later
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import openai

from .config import FAST_MODEL, PRIMARY_MODEL

logger = logging.getLogger(__name__)

WAFER_BASE_URL = "https://pass.wafer.ai/v1"

_client: openai.OpenAI | None = None


def get_client() -> openai.OpenAI:
    """Return a cached OpenAI-compatible client routed through Wafer."""
    global _client
    if _client is not None:
        return _client

    api_key = os.environ.get("WAFER_API_KEY")
    if not api_key:
        raise RuntimeError("Set WAFER_API_KEY in .env (Wafer is required)")
    _client = openai.OpenAI(api_key=api_key, base_url=WAFER_BASE_URL)
    logger.info("Using Wafer-routed OpenAI client at %s", WAFER_BASE_URL)
    return _client


def _strip_fences(text: str) -> str:
    """Remove ```json ... ``` markdown fencing if the model added it."""
    text = text.strip()
    if text.startswith("```"):
        # Drop opening fence (and optional language tag)
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _extract_json(text: str) -> dict[str, Any]:
    """Parse JSON tolerantly: try as-is, then brace-balanced slice."""
    text = _strip_fences(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Brace-balanced extraction: find the first `{` and walk to its match.
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = text[start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    start = -1
                    continue

    # Last-resort regex (handles weird trailing junk after a top-level object).
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if m:
        return json.loads(m.group(0))
    raise json.JSONDecodeError("no JSON object found", text, 0)


def call_json(
    system: str,
    user: str,
    *,
    model: str | None = None,
    max_tokens: int = 2500,
    temperature: float = 0.7,
    retries: int = 2,
) -> dict[str, Any]:
    """Call the model and parse the response as JSON.

    Args:
        system: System prompt.
        user: User prompt.
        model: Override the model. Defaults to PRIMARY_MODEL.
        max_tokens: Output token budget (shared with hidden reasoning on GLM-5.1).
        temperature: 0 for deterministic, higher for self-consistency sampling.
        retries: How many times to retry on JSON parse failure.

    Returns:
        Parsed JSON dict. Raises on persistent failure.
    """
    client = get_client()
    model = model or PRIMARY_MODEL

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
            )
            choice = response.choices[0]
            text = choice.message.content
            if not text:
                # Reasoning models sometimes burn the budget on hidden reasoning
                # and emit nothing visible. Try reasoning_content as a fallback.
                text = getattr(choice.message, "reasoning_content", None) or ""
            if not text:
                raise ValueError(
                    f"empty content (finish_reason={choice.finish_reason})"
                )
            return _extract_json(text)
        except (json.JSONDecodeError, KeyError, IndexError, AttributeError, TypeError, ValueError) as exc:
            last_err = exc
            logger.warning("JSON parse failed (attempt %d): %s", attempt + 1, exc)
            time.sleep(0.5 * (attempt + 1))
        except openai.APIError as exc:
            last_err = exc
            logger.warning("API error (attempt %d): %s", attempt + 1, exc)
            time.sleep(1.0 * (attempt + 1))

    raise RuntimeError(f"call_json failed after {retries + 1} attempts: {last_err}")


def call_fast(system: str, user: str, **kwargs) -> dict[str, Any]:
    """Convenience wrapper using the fast/cheap model."""
    return call_json(system, user, model=FAST_MODEL, **kwargs)
