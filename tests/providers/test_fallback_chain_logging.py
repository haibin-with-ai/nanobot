"""降级链必须在日志里连得起来。

C3 把主备两条路径合并成一条候选链时，整组「谁失败了、接下来试谁」的日志
被一起删掉，只有 tests/agent 里的一条断言发现了。这里补上链路本身的回归。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from loguru import logger

from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.providers.fallback_provider import FallbackProvider


class _Scripted(LLMProvider):
    def __init__(self, model: str, response: LLMResponse) -> None:
        self._model = model
        self._response = response

    def get_default_model(self) -> str:
        return self._model

    async def chat(self, **_kwargs) -> LLMResponse:
        return self._response


def _preset(model: str):
    return SimpleNamespace(
        provider="anthropic", model=model, max_tokens=1024, temperature=0.7, reasoning_effort=None
    )


def _error(content: str) -> LLMResponse:
    return LLMResponse(content=content, finish_reason="error", error_kind="server_error")


@pytest.mark.asyncio
async def test_each_hop_in_the_chain_says_why_it_is_here() -> None:
    providers = {
        "backup-a": _Scripted("backup-a", _error("backup-a exploded")),
        "backup-b": _Scripted("backup-b", LLMResponse(content="ok", finish_reason="stop")),
    }
    logs: list[str] = []
    sink_id = logger.add(lambda message: logs.append(str(message)), format="{message}")
    try:
        provider = FallbackProvider(
            primary=_Scripted("primary-model", _error("primary overloaded")),
            fallback_presets=[_preset("backup-a"), _preset("backup-b")],
            provider_factory=lambda preset: providers[preset.model],
        )
        result = await provider.chat(messages=[{"role": "user", "content": "hi"}], model="primary-model")
    finally:
        logger.remove(sink_id)

    assert result.content == "ok"
    assert any(
        "Primary model 'primary-model' failed: primary overloaded; trying fallback 'backup-a'" in line
        for line in logs
    ), logs
    assert any(
        "Fallback 'backup-a' failed: backup-a exploded; trying fallback 'backup-b'" in line
        for line in logs
    ), logs
