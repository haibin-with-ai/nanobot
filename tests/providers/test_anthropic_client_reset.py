"""Stall 之后换一个 Anthropic client：旧连接可能已经半死。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nanobot.providers.anthropic_provider import AnthropicProvider


class _StallingMessages:
    def __init__(self) -> None:
        self.calls = 0

    def stream(self, **_kwargs: Any):
        self.calls += 1
        raise asyncio.TimeoutError


class _OkMessages:
    def __init__(self) -> None:
        self.calls = 0

    def stream(self, **_kwargs: Any):
        self.calls += 1
        return _OkStream()


class _OkStream:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def get_final_message(self):
        from types import SimpleNamespace

        return SimpleNamespace(
            id="msg_1",
            type="message",
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="ok")],
            usage=None,
        )


class _FakeClient:
    def __init__(self, messages: Any) -> None:
        self.messages = messages
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


@pytest.fixture
def provider() -> AnthropicProvider:
    return AnthropicProvider(api_key="k", api_base="https://example.test/v1")


async def _ask(provider: AnthropicProvider):
    return await provider.chat_stream(messages=[{"role": "user", "content": "hi"}])


class TestClientResetOnStall:
    @pytest.mark.asyncio
    async def test_stall_swaps_in_a_fresh_client(self, provider, monkeypatch) -> None:
        stale = _FakeClient(_StallingMessages())
        fresh = _FakeClient(_OkMessages())
        provider._client = stale
        monkeypatch.setattr(provider, "_new_client", lambda: fresh)

        result = await _ask(provider)

        assert result.error_kind == "timeout"
        assert provider._client is fresh
        # 旧 client 可能还挂着别人的在途流，不能替别人关。
        assert stale.closed == 0

    @pytest.mark.asyncio
    async def test_reset_failure_does_not_mask_the_stall(self, provider, monkeypatch) -> None:
        stale = _FakeClient(_StallingMessages())
        provider._client = stale

        def _explode() -> None:
            raise RuntimeError("cannot rebuild")

        monkeypatch.setattr(provider, "_new_client", _explode)

        result = await _ask(provider)

        assert result.finish_reason == "error"
        assert result.error_kind == "timeout"
        assert provider._client is stale

    @pytest.mark.asyncio
    async def test_healthy_stream_keeps_its_client(self, provider, monkeypatch) -> None:
        healthy = _FakeClient(_OkMessages())
        provider._client = healthy
        monkeypatch.setattr(
            provider, "_new_client", lambda: pytest.fail("不该重建 client")
        )

        result = await _ask(provider)

        assert result.content == "ok"
        assert provider._client is healthy
        assert healthy.closed == 0


class TestClientKwargsAreBuiltOnce:
    def test_refresh_path_reuses_the_normalized_base_url(self, provider) -> None:
        kwargs = provider._client_kwargs("new-token")

        assert kwargs["base_url"] == "https://example.test"
        assert kwargs["max_retries"] == 0

    def test_claude_code_credentials_go_to_auth_token(self) -> None:
        claude_code = AnthropicProvider(api_key="tok", product_mode="claude_code")

        kwargs = claude_code._client_kwargs()

        assert kwargs["auth_token"] == "tok"
        assert "api_key" not in kwargs


class TestToolIdLengthCap:
    """Anthropic 的 tool id 有 64 字符硬上限，超长要摘要成合法 id。"""

    def test_long_but_legal_ids_are_shortened(self) -> None:
        from nanobot.providers.anthropic_provider import _MAX_TOOL_ID_LEN, _sanitize_tool_id

        long_id = "toolu_" + "a" * 200
        result = _sanitize_tool_id(long_id)

        assert len(result) <= _MAX_TOOL_ID_LEN
        assert result != long_id

    def test_the_same_id_always_maps_to_the_same_short_id(self) -> None:
        from nanobot.providers.anthropic_provider import _sanitize_tool_id

        long_id = "toolu_" + "b" * 200
        assert _sanitize_tool_id(long_id) == _sanitize_tool_id(long_id)

    def test_short_ids_are_untouched(self) -> None:
        from nanobot.providers.anthropic_provider import _sanitize_tool_id

        assert _sanitize_tool_id("toolu_01ABC") == "toolu_01ABC"


class TestResetDoesNotBreakConcurrentStreams:
    @pytest.mark.asyncio
    async def test_an_inflight_stream_survives_someone_elses_reset(self, provider, monkeypatch) -> None:
        """一条流 idle 超时触发重建时，另一条正在用旧 client 的流不能被打断。"""
        stale = _FakeClient(_StallingMessages())
        provider._client = stale
        monkeypatch.setattr(provider, "_new_client", lambda: _FakeClient(_OkMessages()))
        inflight = stale

        await _ask(provider)

        assert provider._client is not inflight
        assert inflight.closed == 0


class TestRefreshDoesNotBlockTheLoop:
    """刷新走同步 httpx + 文件锁，必须挪到线程里，别冻住事件循环。"""

    @pytest.mark.asyncio
    async def test_refresh_runs_off_the_event_loop(self, monkeypatch) -> None:
        import threading

        from nanobot.providers.oauth_store import OAuthCredentials

        claude_code = AnthropicProvider(api_key="old", product_mode="claude_code")
        caller_thread = threading.get_ident()
        seen: list[int] = []

        def _get_token(self, force_refresh: bool = False, min_ttl_ms: int = 0):
            seen.append(threading.get_ident())
            return OAuthCredentials("fresh", "r", 0, "acct")

        monkeypatch.setattr(
            "nanobot.providers.oauth_store.OAuthCredentialStore.get_token", _get_token
        )

        assert await claude_code._refresh_credentials() is True
        assert seen and seen[0] != caller_thread
        assert claude_code.api_key == "fresh"
