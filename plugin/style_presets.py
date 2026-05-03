"""Pure data layer for the 10 style presets.

Loads style_presets.yaml once at import time, validates the shape, exposes
load_presets() and the constants module-callers need. Treat the returned
dict as immutable: the StylePreset model has frozen=False so we can mutate
in tests, but production code should not.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from ruamel.yaml import YAML

from .models import StylePreset


logger = logging.getLogger("sprite_studio.style_presets")

PRESETS_PATH = Path("~/.hermes/plugins/sprite-studio/style_presets.yaml").expanduser()
DEFAULT_PRESET_ID = "cartoon_classic"
EXPECTED_IDS: tuple[str, ...] = (
    "cartoon_classic",
    "pixar_3d",
    "watercolor_book",
    "anime_modern",
    "cinematic_realism",
    "ghibli_inspired",
    "pixel_art_retro",
    "noir_comic",
    "storybook_3d",
    "cyberpunk_neon",
)

_cache: Optional[dict[str, StylePreset]] = None
_cache_lock = threading.Lock()


class StylePresetLoadError(RuntimeError):
    pass


def _parse_file(path: Path) -> list[dict]:
    if not path.exists():
        raise StylePresetLoadError(f"style_presets.yaml not found at {path}")
    yaml = YAML(typ="safe")
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.load(fh)
    except Exception as e:
        raise StylePresetLoadError(f"failed to parse {path}: {e}") from e
    if not isinstance(data, list):
        raise StylePresetLoadError(
            f"style_presets.yaml top-level must be a list, got {type(data).__name__}",
        )
    return data


def _validate_unique_and_complete(presets: dict[str, StylePreset]) -> None:
    seen_ids = set(presets.keys())
    expected = set(EXPECTED_IDS)
    missing = expected - seen_ids
    extra = seen_ids - expected
    if missing:
        raise StylePresetLoadError(
            f"style_presets.yaml is missing required preset id(s): {sorted(missing)}",
        )
    if extra:
        raise StylePresetLoadError(
            f"style_presets.yaml has unexpected preset id(s): {sorted(extra)}",
        )


def load_presets(force_reload: bool = False) -> dict[str, StylePreset]:
    """Return the parsed presets, mapping preset_id -> StylePreset."""
    global _cache
    if _cache is not None and not force_reload:
        return _cache
    with _cache_lock:
        if _cache is not None and not force_reload:
            return _cache
        raw = _parse_file(PRESETS_PATH)
        out: dict[str, StylePreset] = {}
        for entry in raw:
            if not isinstance(entry, dict):
                raise StylePresetLoadError(
                    f"each preset entry must be a mapping, got {type(entry).__name__}",
                )
            try:
                preset = StylePreset.model_validate(entry)
            except Exception as e:
                preset_id = entry.get("id", "<unknown>")
                raise StylePresetLoadError(
                    f"preset id={preset_id!r} failed validation: {e}",
                ) from e
            if preset.id in out:
                raise StylePresetLoadError(f"duplicate preset id: {preset.id!r}")
            out[preset.id] = preset
        _validate_unique_and_complete(out)
        _cache = out
        logger.info("loaded %d style presets from %s", len(out), PRESETS_PATH)
        return _cache


def get_preset(preset_id: str) -> StylePreset:
    presets = load_presets()
    if preset_id not in presets:
        raise StylePresetLoadError(
            f"unknown style_preset_id: {preset_id!r}; "
            f"valid ids: {sorted(presets.keys())}",
        )
    return presets[preset_id]


def is_valid_preset_id(preset_id: str) -> bool:
    return preset_id in load_presets()


# Eager-load at import so misconfiguration surfaces immediately on plugin
# register, not during the first /sprite_new call.
load_presets()
