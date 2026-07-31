"""模型能力表：一张表说清 opus-5 一族的开关，且 temperature 只跟采样能力走。"""

from __future__ import annotations

import pytest

from nanobot.providers import anthropic_provider as ap
from nanobot.providers.anthropic_provider import AnthropicProvider


@pytest.fixture
def provider() -> AnthropicProvider:
    return AnthropicProvider(api_key="k")


def _kwargs(provider: AnthropicProvider, model: str, effort: str | None = None) -> dict:
    return provider._build_kwargs(
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        model=model,
        max_tokens=1024,
        temperature=0.3,
        reasoning_effort=effort,
        tool_choice=None,
    )


@pytest.fixture
def sampling_thinker(monkeypatch) -> str:
    """一个默认开 thinking、但照常接受采样参数的模型（今天没有，明天会有）。"""
    caps = ap._ModelCaps(
        omit_sampling=False,
        effort=True,
        thinking_default=True,
        thinking_summarize=True,
    )
    monkeypatch.setitem(ap._MODEL_CAPS, "claude-testium-1", caps)
    return "claude-testium-1-20260101"


class TestTemperatureFollowsSamplingCapabilityOnly:
    def test_thinking_disabled_keeps_temperature(self, provider, sampling_thinker) -> None:
        kwargs = _kwargs(provider, sampling_thinker, "none")

        assert kwargs["thinking"] == {"type": "disabled"}
        assert kwargs["temperature"] == 0.3

    def test_adaptive_default_pins_temperature_to_one(self, provider, sampling_thinker) -> None:
        kwargs = _kwargs(provider, sampling_thinker)

        assert kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
        assert kwargs["temperature"] == 1.0

    def test_model_without_sampling_never_gets_temperature(self, provider) -> None:
        for effort in (None, "none", "disabled", "high", "adaptive"):
            assert "temperature" not in _kwargs(provider, "claude-opus-5-20260901"), effort


class TestCapsLookup:
    def test_single_lookup_covers_all_four_switches(self) -> None:
        caps = ap._caps_for("anthropic/claude-opus-5-20260901")

        assert (caps.omit_sampling, caps.effort, caps.thinking_default, caps.thinking_summarize) == (
            True,
            True,
            True,
            True,
        )

    def test_unknown_model_falls_back_to_plain_caps(self) -> None:
        caps = ap._caps_for("claude-sonnet-4-6-20260101")

        assert (caps.omit_sampling, caps.effort, caps.thinking_default, caps.thinking_summarize) == (
            False,
            False,
            False,
            False,
        )

    def test_prefix_boundary_is_respected(self) -> None:
        assert ap._caps_for("claude-sonnet-5-20260101").omit_sampling is True
        assert ap._caps_for("claude-sonnet-55-20260101").omit_sampling is False

    def test_sampling_only_models_do_not_gain_thinking(self) -> None:
        caps = ap._caps_for("claude-opus-4-7-20260101")

        assert caps.omit_sampling is True
        assert caps.thinking_default is False
