"""Regression: stream stall after partial content must trigger failover.

When content has already been streamed to the user and the primary model
stalls (timeout), the FallbackProvider must attempt failover to another
model.  Losing partial content is better than the entire session dying
on a dead connection.
"""

from __future__ import annotations

import pytest

from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.providers.fallback_provider import FallbackProvider


# ---------------------------------------------------------------------------
# Minimal stub provider
# ---------------------------------------------------------------------------
class _StubProvider(LLMProvider):
    """Fake provider whose chat_stream behaviour is controllable."""

    def __init__(self, *, model: str = "stub-model"):
        self._model = model
        self._call_count = 0
        self._behaviour: str = "ok"  # "ok" | "stall_after_stream"

    def get_default_model(self) -> str:
        return self._model

    async def chat(self, **kw) -> LLMResponse:
        return LLMResponse(content="ok")

    async def chat_stream(self, **kw) -> LLMResponse:
        self._call_count += 1
        delta_cb = kw.get("on_content_delta")

        if self._behaviour == "stall_after_stream":
            # Simulate: partial content streamed, then timeout
            if delta_cb:
                await delta_cb("partial content…")
            return LLMResponse(
                content="Error calling LLM: stream stalled for more than 90 seconds",
                finish_reason="error",
                error_kind="timeout",
            )

        # Normal success
        if delta_cb:
            await delta_cb("full response")
        return LLMResponse(content="full response")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
class _Preset:
    def __init__(self, model: str):
        self.model = model
        self.max_tokens = None
        self.temperature = None
        self.reasoning_effort = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_streamed_stall_falls_back_to_next_model():
    """After partial stream + timeout, fallback should be attempted."""
    primary = _StubProvider(model="primary")
    fallback = _StubProvider(model="fallback")
    primary._behaviour = "stall_after_stream"

    streamed_chunks: list[str] = []

    async def capture_delta(text: str) -> None:
        streamed_chunks.append(text)

    fp = FallbackProvider(primary, [_Preset("fallback")], lambda _p: fallback)

    resp = await fp.chat_stream(on_content_delta=capture_delta)

    # Partial content was streamed from primary
    assert streamed_chunks, "delta callback should have been invoked"

    # Fallback should have succeeded
    assert resp.finish_reason != "error"
    assert resp.content == "full response"

    # Primary was called once, fallback was called once
    assert primary._call_count == 1
    assert fallback._call_count == 1


@pytest.mark.asyncio
async def test_streamed_stall_non_fallbackable_error_skips_failover():
    """After partial stream + non-fallbackable error (e.g. content_filter),
    failover should be skipped and error_should_retry=False returned."""
    primary = _StubProvider(model="primary")
    primary._behaviour = "stall_after_stream"
    # Override to return content_filter instead of timeout
    async def _stall_content_filter(**kw):
        primary._call_count += 1
        delta_cb = kw.get("on_content_delta")
        if delta_cb:
            await delta_cb("partial content…")
        return LLMResponse(
            content="content filtered",
            finish_reason="error",
            error_kind="content_filter",
        )

    primary.chat_stream = _stall_content_filter

    factory = lambda preset: _StubProvider(model=preset.model)
    fp = FallbackProvider(primary, [_Preset("fallback")], factory)

    streamed_chunks: list[str] = []

    async def capture_delta(text: str) -> None:
        streamed_chunks.append(text)

    resp = await fp.chat_stream(on_content_delta=capture_delta)

    assert streamed_chunks, "delta callback should have been invoked"
    assert resp.finish_reason == "error"
    assert resp.error_should_retry is False
    assert primary._call_count == 1


@pytest.mark.asyncio
async def test_non_streamed_stall_still_allows_fallback():
    """When NO content was streamed, normal fallback should proceed."""
    primary = _StubProvider(model="primary")
    fallback = _StubProvider(model="fallback")

    # Primary fails with timeout but without streaming any content
    async def _fail_no_stream(**kw):
        # Don't call delta callback at all
        return LLMResponse(
            content="Error calling LLM: stream stalled for more than 90 seconds",
            finish_reason="error",
            error_kind="timeout",
        )

    primary.chat_stream = _fail_no_stream

    class _Preset:
        def __init__(self, model: str):
            self.model = model
            self.max_tokens = None
            self.temperature = None
            self.reasoning_effort = None

    factory = lambda preset: fallback
    fp = FallbackProvider(primary, [_Preset("fallback")], factory)

    resp = await fp.chat_stream(on_content_delta=None)

    # Fallback should succeed
    assert resp.finish_reason != "error"
    assert resp.content == "full response"
    assert fallback._call_count == 1


@pytest.mark.asyncio
async def test_non_stream_chat_unaffected():
    """Non-streaming chat() path should be unaffected by this fix."""
    primary = _StubProvider(model="primary")
    fallback = _StubProvider(model="fallback")

    class _Preset:
        def __init__(self, model: str):
            self.model = model
            self.max_tokens = None
            self.temperature = None
            self.reasoning_effort = None

    async def _fail_chat(**kw):
        return LLMResponse(
            content="Error: overloaded",
            finish_reason="error",
            error_kind="timeout",
        )

    primary.chat = _fail_chat

    created: list[_StubProvider] = []

    def factory(preset):
        p = _StubProvider(model=preset.model)
        created.append(p)
        return p

    fp = FallbackProvider(primary, [_Preset("fallback")], factory)
    resp = await fp.chat()

    # Should fall through to fallback
    assert resp.content == "ok"
    assert len(created) == 1
