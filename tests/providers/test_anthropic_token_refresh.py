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
    captured: dict[str, Any] = {}

    class _Spy:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    import anthropic

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _Spy)
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
