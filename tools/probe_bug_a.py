"""Manual smoke probe. Verifies a body-less httpx timeout produces a
non-empty ProviderTimeoutError message after the fix.

Points at a local hang server on 127.0.0.1:9911 (a TCP listener that
accepts but never sends bytes), so this works offline."""

import asyncio

import httpx

from plugin.services._retry import call_with_retry
from plugin.services.errors import ProviderTimeoutError


_HANG_URL = "http://127.0.0.1:9911/"


async def _slow_call() -> httpx.Response:
    timeout = httpx.Timeout(connect=2.0, read=0.5, write=2.0, pool=2.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.get(_HANG_URL)


async def main() -> None:
    try:
        await call_with_retry(
            _slow_call, provider="probe_provider", model="probe_model",
        )
        print("UNEXPECTED: call did not time out")
    except ProviderTimeoutError as e:
        msg = str(e)
        print("got:", repr(msg))
        assert msg, "FAIL: ProviderTimeoutError still has empty str(e)"
        assert (
            "ReadTimeout" in msg
            or "ConnectTimeout" in msg
            or "TimeoutException" in msg
        ), msg
        assert "probe_provider" in msg, msg
        assert "probe_model" in msg, msg
        print("PASS")


asyncio.run(main())
