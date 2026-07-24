"""Regression: quota / rate-limit errors must cool a model down.

When a model (primary or fallback) returns a quota-exhausted / rate-limit
error, the FallbackProvider must stop hammering it on every subsequent
request.  The model is put into a cooldown window (default 10 min, or the
provider-supplied retry_after when available) and skipped until it expires.

Both Anthropic (HTTP 429 + retry_after) and Codex (plain-text
"quota exceeded or rate limit" message) must be recognised.
"""

from __future__ import annotations

import pytest

from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.providers.fallback_provider import FallbackProvider


# ---------------------------------------------------------------------------
# Configurable stub provider
# ---------------------------------------------------------------------------
class _StubProvider(LLMProvider):
    def __init__(self, *, model: str, response_factory):
        self._model = model
        self._call_count = 0
        self._response_factory = response_factory

    def get_default_model(self) -> str:
        return self._model

    async def chat(self, **kw) -> LLMResponse:
        self._call_count += 1
        return self._response_factory()

    async def chat_stream(self, **kw) -> LLMResponse:
        self._call_count += 1
        delta_cb = kw.get("on_content_delta")
        resp = self._response_factory()
        if resp.finish_reason != "error" and delta_cb:
            await delta_cb(resp.content)
        return resp


class _Preset:
    def __init__(self, model: str):
        self.model = model
        self.max_tokens = None
        self.temperature = None
        self.reasoning_effort = None


def _ok(content: str = "ok"):
    return lambda: LLMResponse(content=content)


def _anthropic_429():
    return lambda: LLMResponse(
        content="rate limited",
        finish_reason="error",
        error_status_code=429,
        retry_after=None,
    )


def _codex_quota():
    return lambda: LLMResponse(
        content="Error calling Codex: ChatGPT usage quota exceeded or rate limit triggered. Please try again later.",
        finish_reason="error",
    )


def _timeout():
    return lambda: LLMResponse(
        content="Error calling LLM: stream stalled for more than 90 seconds",
        finish_reason="error",
        error_kind="timeout",
    )


def _make_fp(primary_provider, fallbacks: dict[str, _StubProvider]):
    presets = [_Preset(m) for m in fallbacks]

    def factory(preset):
        return fallbacks[preset.model]

    return FallbackProvider(primary_provider, presets, factory)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_quota_error_trips_cooldown_and_skips_on_next_request():
    """A quota-exhausted fallback must be skipped on the next request."""
    primary = _StubProvider(model="primary", response_factory=_anthropic_429())
    fb_a = _StubProvider(model="fb_a", response_factory=_codex_quota())
    fb_b = _StubProvider(model="fb_b", response_factory=_ok("from B"))

    fp = _make_fp(primary, {"fb_a": fb_a, "fb_b": fb_b})

    # Request 1: primary quota -> fb_a quota -> fb_b ok
    resp1 = await fp.chat()
    assert resp1.content == "from B"
    assert primary._call_count == 1
    assert fb_a._call_count == 1
    assert fb_b._call_count == 1

    # Request 2: primary + fb_a are cooling down, must be skipped entirely.
    resp2 = await fp.chat()
    assert resp2.content == "from B"
    assert primary._call_count == 1, "primary should be skipped (quota cooldown)"
    assert fb_a._call_count == 1, "fb_a should be skipped (quota cooldown)"
    assert fb_b._call_count == 2, "fb_b should serve directly"


@pytest.mark.asyncio
async def test_non_quota_error_does_not_trip_quota_cooldown():
    """A generic timeout must NOT put the model into the long quota cooldown."""
    primary = _StubProvider(model="primary", response_factory=_timeout())
    fb_a = _StubProvider(model="fb_a", response_factory=_ok("from A"))

    fp = _make_fp(primary, {"fb_a": fb_a})

    await fp.chat()
    # Timeout is a transient error, not quota. The primary consecutive-failure
    # breaker may still apply, but quota cooldown must be zero.
    assert fp._quota_cooldown_remaining("primary") == 0.0


@pytest.mark.asyncio
async def test_quota_cooldown_respects_retry_after_bounds():
    """When provider supplies retry_after, cooldown uses it within bounds."""
    def _429_retry_after():
        return lambda: LLMResponse(
            content="rate limited",
            finish_reason="error",
            error_status_code=429,
            retry_after=300.0,
        )

    primary = _StubProvider(model="primary", response_factory=_429_retry_after())
    fb_a = _StubProvider(model="fb_a", response_factory=_ok("from A"))
    fp = _make_fp(primary, {"fb_a": fb_a})

    await fp.chat()
    remaining = fp._quota_cooldown_remaining("primary")
    # ~300s, allow small scheduling slack
    assert 290.0 <= remaining <= 300.0


@pytest.mark.asyncio
async def test_quota_default_cooldown_is_ten_minutes():
    """Codex plain-text quota (no retry_after) uses the 10-min default."""
    primary = _StubProvider(model="primary", response_factory=_ok("primary ok"))
    fb_a = _StubProvider(model="fb_a", response_factory=_codex_quota())
    fb_b = _StubProvider(model="fb_b", response_factory=_ok("from B"))
    fp = _make_fp(primary, {"fb_a": fb_a, "fb_b": fb_b})

    # Force primary to fail so we reach fallbacks — use a quota primary instead.
    primary._response_factory = _codex_quota()

    await fp.chat()
    remaining = fp._quota_cooldown_remaining("fb_a")
    assert 590.0 <= remaining <= 600.0
