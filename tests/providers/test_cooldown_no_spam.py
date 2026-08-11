"""C2 消空转：模型进冷却后，后续每个请求不该再逐回合把它排进候选、skip、刷日志。

冷却入口已经打过一条 warning（模型 + 时长）。候选构建阶段静默剔除冷却中的 key，
让日志量与冷却时长解耦——8/6 的 skipped: quota cooldown 噪声就来自这里。
"""

from typing import Any
from unittest.mock import MagicMock

import pytest
from loguru import logger

from nanobot.config.schema import ModelPresetConfig
from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.providers.fallback_provider import FallbackProvider


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def _ok(content: str = "ok") -> LLMResponse:
    return LLMResponse(content=content, finish_reason="stop")


def _rate_limited() -> LLMResponse:
    return LLMResponse(content="rate limit reached", finish_reason="error",
                       error_kind="rate_limit", error_status_code=429)


class _FakeProvider(LLMProvider):
    def __init__(self, name: str, *responses: LLMResponse) -> None:
        super().__init__()
        self.name = name
        self._responses = list(responses) or [_ok()]
        self.calls: list[dict[str, Any]] = []

    def get_default_model(self) -> str:
        return "primary-model"

    async def chat(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(dict(kwargs))
        return self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]

    async def chat_stream(self, **kwargs: Any) -> LLMResponse:
        return await self.chat(**kwargs)


async def _ask(provider: FallbackProvider) -> LLMResponse:
    return await provider.chat(model="primary-model", messages=[{"role": "user", "content": "hi"}])


def _build(clock: _Clock) -> tuple[FallbackProvider, _FakeProvider, _FakeProvider]:
    primary = _FakeProvider("primary", _rate_limited(), _ok("primary back"))
    fallback = _FakeProvider("fallback", _ok("fallback ok"))
    provider = FallbackProvider(
        primary=primary,
        fallback_presets=[ModelPresetConfig(model="fallback-a", provider="backup")],
        provider_factory=MagicMock(return_value=fallback),
        clock=clock,
        primary_name="primary",
    )
    return provider, primary, fallback


@pytest.mark.asyncio
async def test_cooldown_entry_is_logged_once() -> None:
    clock = _Clock()
    provider, _, _ = _build(clock)
    logs: list[str] = []
    sink = logger.add(lambda m: logs.append(str(m)), format="{message}")
    try:
        await _ask(provider)
    finally:
        logger.remove(sink)
    # 进冷却时留一条可诊断的记录（模型 + 时长）。
    assert sum("cooling it down" in line for line in logs) == 1, logs


@pytest.mark.asyncio
async def test_no_repeated_cooldown_skip_log_after_entry() -> None:
    clock = _Clock()
    provider, primary, fallback = _build(clock)
    await _ask(provider)  # 进冷却（这条 warning 不在下面的采集窗内）

    logs: list[str] = []
    sink = logger.add(lambda m: logs.append(str(m)), format="{message}")
    try:
        for _ in range(3):
            assert (await _ask(provider)).content == "fallback ok"
    finally:
        logger.remove(sink)

    # 冷却期内的后续请求：不再逐回合刷 skip 日志。
    assert not any("quota cooldown" in line for line in logs), logs
    assert not any("skipped" in line for line in logs), logs
    # 冷却中的主模型不再被排进候选、不被调用。
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 4
