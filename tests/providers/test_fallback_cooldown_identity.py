"""冷却身份：主模型和备用模型必须用同一套 (provider, model) 命名。

主 provider 过去用类名/实例名当 key，备用 preset 用配置里的 provider 字段，
同一个端点因此记成两个 key：主模型被限流进冷却后，指向同一端点的备用 preset
仍会被当成「另一个模型」再打一次，冷却形同虚设。
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from nanobot.config.schema import ModelPresetConfig
from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.providers.fallback_provider import FallbackProvider


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _ok(content: str = "ok") -> LLMResponse:
    return LLMResponse(content=content, finish_reason="stop")


def _rate_limited() -> LLMResponse:
    return LLMResponse(
        content="rate limit reached",
        finish_reason="error",
        error_kind="rate_limit",
        error_status_code=429,
    )


class _FakeProvider(LLMProvider):
    def __init__(self, name: str, *responses: LLMResponse) -> None:
        super().__init__()
        self.name = name
        self._responses = list(responses) or [_ok()]
        self.calls: list[dict[str, Any]] = []

    def get_default_model(self) -> str:
        return "claude-opus-5"

    async def chat(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(dict(kwargs))
        return self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]

    async def chat_stream(self, **kwargs: Any) -> LLMResponse:
        return await self.chat(**kwargs)


async def _ask(provider: FallbackProvider) -> LLMResponse:
    return await provider.chat(model="claude-opus-5", messages=[{"role": "user", "content": "hi"}])


class TestCooldownIdentity:
    @pytest.mark.asyncio
    async def test_same_endpoint_fallback_is_skipped_while_primary_cools(self) -> None:
        """备用 preset 指向和主模型同一个 (provider, model) 时，冷却期内不许再打它。"""
        clock = _Clock()
        primary = _FakeProvider("AnthropicProvider", _rate_limited())
        twin = _FakeProvider("twin", _ok("twin ok"))
        spare = _FakeProvider("spare", _ok("spare ok"))
        presets = [
            ModelPresetConfig(model="claude-opus-5", provider="auto"),
            ModelPresetConfig(model="grok-4", provider="xai"),
        ]
        built = {"claude-opus-5": twin, "grok-4": spare}
        factory = MagicMock(side_effect=lambda preset: built[preset.model])
        provider = FallbackProvider(
            primary=primary,
            fallback_presets=presets,
            provider_factory=factory,
            clock=clock,
            primary_name="anthropic",
            provider_name_resolver=lambda preset: (
                "anthropic" if preset.model.startswith("claude") else preset.provider
            ),
        )

        first = await _ask(provider)

        assert first.content == "twin ok"
        assert len(twin.calls) == 1

        second = await _ask(provider)

        assert second.content == "spare ok"
        assert len(primary.calls) == 1, "主模型在冷却期内不该再被调用"
        assert len(twin.calls) == 1, "同一端点的备用 preset 必须跟着主模型一起冷却"

    @pytest.mark.asyncio
    async def test_declared_provider_field_is_the_default_namespace(self) -> None:
        """不传解析器时退回配置里声明的 provider 字段，行为与既有配置一致。"""
        clock = _Clock()
        primary = _FakeProvider("primary", _rate_limited(), _ok("primary back"))
        backup = _FakeProvider("backup", _ok("backup ok"))
        provider = FallbackProvider(
            primary=primary,
            fallback_presets=[ModelPresetConfig(model="backup-model", provider="backup")],
            provider_factory=MagicMock(return_value=backup),
            clock=clock,
        )

        assert (await _ask(provider)).content == "backup ok"
