"""Seedance 2.0 image-to-video client (TokenRouter).

Spec source:
- Authoritative: _verified_shapes/_SUMMARY.md "CORRECTIONS - 2026-05-01 evening"
- Live captures: _verified_shapes/09_seedance_*_corrected.json (submit shape).
- Poll-success shape is per the published doc; not yet live-verified
  (the polling endpoint returned 403 for the test token in this environment).

Constraints encoded as policy:
- Image input is the "images" field (array of strings). Never the legacy
  single-string "image_url" form.
- All tunables (duration, resolution, ratio, generate_audio) live inside the
  "metadata" object; never at top level.
- Aspect ratio key is "ratio". Never "aspect_ratio".
- Polling is GET /v1/video/generations/{task_id}. Doc-spec response is
  wrapped in a "data" envelope; we read data.status and data.result_url.
- Terminal statuses are SUCCESS and FAILURE (uppercase per doc).
- There is no documented cancel endpoint. Hard timeout = 600s; on timeout
  we mark the job failed and return.
- The /openai prefix on api.tokenrouter.com is a wildcard 200 fallthrough
  and must never be used as a base_url here.

Native dialog generation (verified live 2026-05-02)
- With ``generate_audio=true`` and a quoted line in the prompt, Seedance
  produces lip-synced (or off-screen) speech inside the MP4. Verified
  task IDs: ``task_BZIWbyiBusEfs0tFcUQpDzwRJFJBXMJW`` (full dialog scene)
  plus a two-shot consistency pair where the same fox character kept the
  same voice across scenes.
- Format that works: ``'A red fox stands. The fox says: "You want to fight them?"'``
- Off-screen dialog: prefix the speech in the action text with
  ``Off-screen,`` to signal the speaker is not visible. Example:
  ``'Camera holds on the pigeon. Off-screen, Fox says: "I have a plan."'``

Voice consistency across shots
- Seedance picks character voices based on the visual description in the
  reference still and the prompt's character descriptors. To keep a
  character's voice consistent across shots:
  * Reuse the same reference still anchor for every appearance.
  * Describe the character with consistent descriptors ("the small red
    fox" / "Mira"), not shifting nouns ("the predator" → "Mira").
  * Keep dialog tone descriptors aligned. ``says, confidently`` vs
    ``says, nervously`` shifts vocal performance within the same
    character — use intentionally.
- Voice drift is observed but not deterministic; treat consistency as
  best-effort. P18 production runs are expected to surface concrete
  failure modes if any.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from ulid import ULID

from .. import db, env
from . import _concurrency, _http, _pricing, _retry
from .errors import (
    ProviderInvalidRequestError,
    ProviderResponseShapeError,
    ProviderServerError,
    ProviderTimeoutError,
    SpriteStudioError,
)


logger = logging.getLogger("sprite_studio.services.seedance")

MODEL_FAST = "dreamina-seedance-2-0-fast-260128"
MODEL_STANDARD = "dreamina-seedance-2-0-260128"
ALLOWED_MODELS = {MODEL_FAST, MODEL_STANDARD}

# Doc lists 5/8/10/12/15. We accept any int in 5..15 inclusive but warn for
# off-list values (provider may reject). Submit forwards the raw value.
ALLOWED_DURATIONS = {5, 8, 10, 12, 15}

ALLOWED_RESOLUTIONS = {"720p", "1080p"}
ALLOWED_RATIOS = {"9:16", "16:9"}

# Image upload cap. Provider has not published a hard limit; we downscale
# anything over 8 MB. Seedance generally tolerates <= 10 MB.
IMAGE_BYTE_CEILING = 8 * 1024 * 1024
IMAGE_LONG_EDGE_AFTER_DOWNSCALE = 1280
IMAGE_LONG_EDGE_FORCED = 960  # tighter retry size after a 413

POLL_INTERVAL_SECONDS = 5
POLL_HARD_TIMEOUT_SECONDS = 600

# Downloaded MP4 must clear this floor; below it we treat the file as junk.
MIN_VIDEO_BYTES = 50_000

_DEBUG_DIR = Path("~/.hermes/plugins/sprite-studio/projects/_debug").expanduser()


# Conservative dialog detection. False positives cost a small amount of
# extra generation tokens (Seedance handles audio-on with no actual
# dialog gracefully); false negatives miss intended speech, which is
# worse. Bias toward detection.
_DIALOG_PATTERNS = (
    # Speech verb followed by colon/comma + opening quote.
    re.compile(
        r"\b(says|said|replies|replied|whispers|whispered|shouts|shouted|"
        r"asks|asked|exclaims|exclaimed|murmurs|murmured|calls|called|"
        r"announces|announced|responds|responded)\b[,:]?\s*[\"'“]",
        re.IGNORECASE,
    ),
    # Off-screen speech marker.
    re.compile(
        r"\boff[-\s]?screen[,:]?\s+\w+\s+(says|said|replies|replied|whispers)",
        re.IGNORECASE,
    ),
    # Any quoted string of 2+ characters (Unicode curly or straight quotes).
    # Yes, this fires on descriptive quotes ("a sign reading 'OPEN'"). The
    # extra cost is small and documented; correctness on real dialog wins.
    re.compile(r"[\"“][^\"”]{2,}[\"”]"),
)


def _action_has_dialog(action: str) -> bool:
    """Return True if action text contains spoken dialog Seedance should voice.

    Triggers on: speech verbs followed by a quote (``says: "..."``,
    ``whispered, "..."``); off-screen markers (``Off-screen, X says:
    "..."``); or any quoted string of 2+ characters.

    False positives (e.g. ``a sign reading "OPEN"``) cost a small amount
    of generation tokens but produce a working video. False negatives
    miss intended speech, which is worse. Bias toward detection.
    """
    if not action:
        return False
    return any(p.search(action) for p in _DIALOG_PATTERNS)


class VideoClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.tokenrouter.com/v1",
    ) -> None:
        self._api_key = api_key or env.require_env("TOKENROUTER_API_KEY")
        self._base_url = base_url.rstrip("/")
        # The wildcard openai-prefix route returns empty 200 for anything;
        # fail loud if a caller misconfigures the base URL.
        if "/openai" in self._base_url:
            raise ProviderInvalidRequestError(
                "base_url must not contain the openai-prefix wildcard route",
                provider="tokenrouter",
            )

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def submit(
        self,
        *,
        model: str,
        image: Path,
        prompt: str,
        duration: int,
        ratio: str = "9:16",
        resolution: str = "720p",
        generate_audio: bool = False,
        project_id: str | None = None,
    ) -> dict:
        """Submit an image-to-video job.

        Returns {task_id, model, estimated_cost_usd, job_row_id, submit_response}.
        """
        if model not in ALLOWED_MODELS:
            raise ProviderInvalidRequestError(
                f"unknown seedance model: {model}",
                provider="tokenrouter", model=model,
            )
        if not 5 <= duration <= 15:
            raise ProviderInvalidRequestError(
                f"duration must be 5..15 (got {duration})",
                provider="tokenrouter", model=model,
            )
        if duration not in ALLOWED_DURATIONS:
            logger.warning(
                "duration %d not in canonical set %s",
                duration, sorted(ALLOWED_DURATIONS),
            )
        if resolution not in ALLOWED_RESOLUTIONS:
            raise ProviderInvalidRequestError(
                f"resolution must be one of {sorted(ALLOWED_RESOLUTIONS)}",
                provider="tokenrouter", model=model,
            )
        if ratio not in ALLOWED_RATIOS:
            raise ProviderInvalidRequestError(
                f"ratio must be one of {sorted(ALLOWED_RATIOS)}",
                provider="tokenrouter", model=model,
            )
        if not image.exists():
            raise ProviderInvalidRequestError(
                f"image file not found: {image}",
                provider="tokenrouter", model=model,
            )
        if len(prompt) < 10:
            raise ProviderInvalidRequestError(
                "prompt too short (min 10 chars)",
                provider="tokenrouter", model=model,
            )

        data_uri = await self._encode_image_data_uri(image)

        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "images": [data_uri],
            "metadata": {
                "duration": duration,
                "resolution": resolution,
                "ratio": ratio,
                "generate_audio": generate_audio,
            },
        }

        est_cost = _pricing.seedance_cost_usd(
            model=model, resolution=resolution, ratio=ratio,
            duration_seconds=duration,
        )

        job_row: dict | None = None
        if project_id:
            job_row = db.create_job(
                project_id=project_id,
                job_type="video_gen",
                provider="tokenrouter",
                model=model,
                input_payload={
                    "image_path": str(image),
                    "prompt_len": len(prompt),
                    "prompt_head": prompt[:80],
                    "duration": duration,
                    "resolution": resolution,
                    "ratio": ratio,
                    "generate_audio": generate_audio,
                    "estimated_cost_usd": round(est_cost, 4),
                },
            )

        url = f"{self._base_url}/video/generations"
        started = time.perf_counter()
        async with _concurrency.VIDEO_SEMAPHORE:
            client = await _http.get_client()

            async def _do() -> httpx.Response:
                return await client.post(
                    url, json=body, headers=self._auth_headers(),
                    timeout=httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=5.0),
                )

            try:
                if job_row:
                    db.mark_job_running(job_row["id"])
                resp = await _retry.call_with_retry(
                    _do, provider="tokenrouter", model=model, attempts=3,
                )
            except SpriteStudioError as e:
                # 413 falls through _retry.classify_response to the base
                # SpriteStudioError class. Recover by downscaling once and
                # re-submitting; surface anything else.
                if e.http_status == 413:
                    logger.warning(
                        "submit returned 413; retrying with downscaled image",
                    )
                    smaller_uri = await self._encode_image_data_uri(
                        image, force_downscale=True,
                    )
                    body["images"] = [smaller_uri]

                    async def _do2() -> httpx.Response:
                        return await client.post(
                            url, json=body, headers=self._auth_headers(),
                            timeout=httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=5.0),
                        )

                    try:
                        resp = await _retry.call_with_retry(
                            _do2, provider="tokenrouter", model=model, attempts=2,
                        )
                    except Exception as e2:
                        if job_row:
                            db.mark_job_failed(
                                job_row["id"],
                                f"submit failed (after downscale): {e2}",
                            )
                        raise
                else:
                    if job_row:
                        db.mark_job_failed(job_row["id"], f"submit failed: {e}")
                    raise
            except Exception as e:
                if job_row:
                    db.mark_job_failed(job_row["id"], f"submit failed: {e}")
                raise

        try:
            body_json = resp.json()
        except Exception as e:
            if job_row:
                db.mark_job_failed(job_row["id"], "non-json submit response")
            raise ProviderResponseShapeError(
                "non-json submit response",
                provider="tokenrouter", model=model,
            ) from e

        task_id = self._extract_task_id(body_json)
        if not task_id:
            self._dump_raw(body_json, "submit_no_task_id")
            if job_row:
                db.mark_job_failed(job_row["id"], "no task_id in submit response")
            raise ProviderResponseShapeError(
                "submit response missing task_id",
                provider="tokenrouter", model=model,
                extra={
                    "received_keys": list(body_json.keys())
                    if isinstance(body_json, dict) else None,
                },
            )

        if job_row:
            with db.txn() as conn:
                conn.execute(
                    "UPDATE generation_jobs SET external_job_id = ? WHERE id = ?",
                    (task_id, job_row["id"]),
                )

        elapsed = time.perf_counter() - started
        logger.info(
            "seedance submit ok task_id=%s model=%s dur=%ds res=%s ratio=%s "
            "elapsed=%.2fs est=$%.4f",
            task_id, model, duration, resolution, ratio, elapsed, est_cost,
        )
        return {
            "task_id": task_id,
            "model": model,
            "estimated_cost_usd": est_cost,
            "job_row_id": job_row["id"] if job_row else None,
            "submit_response": body_json,
        }

    @staticmethod
    def _extract_task_id(body: Any) -> str | None:
        """Tolerate both flat and {data:{...}}-wrapped submit responses.

        Verified live shape (09_seedance_*_corrected.json) is flat. The doc
        suggests a wrapped form for poll responses; we accept either here so
        a future submit-shape change cannot silently break us.
        """
        if not isinstance(body, dict):
            return None
        if isinstance(body.get("task_id"), str):
            return body["task_id"]
        if isinstance(body.get("id"), str) and body["id"].startswith("task_"):
            return body["id"]
        data = body.get("data")
        if isinstance(data, dict):
            if isinstance(data.get("task_id"), str):
                return data["task_id"]
            if isinstance(data.get("id"), str) and data["id"].startswith("task_"):
                return data["id"]
        return None

    async def poll(
        self,
        task_id: str,
        *,
        timeout_seconds: int = POLL_HARD_TIMEOUT_SECONDS,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        job_row_id: str | None = None,
    ) -> dict:
        """Poll until terminal. Returns {task_id, status, result_url, raw}."""
        url = f"{self._base_url}/video/generations/{task_id}"
        deadline = time.monotonic() + timeout_seconds
        last_status: str | None = None
        attempt = 0

        while True:
            attempt += 1
            if time.monotonic() > deadline:
                if job_row_id:
                    db.mark_job_failed(
                        job_row_id, f"poll timeout after {timeout_seconds}s",
                    )
                raise ProviderTimeoutError(
                    f"seedance poll timeout after {timeout_seconds}s",
                    provider="tokenrouter",
                )

            client = await _http.get_client()

            async def _do() -> httpx.Response:
                return await client.get(
                    url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0),
                )

            try:
                resp = await _retry.call_with_retry(
                    _do, provider="tokenrouter", model=None, attempts=3,
                )
            except Exception as e:
                if job_row_id:
                    db.mark_job_failed(job_row_id, f"poll error: {e}")
                raise

            try:
                body = resp.json()
            except Exception as e:
                raise ProviderResponseShapeError(
                    "non-json poll response", provider="tokenrouter",
                ) from e

            data = body.get("data") if isinstance(body, dict) else None
            if not isinstance(data, dict):
                self._dump_raw(body, f"poll_no_data_envelope_{task_id}")
                raise ProviderResponseShapeError(
                    "poll response missing data envelope",
                    provider="tokenrouter",
                    extra={
                        "top_level_keys": list(body.keys())
                        if isinstance(body, dict) else None,
                    },
                )

            status = (data.get("status") or "").upper()
            progress = data.get("progress")
            if status != last_status:
                logger.info(
                    "seedance poll task=%s status=%s progress=%s attempt=%d",
                    task_id, status, progress, attempt,
                )
                last_status = status

            if status == "SUCCESS":
                result_url = data.get("result_url") or data.get("video_url")
                if not result_url:
                    if job_row_id:
                        db.mark_job_failed(
                            job_row_id, "SUCCESS but no result_url",
                        )
                    raise ProviderResponseShapeError(
                        "poll SUCCESS but no result_url",
                        provider="tokenrouter",
                    )
                return {
                    "task_id": task_id,
                    "status": "SUCCESS",
                    "result_url": result_url,
                    "raw": body,
                }
            if status == "FAILURE":
                msg = (
                    body.get("message")
                    or (data.get("data") or {}).get("error")
                    or "seedance task failed"
                )
                if job_row_id:
                    db.mark_job_failed(job_row_id, str(msg)[:500])
                raise ProviderServerError(
                    f"seedance task failed: {msg}",
                    provider="tokenrouter",
                )
            # QUEUED / SUBMITTED / IN_PROGRESS - keep polling.
            await asyncio.sleep(poll_interval)

    async def download(self, video_url: str, save_to: Path) -> Path:
        """Stream the MP4 to disk and validate via ffprobe."""
        save_to.mkdir(parents=True, exist_ok=True)
        file_id = str(ULID())
        dest = save_to / f"{file_id}.mp4"

        attempts = 3
        last_err: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                client = await _http.get_client()
                async with client.stream(
                    "GET", video_url,
                    timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=5.0),
                ) as r:
                    if r.status_code != 200:
                        # 403 here means the signed CDN URL likely expired;
                        # caller decides whether to re-poll for a fresh one.
                        raise ProviderServerError(
                            f"download HTTP {r.status_code}",
                            provider="tokenrouter",
                            http_status=r.status_code,
                        )
                    written = 0
                    try:
                        with open(dest, "wb") as f:
                            async for chunk in r.aiter_bytes(chunk_size=1024 * 1024):
                                if not chunk:
                                    continue
                                f.write(chunk)
                                written += len(chunk)
                    except OSError as e:
                        if dest.exists():
                            try:
                                dest.unlink()
                            except OSError:
                                pass
                        raise SpriteStudioError(
                            f"local write failed during video download: {e}",
                            provider="local",
                        ) from e

                if written < MIN_VIDEO_BYTES:
                    if dest.exists():
                        try:
                            dest.unlink()
                        except OSError:
                            pass
                    raise ProviderResponseShapeError(
                        f"downloaded video too small ({written} bytes)",
                        provider="tokenrouter",
                    )

                if shutil.which("ffprobe"):
                    proc = subprocess.run(
                        ["ffprobe", "-v", "error", "-print_format", "json",
                         "-show_streams", str(dest)],
                        capture_output=True, text=True, timeout=30,
                    )
                    if proc.returncode != 0:
                        if dest.exists():
                            try:
                                dest.unlink()
                            except OSError:
                                pass
                        raise ProviderResponseShapeError(
                            "downloaded file is not a valid MP4 "
                            f"(ffprobe stderr: {proc.stderr[:300]})",
                            provider="tokenrouter",
                        )
                else:
                    logger.warning("ffprobe not on PATH; skipping MP4 validation")

                return dest

            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
                last_err = e
                logger.warning("download attempt %d failed: %s", attempt, e)
                if dest.exists():
                    try:
                        dest.unlink()
                    except OSError:
                        pass
                if attempt == attempts:
                    break
                await asyncio.sleep(2 ** attempt)
            except ProviderServerError:
                # Bubble up immediately so callers (image_to_video) can decide
                # whether to re-poll for a fresh signed URL.
                raise

        assert last_err is not None
        raise ProviderTimeoutError(
            f"download failed after {attempts} attempts: {last_err}",
            provider="tokenrouter",
        )

    async def image_to_video(
        self,
        *,
        model: str,
        image: Path,
        prompt: str,
        duration: int,
        ratio: str = "9:16",
        resolution: str = "720p",
        save_to: Path,
        project_id: Optional[str] = None,
        has_dialog: Optional[bool] = None,
        generate_audio: Optional[bool] = None,
    ) -> Path:
        """Convenience: submit + poll + download. Returns the saved MP4 path.

        Audio routing precedence (highest first):

        1. ``generate_audio`` explicitly True/False  → use it (``explicit_request``).
        2. ``has_dialog`` flag passed by caller       → mirror it
           (``has_dialog_flag`` / ``has_dialog_flag_off``).
        3. Regex on the prompt text                   → ``dialog_detected_in_prompt``
           if a quoted line is found, else ``no_dialog_signal``.

        The resolved boolean and reason are persisted in the job row's
        ``output_payload`` so audit logs can answer "why was audio on for
        this shot?" without replaying the prompt through the regex.
        """
        if generate_audio is not None:
            final_audio = bool(generate_audio)
            audio_reason = "explicit_request"
        elif has_dialog is True:
            final_audio = True
            audio_reason = "has_dialog_flag"
        elif has_dialog is False:
            final_audio = False
            audio_reason = "has_dialog_flag_off"
        elif _action_has_dialog(prompt):
            final_audio = True
            audio_reason = "dialog_detected_in_prompt"
        else:
            final_audio = False
            audio_reason = "no_dialog_signal"

        logger.info(
            "seedance audio decision: %s (reason=%s) project=%s",
            final_audio, audio_reason, project_id,
        )

        submit_result = await self.submit(
            model=model, image=image, prompt=prompt,
            duration=duration, ratio=ratio, resolution=resolution,
            generate_audio=final_audio, project_id=project_id,
        )
        task_id: str = submit_result["task_id"]
        job_row_id: str | None = submit_result["job_row_id"]
        est_cost: float = submit_result["estimated_cost_usd"]

        poll_result = await self.poll(task_id, job_row_id=job_row_id)
        result_url: str = poll_result["result_url"]

        try:
            try:
                dest = await self.download(result_url, save_to=save_to)
            except ProviderServerError as e:
                if e.http_status != 403:
                    raise
                logger.warning(
                    "download 403 (signed URL expired?); re-polling once",
                )
                poll_result = await self.poll(
                    task_id, job_row_id=job_row_id, timeout_seconds=60,
                )
                result_url = poll_result["result_url"]
                dest = await self.download(result_url, save_to=save_to)
        except Exception as e:
            # Any failure after a successful poll-SUCCESS lands here; poll()
            # already handled its own errors. Mark this row failed once.
            if job_row_id:
                db.mark_job_failed(job_row_id, f"download failed: {e}")
            raise

        # Prefer the provider-billed token count as ground truth; fall back
        # to our pre-flight estimate only if the usage block is absent.
        actual_tokens = self._extract_billed_tokens(poll_result["raw"])
        if actual_tokens:
            actual_cost = _pricing.seedance_cost_from_tokens(
                model=model, tokens=actual_tokens,
            )
            cost_source = "provider_usage"
        else:
            actual_cost = est_cost
            cost_source = "estimate_fallback"

        if job_row_id:
            db.mark_job_done(
                job_row_id,
                output_payload={
                    "task_id": task_id,
                    "result_url_final": "<sanitized>",
                    "video_path": str(dest),
                    "raw_status": poll_result["raw"].get("data", {}).get("status"),
                    "billed_tokens": actual_tokens,
                    "estimated_cost_usd": round(est_cost, 6),
                    "actual_cost_usd": round(actual_cost, 6),
                    "cost_source": cost_source,
                    "generate_audio": final_audio,
                    "audio_reason": audio_reason,
                },
                cost_usd=actual_cost,
            )
            if project_id:
                db.increment_project_cost(project_id, actual_cost)

        logger.info(
            "seedance i2v done task=%s -> %s actual=$%.4f estimate=$%.4f "
            "tokens=%s source=%s audio=%s reason=%s",
            task_id, dest, actual_cost, est_cost, actual_tokens, cost_source,
            final_audio, audio_reason,
        )
        return dest

    @staticmethod
    def _extract_billed_tokens(raw: Any) -> int | None:
        """Return data.data.usage.completion_tokens if present, else None.

        Provider-billed token count lives in the nested ``data.data.usage``
        block on the poll-success response. Verified live 2026-05-02.
        """
        if not isinstance(raw, dict):
            return None
        outer = raw.get("data")
        if not isinstance(outer, dict):
            return None
        inner = outer.get("data")
        if not isinstance(inner, dict):
            return None
        usage = inner.get("usage")
        if not isinstance(usage, dict):
            return None
        tokens = usage.get("completion_tokens")
        if isinstance(tokens, int) and tokens > 0:
            return tokens
        return None

    async def _encode_image_data_uri(
        self,
        image: Path,
        *,
        force_downscale: bool = False,
    ) -> str:
        """Encode the local file as a base64 data URI suitable for the
        ``images`` array. Always prefer data URIs over hosted URLs for
        Sprite Studio inputs.

        Why we don't pass URLs: BytePlus's upstream image fetcher rejects
        certain hosts. Verified live: Wikipedia URLs return
        ``fail_to_fetch_task`` 400 (User-Agent block); ``raw.githubusercontent.com``,
        ``picsum.photos``, and ``data:image/...;base64,...`` URIs all fetch
        cleanly. Reference frames in this plugin are produced locally by
        ImageClient and never hosted, so the data-URI path is the only one
        we exercise.
        """
        raw = image.read_bytes()
        too_big = len(raw) > IMAGE_BYTE_CEILING
        if too_big or force_downscale:
            long_edge = (
                IMAGE_LONG_EDGE_FORCED if force_downscale
                else IMAGE_LONG_EDGE_AFTER_DOWNSCALE
            )
            raw = await asyncio.to_thread(
                self._downscale_png_bytes, raw, long_edge,
            )
            logger.info(
                "downscaled image %s to %d bytes (long_edge=%d)",
                image, len(raw), long_edge,
            )
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:image/png;base64,{b64}"

    @staticmethod
    def _downscale_png_bytes(raw: bytes, long_edge: int) -> bytes:
        from PIL import Image

        img = Image.open(io.BytesIO(raw))
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        w, h = img.size
        if max(w, h) <= long_edge:
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            return buf.getvalue()
        scale = long_edge / max(w, h)
        new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
        img = img.resize(new_size, Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    @staticmethod
    def _dump_raw(obj: Any, tag: str) -> None:
        try:
            _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        p = _DEBUG_DIR / f"{int(time.time())}_seedance_{tag}.json"
        try:
            safe = json.loads(json.dumps(obj, default=str))
        except (TypeError, ValueError):
            safe = {"_unserializable_repr": repr(obj)[:5000]}
        if isinstance(safe, dict) and "images" in safe:
            safe["images"] = [
                "<data uri redacted>" if isinstance(s, str) and s.startswith("data:") else s
                for s in safe.get("images", []) or []
            ]
        try:
            p.write_text(json.dumps(safe, indent=2))
        except OSError:
            return
        logger.warning("seedance debug dump: %s", p)
