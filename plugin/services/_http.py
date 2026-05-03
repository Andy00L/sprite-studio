"""Shared httpx client factory for the service layer.

A single AsyncClient is shared by ChatClient and ImageClient (and later VideoClient,
TtsClient). Each per-call site overrides timeout via .request()/.post() kwargs.
"""
from __future__ import annotations

import asyncio
import atexit
import logging
from typing import Optional

import httpx


logger = logging.getLogger("sprite_studio.services.http")

HTTP_CONNECT = 10.0
# Kimi K2.6 has internal reasoning latency before emitting tokens; the
# brief-clarifier call alone runs ~50s and the cast-designer call is
# bigger. 60s was too tight in production. 180s read accommodates
# realistic reasoning + larger JSON outputs.
HTTP_READ_CHAT = 180.0
HTTP_TOTAL_CHAT = 240.0
HTTP_READ_IMAGE = 240.0
HTTP_TOTAL_IMAGE = 300.0

# Per-request Timeout objects exposed for callers that need to override
# the shared AsyncClient default. The shared client default below is left
# unchanged so unrelated call paths are not affected.
#
# DEFAULT_TIMEOUT: routine HTTP calls (image gen submit/poll, seedance
# poll/download, elevenlabs synthesize) where vendor-side latency is
# bounded.
# LLM_TIMEOUT: chat completions where Kimi K2.6 reasoning latency is the
# bottleneck. Live evidence: 5-character timeline writer call exceeded
# the prior 300s ceiling on the Hippo Incident render. 600s gives ~3x
# headroom over the observed worst case without letting a stuck call
# hang for many minutes.
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
LLM_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)

USER_AGENT = "sprite-studio/0.1.0 (+hermes plugin)"

_client: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()


def _build_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(HTTP_CONNECT, read=HTTP_READ_CHAT, write=30.0, pool=5.0),
        limits=httpx.Limits(
            max_connections=8,
            max_keepalive_connections=4,
            keepalive_expiry=30.0,
        ),
        follow_redirects=False,
        http2=False,
        headers={"User-Agent": USER_AGENT},
    )


async def get_client() -> httpx.AsyncClient:
    """Return the module-level AsyncClient, creating it on first call."""
    global _client
    if _client is not None and not _client.is_closed:
        return _client
    async with _client_lock:
        if _client is None or _client.is_closed:
            _client = _build_client()
            logger.debug("created shared AsyncClient")
    return _client


async def aclose() -> None:
    """Close the shared client. Called from atexit and from tests."""
    global _client
    c = _client
    _client = None
    if c is not None and not c.is_closed:
        try:
            await c.aclose()
        except Exception as exc:
            logger.debug("aclose error: %s", exc)


def _atexit_close() -> None:
    """Best-effort sync close at interpreter shutdown.

    asyncio cleanup is messy at shutdown; if the loop is gone we let the
    OS reclaim the sockets. A warning here is acceptable.
    """
    if _client is None or _client.is_closed:
        return
    try:
        loop = asyncio.get_event_loop_policy().get_event_loop()
    except Exception:
        return
    if loop.is_running():
        # Inside a running loop; can't run_until_complete. Schedule and hope.
        try:
            loop.create_task(aclose())
        except Exception:
            pass
        return
    try:
        loop.run_until_complete(aclose())
    except Exception as exc:
        logger.debug("atexit aclose skipped: %s", exc)


atexit.register(_atexit_close)
