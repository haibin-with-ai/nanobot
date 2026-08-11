"""C1 按错误类型分流冷却。

单一阈值把「瞬时限流」和「账号配额耗尽」混一起，两头不讨好：配额型该长冷却切走，
瞬时型该秒级恢复。分流后：
- 配额/账单型（NON_RETRYABLE）→ 固定 600s，不读 Retry-After 缩短。
- 瞬时型（RETRYABLE）→ honor Retry-After，clamp 到 [5, 120]。
- 未知 429 → 走长冷却（有替补，宁可误判成长；对应 base._is_transient_429 的默认 False）。
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from nanobot.config.schema import ModelPresetConfig
from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.providers.fallback_provider import (
    FallbackProvider,
    QUOTA_EXHAUSTED_COOLDOWN_S,
    TRANSIENT_COOLDOWN_MAX_S,
    TRANSIENT_COOLDOWN_MIN_S,
)


# --- base: 三态分类 + 瞬时谓词 ------------------------------------------------


def _resp(content: str = "", **kw: Any) -> LLMResponse:
    return LLMResponse(content=content, finish_reason="error", **kw)


class TestIsTransient429:
    def test_quota_token_is_not_transient(self) -> None:
        assert LLMProvider._is_transient_429(_resp(error_code="insufficient_quota")) is False

    def test_quota_text_is_not_transient(self) -> None:
        # 8/6 的真实报文：codex "usage quota exceeded" 命中 "quota exceeded"。
        assert LLMProvider._is_transient_429(_resp(content="usage quota exceeded")) is False

    def test_rate_limit_token_is_transient(self) -> None:
        assert LLMProvider._is_transient_429(_resp(error_type="rate_limit_exceeded")) is True

    def test_overloaded_is_transient(self) -> None:
        assert LLMProvider._is_transient_429(_resp(error_type="overloaded_error")) is True

    def test_unknown_429_is_not_transient(self) -> None:
        # 只有 status 429、无可辨 token/marker → 走长冷却（默认非瞬时）。
        assert LLMProvider._is_transient_429(_resp(error_status_code=429, content="429")) is False


# --- fallback: 冷却时长分流 ---------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class _FakeProvider(LLMProvider):
    def __init__(self, name: str, response: LLMResponse) -> None:
        super().__init__()
        self.name = name
        self._response = response

    def get_default_model(self) -> str:
        return "primary-model"

    async def chat(self, **kwargs: Any) -> LLMResponse:
        return self._response

    async def chat_stream(self, **kwargs: Any) -> LLMResponse:
        return self._response


def _build(primary_response: LLMResponse, clock: _Clock) -> tuple[FallbackProvider, tuple[str, str]]:
    primary = _FakeProvider("primary", primary_response)
    spare = _FakeProvider("spare", LLMResponse(content="ok", finish_reason="stop"))
    provider = FallbackProvider(
        primary=primary,
        fallback_presets=[ModelPresetConfig(model="spare-model", provider="spare")],
        provider_factory=MagicMock(return_value=spare),
        clock=clock,
        primary_name="primary",
    )
    return provider, ("primary", "primary-model")


def _cool_seconds(provider: FallbackProvider, key: tuple[str, str], clock: _Clock) -> float:
    return provider._cooldowns[key] - clock()


async def _ask(provider: FallbackProvider) -> LLMResponse:
    return await provider.chat(model="primary-model", messages=[{"role": "user", "content": "hi"}])


class TestCooldownSplit:
    @pytest.mark.asyncio
    async def test_quota_exhausted_gets_long_cooldown(self) -> None:
        clock = _Clock()
        resp = LLMResponse(content="usage quota exceeded", finish_reason="error",
                           error_kind="quota", error_status_code=429)
        provider, key = _build(resp, clock)
        await _ask(provider)
        assert _cool_seconds(provider, key, clock) == QUOTA_EXHAUSTED_COOLDOWN_S

    @pytest.mark.asyncio
    async def test_quota_ignores_retry_after(self) -> None:
        clock = _Clock()
        resp = LLMResponse(content="quota exceeded", finish_reason="error",
                           error_kind="quota", error_status_code=429, retry_after=5.0)
        provider, key = _build(resp, clock)
        await _ask(provider)
        # 配额型不因 Retry-After 缩短：仍是 600，不是 5。
        assert _cool_seconds(provider, key, clock) == QUOTA_EXHAUSTED_COOLDOWN_S

    @pytest.mark.asyncio
    async def test_transient_honors_retry_after(self) -> None:
        clock = _Clock()
        resp = LLMResponse(content="rate limit reached", finish_reason="error",
                           error_type="rate_limit_exceeded", error_status_code=429, retry_after=5.0)
        provider, key = _build(resp, clock)
        await _ask(provider)
        # 瞬时型不再被抬到旧的 60 下限。
        assert _cool_seconds(provider, key, clock) == 5.0

    @pytest.mark.asyncio
    async def test_transient_clamps_below_min(self) -> None:
        clock = _Clock()
        resp = LLMResponse(content="rate limit reached", finish_reason="error",
                           error_type="rate_limit_exceeded", error_status_code=429, retry_after=1.0)
        provider, key = _build(resp, clock)
        await _ask(provider)
        assert _cool_seconds(provider, key, clock) == TRANSIENT_COOLDOWN_MIN_S

    @pytest.mark.asyncio
    async def test_transient_clamps_above_max(self) -> None:
        clock = _Clock()
        resp = LLMResponse(content="rate limit reached", finish_reason="error",
                           error_type="rate_limit_exceeded", error_status_code=429, retry_after=9999.0)
        provider, key = _build(resp, clock)
        await _ask(provider)
        assert _cool_seconds(provider, key, clock) == TRANSIENT_COOLDOWN_MAX_S

    @pytest.mark.asyncio
    async def test_unknown_429_gets_long_cooldown(self) -> None:
        clock = _Clock()
        resp = LLMResponse(content="429", finish_reason="error",
                           error_kind="rate_limit", error_status_code=429)
        provider, key = _build(resp, clock)
        await _ask(provider)
        # 未知 429（无 token/marker）走长冷却，验证 spec 的风险缓解。
        assert _cool_seconds(provider, key, clock) == QUOTA_EXHAUSTED_COOLDOWN_S
