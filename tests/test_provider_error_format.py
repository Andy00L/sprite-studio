import asyncio

import httpx
import pytest

from plugin.services.errors import format_provider_error


def test_empty_str_exc_yields_class_and_context():
    msg = format_provider_error(
        httpx.ReadTimeout(""),
        provider="tokenrouter",
        model="moonshotai/kimi-k2.6",
        attempt=3,
        max_attempts=3,
    )
    assert msg.startswith("ReadTimeout"), msg
    assert "tokenrouter" in msg
    assert "moonshotai/kimi-k2.6" in msg
    assert "attempt=3/3" in msg
    assert msg.strip() != ""
    assert ":" not in msg.split(" (")[0]  # no "ReadTimeout: " prefix when body empty


def test_asyncio_timeout_no_message():
    msg = format_provider_error(asyncio.TimeoutError(), provider="x", model="y")
    assert "TimeoutError" in msg
    assert "x/y" in msg
    assert msg.strip() != ""


def test_non_empty_str_preserved():
    msg = format_provider_error(ValueError("the body"), provider="p", model="m")
    assert "ValueError" in msg
    assert "the body" in msg
    assert "p/m" in msg


def test_no_provider_no_model():
    msg = format_provider_error(ValueError("boom"))
    assert msg == "ValueError: boom"


def test_secret_redaction(monkeypatch):
    monkeypatch.setenv("FAKE_API_KEY", "sk-supersecretvalueoflength")
    msg = format_provider_error(
        ValueError("leaked: sk-supersecretvalueoflength"),
        provider="p",
        model="m",
    )
    assert "sk-supersecretvalueoflength" not in msg
    assert "***" in msg


def test_secret_redaction_skips_short_values(monkeypatch):
    # 3 chars, must NOT be redacted (would eat common words)
    monkeypatch.setenv("API_KEY_SHORT", "abc")
    msg = format_provider_error(ValueError("hello abc world"), provider="p", model="m")
    assert "abc" in msg


def test_truncation():
    msg = format_provider_error(ValueError("x" * 1000), provider="p", model="m")
    assert len(msg) <= 500
    assert msg.endswith("...")
