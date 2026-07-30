from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.providers.fallback_provider import FallbackProvider


class _Primary(LLMProvider):
    def get_default_model(self) -> str:
        return "primary"

    async def chat(self, **kwargs) -> LLMResponse:
        return LLMResponse(content="primary failed", finish_reason="error", error_kind="server_error")


class _Fallback(LLMProvider):
    def get_default_model(self) -> str:
        return "backup"

    async def chat(self, **kwargs) -> LLMResponse:
        return LLMResponse(content="ok", finish_reason="stop")


@pytest.mark.asyncio
async def test_fallback_provider_construction_runs_off_event_loop() -> None:
    caller_thread = threading.get_ident()
    factory_threads: list[int] = []

    def factory(_preset):
        factory_threads.append(threading.get_ident())
        return _Fallback()

    provider = FallbackProvider(
        primary=_Primary(),
        fallback_presets=[SimpleNamespace(
            provider="anthropic_claude_code",
            model="backup",
            max_tokens=10,
            temperature=None,
            reasoning_effort=None,
        )],
        provider_factory=factory,
    )

    result = await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert result.content == "ok"
    assert factory_threads and factory_threads[0] != caller_thread
