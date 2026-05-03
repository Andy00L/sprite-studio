"""Environment-variable resolution for the sprite-studio plugin.

Resolution order: process environment, then ~/.hermes/.env, then default.
Logs presence (boolean) and length (int) only — never values.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Iterable, Optional


logger = logging.getLogger("sprite_studio.env")


class SpriteStudioConfigError(RuntimeError):
    pass


_HERMES_ENV_PATH = Path("~/.hermes/.env").expanduser()

_dotenv_cache: Optional[dict[str, str]] = None
_dotenv_lock = threading.Lock()


def _parse_dotenv(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE file. Tolerates comments, blank lines, quoted values."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("could not read %s: %s", path, exc)
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if (len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}):
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def _load_dotenv() -> dict[str, str]:
    global _dotenv_cache
    if _dotenv_cache is not None:
        return _dotenv_cache
    with _dotenv_lock:
        if _dotenv_cache is not None:
            return _dotenv_cache
        parsed = _parse_dotenv(_HERMES_ENV_PATH)
        logger.debug("parsed %s: %d keys", _HERMES_ENV_PATH, len(parsed))
        _dotenv_cache = parsed
        return parsed


def get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    val = os.environ.get(name)
    if val is not None:
        return val
    dotenv = _load_dotenv()
    if name in dotenv:
        return dotenv[name]
    return default


def require_env(name: str) -> str:
    val = get_env(name)
    if val is None or val == "":
        logger.error("missing required env var: %s", name)
        raise SpriteStudioConfigError(f"missing required env var: {name}")
    return val


def check_required_env(names: Iterable[str]) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for name in names:
        val = get_env(name)
        present = val is not None and val != ""
        out[name] = present
        if present:
            logger.debug("env var %s present (length=%d)", name, len(val))
        else:
            logger.debug("env var %s missing", name)
    return out


def get_video_tier() -> str:
    """Return 'fast' or 'standard'. Default 'fast' for hackathon budget control.

    Resolution mirrors get_env: process env then ~/.hermes/.env then default.
    Invalid values are logged and fall back to 'fast' so a typo never causes
    a paid STANDARD-tier render by accident.
    """
    raw = get_env("SPRITE_STUDIO_VIDEO_TIER", "fast") or "fast"
    val = raw.lower().strip()
    if val not in ("fast", "standard"):
        logger.warning(
            "SPRITE_STUDIO_VIDEO_TIER=%r invalid, falling back to 'fast'",
            raw,
        )
        return "fast"
    return val
