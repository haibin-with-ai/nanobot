"""Regression: Anthropic ``stop_reason="refusal"`` must trigger model fallback.

A model-level refusal (风控/policy decline) used to leak through as
``finish_reason="refusal"`` with empty content.  FallbackProvider treated a
non-``error`` finish_reason as success, and the runner swallowed the blank
content as an "empty response", retrying the SAME refusing model and finally
giving up with "couldn't produce a final answer" — never failing over to
another model.

After the fix:
  * ``_parse_response`` converts ``stop_reason="refusal"`` into a fallbackable
    error response (``finish_reason="error"``, ``error_kind="refusal"``,
    ``error_should_retry=True``).
  * ``FallbackProvider._should_fallback`` allows ``refusal`` to fail over.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from nanobot.providers.anthropic_provider import AnthropicProvider
from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.providers.fallback_provider import FallbackProvider


def _make_provider(model: str = "claude-fable-5") -> AnthropicProvider:
    with patch("anthropic.AsyncAnthropic"):
        return AnthropicProvider(api_key="sk-test", default_model=model)


def _fake_message(*, stop_reason: str, text: str = "") -> SimpleNamespace:
    content = [SimpleNamespace(type="text", text=text)] if text else []
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=10, output_tokens=2),
    )


# ---------------------------------------------------------------------------
# 1. _parse_response: refusal becomes a fallbackable error
# ---------------------------------------------------------------------------
def test_parse_response_refusal_becomes_error() -> None:
    resp = AnthropicProvider._parse_response(_fake_message(stop_reason="refusal"))
    assert resp.finish_reason == "error"
    assert resp.error_kind == "refusal"
    assert resp.error_should_retry is True


def test_parse_response_end_turn_still_stop() -> None:
    resp = AnthropicProvider._parse_response(
        _fake_message(stop_reason="end_turn", text="hi")
    )
    assert resp.finish_reason == "stop"
    assert resp.error_kind is None


# ---------------------------------------------------------------------------
# 2. _should_fallback: refusal is fallbackable
# ---------------------------------------------------------------------------
def test_should_fallback_on_refusal() -> None:
    resp = LLMResponse(
        content="",
        finish_reason="error",
        error_kind="refusal",
        error_should_retry=True,
    )
    assert FallbackProvider._should_fallback(resp) is True


def test_content_filter_still_not_fallbackable() -> None:
    resp = LLMResponse(
        content="",
        finish_reason="error",
        error_kind="content_filter",
        error_should_retry=True,
    )
    assert FallbackProvider._should_fallback(resp) is False


# ---------------------------------------------------------------------------
# 3. e2e: primary refuses -> fallback model answers
# ---------------------------------------------------------------------------
class _StubProvider(LLMProvider):
    def __init__(self, *, model: str = "stub", behaviour: str = "ok"):
        self._model = model
        self._behaviour = behaviour
        self._call_count = 0

    def get_default_model(self) -> str:
        return self._model

    async def chat(self, **kw) -> LLMResponse:
        self._call_count += 1
        if self._behaviour == "refusal":
            return LLMResponse(
                content=None,
                finish_reason="error",
                error_kind="refusal",
                error_should_retry=True,
            )
        return LLMResponse(content="answer from fallback")

    async def chat_stream(self, **kw) -> LLMResponse:
        return await self.chat(**kw)


@pytest.mark.asyncio
async def test_refusal_fails_over_to_next_model() -> None:
    primary = _StubProvider(model="claude-fable-5", behaviour="refusal")
    fallback = _StubProvider(model="fallback", behaviour="ok")

    preset = SimpleNamespace(
        model="fallback", max_tokens=None, temperature=None, reasoning_effort=None
    )
    factory = lambda preset: fallback
    fp = FallbackProvider(primary, [preset], factory)

    resp = await fp.chat()

    assert resp.finish_reason != "error"
    assert resp.content == "answer from fallback"
    assert primary._call_count == 1
    assert fallback._call_count == 1
