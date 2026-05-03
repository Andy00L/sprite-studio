"""Retry policy for HTTP calls in the service layer.

httpx returns Response objects on non-2xx (not exceptions). To make tenacity
backoff usable, we convert retryable HTTP failures (429, 5xx) into a small
internal exception and let tenacity decide. Terminal failures raise the
appropriate typed error from .errors immediately.
"""
from __future__ import annotations

import logging

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
    before_sleep_log,
)

from .errors import (
    ProviderAuthError,
    ProviderContentPolicyError,
    ProviderInsufficientCreditsError,
    ProviderInvalidRequestError,
    ProviderNotFoundError,
    ProviderRateLimitError,
    ProviderResponseShapeError,  # noqa: F401  (re-exported for callers)
    ProviderServerError,
    ProviderTimeoutError,
    SpriteStudioError,
)


logger = logging.getLogger("sprite_studio.services.retry")


class _Retryable(Exception):
    """Internal sentinel: this HTTP call should be retried by tenacity."""

    def __init__(self, status: int, message: str, request_id: str | None) -> None:
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message
        self.request_id = request_id


RETRYABLE_STATUSES = (429, 500, 502, 503, 504)
RETRYABLE_TRANSPORT = (
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)


def _extract_request_id(response: httpx.Response) -> str | None:
    return (
        response.headers.get("x-tokenrouter-request-id")
        or response.headers.get("x-request-id")
        or response.headers.get("openai-request-id")
    )


def _extract_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except Exception:
        body = None
    msg = ""
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict):
            msg = err.get("message") or err.get("code") or ""
        elif isinstance(err, str):
            msg = err
    if not msg:
        msg = response.text[:300]
    return msg or ""


def classify_response(
    response: httpx.Response,
    *,
    provider: str,
    model: str | None,
) -> None:
    """If 2xx, return None. If retryable, raise _Retryable. Else raise typed error."""
    status = response.status_code
    if 200 <= status < 300:
        return

    request_id = _extract_request_id(response)
    msg = _extract_message(response)
    low = msg.lower()

    if (
        "content_policy" in low
        or "moderation" in low
        or "safety" in low
        or "violation" in low
    ):
        raise ProviderContentPolicyError(
            msg, provider=provider, model=model, http_status=status,
            request_id=request_id, original_message=msg,
        )

    if status in (401, 403):
        raise ProviderAuthError(
            "Authentication failed", provider=provider, model=model,
            http_status=status, request_id=request_id, original_message=msg,
        )
    if status == 402:
        raise ProviderInsufficientCreditsError(
            msg or "Insufficient credits", provider=provider, model=model,
            http_status=status, request_id=request_id, original_message=msg,
        )
    if status == 404:
        raise ProviderNotFoundError(
            msg or "Not found", provider=provider, model=model,
            http_status=status, request_id=request_id, original_message=msg,
        )
    if status in (400, 422):
        raise ProviderInvalidRequestError(
            msg or "Invalid request", provider=provider, model=model,
            http_status=status, request_id=request_id, original_message=msg,
        )

    if status in RETRYABLE_STATUSES:
        raise _Retryable(status, msg, request_id)

    raise SpriteStudioError(
        msg or f"HTTP {status}", provider=provider, model=model,
        http_status=status, request_id=request_id, original_message=msg,
    )


def make_retry(*, attempts: int = 3, base: float = 1.0, cap: float = 8.0) -> AsyncRetrying:
    return AsyncRetrying(
        reraise=True,
        stop=stop_after_attempt(attempts),
        wait=wait_random_exponential(multiplier=base, max=cap),
        retry=retry_if_exception_type((_Retryable, *RETRYABLE_TRANSPORT)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )


async def call_with_retry(
    do_call,
    *,
    provider: str,
    model: str | None,
    attempts: int = 3,
) -> httpx.Response:
    """Run a zero-arg async callable through the retry policy.

    Returns the httpx.Response on success. On terminal failure raises the
    appropriate typed SpriteStudioError subclass.
    """
    retrier = make_retry(attempts=attempts)
    try:
        async for attempt in retrier:
            with attempt:
                try:
                    resp = await do_call()
                except RETRYABLE_TRANSPORT:
                    raise
                except httpx.TimeoutException as e:
                    raise ProviderTimeoutError(
                        str(e), provider=provider, model=model,
                    ) from e
                classify_response(resp, provider=provider, model=model)
                return resp
    except _Retryable as final:
        if final.status == 429:
            raise ProviderRateLimitError(
                final.message, provider=provider, model=model,
                http_status=final.status, request_id=final.request_id,
            )
        raise ProviderServerError(
            final.message, provider=provider, model=model,
            http_status=final.status, request_id=final.request_id,
        )
    except RETRYABLE_TRANSPORT as e:
        raise ProviderTimeoutError(str(e), provider=provider, model=model) from e

    raise ProviderTimeoutError(
        "retry loop exited without response", provider=provider, model=model,
    )
