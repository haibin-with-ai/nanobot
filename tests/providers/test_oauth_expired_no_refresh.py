"""过期且无 refresh_token 时不得返回注定 401 的陈旧凭据。

真实故障形态：订阅登录早就过期、本地又没有 refresh_token，get_token() 却把
那份死凭据原样交出去，调用方拿它发请求只能收到 401，用户看到的只有一句
authentication error，真实原因在 store 里被吞掉。

同时钉住环境变量注入的 expires_at=0 凭据：它没有过期信息，不算过期，
即使 force_refresh 也必须照常返回，不能被这次修复误伤。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from nanobot.providers.oauth_store import OAuthCredentials, OAuthCredentialStore


@pytest.fixture
def store(tmp_path: Path) -> OAuthCredentialStore:
    return OAuthCredentialStore(data_dir=tmp_path)


@pytest.fixture(autouse=True)
def _isolate_migration_sources(monkeypatch, tmp_path: Path):
    """切断全部迁移来源，避免读到开发机上的真实凭据。"""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(
        "nanobot.providers.oauth_store._CLAUDE_CLI_CREDENTIALS_PATH",
        tmp_path / "absent" / ".credentials.json",
    )
    monkeypatch.setattr(
        "nanobot.providers.oauth_store._CLAUDE_CLI_CONFIG_PATH",
        tmp_path / "absent" / ".claude.json",
    )
    monkeypatch.setattr(
        "nanobot.providers.oauth_store._flat_credentials_path",
        lambda: tmp_path / "absent" / "oauth_credentials.json",
    )


def _ms(offset_seconds: int) -> int:
    return int(time.time() * 1000) + offset_seconds * 1000


def test_expired_without_refresh_token_returns_none(store: OAuthCredentialStore) -> None:
    store.save(
        OAuthCredentials(
            access_token="dead", refresh_token="", expires_at=_ms(-3600), account_id="acct-1"
        )
    )

    assert store.get_token() is None


def test_expired_without_refresh_token_returns_none_under_force_refresh(
    store: OAuthCredentialStore,
) -> None:
    store.save(
        OAuthCredentials(access_token="dead", refresh_token="", expires_at=_ms(-60))
    )

    assert store.get_token(force_refresh=True) is None


def test_env_token_without_expiry_is_still_returned(
    store: OAuthCredentialStore, monkeypatch
) -> None:
    """_from_env 的 expires_at=0 表示没有过期信息，不是过期，不能被误伤。"""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "env-token")

    token = store.get_token()

    assert token is not None
    assert token.access_token == "env-token"


def test_env_token_without_expiry_survives_force_refresh(
    store: OAuthCredentialStore, monkeypatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "env-token")

    token = store.get_token(force_refresh=True)

    assert token is not None
    assert token.access_token == "env-token"


def test_not_yet_expired_without_refresh_token_is_still_returned(
    store: OAuthCredentialStore,
) -> None:
    """离过期还有 60 秒（低于 5 分钟刷新余量）但确实还能用，照常返回。"""
    store.save(
        OAuthCredentials(access_token="almost", refresh_token="", expires_at=_ms(60))
    )

    token = store.get_token()

    assert token is not None
    assert token.access_token == "almost"
