"""Cost math for provider calls.

Handles two response usage shapes:
- Legacy chat shape: prompt_tokens / completion_tokens / total_tokens.
- New responses-API shape: input_tokens / output_tokens / total_tokens
  with input_tokens_details / output_tokens_details breakdown.
"""
from __future__ import annotations


# Per-1M-token pricing in USD. Update if TokenRouter posts changes.
# Source: TokenRouter dashboard / model card listings as of 2026-05-01.
PRICING: dict[str, dict[str, float]] = {
    "moonshotai/kimi-k2.6": {
        "input_per_million": 0.95,
        "output_per_million": 4.00,
    },
    "openai/gpt-5.4-image-2": {
        "input_per_million": 8.00,
        "output_per_million": 30.00,
    },
    # Seedance is single-token-stream (per-pixel-per-frame); no in/out split.
    "dreamina-seedance-2-0-fast-260128": {
        "tokens_per_million_usd": 5.60,
        "tier": "fast",
    },
    "dreamina-seedance-2-0-260128": {
        "tokens_per_million_usd": 7.00,
        "tier": "standard",
    },
}


# Seedance resolution -> (width, height) per orientation. Source:
# BytePlus ModelArk + WaveSpeedAI Seedance 2.0 docs (cross-verified).
_SEEDANCE_DIMS_9x16 = {
    "720p":  (720, 1280),
    "1080p": (1080, 1920),
}
_SEEDANCE_DIMS_16x9 = {
    "720p":  (1280, 720),
    "1080p": (1920, 1080),
}


def chat_cost_usd(model: str, usage: dict) -> float:
    """USD cost from a chat completions usage block."""
    p = PRICING.get(model)
    if not p:
        return 0.0
    in_tok = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    out_tok = usage.get("completion_tokens") or usage.get("output_tokens") or 0
    return (
        in_tok * p["input_per_million"] + out_tok * p["output_per_million"]
    ) / 1_000_000.0


def image_cost_usd(model: str, usage: dict) -> float:
    """USD cost from an image generation/edit usage block.

    The image API uses input_tokens / output_tokens with details.
    output_tokens are image tokens (priced at output_per_million).
    input_tokens may be split between text and image (priced at input_per_million).
    """
    p = PRICING.get(model)
    if not p:
        return 0.0
    in_tok = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
    out_tok = usage.get("output_tokens") or usage.get("completion_tokens") or 0
    return (
        in_tok * p["input_per_million"] + out_tok * p["output_per_million"]
    ) / 1_000_000.0


def seedance_token_count(
    *,
    resolution: str,
    ratio: str,
    duration_seconds: int,
    fps: int = 24,
) -> int:
    """Compute Seedance billing token count.

    Formula: (width * height * duration * fps) / 1024.
    Source: BytePlus ModelArk + WaveSpeedAI Seedance 2.0 docs.
    """
    table = _SEEDANCE_DIMS_9x16 if ratio == "9:16" else _SEEDANCE_DIMS_16x9
    if resolution not in table:
        raise ValueError(f"unknown resolution {resolution!r} for Seedance")
    w, h = table[resolution]
    return int((w * h * duration_seconds * fps) / 1024)


def seedance_cost_from_tokens(*, model: str, tokens: int) -> float:
    """USD cost for a Seedance generation given an exact token count.

    Use this with the provider-billed token count
    (response.data.data.usage.completion_tokens) for ground-truth cost.
    Returns 0.0 for unknown models or non-positive token counts.
    """
    if tokens is None or tokens <= 0:
        return 0.0
    p = PRICING.get(model)
    if not p or "tokens_per_million_usd" not in p:
        return 0.0
    return tokens * p["tokens_per_million_usd"] / 1_000_000.0


def tts_notional_cost_usd(*, model: str, char_count: int) -> float:
    """Notional USD cost for an ElevenLabs Multilingual v2 synthesis.

    ElevenLabs bills in *credits* (≈1 credit/char on Multilingual v2),
    not USD. We persist a notional USD figure at $0.0003/char so the
    project-level total_cost_usd column stays comparable across
    providers; callers that need credits should read input_payload
    where cost_basis="billing_credits" is recorded alongside the
    character_count.
    """
    if char_count <= 0:
        return 0.0
    if not model.startswith("eleven_"):
        return 0.0
    return char_count * 0.0003


def seedance_cost_usd(
    *,
    model: str,
    resolution: str,
    ratio: str,
    duration_seconds: int,
) -> float:
    """Pre-flight cost estimate using the formula token count.

    Within ~1% of the provider-billed cost in practice; suitable for budget
    guards and UI display before submit. After SUCCESS, prefer
    seedance_cost_from_tokens() with the provider's completion_tokens.
    """
    tokens = seedance_token_count(
        resolution=resolution, ratio=ratio, duration_seconds=duration_seconds,
    )
    return seedance_cost_from_tokens(model=model, tokens=tokens)
