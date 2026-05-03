"""Typed errors for the Sprite Studio service layer."""
from __future__ import annotations

from typing import Any


class SpriteStudioError(Exception):
    """Base for all sprite-studio service errors."""

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        http_status: int | None = None,
        request_id: str | None = None,
        original_message: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.http_status = http_status
        self.request_id = request_id
        self.original_message = original_message
        self.extra = extra or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_class": self.__class__.__name__,
            "message": str(self),
            "provider": self.provider,
            "model": self.model,
            "http_status": self.http_status,
            "request_id": self.request_id,
        }


class ProviderAuthError(SpriteStudioError):
    """401 / 403 — credential rejected."""


class ProviderNotFoundError(SpriteStudioError):
    """404 — model, route, or job not found."""


class ProviderRateLimitError(SpriteStudioError):
    """429 after retry budget exhausted."""


class ProviderServerError(SpriteStudioError):
    """5xx after retry budget exhausted."""


class ProviderContentPolicyError(SpriteStudioError):
    """content_policy_violation, moderation_blocked, safety_filter, etc.
    NEVER retried. The caller surfaces a friendly message to the user."""


class ProviderTimeoutError(SpriteStudioError):
    """Connection or read timeout."""


class ProviderResponseShapeError(SpriteStudioError):
    """Response was 200 but missing required fields, non-JSON, or otherwise unparseable.
    The raw response is saved to disk under projects/_debug/ for forensics."""


class ProviderInsufficientCreditsError(SpriteStudioError):
    """402 — account out of credit. Render worker must surface and pause."""


class ProviderInvalidRequestError(SpriteStudioError):
    """400 / 422 — request shape rejected. Bug in our code, do not retry."""


class FFmpegError(SpriteStudioError):
    """ffmpeg subprocess returned non-zero or timed out."""


class FileInvalidError(SpriteStudioError):
    """Input file exists but is empty, corrupt, or unreadable by ffprobe."""


class BudgetExceededError(SpriteStudioError):
    """Project's total_cost_usd has reached the configured hard cap.

    Raised by the render worker before issuing further paid calls; partial
    artifacts are preserved and the project lands in phase='failed' with an
    error_message documenting the overshoot.
    """
