"""Opus 5 的能力开关：默认自适应思考、支持 effort、显式关闭优先。"""

from __future__ import annotations

import pytest

from nanobot.providers.anthropic_provider import AnthropicProvider


@pytest.fixture
def provider() -> AnthropicProvider:
    return AnthropicProvider(api_key="k")


_TOOLS = [
    {
        "type": "function",
        "function": {"name": "list_dir", "description": "ls", "parameters": {"type": "object"}},
    }
]


def _kwargs(
    provider: AnthropicProvider,
    model: str,
    effort: str | None = None,
    *,
    tools=None,
    tool_choice=None,
) -> dict:
    return provider._build_kwargs(
        messages=[{"role": "user", "content": "hi"}],
        tools=tools,
        model=model,
        max_tokens=1024,
        temperature=0.7,
        reasoning_effort=effort,
        tool_choice=tool_choice,
    )


class TestToolChoiceKnowsThinkingIsOn:
    """thinking 开着时不能发 tool_choice: any，Anthropic 直接 400。"""

    def test_default_thinking_model_forces_auto(self) -> None:
        provider = AnthropicProvider(api_key="k")

        kwargs = _kwargs(
            provider, "claude-opus-5-20260901", tools=_TOOLS, tool_choice="required"
        )

        assert kwargs["thinking"]["type"] == "adaptive"
        assert kwargs["tool_choice"] == {"type": "auto"}

    def test_none_on_default_thinking_model_still_forces_auto(self) -> None:
        """none 在默认思考模型上译成 adaptive low，thinking 仍开着，tool_choice 只能 auto。"""
        provider = AnthropicProvider(api_key="k")

        kwargs = _kwargs(
            provider,
            "claude-opus-5-20260901",
            "none",
            tools=_TOOLS,
            tool_choice="required",
        )

        assert kwargs["thinking"]["type"] == "adaptive"
        assert kwargs["tool_choice"] == {"type": "auto"}


class TestOpus5Defaults:
    def test_thinking_is_adaptive_and_summarized_by_default(self, provider) -> None:
        kwargs = _kwargs(provider, "claude-opus-5-20260901")

        assert kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}

    def test_sampling_params_are_dropped(self, provider) -> None:
        assert "temperature" not in _kwargs(provider, "claude-opus-5-20260901")
        assert "temperature" not in _kwargs(provider, "claude-opus-5-20260901", "high")

    def test_effort_is_forwarded(self, provider) -> None:
        kwargs = _kwargs(provider, "claude-opus-5-20260901", "xhigh")

        assert kwargs["output_config"] == {"effort": "xhigh"}

    def test_unknown_effort_is_not_forwarded(self, provider) -> None:
        assert "output_config" not in _kwargs(provider, "claude-opus-5-20260901", "turbo")

    @pytest.mark.parametrize("model", ["claude-opus-5-20260901", "claude-fable-5-1"])
    @pytest.mark.parametrize("effort", ["none", "disabled"])
    def test_disable_becomes_adaptive_low(self, provider, model, effort) -> None:
        """默认思考模型拒收 thinking.disabled：关思考只能降到 adaptive + effort low。"""
        kwargs = _kwargs(provider, model, effort)

        assert kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
        assert kwargs["output_config"] == {"effort": "low"}
        assert "temperature" not in kwargs


class TestModelMatchingHasBoundaries:
    def test_prefix_only_matches_on_a_separator(self, provider) -> None:
        assert "temperature" not in _kwargs(provider, "claude-sonnet-5-20260101")
        assert "temperature" in _kwargs(provider, "claude-sonnet-55-20260101")

    def test_vendor_prefix_is_ignored(self, provider) -> None:
        assert "temperature" not in _kwargs(provider, "anthropic/claude-opus-5")

    def test_older_models_keep_the_budget_path(self, provider) -> None:
        kwargs = _kwargs(provider, "claude-sonnet-4-5-20260101", "high")

        assert kwargs["thinking"]["type"] == "enabled"
        assert kwargs["thinking"]["budget_tokens"] >= 8192
        assert kwargs["temperature"] == 1.0

    def test_effort_is_reserved_for_models_that_support_it(self, provider) -> None:
        kwargs = _kwargs(provider, "claude-sonnet-4-6-20260101", "adaptive")

        assert kwargs["thinking"] == {"type": "adaptive"}
        assert "output_config" not in kwargs
