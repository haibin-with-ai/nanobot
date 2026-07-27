"""Claude Opus 5 request compatibility regression tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.providers.anthropic_provider import AnthropicProvider


def _provider(model: str = "claude-opus-5") -> AnthropicProvider:
    provider = AnthropicProvider(api_key="test-key", default_model=model)
    provider._client = MagicMock()
    provider._client.messages.create = AsyncMock(
        return_value=SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )
    )
    return provider


def _kwargs(
    provider: AnthropicProvider,
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
    temperature: float = 0.7,
) -> dict[str, Any]:
    return provider._build_kwargs(
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        model=model or provider.default_model,
        max_tokens=128,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        tool_choice=None,
    )


@pytest.mark.parametrize(
    "model",
    [
        "claude-opus-5",
        "anthropic/claude-opus-5",
        "provider-prefix/anthropic/claude-opus-5",
        "claude-opus-5-20260727",
    ],
)
def test_opus5_omits_sampling_parameters(model: str) -> None:
    kwargs = _kwargs(_provider(model))

    assert "temperature" not in kwargs
    assert "top_p" not in kwargs
    assert "top_k" not in kwargs


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_opus5_sends_full_effort_range(effort: str) -> None:
    kwargs = _kwargs(_provider(), reasoning_effort=effort)

    assert kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert kwargs["output_config"] == {"effort": effort}


def test_opus5_default_requests_summarized_adaptive_thinking() -> None:
    kwargs = _kwargs(_provider())

    assert kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert "output_config" not in kwargs


def test_opus5_adaptive_alias_keeps_provider_default_effort() -> None:
    kwargs = _kwargs(_provider(), reasoning_effort="adaptive")

    assert kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert "output_config" not in kwargs


def test_opus5_explicit_none_disables_default_thinking() -> None:
    kwargs = _kwargs(_provider(), reasoning_effort="none")

    assert kwargs["thinking"] == {"type": "disabled"}
    assert "output_config" not in kwargs
    assert "temperature" not in kwargs


def test_opus5_model_matching_rejects_near_miss_name() -> None:
    kwargs = _kwargs(_provider("claude-opus-50"), reasoning_effort="high")

    assert kwargs["thinking"] == {"type": "adaptive"}
    assert "output_config" not in kwargs
    assert kwargs["temperature"] == 1.0


def test_opus47_keeps_existing_adaptive_thinking_behavior() -> None:
    kwargs = _kwargs(_provider("claude-opus-4-7"), reasoning_effort="high")

    assert kwargs["thinking"] == {"type": "adaptive"}
    assert "output_config" not in kwargs
    assert "temperature" not in kwargs


@pytest.mark.asyncio
async def test_opus5_chat_uses_summarized_thinking() -> None:
    provider = _provider()

    response = await provider.chat(
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=128,
        reasoning_effort="max",
    )

    assert response.content == "ok"
    kwargs = provider._client.messages.create.await_args.kwargs
    assert kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert kwargs["output_config"] == {"effort": "max"}
