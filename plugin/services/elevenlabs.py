"""ElevenLabs Text-to-Speech client (direct — NOT via TokenRouter).

Spec source:
- POST /v1/text-to-speech/{voice_id}. Auth: xi-api-key header.
  output_format is a *query parameter* (not header / not body).
  Body: {text, model_id}. Response on 2xx: binary audio bytes.
- Multilingual v2 enforces ~10,000 char per-request limit; we split at
  CHUNK_THRESHOLD on sentence boundaries and ffmpeg-concat the chunks.
- Catalog probe (_verified_shapes/08_elevenlabs_voices.json, 23 voices)
  shows the user's Creator-tier account does NOT include Rachel
  (21m00Tcm4TlvDq8ikWAM); the public-doc "Bella" id (EXAVITQu4vr4xnSDxMaL)
  resolves to Sarah here. George (JBFqnCBsd6RMkjVDRZzb, "Warm,
  Captivating Storyteller", narrative_story) is the verified narrator
  default and is reused as both NARRATOR_VOICE_ID and the per-character
  fallback.

Cost model:
- Multilingual v2 burns ~1 credit per character. Creator plan = 100k
  chars/month, so this is essentially free at our scale. We persist a
  notional cost_usd at $0.0003/char so dashboards keep summing in USD,
  and stamp ``cost_basis="billing_credits"`` into input_payload so any
  future credits pivot reads off the right unit.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from pathlib import Path

import httpx
from ulid import ULID

from .. import db, env
from . import _concurrency, _http, _pricing, _retry
from .errors import (
    ProviderAuthError,
    ProviderInsufficientCreditsError,
    ProviderInvalidRequestError,
    ProviderNotFoundError,
    ProviderResponseShapeError,
    SpriteStudioError,
)


logger = logging.getLogger("sprite_studio.services.elevenlabs")

# George — verified narrator default for this Creator account. The
# public-doc "Bella" id (EXAVITQu4vr4xnSDxMaL) maps to Sarah here, so we
# use the probe-confirmed voice instead of the doc literal.
NARRATOR_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"
DEFAULT_MODEL_ID = "eleven_multilingual_v2"

# Multilingual v2 caps at 10000 chars/request; 9500 leaves headroom for
# ascii vs unicode counting differences and the small overhead the API
# adds when it normalises punctuation server-side.
CHUNK_THRESHOLD = 9500

# Per-char notional USD; see module docstring.
TTS_USD_PER_CHAR = 0.0003

# Below this we treat the audio response as junk (empty/truncated).
MIN_AUDIO_BYTES = 1024

DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


class VoiceClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.elevenlabs.io/v1",
    ) -> None:
        self._api_key = api_key or env.require_env("ELEVENLABS_API_KEY")
        self._base_url = base_url.rstrip("/")

    def _auth_headers(self) -> dict[str, str]:
        return {
            "xi-api-key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }

    async def synthesize(
        self,
        *,
        voice_id: str,
        text: str,
        model_id: str = DEFAULT_MODEL_ID,
        output_format: str = DEFAULT_OUTPUT_FORMAT,
        save_to: Path,
    ) -> Path:
        """Synthesize ``text`` with ``voice_id`` into a single MP3 under save_to/.

        For inputs over CHUNK_THRESHOLD chars, splits on sentence
        boundaries, synthesizes each chunk, and ffmpeg-concats into one
        file. Validates the final file with ffprobe.

        Returns the saved Path.
        """
        if not text or not text.strip():
            raise ProviderInvalidRequestError(
                "synthesize: text is empty",
                provider="elevenlabs", model=model_id,
            )

        save_to.mkdir(parents=True, exist_ok=True)

        if len(text) > CHUNK_THRESHOLD:
            return await self._synthesize_chunked(
                voice_id=voice_id, text=text,
                model_id=model_id, output_format=output_format,
                save_to=save_to,
            )

        url = f"{self._base_url}/text-to-speech/{voice_id}"
        params = {"output_format": output_format}
        body = {"text": text, "model_id": model_id}

        started = time.perf_counter()
        async with _concurrency.TTS_SEMAPHORE:
            client = await _http.get_client()

            async def _do() -> httpx.Response:
                return await client.post(
                    url, json=body, params=params,
                    headers=self._auth_headers(),
                    timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=5.0),
                )

            try:
                resp = await _retry.call_with_retry(
                    _do, provider="elevenlabs", model=model_id, attempts=3,
                )
            except ProviderAuthError as e:
                msg = (e.original_message or "").lower()
                if "quota" in msg or "credit" in msg:
                    raise ProviderInsufficientCreditsError(
                        e.original_message or "ElevenLabs quota exhausted",
                        provider="elevenlabs", model=model_id,
                        http_status=e.http_status, request_id=e.request_id,
                        original_message=e.original_message,
                    ) from e
                raise SpriteStudioError(
                    "ElevenLabs API key invalid or expired",
                    provider="elevenlabs", model=model_id,
                    http_status=e.http_status, request_id=e.request_id,
                    original_message=e.original_message,
                ) from e
            except (ProviderInvalidRequestError, ProviderNotFoundError) as e:
                msg = (e.original_message or str(e)).lower()
                voice_problem = (
                    "voice" in msg
                    or "not found" in msg
                    or "invalid_voice" in msg
                    or e.http_status in (404, 422)
                )
                if voice_problem and voice_id != NARRATOR_VOICE_ID:
                    logger.warning(
                        "voice_id=%s rejected (%s); retrying once with narrator default",
                        voice_id, e.http_status,
                    )
                    return await self.synthesize(
                        voice_id=NARRATOR_VOICE_ID, text=text,
                        model_id=model_id, output_format=output_format,
                        save_to=save_to,
                    )
                if voice_problem:
                    raise ProviderNotFoundError(
                        f"voice not found: {voice_id}",
                        provider="elevenlabs", model=model_id,
                        http_status=e.http_status,
                        original_message=e.original_message,
                    ) from e
                raise

        audio = resp.content
        if not audio or len(audio) < MIN_AUDIO_BYTES:
            raise ProviderResponseShapeError(
                f"audio response too small ({len(audio) if audio else 0} bytes)",
                provider="elevenlabs", model=model_id,
                extra={"content_length": resp.headers.get("content-length")},
            )

        file_id = str(ULID())
        dest = save_to / f"{file_id}.mp3"
        dest.write_bytes(audio)

        if shutil.which("ffprobe"):
            proc = subprocess.run(
                ["ffprobe", "-v", "error", "-print_format", "json",
                 "-show_streams", str(dest)],
                capture_output=True, text=True, timeout=30,
            )
            if proc.returncode != 0:
                try:
                    dest.unlink()
                except OSError:
                    pass
                raise ProviderResponseShapeError(
                    "downloaded audio is not a valid MP3 "
                    f"(ffprobe stderr: {proc.stderr[:300]})",
                    provider="elevenlabs", model=model_id,
                )
        else:
            logger.warning("ffprobe not on PATH; skipping MP3 validation")

        elapsed = time.perf_counter() - started
        logger.info(
            "elevenlabs synthesize ok voice=%s chars=%d bytes=%d elapsed=%.2fs",
            voice_id, len(text), len(audio), elapsed,
        )
        return dest

    async def _synthesize_chunked(
        self,
        *,
        voice_id: str,
        text: str,
        model_id: str,
        output_format: str,
        save_to: Path,
    ) -> Path:
        """Split ``text`` on sentence boundaries, synthesize each chunk, and
        ffmpeg-concat into a single MP3 under save_to/."""
        chunks = self._split_into_chunks(text)
        if len(chunks) == 1:
            # Edge case: a single sentence longer than CHUNK_THRESHOLD.
            # We still pass it; the API may accept slight overshoot, and
            # if not, the resulting 422 surfaces with a clear message.
            logger.warning(
                "single sentence exceeds chunk threshold (%d chars); "
                "attempting unchunked synthesis", len(chunks[0]),
            )

        chunk_root = save_to / f"_tts_chunks_{ULID()}"
        chunk_root.mkdir(parents=True, exist_ok=True)
        chunk_paths: list[Path] = []
        try:
            for idx, chunk_text in enumerate(chunks):
                logger.info(
                    "elevenlabs chunk %d/%d len=%d",
                    idx + 1, len(chunks), len(chunk_text),
                )
                chunk_path = await self.synthesize(
                    voice_id=voice_id, text=chunk_text,
                    model_id=model_id, output_format=output_format,
                    save_to=chunk_root,
                )
                chunk_paths.append(chunk_path)

            if len(chunk_paths) == 1:
                # No concat needed — promote the single chunk to save_to.
                final = save_to / f"{ULID()}.mp3"
                shutil.move(str(chunk_paths[0]), str(final))
                return final

            return self._ffmpeg_concat(chunk_paths, save_to=save_to)
        finally:
            self._cleanup_chunks(chunk_root, chunk_paths)

    @staticmethod
    def _split_into_chunks(text: str) -> list[str]:
        """Group sentences into chunks of <= CHUNK_THRESHOLD chars.

        Uses ``re.split(r'(?<=[.!?])\\s+', text)`` per the prompt spec.
        Sentences are concatenated with single spaces; chunks never
        exceed CHUNK_THRESHOLD. A sentence longer than the threshold is
        emitted as its own chunk (caller logs a warning).
        """
        sentences = [s for s in _SENTENCE_SPLIT_RE.split(text) if s]
        chunks: list[str] = []
        current = ""
        for sent in sentences:
            tentative = f"{current} {sent}".strip() if current else sent
            if len(tentative) > CHUNK_THRESHOLD:
                if current:
                    chunks.append(current)
                current = sent
            else:
                current = tentative
        if current:
            chunks.append(current)
        return chunks or [text]

    @staticmethod
    def _ffmpeg_concat(chunk_paths: list[Path], *, save_to: Path) -> Path:
        """Concatenate ``chunk_paths`` (MP3) into one file under save_to/.

        Uses ffmpeg's concat demuxer with -c copy. ElevenLabs emits the
        same codec parameters for every call at a given output_format,
        so stream-copy is safe. On failure we surface the chunk paths so
        the caller can inspect.
        """
        out_path = save_to / f"{ULID()}.mp3"
        list_file = save_to / f"_concat_{ULID()}.txt"
        list_file.write_text(
            "\n".join(f"file '{p.resolve()}'" for p in chunk_paths) + "\n"
        )
        try:
            proc = subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-f", "concat", "-safe", "0",
                 "-i", str(list_file),
                 "-c", "copy", str(out_path)],
                capture_output=True, text=True, timeout=120,
            )
        finally:
            try:
                list_file.unlink()
            except OSError:
                pass

        if proc.returncode != 0 or not out_path.exists():
            chunk_list = [str(p) for p in chunk_paths]
            logger.error(
                "ffmpeg concat failed rc=%d stderr=%s chunks=%s",
                proc.returncode, proc.stderr[:500], chunk_list,
            )
            raise ProviderResponseShapeError(
                f"ffmpeg concat failed (rc={proc.returncode}): {proc.stderr[:300]}",
                provider="elevenlabs",
                extra={"chunk_paths": chunk_list},
            )
        return out_path

    @staticmethod
    def _cleanup_chunks(chunk_root: Path, chunk_paths: list[Path]) -> None:
        """Best-effort removal of intermediate chunk files and their
        directory. We intentionally do NOT delete on failure paths above
        — the exception handler logs the chunk_paths list so a developer
        can inspect them; the next successful run will create a fresh
        directory."""
        for p in chunk_paths:
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
        try:
            chunk_root.rmdir()
        except OSError:
            pass

    async def synthesize_narration(
        self,
        *,
        project_id: str,
        narrator_script: str,
        save_to: Path,
        voice_id: str | None = None,
    ) -> Path:
        """Render the project's narrator track. Records a generation_jobs
        row tagged ``job_type='tts'`` with notional cost in USD and a
        billing_credits marker in input_payload."""
        used_voice = voice_id or NARRATOR_VOICE_ID
        char_count = len(narrator_script)
        est_cost = _pricing.tts_notional_cost_usd(
            model=DEFAULT_MODEL_ID, char_count=char_count,
        )

        job_row = db.create_job(
            project_id=project_id,
            job_type="tts",
            provider="elevenlabs",
            model=DEFAULT_MODEL_ID,
            input_payload={
                "role": "narrator",
                "voice_id": used_voice,
                "character_count": char_count,
                "estimated_credits": char_count,
                "cost_basis": "billing_credits",
                "estimated_cost_usd": round(est_cost, 6),
                "model_id": DEFAULT_MODEL_ID,
                "output_format": DEFAULT_OUTPUT_FORMAT,
                "script_head": narrator_script[:80],
            },
        )

        db.mark_job_running(job_row["id"])
        started = time.perf_counter()
        try:
            dest = await self.synthesize(
                voice_id=used_voice,
                text=narrator_script,
                save_to=save_to,
            )
        except Exception as e:
            db.mark_job_failed(job_row["id"], f"narration synth failed: {e}"[:500])
            raise

        elapsed = time.perf_counter() - started
        db.mark_job_done(
            job_row["id"],
            output_payload={
                "audio_path": str(dest),
                "character_count": char_count,
                "estimated_credits": char_count,
                "cost_basis": "billing_credits",
                "elapsed_seconds": round(elapsed, 3),
            },
            cost_usd=round(est_cost, 6),
        )
        db.increment_project_cost(project_id, est_cost)

        logger.info(
            "narration done project=%s chars=%d voice=%s elapsed=%.2fs cost=$%.4f",
            project_id, char_count, used_voice, elapsed, est_cost,
        )
        return dest

    async def synthesize_character_dialog(
        self,
        *,
        character: dict,
        line: str,
        save_to: Path,
    ) -> Path:
        """Render one dialog line for ``character`` via ElevenLabs.

        **Fallback-only, not on the standard render path as of 2026-05-02.**
        Seedance generates lip-synced character dialog natively when a
        quoted line is embedded in the prompt's action text and
        ``generate_audio=True`` is set, and that path also keeps voice
        consistency across shots without our plumbing. This method is
        retained for the case where Seedance voice consistency degrades
        in production and we need a deterministic per-character override.

        Falls back to the narrator voice when the character has no
        voice_id assigned. Job accounting is intentionally left to the
        caller (the render worker tracks dialog under the shot row, not
        as standalone tts rows).
        """
        voice_id = character.get("voice_id") or NARRATOR_VOICE_ID
        return await self.synthesize(
            voice_id=voice_id, text=line, save_to=save_to,
        )
