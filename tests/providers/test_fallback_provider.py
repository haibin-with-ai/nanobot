"""FallbackProvider：拒绝回答要换模型，限流的模型要进冷却。"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from nanobot.config.schema import ModelPresetConfig
from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.providers.fallback_provider import (
    TRANSIENT_COOLDOWN_DEFAULT_S,
    TRANSIENT_COOLDOWN_MAX_S,
    TRANSIENT_COOLDOWN_MIN_S,
    FallbackProvider,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _ok(content: str = "ok") -> LLMResponse:
    return LLMResponse(content=content, finish_reason="stop")


def _refusal() -> LLMResponse:
    return LLMResponse(content="I can't help with that", finish_reason="error", error_kind="refusal")


def _rate_limited(retry_after: float | None = None) -> LLMResponse:
    return LLMResponse(
        content="rate limit reached",
        finish_reason="error",
        error_kind="rate_limit",
        error_status_code=429,
        error_retry_after_s=retry_after,
    )


def _server_error() -> LLMResponse:
    return LLMResponse(content="boom", finish_reason="error", error_kind="server_error")


def _preset(model: str, provider: str = "backup") -> ModelPresetConfig:
    return ModelPresetConfig(model=model, provider=provider)


class _FakeProvider(LLMProvider):
    def __init__(self, name: str, *responses: LLMResponse) -> None:
        super().__init__()
        self.name = name
        self._responses = list(responses) or [_ok()]
        self.calls: list[dict[str, Any]] = []

    def get_default_model(self) -> str:
        return f"{self.name}/model"

    async def chat(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(dict(kwargs))
        return self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]

    async def chat_stream(self, **kwargs: Any) -> LLMResponse:
        return await self.chat(**kwargs)


def _build(primary: _FakeProvider, fallback: _FakeProvider | None = None, *, clock=None):
    presets = [_preset("fallback-a")] if fallback is not None else []
    factory = MagicMock(return_value=fallback)
    provider = FallbackProvider(
        primary=primary,
        fallback_presets=presets,
        provider_factory=factory,
        clock=clock,
    )
    return provider, factory


async def _ask(provider: FallbackProvider, model: str = "primary-model") -> LLMResponse:
    return await provider.chat(messages=[{"role": "user", "content": "hi"}], model=model)


class TestRefusalSwitchesModel:
    """本地策略与上游相反：模型拒答就换一个，不当作终态。"""

    @pytest.mark.asyncio
    async def test_refusal_falls_back(self) -> None:
        primary = _FakeProvider("primary", _refusal())
        fallback = _FakeProvider("fallback", _ok("fallback ok"))
        provider, _ = _build(primary, fallback)

        result = await _ask(provider)

        assert result.content == "fallback ok"
        assert len(fallback.calls) == 1

    @pytest.mark.asyncio
    async def test_refusal_does_not_cool_the_model_down(self) -> None:
        primary = _FakeProvider("primary", _refusal(), _ok("primary back"))
        fallback = _FakeProvider("fallback", _ok("fallback ok"))
        provider, _ = _build(primary, fallback)

        await _ask(provider)
        result = await _ask(provider)

        assert result.content == "primary back"
        assert len(primary.calls) == 2


class TestQuotaCooldown:
    @pytest.mark.asyncio
    async def test_rate_limited_primary_is_skipped_next_time(self) -> None:
        clock = _Clock()
        primary = _FakeProvider("primary", _rate_limited(), _ok("primary back"))
        fallback = _FakeProvider("fallback", _ok("fallback ok"))
        provider, _ = _build(primary, fallback, clock=clock)

        await _ask(provider)
        result = await _ask(provider)

        assert result.content == "fallback ok"
        assert len(primary.calls) == 1
        assert len(fallback.calls) == 2

    @pytest.mark.asyncio
    async def test_cooldown_expires(self) -> None:
        clock = _Clock()
        primary = _FakeProvider("primary", _rate_limited(), _ok("primary back"))
        fallback = _FakeProvider("fallback", _ok("fallback ok"))
        provider, _ = _build(primary, fallback, clock=clock)

        await _ask(provider)
        clock.advance(TRANSIENT_COOLDOWN_DEFAULT_S + 1)
        result = await _ask(provider)

        assert result.content == "primary back"

    @pytest.mark.asyncio
    async def test_retry_after_is_clamped_to_the_floor(self) -> None:
        # 瞬时型 Retry-After=2 被抬到下限 5，冷却期内跳过、过点后放回。
        clock = _Clock()
        primary = _FakeProvider("primary", _rate_limited(retry_after=2), _ok("primary back"))
        fallback = _FakeProvider("fallback", _ok("fallback ok"))
        provider, _ = _build(primary, fallback, clock=clock)

        await _ask(provider)
        clock.advance(TRANSIENT_COOLDOWN_MIN_S - 1)
        assert (await _ask(provider)).content == "fallback ok"
        clock.advance(2)
        assert (await _ask(provider)).content == "primary back"

    @pytest.mark.asyncio
    async def test_retry_after_is_clamped_to_the_ceiling(self) -> None:
        # 瞬时型 Retry-After 巨大时被夹到上限 120。
        clock = _Clock()
        primary = _FakeProvider("primary", _rate_limited(retry_after=99_999), _ok("primary back"))
        fallback = _FakeProvider("fallback", _ok("fallback ok"))
        provider, _ = _build(primary, fallback, clock=clock)

        await _ask(provider)
        clock.advance(TRANSIENT_COOLDOWN_MAX_S + 1)

        assert (await _ask(provider)).content == "primary back"

    @pytest.mark.asyncio
    async def test_plain_errors_do_not_cool_anything_down(self) -> None:
        clock = _Clock()
        primary = _FakeProvider("primary", _server_error(), _ok("primary back"))
        fallback = _FakeProvider("fallback", _ok("fallback ok"))
        provider, _ = _build(primary, fallback, clock=clock)

        await _ask(provider)

        assert (await _ask(provider)).content == "primary back"

    @pytest.mark.asyncio
    async def test_everything_cooling_still_attempts_the_freest_model(self) -> None:
        """全员冷却时不能直接摆烂，挑冷却剩余最短的那个继续打。"""
        clock = _Clock()
        primary = _FakeProvider("primary", _rate_limited(retry_after=TRANSIENT_COOLDOWN_MAX_S))
        fallback = _FakeProvider("fallback", _rate_limited(retry_after=TRANSIENT_COOLDOWN_MIN_S), _ok("fallback ok"))
        provider, _ = _build(primary, fallback, clock=clock)

        await _ask(provider)
        result = await _ask(provider)

        assert result.content == "fallback ok"
        assert len(primary.calls) == 1
        assert len(fallback.calls) == 2


class TestAnthropicRefusalSignal:
    """拒答要以可切换错误的形式暴露出来，否则 fallback 根本看不见。"""

    def _response(self, stop_reason: str):
        from types import SimpleNamespace

        block = SimpleNamespace(type="text", text="I can't help with that")
        return SimpleNamespace(
            id="msg_1", type="message", stop_reason=stop_reason, content=[block], usage=None
        )

    def test_refusal_becomes_a_switchable_error(self) -> None:
        from nanobot.providers.anthropic_provider import AnthropicProvider

        result = AnthropicProvider._parse_response(self._response("refusal"))

        assert result.finish_reason == "error"
        assert result.error_kind == "refusal"
        assert result.content == "I can't help with that"

    def test_normal_stop_is_untouched(self) -> None:
        from nanobot.providers.anthropic_provider import AnthropicProvider

        result = AnthropicProvider._parse_response(self._response("end_turn"))

        assert result.finish_reason == "stop"
        assert result.error_kind is None


class TestPrimaryErrorSurvivesSkippedFallbacks:
    """备用模型全被跳过时，用户要看到主模型的真实错误，不是「熔断」这种空话。"""

    @pytest.mark.asyncio
    async def test_error_is_returned_when_the_only_fallback_is_cooling(self) -> None:
        clock = _Clock()
        primary = _FakeProvider("primary", _server_error(), _server_error())
        fallback = _FakeProvider("fallback", _rate_limited(), _ok("late"))
        provider, _ = _build(primary, fallback, clock=clock)

        await _ask(provider)
        result = await _ask(provider)

        assert result.content == "boom"
        assert len(fallback.calls) == 1

    @pytest.mark.asyncio
    async def test_provider_factory_failure_still_reports_the_primary_error(self) -> None:
        primary = _FakeProvider("primary", _server_error())
        provider, factory = _build(primary, _FakeProvider("fallback"))
        factory.side_effect = RuntimeError("no such provider")

        result = await _ask(provider)

        assert result.content == "boom"
        assert result.finish_reason == "error"


class TestCooldownIsForRateLimitsOnly:
    @pytest.mark.asyncio
    async def test_retry_after_on_a_server_error_does_not_cool_down(self) -> None:
        clock = _Clock()
        overloaded = LLMResponse(
            content="overloaded",
            finish_reason="error",
            error_kind="server_error",
            error_status_code=503,
            error_retry_after_s=30.0,
        )
        primary = _FakeProvider("primary", overloaded, _ok("primary back"))
        fallback = _FakeProvider("fallback", _ok("fallback ok"))
        provider, _ = _build(primary, fallback, clock=clock)

        await _ask(provider)
        result = await _ask(provider)

        assert result.content == "primary back"
        assert len(primary.calls) == 2


class TestRefusalDoesNotTripTheBreaker:
    @pytest.mark.asyncio
    async def test_repeated_refusals_keep_probing_the_primary(self) -> None:
        primary = _FakeProvider("primary", _refusal(), _refusal(), _refusal(), _ok("back"))
        fallback = _FakeProvider("fallback", _ok("fallback ok"))
        provider, _ = _build(primary, fallback)

        for _ in range(3):
            await _ask(provider)
        result = await _ask(provider)

        assert result.content == "back"
        assert len(primary.calls) == 4


class TestStreamedRefusalStaysPut:
    """口径：已经吐给用户的拒答不再换模型，换了会看到两段自相矛盾的回答。"""

    @pytest.mark.asyncio
    async def test_refusal_after_output_does_not_switch(self) -> None:
        class _StreamingPrimary(_FakeProvider):
            async def chat_stream(self, **kwargs: Any) -> LLMResponse:
                delta = kwargs.get("on_content_delta")
                if delta:
                    await delta("我不能帮你做这个。")
                return await self.chat(**kwargs)

        primary = _StreamingPrimary("primary", _refusal())
        fallback = _FakeProvider("fallback", _ok("fallback ok"))
        provider, _ = _build(primary, fallback)

        seen: list[str] = []

        async def _sink(text: str) -> None:
            seen.append(text)

        result = await provider.chat_stream(
            messages=[{"role": "user", "content": "hi"}],
            model="primary-model",
            on_content_delta=_sink,
        )

        assert seen == ["我不能帮你做这个。"]
        assert result.error_kind == "refusal"
        assert fallback.calls == []

    @pytest.mark.asyncio
    async def test_refusal_without_output_still_switches(self) -> None:
        primary = _FakeProvider("primary", _refusal())
        fallback = _FakeProvider("fallback", _ok("fallback ok"))
        provider, _ = _build(primary, fallback)

        result = await provider.chat_stream(
            messages=[{"role": "user", "content": "hi"}], model="primary-model"
        )

        assert result.content == "fallback ok"
