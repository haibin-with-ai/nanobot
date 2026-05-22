"""Tests for stream stall auto-retry in AnthropicProvider.chat_stream."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from nanobot.providers.anthropic_provider import AnthropicProvider, _StreamStall
from nanobot.providers.base import LLMResponse


@pytest.fixture()
def provider():
    """Create an AnthropicProvider with mocked internals."""
    with patch.object(AnthropicProvider, "__init__", lambda self: None):
        p = AnthropicProvider.__new__(AnthropicProvider)
        p._client = AsyncMock()
        p._auth_token = "fake"
        p._token_expiry = None
        p._credential_store_path = None
        p.default_model = "claude-sonnet-4-20250514"
        p._ensure_valid_token = AsyncMock()
        p._build_kwargs = lambda *a, **kw: {"model": "claude-sonnet-4-20250514"}
        return p


def _ok_response() -> LLMResponse:
    return LLMResponse(content="hello", finish_reason="end_turn")


# ── 1. Zero-content stall, retry succeeds ──────────────────────────

@pytest.mark.asyncio
async def test_no_content_stall_retry_succeeds(provider):
    """First call stalls with no content; retry returns normally."""
    provider._do_stream = AsyncMock(
        side_effect=[_StreamStall(90, had_content=False), _ok_response()]
    )

    resp = await provider.chat_stream(messages=[{"role": "user", "content": "hi"}])

    assert resp.content == "hello"
    assert resp.finish_reason == "end_turn"
    assert provider._do_stream.call_count == 2


# ── 2. Zero-content stall, retry also stalls ───────────────────────

@pytest.mark.asyncio
async def test_no_content_stall_retry_also_stalls(provider):
    """Both attempts stall → error with error_should_retry=True."""
    provider._do_stream = AsyncMock(
        side_effect=[
            _StreamStall(90, had_content=False),
            _StreamStall(90, had_content=False),
        ]
    )

    resp = await provider.chat_stream(messages=[{"role": "user", "content": "hi"}])

    assert resp.finish_reason == "error"
    assert resp.error_kind == "timeout"
    assert resp.error_should_retry is True
    assert "retried once" in resp.content
    assert provider._do_stream.call_count == 2


# ── 3. Partial-content stall → no retry ────────────────────────────

@pytest.mark.asyncio
async def test_partial_content_stall_no_retry(provider):
    """Content already pushed → no retry, error_should_retry=False."""
    provider._do_stream = AsyncMock(
        side_effect=_StreamStall(90, had_content=True)
    )

    resp = await provider.chat_stream(messages=[{"role": "user", "content": "hi"}])

    assert resp.finish_reason == "error"
    assert resp.error_kind == "timeout"
    assert resp.error_should_retry is False
    assert provider._do_stream.call_count == 1


# ── 4. Zero-content stall, retry raises other exception ────────────

@pytest.mark.asyncio
async def test_no_content_stall_retry_other_error(provider):
    """First call stalls; retry raises a non-stall exception → _handle_error."""
    provider._do_stream = AsyncMock(
        side_effect=[
            _StreamStall(90, had_content=False),
            RuntimeError("connection reset"),
        ]
    )
    provider._handle_error = lambda e: LLMResponse(
        content=f"Error: {e}", finish_reason="error", error_kind="api_error",
    )

    resp = await provider.chat_stream(messages=[{"role": "user", "content": "hi"}])

    assert resp.finish_reason == "error"
    assert "connection reset" in resp.content
    assert provider._do_stream.call_count == 2


# ── 5. Normal stream — no retry triggered ──────────────────────────

@pytest.mark.asyncio
async def test_normal_stream_no_retry(provider):
    """Happy path: no stall, no retry."""
    provider._do_stream = AsyncMock(return_value=_ok_response())

    resp = await provider.chat_stream(messages=[{"role": "user", "content": "hi"}])

    assert resp.content == "hello"
    assert provider._do_stream.call_count == 1


# ── 6. _StreamStall exception carries correct metadata ─────────────

def test_stream_stall_exception_attrs():
    exc = _StreamStall(90, had_content=True)
    assert exc.idle_timeout_s == 90
    assert exc.had_content is True
    assert "had_content=True" in str(exc)

    exc2 = _StreamStall(60, had_content=False)
    assert exc2.had_content is False
    assert "60s" in str(exc2)
