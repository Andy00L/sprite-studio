"""TokenRouter chat client for moonshotai/kimi-k2.6.

IMPORTANT MODEL CONSTRAINTS (verified live, see _SUMMARY.md CORRECTIONS section):
- moonshotai/kimi-k2.6 rejects any temperature value other than 1.
  We never pass the field for this model. Hard policy below.
- Responses include a non-standard reasoning_content field on choices[0].message.
  Tolerate it. It is not part of the user-facing output. Tokens for it ARE counted
  in completion_tokens, so cost accounting works correctly already.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from .. import db, env
from . import _concurrency, _http, _pricing, _retry
from .errors import ProviderInvalidRequestError, ProviderResponseShapeError


logger = logging.getLogger("sprite_studio.services.tokenrouter")

_DEBUG_DIR = Path("~/.hermes/plugins/sprite-studio/projects/_debug").expanduser()

# Models that REJECT a temperature override and must omit the field.
_MODELS_FORBID_TEMPERATURE = {"moonshotai/kimi-k2.6"}

# Per https://www.python-httpx.org/advanced/timeouts/ the read timeout is
# "the maximum duration to wait for a chunk of data to be received". Kimi
# K2.6's hidden reasoning trace can suppress emission for 100-200s on
# medium-sized JSON outputs (cast designer empirically hit 172s in prod).
# 300s gives ~2x headroom over observed worst case without letting a stuck
# call hang for many minutes. Used as the fallback when a caller does not
# supply read_timeout_seconds explicitly.
DEFAULT_LLM_READ_TIMEOUT_S = 300.0


class ChatClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.tokenrouter.com/v1",
    ) -> None:
        self._api_key = api_key or env.require_env("TOKENROUTER_API_KEY")
        self._base_url = base_url.rstrip("/")

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _build_body(
        self,
        *,
        model: str,
        messages: list[dict],
        temperature: float | None,
        max_tokens: int | None,
        response_format: dict | None,
    ) -> dict:
        body: dict[str, Any] = {"model": model, "messages": messages}
        if model not in _MODELS_FORBID_TEMPERATURE and temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if response_format is not None:
            body["response_format"] = response_format
        return body

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        project_id: str | None = None,
    ) -> str:
        """Plain-text chat. Returns choices[0].message.content."""
        data = await self._chat_raw(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=None,
            project_id=project_id,
        )
        return data["choices"][0]["message"]["content"]

    async def chat_json(
        self,
        *,
        model: str,
        messages: list[dict],
        max_tokens: int | None = None,
        project_id: str | None = None,
        read_timeout_seconds: float | None = None,
    ) -> dict:
        """JSON-mode chat. Returns the parsed JSON object that came back as
        choices[0].message.content. read_timeout_seconds optionally
        overrides DEFAULT_LLM_READ_TIMEOUT_S for this call (Kimi reasoning
        traces can outrun the default for big outputs)."""
        data = await self._chat_raw(
            model=model,
            messages=messages,
            temperature=None,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            project_id=project_id,
            read_timeout_seconds=read_timeout_seconds,
        )
        content = data["choices"][0]["message"]["content"]
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            raw_path = _DEBUG_DIR / f"{int(time.time())}_kimi_raw.txt"
            try:
                raw_path.write_text(content)
            except OSError:
                pass
            raise ProviderResponseShapeError(
                f"Kimi JSON-mode returned non-JSON content (raw saved to {raw_path})",
                provider="tokenrouter",
                model=model,
            ) from e

    async def _chat_raw(
        self,
        *,
        model: str,
        messages: list[dict],
        temperature: float | None,
        max_tokens: int | None,
        response_format: dict | None,
        project_id: str | None,
        read_timeout_seconds: float | None = None,
    ) -> dict:
        if not messages:
            raise ProviderInvalidRequestError(
                "messages must not be empty", provider="tokenrouter", model=model,
            )
        body = self._build_body(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )

        job_row: dict | None = None
        if project_id:
            job_row = db.create_job(
                project_id=project_id,
                job_type="llm",
                provider="tokenrouter",
                model=model,
                input_payload={
                    "message_count": len(messages),
                    "response_format": response_format is not None,
                },
            )

        started = time.perf_counter()
        async with _concurrency.CHAT_SEMAPHORE:
            client = await _http.get_client()
            url = f"{self._base_url}/chat/completions"

            read_to = (
                read_timeout_seconds
                if read_timeout_seconds is not None
                else DEFAULT_LLM_READ_TIMEOUT_S
            )

            async def _do() -> httpx.Response:
                return await client.post(
                    url,
                    json=body,
                    headers=self._auth_headers(),
                    timeout=httpx.Timeout(
                        connect=_http.HTTP_CONNECT,
                        read=read_to,
                        write=30.0,
                        pool=5.0,
                    ),
                )

            try:
                if job_row:
                    db.mark_job_running(job_row["id"])
                resp = await _retry.call_with_retry(
                    _do, provider="tokenrouter", model=model,
                )
            except Exception as e:
                if job_row:
                    db.mark_job_failed(job_row["id"], str(e))
                raise

        elapsed = time.perf_counter() - started

        try:
            data = resp.json()
        except Exception as e:
            if job_row:
                db.mark_job_failed(job_row["id"], "non-json response")
            raise ProviderResponseShapeError(
                "non-json response from /chat/completions",
                provider="tokenrouter",
                model=model,
            ) from e

        try:
            choice = data["choices"][0]
            _ = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            if job_row:
                db.mark_job_failed(job_row["id"], "missing choices[0].message.content")
            raise ProviderResponseShapeError(
                "response missing choices[0].message.content",
                provider="tokenrouter",
                model=model,
            ) from e

        # reasoning_content is tolerated; never read or surfaced.

        usage = data.get("usage") or {}
        cost = _pricing.chat_cost_usd(model, usage)

        if job_row:
            db.mark_job_done(
                job_row["id"],
                output_payload={
                    "usage": usage,
                    "elapsed_seconds": round(elapsed, 3),
                    "model": data.get("model", model),
                },
                cost_usd=cost,
            )
            db.increment_project_cost(project_id, cost)

        logger.info(
            "kimi chat ok model=%s tokens_in=%s tokens_out=%s elapsed=%.2fs cost=$%.4f",
            model,
            usage.get("prompt_tokens", usage.get("input_tokens", "?")),
            usage.get("completion_tokens", usage.get("output_tokens", "?")),
            elapsed,
            cost,
        )
        return data
