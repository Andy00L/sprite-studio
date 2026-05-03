"""gpt-5.4-image-2 client (text-to-image and image edit).

IMPORTANT MODEL CONSTRAINTS (verified live, see _SUMMARY.md CORRECTIONS section):
- input_fidelity is REJECTED on edits for openai/gpt-5.4-image-2.
  The underlying model (gpt-image-2-2026-04-21) auto-processes inputs at high fidelity.
  We never pass the field. Not even as a kwarg with default None — just absent.
- The background field is unsupported on this model; we never pass it.
- Response data[i] only has b64_json. No url field, no revised_prompt.
"""
from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import Any

import httpx
from ulid import ULID

from .. import db, env
from . import _concurrency, _http, _pricing, _retry
from .errors import (
    ProviderInvalidRequestError,
    ProviderResponseShapeError,
    SpriteStudioError,
)


logger = logging.getLogger("sprite_studio.services.gpt_image")

IMAGE_MODEL = "openai/gpt-5.4-image-2"

SIZE_SQUARE = "1024x1024"
SIZE_PORTRAIT = "1024x1536"
SIZE_LANDSCAPE = "1536x1024"
ALLOWED_SIZES = {SIZE_SQUARE, SIZE_PORTRAIT, SIZE_LANDSCAPE}

QUALITY_LOW = "low"
QUALITY_MEDIUM = "medium"
QUALITY_HIGH = "high"
ALLOWED_QUALITIES = {QUALITY_LOW, QUALITY_MEDIUM, QUALITY_HIGH}

_DEBUG_DIR = Path("~/.hermes/plugins/sprite-studio/projects/_debug").expanduser()


class ImageClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.tokenrouter.com/v1",
    ) -> None:
        self._api_key = api_key or env.require_env("TOKENROUTER_API_KEY")
        self._base_url = base_url.rstrip("/")

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def generate(
        self,
        *,
        prompt: str,
        size: str = SIZE_SQUARE,
        quality: str = QUALITY_MEDIUM,
        n: int = 1,
        save_to: Path,
        project_id: str | None = None,
    ) -> list[Path]:
        """Text-to-image. Returns a list of saved file paths."""
        if size not in ALLOWED_SIZES:
            raise ProviderInvalidRequestError(
                f"invalid size {size}", provider="tokenrouter", model=IMAGE_MODEL,
            )
        if quality not in ALLOWED_QUALITIES:
            raise ProviderInvalidRequestError(
                f"invalid quality {quality}", provider="tokenrouter", model=IMAGE_MODEL,
            )
        if not 1 <= n <= 4:
            raise ProviderInvalidRequestError(
                f"n must be 1..4 (got {n})", provider="tokenrouter", model=IMAGE_MODEL,
            )
        if len(prompt) < 5:
            raise ProviderInvalidRequestError(
                "prompt too short", provider="tokenrouter", model=IMAGE_MODEL,
            )

        save_to.mkdir(parents=True, exist_ok=True)
        body: dict[str, Any] = {
            "model": IMAGE_MODEL,
            "prompt": prompt,
            "size": size,
            "quality": quality,
            "n": n,
        }

        job_row: dict | None = None
        if project_id:
            job_row = db.create_job(
                project_id=project_id,
                job_type="image_gen",
                provider="tokenrouter",
                model=IMAGE_MODEL,
                input_payload={
                    "size": size, "quality": quality, "n": n,
                    "prompt_len": len(prompt),
                },
            )

        started = time.perf_counter()
        async with _concurrency.IMAGE_SEMAPHORE:
            client = await _http.get_client()
            url = f"{self._base_url}/images/generations"

            async def _do() -> httpx.Response:
                return await client.post(
                    url,
                    json=body,
                    headers={**self._auth_headers(), "Content-Type": "application/json"},
                    timeout=httpx.Timeout(
                        connect=_http.HTTP_CONNECT,
                        read=_http.HTTP_READ_IMAGE,
                        write=30.0,
                        pool=5.0,
                    ),
                )

            try:
                if job_row:
                    db.mark_job_running(job_row["id"])
                resp = await _retry.call_with_retry(
                    _do, provider="tokenrouter", model=IMAGE_MODEL,
                )
            except Exception as e:
                if job_row:
                    db.mark_job_failed(job_row["id"], str(e))
                raise

        elapsed = time.perf_counter() - started
        try:
            paths = self._extract_and_save(resp, save_to=save_to)
            usage = (resp.json() or {}).get("usage", {}) or {}
        except SpriteStudioError as e:
            if job_row:
                db.mark_job_failed(job_row["id"], str(e))
            raise

        cost = _pricing.image_cost_usd(IMAGE_MODEL, usage)

        if job_row:
            db.mark_job_done(
                job_row["id"],
                output_payload={
                    "usage": usage,
                    "elapsed_seconds": round(elapsed, 3),
                    "image_count": len(paths),
                },
                cost_usd=cost,
            )
            db.increment_project_cost(project_id, cost)

        logger.info(
            "image_gen ok model=%s size=%s quality=%s n=%d elapsed=%.2fs cost=$%.4f",
            IMAGE_MODEL, size, quality, len(paths), elapsed, cost,
        )
        return paths

    async def edit(
        self,
        *,
        prompt: str,
        images: list[Path],
        size: str = SIZE_SQUARE,
        quality: str = QUALITY_MEDIUM,
        mask: Path | None = None,
        save_to: Path,
        project_id: str | None = None,
    ) -> Path:
        """Image edit / multi-reference. Returns the saved file path."""
        if size not in ALLOWED_SIZES:
            raise ProviderInvalidRequestError(
                f"invalid size {size}", provider="tokenrouter", model=IMAGE_MODEL,
            )
        if quality not in ALLOWED_QUALITIES:
            raise ProviderInvalidRequestError(
                f"invalid quality {quality}", provider="tokenrouter", model=IMAGE_MODEL,
            )
        if not 1 <= len(images) <= 16:
            raise ProviderInvalidRequestError(
                f"images must be 1..16 (got {len(images)})",
                provider="tokenrouter", model=IMAGE_MODEL,
            )
        for p in images:
            if not p.exists():
                raise ProviderInvalidRequestError(
                    f"reference image missing: {p}",
                    provider="tokenrouter", model=IMAGE_MODEL,
                )
        if mask is not None and not mask.exists():
            raise ProviderInvalidRequestError(
                f"mask missing: {mask}",
                provider="tokenrouter", model=IMAGE_MODEL,
            )

        save_to.mkdir(parents=True, exist_ok=True)

        # Multipart form. We do NOT pass input_fidelity (rejected). We do NOT
        # pass background. n=1 by default.
        data_fields = [
            ("model", IMAGE_MODEL),
            ("prompt", prompt),
            ("size", size),
            ("quality", quality),
            ("n", "1"),
        ]

        open_handles: list[Any] = []
        file_parts: list[tuple[str, tuple[str, Any, str]]] = []
        job_row: dict | None = None
        try:
            for p in images:
                fh = open(p, "rb")
                open_handles.append(fh)
                file_parts.append(("image[]", (p.name, fh, "image/png")))
            if mask is not None:
                mh = open(mask, "rb")
                open_handles.append(mh)
                file_parts.append(("mask", (mask.name, mh, "image/png")))

            if project_id:
                job_row = db.create_job(
                    project_id=project_id,
                    job_type="image_edit",
                    provider="tokenrouter",
                    model=IMAGE_MODEL,
                    input_payload={
                        "size": size, "quality": quality,
                        "image_count": len(images),
                        "has_mask": mask is not None,
                        "prompt_len": len(prompt),
                    },
                )

            started = time.perf_counter()
            async with _concurrency.IMAGE_SEMAPHORE:
                client = await _http.get_client()
                url = f"{self._base_url}/images/edits"

                async def _do() -> httpx.Response:
                    # Reset file pointers each retry (httpx consumes streams).
                    for fh in open_handles:
                        fh.seek(0)
                    return await client.post(
                        url,
                        data=dict(data_fields),
                        files=file_parts,
                        headers=self._auth_headers(),
                        timeout=httpx.Timeout(
                            connect=_http.HTTP_CONNECT,
                            read=_http.HTTP_READ_IMAGE,
                            write=60.0,
                            pool=5.0,
                        ),
                    )

                try:
                    if job_row:
                        db.mark_job_running(job_row["id"])
                    resp = await _retry.call_with_retry(
                        _do, provider="tokenrouter", model=IMAGE_MODEL,
                    )
                except Exception as e:
                    if job_row:
                        db.mark_job_failed(job_row["id"], str(e))
                    raise
        finally:
            for fh in open_handles:
                try:
                    fh.close()
                except Exception:
                    pass

        elapsed = time.perf_counter() - started
        try:
            paths = self._extract_and_save(resp, save_to=save_to)
        except SpriteStudioError as e:
            if job_row:
                db.mark_job_failed(job_row["id"], str(e))
            raise
        if not paths:
            if job_row:
                db.mark_job_failed(job_row["id"], "edit returned 0 images")
            raise ProviderResponseShapeError(
                "edit returned 0 images",
                provider="tokenrouter", model=IMAGE_MODEL,
            )

        usage = (resp.json() or {}).get("usage", {}) or {}
        cost = _pricing.image_cost_usd(IMAGE_MODEL, usage)

        if job_row:
            db.mark_job_done(
                job_row["id"],
                output_payload={
                    "usage": usage,
                    "elapsed_seconds": round(elapsed, 3),
                },
                cost_usd=cost,
            )
            db.increment_project_cost(project_id, cost)

        logger.info(
            "image_edit ok model=%s inputs=%d size=%s quality=%s elapsed=%.2fs cost=$%.4f",
            IMAGE_MODEL, len(images), size, quality, elapsed, cost,
        )
        return paths[0]

    def _extract_and_save(
        self,
        resp: httpx.Response,
        *,
        save_to: Path,
    ) -> list[Path]:
        try:
            data = resp.json()
        except Exception as e:
            self._dump_raw(resp, "non_json")
            raise ProviderResponseShapeError(
                "non-json image response",
                provider="tokenrouter", model=IMAGE_MODEL,
            ) from e
        items = data.get("data") or []
        if not items:
            self._dump_raw(resp, "empty_data")
            raise ProviderResponseShapeError(
                "response data array empty",
                provider="tokenrouter", model=IMAGE_MODEL,
            )

        out_paths: list[Path] = []
        for i, item in enumerate(items):
            b64 = item.get("b64_json") if isinstance(item, dict) else None
            if not b64:
                self._dump_raw(resp, f"missing_b64_idx{i}")
                raise ProviderResponseShapeError(
                    "data[].b64_json missing",
                    provider="tokenrouter", model=IMAGE_MODEL,
                )
            try:
                raw = base64.b64decode(b64)
            except Exception as e:
                raise ProviderResponseShapeError(
                    "data[].b64_json not valid base64",
                    provider="tokenrouter", model=IMAGE_MODEL,
                ) from e
            file_id = str(ULID())
            out = save_to / f"{file_id}.png"
            try:
                out.write_bytes(raw)
            except OSError as e:
                # Disk full / perms. Best-effort cleanup of partial file.
                try:
                    if out.exists():
                        out.unlink()
                except Exception:
                    pass
                raise SpriteStudioError(
                    f"OS error during image save: {e}",
                    provider="local", model=IMAGE_MODEL,
                ) from e
            out_paths.append(out)
        return out_paths

    def _dump_raw(self, resp: httpx.Response, tag: str) -> None:
        try:
            _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            return
        p = _DEBUG_DIR / f"{int(time.time())}_image_{tag}.txt"
        try:
            body = resp.json()
            for it in body.get("data", []):
                if isinstance(it, dict) and "b64_json" in it:
                    it["b64_json"] = f"<{len(it['b64_json'])} bytes>"
            import json as _json
            p.write_text(_json.dumps(body, indent=2))
        except Exception:
            try:
                p.write_text(resp.text[:5000])
            except Exception:
                return
        logger.warning("image debug dump: %s", p)
