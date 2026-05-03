"""Concurrency caps for provider calls. Module-level so all callers share."""
from __future__ import annotations

import asyncio


# Image gen is the long pole (gpt-image-2 at quality=high runs ~30-120s),
# so the wall-clock for a cast or shot fan-out is dominated by how many
# image jobs run in parallel. Sized at 6 (above the 4 used elsewhere)
# because each image call is mostly idle wait, not RPS pressure. OpenAI
# Tier 2+ allows 20 IPM, well above 6 concurrent at typical latency.
# Lower this if a 429 storm appears on the image endpoint.
IMAGE_SEMAPHORE = asyncio.Semaphore(6)
CHAT_SEMAPHORE = asyncio.Semaphore(4)
VIDEO_SEMAPHORE = asyncio.Semaphore(4)
TTS_SEMAPHORE = asyncio.Semaphore(4)
