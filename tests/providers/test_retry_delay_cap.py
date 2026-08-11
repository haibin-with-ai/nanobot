"""标准重试分支的封顶与 fail-fast（选项 C：只修旧的“不封顶死等”，不引入链尾原地重试）。

旧行为：非 persistent 分支对 delay 完全不封顶，一个 Retry-After: 600 会让单个 provider
睡满约 600s、最多重试 3 次，把一个 turn 焊在坏端点上。修法：给标准分支封顶，Retry-After
超封顶时 fail-fast 返回错误，交给外层 FallbackProvider 换模型（persistent 语义不变，仍是封顶续等）。
"""

import pytest

from nanobot.providers.base import LLMProvider, LLMResponse


class _Scripted(LLMProvider):
    def __init__(self, responses: list) -> None:
        super().__init__()
        self._responses = list(responses)
        self.calls = 0

    async def chat(self, *args, **kwargs) -> LLMResponse:
        self.calls += 1
        return self._responses.pop(0)

    async def chat_stream(self, *args, **kwargs) -> LLMResponse:
        return await self.chat(*args, **kwargs)

    def get_default_model(self) -> str:
        return "test-model"


def _rate_limited(retry_after: float) -> LLMResponse:
    return LLMResponse(
        content="429 rate limit reached",
        finish_reason="error",
        error_status_code=429,
        error_retry_after_s=retry_after,
    )


@pytest.fixture
def slept(monkeypatch):
    # 直接抓 _sleep_with_heartbeat 的总 delay，绕开它内部把长睡切成心跳块的细节。
    delays: list[float] = []

    async def _fake_sleep(self, delay, **_kwargs) -> None:
        delays.append(delay)

    monkeypatch.setattr(LLMProvider, "_sleep_with_heartbeat", _fake_sleep)
    return delays


@pytest.mark.asyncio
async def test_standard_fails_fast_when_retry_after_exceeds_cap(slept) -> None:
    provider = _Scripted([_rate_limited(600), LLMResponse(content="ok")])
    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hi"}])
    # fail-fast：返回错误、不重试、一秒都不睡，让外层换模型。
    assert response.finish_reason == "error"
    assert provider.calls == 1
    assert slept == []


@pytest.mark.asyncio
async def test_standard_caps_delay_within_bound(slept) -> None:
    # Retry-After=60 → 60+buffer=61 被封顶到 60；不超封顶所以正常重试。
    provider = _Scripted([_rate_limited(60), LLMResponse(content="ok")])
    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hi"}])
    assert response.content == "ok"
    assert provider.calls == 2
    assert slept == [LLMProvider._PERSISTENT_MAX_DELAY]  # 标准与 persistent 共用同一封顶值


@pytest.mark.asyncio
async def test_persistent_still_waits_capped_not_fail_fast(slept) -> None:
    # persistent 语义不变：Retry-After 超封顶也不 fail-fast，夹到封顶后续等。
    provider = _Scripted([_rate_limited(600), LLMResponse(content="ok")])
    response = await provider.chat_with_retry(
        messages=[{"role": "user", "content": "hi"}], retry_mode="persistent"
    )
    assert response.content == "ok"
    assert provider.calls == 2
    assert slept == [LLMProvider._PERSISTENT_MAX_DELAY]
