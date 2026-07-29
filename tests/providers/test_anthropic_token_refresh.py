"""factory 侧的 Claude Code 接线：凭据从 OAuth store 取，product_mode 传下去。

配置里没有 api_key 是 OAuth provider 的正常状态，不能因此报「No API key configured」。
"""

from __future__ import annotations

from typing import Any

import pytest

from nanobot.config.schema import Config
from nanobot.providers.factory import _make_provider_core
from nanobot.providers.oauth_store import OAuthCredentials


@pytest.fixture
def config() -> Config:
    return Config.model_validate(
        {
            "providers": {"anthropicClaudeCode": {}},
            "modelPresets": {
                "main": {"provider": "anthropic_claude_code", "model": "claude-opus-4-6"}
            },
        }
    )


@pytest.fixture
def fake_token(monkeypatch) -> list[bool]:
    """拦掉真实 storage，记录是否被问过。"""
    asked: list[bool] = []

    def _get_token(self, force_refresh: bool = False, min_ttl_ms: int = 0) -> OAuthCredentials:
        asked.append(force_refresh)
        return OAuthCredentials("oauth-access", "r", 0, "acct")

    monkeypatch.setattr(
        "nanobot.providers.oauth_store.OAuthCredentialStore.get_token", _get_token
    )
    return asked


@pytest.fixture
def spy_client(monkeypatch) -> dict[str, Any]:
    """记录 client 构造参数；重建 client 后仍复用同一个 messages 桩，
    否则刷新路径一重建就把测试挂上去的桩丢了。"""
    captured: dict[str, Any] = {"constructions": []}

    class _Spy:
        messages_slot: Any = None

        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)
            captured["constructions"].append(kwargs)
            self.messages = _Spy.messages_slot

    import anthropic

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _Spy)
    captured["spy"] = _Spy
    return captured


def test_oauth_provider_pulls_token_from_store(config, fake_token, spy_client) -> None:
    _make_provider_core(config, preset_name="main")

    assert spy_client.get("auth_token") == "oauth-access"
    assert fake_token == [False]


def test_oauth_provider_gets_product_mode(config, fake_token, spy_client) -> None:
    provider = _make_provider_core(config, preset_name="main")

    assert provider.product_mode == "claude_code"


def test_oauth_provider_carries_identity_headers(config, fake_token, spy_client) -> None:
    provider = _make_provider_core(config, preset_name="main")

    assert "oauth-2025-04-20" in provider.extra_headers["anthropic-beta"]


def test_missing_api_key_is_not_an_error_for_oauth(config, fake_token, spy_client) -> None:
    """OAuth provider 配置里本来就不该有 api_key。"""
    _make_provider_core(config, preset_name="main")


def test_plain_anthropic_untouched(monkeypatch, spy_client) -> None:
    config = Config.model_validate(
        {
            "providers": {"anthropic": {"api_key": "sk-ant-real"}},
            "modelPresets": {"main": {"provider": "anthropic", "model": "claude-opus-4-6"}},
        }
    )

    provider = _make_provider_core(config, preset_name="main")

    assert spy_client.get("api_key") == "sk-ant-real"
    assert provider.product_mode == ""


def test_no_credentials_leaves_token_empty(config, monkeypatch, spy_client) -> None:
    monkeypatch.setattr(
        "nanobot.providers.oauth_store.OAuthCredentialStore.get_token",
        lambda self, force_refresh=False, min_ttl_ms=0: None,
    )

    _make_provider_core(config, preset_name="main")

    assert not spy_client.get("auth_token")


# ---------------------------------------------------------------------------
# 401/403 自愈：刷新一次、重建 client、重试一次
# ---------------------------------------------------------------------------


class _AuthError(Exception):
    status_code = 401


class _ServerError(Exception):
    status_code = 500


class _FakeMessages:
    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        result = self._outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _stub_response() -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text="ok")],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        model="claude-opus-4-6",
    )


@pytest.fixture
def oauth_provider(monkeypatch, spy_client):
    from nanobot.providers.anthropic_provider import AnthropicProvider

    return AnthropicProvider(api_key="old-token", product_mode="claude_code")


def _attach(provider: Any, spy: dict[str, Any], outcomes: list[Any]) -> "_FakeMessages":
    fake = _FakeMessages(outcomes)
    spy["spy"].messages_slot = fake
    provider._client.messages = fake
    return fake


async def test_auth_error_triggers_refresh_and_retry(
    oauth_provider, monkeypatch, spy_client
) -> None:
    refreshed: list[bool] = []

    def _get_token(self, force_refresh: bool = False, min_ttl_ms: int = 0):
        refreshed.append(force_refresh)
        return OAuthCredentials("new-token", "r", 0, "acct")

    monkeypatch.setattr(
        "nanobot.providers.oauth_store.OAuthCredentialStore.get_token", _get_token
    )
    fake = _attach(oauth_provider, spy_client, [_AuthError("401 unauthorized"), _stub_response()])

    result = await oauth_provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert result.content == "ok"
    assert refreshed == [True]
    assert len(fake.calls) == 2
    assert spy_client["auth_token"] == "new-token"


async def test_refresh_happens_only_once(oauth_provider, monkeypatch, spy_client) -> None:
    monkeypatch.setattr(
        "nanobot.providers.oauth_store.OAuthCredentialStore.get_token",
        lambda self, force_refresh=False, min_ttl_ms=0: OAuthCredentials("new", "r", 0, "a"),
    )
    fake = _attach(oauth_provider, spy_client, [_AuthError("401"), _AuthError("401 again")])

    result = await oauth_provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert len(fake.calls) == 2
    assert result.finish_reason == "error"


async def test_non_auth_error_does_not_refresh(
    oauth_provider, monkeypatch, spy_client
) -> None:
    def _boom(self, force_refresh: bool = False, min_ttl_ms: int = 0):
        raise AssertionError("500 不该触发刷新")

    monkeypatch.setattr(
        "nanobot.providers.oauth_store.OAuthCredentialStore.get_token", _boom
    )
    fake = _attach(oauth_provider, spy_client, [_ServerError("500 boom")])

    result = await oauth_provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert len(fake.calls) == 1
    assert result.finish_reason == "error"


async def test_api_key_mode_never_refreshes(monkeypatch, spy_client) -> None:
    from nanobot.providers.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(api_key="sk-ant-real")
    monkeypatch.setattr(
        "nanobot.providers.oauth_store.OAuthCredentialStore.get_token",
        lambda self, **kw: (_ for _ in ()).throw(AssertionError("不该刷新")),
    )
    fake = _attach(provider, spy_client, [_AuthError("401")])

    result = await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert len(fake.calls) == 1
    assert result.finish_reason == "error"


async def test_refresh_failure_returns_original_error(
    oauth_provider, monkeypatch, spy_client
) -> None:
    monkeypatch.setattr(
        "nanobot.providers.oauth_store.OAuthCredentialStore.get_token",
        lambda self, **kw: (_ for _ in ()).throw(RuntimeError("refresh dead")),
    )
    _attach(oauth_provider, spy_client, [_AuthError("401 unauthorized")])

    result = await oauth_provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert result.finish_reason == "error"
    assert "401" in (result.content or "")


# ---------------------------------------------------------------------------
# 流式路径同样要自愈：runner 主链路走的是 chat_stream，不是 chat
# ---------------------------------------------------------------------------


class _FakeStreamCtx:
    def __init__(self, outcome: Any) -> None:
        self._outcome = outcome

    async def __aenter__(self) -> Any:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def get_final_message(self) -> Any:
        return self._outcome


class _FakeStreamMessages:
    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    def stream(self, **kwargs: Any) -> _FakeStreamCtx:
        self.calls.append(kwargs)
        return _FakeStreamCtx(self._outcomes.pop(0))


def _attach_stream(provider: Any, spy: dict[str, Any], outcomes: list[Any]) -> _FakeStreamMessages:
    fake = _FakeStreamMessages(outcomes)
    spy["spy"].messages_slot = fake
    provider._client.messages = fake
    return fake


async def test_stream_auth_error_triggers_refresh_and_retry(
    oauth_provider, monkeypatch, spy_client
) -> None:
    refreshed: list[bool] = []

    def _get_token(self, force_refresh: bool = False, min_ttl_ms: int = 0):
        refreshed.append(force_refresh)
        return OAuthCredentials("new-token", "r", 0, "acct")

    monkeypatch.setattr(
        "nanobot.providers.oauth_store.OAuthCredentialStore.get_token", _get_token
    )
    fake = _attach_stream(
        oauth_provider, spy_client, [_AuthError("401 unauthorized"), _stub_response()]
    )

    result = await oauth_provider.chat_stream(messages=[{"role": "user", "content": "hi"}])

    assert result.content == "ok"
    assert refreshed == [True]
    assert len(fake.calls) == 2
    assert spy_client["auth_token"] == "new-token"


async def test_stream_refresh_happens_only_once(
    oauth_provider, monkeypatch, spy_client
) -> None:
    monkeypatch.setattr(
        "nanobot.providers.oauth_store.OAuthCredentialStore.get_token",
        lambda self, force_refresh=False, min_ttl_ms=0: OAuthCredentials("new", "r", 0, "a"),
    )
    fake = _attach_stream(oauth_provider, spy_client, [_AuthError("401"), _AuthError("401 again")])

    result = await oauth_provider.chat_stream(messages=[{"role": "user", "content": "hi"}])

    assert len(fake.calls) == 2
    assert result.finish_reason == "error"


async def test_stream_non_auth_error_does_not_refresh(
    oauth_provider, monkeypatch, spy_client
) -> None:
    def _boom(self, force_refresh: bool = False, min_ttl_ms: int = 0):
        raise AssertionError("500 不该触发刷新")

    monkeypatch.setattr(
        "nanobot.providers.oauth_store.OAuthCredentialStore.get_token", _boom
    )
    fake = _attach_stream(oauth_provider, spy_client, [_ServerError("500 boom")])

    result = await oauth_provider.chat_stream(messages=[{"role": "user", "content": "hi"}])

    assert len(fake.calls) == 1
    assert result.finish_reason == "error"


# ---------------------------------------------------------------------------
# 流式 401 自愈：只在还没吐字时重试，否则用户会看到重复输出
# ---------------------------------------------------------------------------


class _ScriptedStream:
    """先吐若干 text_delta，再按脚本抛错或正常收尾。"""

    def __init__(self, deltas: list[str], error: Exception | None) -> None:
        self._deltas = deltas
        self._error = error

    async def __aenter__(self) -> "_ScriptedStream":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    def __aiter__(self) -> "_ScriptedStream":
        return self

    async def __anext__(self) -> Any:
        from types import SimpleNamespace

        if self._deltas:
            text = self._deltas.pop(0)
            return SimpleNamespace(
                type="content_block_delta",
                index=0,
                delta=SimpleNamespace(type="text_delta", text=text),
            )
        if self._error is not None:
            error, self._error = self._error, None
            raise error
        raise StopAsyncIteration

    async def get_final_message(self) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            model="claude-opus-4-6",
        )


class _StreamingMessages:
    def __init__(self, scripts: list[tuple[list[str], Exception | None]]) -> None:
        self._scripts = scripts
        self.calls: list[dict[str, Any]] = []

    def stream(self, **kwargs: Any) -> _ScriptedStream:
        self.calls.append(kwargs)
        deltas, error = self._scripts.pop(0)
        if error is not None and not deltas:
            raise error
        return _ScriptedStream(list(deltas), error)


def _refresh_always(monkeypatch) -> list[bool]:
    seen: list[bool] = []

    def _get_token(self, force_refresh: bool = False, min_ttl_ms: int = 0):
        seen.append(force_refresh)
        return OAuthCredentials("new-token", "r", 0, "acct")

    monkeypatch.setattr(
        "nanobot.providers.oauth_store.OAuthCredentialStore.get_token", _get_token
    )
    return seen


async def test_stream_401_before_any_output_is_retried(
    oauth_provider, monkeypatch, spy_client
) -> None:
    refreshed = _refresh_always(monkeypatch)
    fake = _StreamingMessages([([], _AuthError("401")), ([], None)])
    oauth_provider._client.messages = fake
    spy_client["spy"].messages_slot = fake

    chunks: list[str] = []

    async def _sink(text: str) -> None:
        chunks.append(text)

    result = await oauth_provider.chat_stream(
        messages=[{"role": "user", "content": "hi"}], on_content_delta=_sink
    )

    assert refreshed == [True]
    assert len(fake.calls) == 2
    assert result.finish_reason == "stop"


async def test_stream_401_after_output_is_not_retried(
    oauth_provider, monkeypatch, spy_client
) -> None:
    refreshed = _refresh_always(monkeypatch)
    fake = _StreamingMessages([(["前半段"], _AuthError("401")), ([], None)])
    oauth_provider._client.messages = fake
    spy_client["spy"].messages_slot = fake

    chunks: list[str] = []

    async def _sink(text: str) -> None:
        chunks.append(text)

    result = await oauth_provider.chat_stream(
        messages=[{"role": "user", "content": "hi"}], on_content_delta=_sink
    )

    assert chunks == ["前半段"]
    assert len(fake.calls) == 1
    assert refreshed == []
    assert result.finish_reason == "error"
