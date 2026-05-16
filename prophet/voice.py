"""ElevenLabs voice synthesis for the broadcast commentary.

Streaming text-to-speech with the Flash latency model so audio starts playing
before the text is even finished generating.

Falls back to "no audio" mode silently if ELEVENLABS_API_KEY is not set,
so the rest of the pipeline still works.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# Voice IDs from ElevenLabs's public library. These are good defaults for
# a sports-broadcast feel; swap for your own clones if you have them.
DEFAULT_VOICES = {
    "analyst": "JBFqnCBsd6RMkjVDRZzb",      # "George" — measured, deep, broadcast-y
    "play_by_play": "TX3LPaxmHKxFdv7VOQHJ", # "Liam" — energetic, faster delivery
    "color": "pNInz6obpgDQGcFmaJgB",        # "Adam" — warm, analytical
}

API_BASE = "https://api.elevenlabs.io/v1"
MODEL = "eleven_flash_v2_5"                  # Fastest streaming model


def is_configured() -> bool:
    return bool(os.environ.get("ELEVENLABS_API_KEY"))


def synthesize(
    text: str,
    voice: str = "analyst",
    *,
    output_path: str | Path | None = None,
) -> bytes | None:
    """Synthesize speech for a single line of text.

    Returns audio bytes (MP3), or None if ElevenLabs is not configured.
    If output_path is given, also writes the audio to disk.
    """
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        logger.debug("ElevenLabs not configured; skipping synthesis.")
        return None

    voice_id = DEFAULT_VOICES.get(voice, voice)  # Allow direct ID
    url = f"{API_BASE}/text-to-speech/{voice_id}"
    payload = {
        "text": text,
        "model_id": MODEL,
        "voice_settings": {
            "stability": 0.4,
            "similarity_boost": 0.75,
            "style": 0.3,
            "use_speaker_boost": True,
        },
    }
    headers = {"xi-api-key": key, "Content-Type": "application/json"}

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("ElevenLabs synth failed: %s", exc)
        return None

    audio = resp.content
    if output_path:
        Path(output_path).write_bytes(audio)
    return audio


def stream_url(text: str, voice: str = "analyst") -> str:
    """Return a URL the frontend can request directly for streaming audio.

    The frontend uses this to make the browser do the network round-trip
    instead of round-tripping through our server.
    """
    voice_id = DEFAULT_VOICES.get(voice, voice)
    return f"{API_BASE}/text-to-speech/{voice_id}/stream"
