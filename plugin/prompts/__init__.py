"""Prompt loader.

Reads .md prompt files from this package and returns their contents with
optional {placeholder} substitution.

Substitution uses a string.Formatter subclass that catches KeyError and
AttributeError lookups for missing fields and re-emits the literal
"{field_name}" brace expression instead of raising. This is the safe-
escape behavior the spec asks for. A defaultdict mapping is also used so
the underlying get_value never raises before our safety net catches.

Literal `{` and `}` characters in the .md file (for example, JSON
schema examples) MUST be escaped as `{{` and `}}` per str.format
convention.

Files are read fresh every call — .md files are tiny and a stale cache
during dev iteration is more painful than a few extra reads.
"""
from __future__ import annotations

import string
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any


_PROMPTS_DIR = Path(__file__).resolve().parent
_read_lock = threading.Lock()


class PromptNotFoundError(FileNotFoundError):
    pass


class _LiteralBraceDict(defaultdict):
    """defaultdict factory: missing keys return a sentinel that, when
    converted to a string, formats as '{key}'. The Formatter sub-class
    below also re-catches downstream AttributeErrors on dotted lookups.
    """

    def __init__(self, supplied: dict[str, Any]) -> None:
        super().__init__()
        self.update(supplied)

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class _SafeFormatter(string.Formatter):
    """string.Formatter that turns missing-key / missing-attr lookups
    into literal "{field_name}" output instead of raising.
    """

    def get_field(self, field_name: str, args, kwargs):  # type: ignore[override]
        try:
            return super().get_field(field_name, args, kwargs)
        except (KeyError, AttributeError, IndexError):
            return "{" + field_name + "}", field_name

    def format_field(self, value: Any, format_spec: str) -> str:
        # If the value is already our literal-brace fallback string and
        # someone wrote a format spec like {x:d}, ignore the spec.
        if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
            try:
                return format(value, format_spec) if format_spec else value
            except (ValueError, TypeError):
                return value
        return super().format_field(value, format_spec)


def load_prompt(name: str, **substitutions: Any) -> str:
    """Read prompts/<name>.md, optionally substitute {placeholder} fields.

    `name` may include the .md suffix or omit it. Path traversal (`..`,
    absolute paths) is rejected — only files inside this package directory
    are loadable.

    Missing keys leave the brace literal in the output (no exception).
    Provided keys whose value is None are formatted as the empty string.
    """
    safe = name if name.endswith(".md") else f"{name}.md"
    p = (_PROMPTS_DIR / safe).resolve()
    try:
        p.relative_to(_PROMPTS_DIR)
    except ValueError as e:
        raise PromptNotFoundError(f"prompt path escapes package: {name!r}") from e
    if not p.is_file():
        raise PromptNotFoundError(f"prompt file not found: {p}")
    with _read_lock:
        text = p.read_text(encoding="utf-8")
    if not substitutions:
        return text
    # We DO NOT pre-stringify values. string.Formatter calls str() at the
    # leaf; if we stringify here, dotted placeholders like
    # {style_preset.descriptor} would attempt attribute access on a
    # stringified object and break. None becomes "" before insertion.
    cleaned: dict[str, Any] = {
        k: ("" if v is None else v) for k, v in substitutions.items()
    }
    return _SafeFormatter().vformat(text, (), _LiteralBraceDict(cleaned))


__all__ = ["load_prompt", "PromptNotFoundError"]
