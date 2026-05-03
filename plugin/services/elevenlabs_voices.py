"""ElevenLabs voice catalog cache and naive personality matcher.

This is the minimum needed for prompt 4: fetch /v1/voices once, cache it
in memory, expose pick_voice() that scores voices against keyword tokens
in a personality string and falls back to a known-good voice id when no
match is found.

Fetch failures are non-fatal: pick_voice() always returns *some* voice id
so the cast phase keeps moving.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import Optional

import httpx

from .. import env


logger = logging.getLogger("sprite_studio.services.elevenlabs_voices")

# Per task spec: Rachel is the documented universal default. We try her
# first. The user's Creator-tier catalog (verified live) does not include
# Rachel, so if a fetched catalog lists voices and Rachel is absent, we
# fall back to George (verified narrator default in the captured catalog).
RACHEL_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
GEORGE_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # narrator default in this account
FALLBACK_VOICE_ID = RACHEL_VOICE_ID

VOICES_URL = "https://api.elevenlabs.io/v1/voices"
FETCH_TIMEOUT = 15.0


@dataclass(frozen=True)
class Voice:
    voice_id: str
    name: str
    labels: dict[str, str]  # gender, accent, age, use_case, descriptive
    description: str

    def label_blob(self) -> str:
        parts = [self.name, self.description] + list(self.labels.values())
        return " ".join(p for p in parts if p).lower()


_cache_lock = threading.Lock()
_voices: list[Voice] = []
_fetch_attempted = False
_fallback_voice_id: str = FALLBACK_VOICE_ID


# Keyword → label affinity. Each key is a token we look for in the
# personality string supplied by the cast designer; each value is a list
# of fragments to score against the voice's label blob.
_KEYWORD_AFFINITY: dict[str, list[str]] = {
    "warm": ["warm", "captivating", "playful", "bright"],
    "deep": ["deep", "resonant", "comforting", "smooth"],
    "young": ["young"],
    "old": ["old", "mature", "wise"],
    "feminine": ["female"],
    "masculine": ["male"],
    "british": ["british"],
    "american": ["american"],
    "narrator": ["narrative_story", "narrator", "storyteller"],
    "educator": ["informative_educational", "educator"],
}


def _parse_voice(raw: dict) -> Optional[Voice]:
    voice_id = raw.get("voice_id")
    name = raw.get("name") or ""
    if not isinstance(voice_id, str) or not voice_id:
        return None
    labels = raw.get("labels") or {}
    if not isinstance(labels, dict):
        labels = {}
    return Voice(
        voice_id=voice_id,
        name=name if isinstance(name, str) else "",
        labels={str(k): str(v) for k, v in labels.items() if v is not None},
        description=str(raw.get("description") or ""),
    )


def _select_fallback_locally(voices: list[Voice]) -> str:
    """Pick a fallback voice id. Chain: Rachel → George → first-in-catalog.

    Rachel-missing on this account is the documented happy path (verified
    Creator-tier catalog has 23 voices, no Rachel). We log at DEBUG, not
    WARN, when we drop down to George — WARN is reserved for genuine
    fetch failures.
    """
    if not voices:
        return RACHEL_VOICE_ID
    for v in voices:
        if v.voice_id == RACHEL_VOICE_ID:
            return RACHEL_VOICE_ID
    for v in voices:
        if v.voice_id == GEORGE_VOICE_ID:
            logger.debug(
                "Rachel (%s) not in catalog; falling back to George (%s)",
                RACHEL_VOICE_ID, GEORGE_VOICE_ID,
            )
            return GEORGE_VOICE_ID
    first = voices[0].voice_id
    logger.debug(
        "Rachel and George not in catalog; falling back to first voice %s",
        first,
    )
    return first


async def fetch_voice_catalog() -> list[Voice]:
    """Fetch /v1/voices once. On failure logs a warning and returns []."""
    global _voices, _fetch_attempted, _fallback_voice_id

    api_key = env.get_env("ELEVENLABS_API_KEY")
    if not api_key:
        logger.warning("ELEVENLABS_API_KEY missing; voice catalog skipped")
        with _cache_lock:
            _fetch_attempted = True
        return []

    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
            resp = await client.get(
                VOICES_URL, headers={"xi-api-key": api_key},
            )
            if resp.status_code != 200:
                logger.warning(
                    "ElevenLabs /v1/voices returned %d; using fallback voice id",
                    resp.status_code,
                )
                with _cache_lock:
                    _fetch_attempted = True
                return []
            payload = resp.json() or {}
    except Exception as exc:
        logger.warning("ElevenLabs /v1/voices fetch failed: %s", exc)
        with _cache_lock:
            _fetch_attempted = True
        return []

    raw_voices = payload.get("voices") or []
    parsed: list[Voice] = []
    for raw in raw_voices:
        v = _parse_voice(raw) if isinstance(raw, dict) else None
        if v is not None:
            parsed.append(v)

    with _cache_lock:
        _voices = parsed
        _fetch_attempted = True
        _fallback_voice_id = _select_fallback_locally(parsed)
    logger.info(
        "loaded %d ElevenLabs voices (fallback=%s)",
        len(parsed),
        _fallback_voice_id,
    )
    return parsed


def _score_voice(voice: Voice, tokens: list[str]) -> int:
    blob = voice.label_blob()
    score = 0
    for tok in tokens:
        for fragment in _KEYWORD_AFFINITY.get(tok, [tok]):
            if fragment in blob:
                score += 1
    return score


def pick_voice(voice_personality: Optional[str]) -> str:
    """Return the best-matching voice_id for a personality phrase.

    Always returns a voice id. If the catalog hasn't loaded or no voice
    matches, returns the configured fallback id.
    """
    with _cache_lock:
        voices = list(_voices)
        fallback = _fallback_voice_id

    if not voices or not voice_personality:
        return fallback

    tokens = [t for t in voice_personality.lower().split() if t]
    tokens = [t.strip(",.;:!?\"'") for t in tokens]
    tokens = [t for t in tokens if t]
    if not tokens:
        return fallback

    best_voice: Voice | None = None
    best_score = 0
    for v in voices:
        score = _score_voice(v, tokens)
        if score > best_score:
            best_score = score
            best_voice = v

    if best_voice is None or best_score == 0:
        return fallback
    return best_voice.voice_id


def fallback_voice_id() -> str:
    with _cache_lock:
        return _fallback_voice_id


def has_loaded() -> bool:
    with _cache_lock:
        return _fetch_attempted


async def ensure_loaded() -> None:
    """Lazy fetch helper for callers that don't run at plugin import."""
    if has_loaded():
        return
    await fetch_voice_catalog()


def schedule_initial_load() -> None:
    """Best-effort fire-and-forget catalog load at plugin import.

    If we are inside a running event loop (tests, workers), schedule the
    fetch as a background task. Otherwise spawn a one-shot loop in a
    daemon thread so the import is non-blocking and never raises.
    """
    if has_loaded():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        try:
            loop.create_task(fetch_voice_catalog())
        except Exception as exc:
            logger.debug("could not schedule voice fetch on running loop: %s", exc)
        return

    def _runner() -> None:
        try:
            asyncio.run(fetch_voice_catalog())
        except Exception as exc:
            logger.debug("voice fetch thread failed: %s", exc)

    t = threading.Thread(
        target=_runner, name="sprite-studio-voice-catalog", daemon=True,
    )
    try:
        t.start()
    except Exception as exc:
        logger.debug("voice fetch thread could not start: %s", exc)
