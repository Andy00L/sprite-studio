"""Service-layer public exports."""
from .errors import (
    ProviderAuthError,
    ProviderContentPolicyError,
    ProviderInsufficientCreditsError,
    ProviderInvalidRequestError,
    ProviderNotFoundError,
    ProviderRateLimitError,
    ProviderResponseShapeError,
    ProviderServerError,
    ProviderTimeoutError,
    SpriteStudioError,
)
from .gpt_image import (
    ALLOWED_QUALITIES,
    ALLOWED_SIZES,
    IMAGE_MODEL,
    ImageClient,
    QUALITY_HIGH,
    QUALITY_LOW,
    QUALITY_MEDIUM,
    SIZE_LANDSCAPE,
    SIZE_PORTRAIT,
    SIZE_SQUARE,
)
from .elevenlabs import (
    CHUNK_THRESHOLD as TTS_CHUNK_THRESHOLD,
    DEFAULT_MODEL_ID as TTS_DEFAULT_MODEL_ID,
    NARRATOR_VOICE_ID,
    VoiceClient,
)
from .seedance import MODEL_FAST, MODEL_STANDARD, VideoClient
from .tokenrouter import ChatClient
from ._pricing import (
    seedance_cost_from_tokens,
    seedance_token_count,
)
from .ffmpeg_runner import (
    build_ducking_volume_expr,
    compute_dialog_windows,
    concat_audios,
    concat_videos,
    get_duration_seconds,
    probe,
    stitch_final,
)


__all__ = [
    "ChatClient",
    "ImageClient",
    "IMAGE_MODEL",
    "SIZE_SQUARE",
    "SIZE_PORTRAIT",
    "SIZE_LANDSCAPE",
    "ALLOWED_SIZES",
    "QUALITY_LOW",
    "QUALITY_MEDIUM",
    "QUALITY_HIGH",
    "ALLOWED_QUALITIES",
    "VideoClient",
    "MODEL_FAST",
    "MODEL_STANDARD",
    "VoiceClient",
    "NARRATOR_VOICE_ID",
    "TTS_DEFAULT_MODEL_ID",
    "TTS_CHUNK_THRESHOLD",
    "SpriteStudioError",
    "ProviderAuthError",
    "ProviderNotFoundError",
    "ProviderRateLimitError",
    "ProviderServerError",
    "ProviderContentPolicyError",
    "ProviderTimeoutError",
    "ProviderResponseShapeError",
    "ProviderInsufficientCreditsError",
    "ProviderInvalidRequestError",
    "probe",
    "get_duration_seconds",
    "concat_videos",
    "concat_audios",
    "stitch_final",
    "compute_dialog_windows",
    "build_ducking_volume_expr",
    "seedance_token_count",
    "seedance_cost_from_tokens",
]
