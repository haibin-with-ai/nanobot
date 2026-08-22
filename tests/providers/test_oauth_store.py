"""Anthropic Claude Code OAuth 凭据存取、迁移与并发刷新。

参照 tests/providers/test_xai_oauth.py 的加锁刷新形态，但凭据来源不同：
Claude Code 有三级迁移链（环境变量 / CLI 凭据文件 / legacy 配置），
每一级都必须有用例，否则换机器登录会静默失败。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from filelock import FileLock, Timeout

from nanobot.providers.oauth_store import (
    InvalidGrantError,
    OAuthCredentials,
    OAuthCredentialStore,
    TokenRefreshError,
    refresh_anthropic_token,
)


@pytest.fixture
def store(tmp_path: Path) -> OAuthCredentialStore:
    return OAuthCredentialStore(data_dir=tmp_path)


@pytest.fixture(autouse=True)
def _isolate_migration_sources(monkeypatch, tmp_path: Path):
    """默认切断全部迁移来源，避免测试读到开发机上的真实凭据。"""
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


def _creds(access: str = "tok", expires_at: int | None = None) -> OAuthCredentials:
    return OAuthCredentials(
        access_token=access,
        refresh_token="refresh",
        expires_at=expires_at if expires_at is not None else _future_ms(3600),
        account_id="acct-1",
    )


def _future_ms(seconds: int) -> int:
    return int(time.time() * 1000) + seconds * 1000


# ---------------------------------------------------------------------------
# 存取往返
# ---------------------------------------------------------------------------


def test_save_then_load_roundtrip(store: OAuthCredentialStore) -> None:
    store.save(_creds("access-1"))

    loaded = store.load()

    assert loaded is not None
    assert loaded.access_token == "access-1"
    assert loaded.refresh_token == "refresh"
    assert loaded.account_id == "acct-1"


def test_load_returns_none_when_no_source(store: OAuthCredentialStore) -> None:
    assert store.load() is None


def test_token_path_lives_under_data_dir(store: OAuthCredentialStore, tmp_path: Path) -> None:
    assert tmp_path in store.get_token_path().parents


# ---------------------------------------------------------------------------
# 三级迁移链
# ---------------------------------------------------------------------------


def test_env_var_wins_over_credential_files(
    store: OAuthCredentialStore, monkeypatch, tmp_path: Path
) -> None:
    cred_file = tmp_path / "credentials.json"
    cred_file.write_text(json.dumps({"claudeAiOauth": {"accessToken": "from-file"}}))
    monkeypatch.setattr(
        "nanobot.providers.oauth_store._CLAUDE_CLI_CREDENTIALS_PATH", cred_file
    )
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "from-env")

    loaded = store.load()

    assert loaded is not None
    assert loaded.access_token == "from-env"


def test_migrates_from_claude_cli_credentials_file(
    store: OAuthCredentialStore, monkeypatch, tmp_path: Path
) -> None:
    cred_file = tmp_path / "credentials.json"
    cred_file.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "cli-access",
                    "refreshToken": "cli-refresh",
                    "expiresAt": 1893456000000,
                    "accountUuid": "uuid-9",
                }
            }
        )
    )
    monkeypatch.setattr(
        "nanobot.providers.oauth_store._CLAUDE_CLI_CREDENTIALS_PATH", cred_file
    )

    loaded = store.load()

    assert loaded is not None
    assert loaded.access_token == "cli-access"
    assert loaded.refresh_token == "cli-refresh"
    assert loaded.account_id == "uuid-9"


def test_migrates_from_legacy_claude_json(
    store: OAuthCredentialStore, monkeypatch, tmp_path: Path
) -> None:
    legacy = tmp_path / ".claude.json"
    legacy.write_text(
        json.dumps({"oauth": {"access_token": "legacy-access", "refresh_token": "legacy-refresh"}})
    )
    monkeypatch.setattr("nanobot.providers.oauth_store._CLAUDE_CLI_CONFIG_PATH", legacy)

    loaded = store.load()

    assert loaded is not None
    assert loaded.access_token == "legacy-access"


def test_credentials_file_takes_precedence_over_legacy(
    store: OAuthCredentialStore, monkeypatch, tmp_path: Path
) -> None:
    cred_file = tmp_path / "credentials.json"
    cred_file.write_text(json.dumps({"claudeAiOauth": {"accessToken": "new-path"}}))
    legacy = tmp_path / ".claude.json"
    legacy.write_text(json.dumps({"oauth": {"access_token": "old-path"}}))
    monkeypatch.setattr(
        "nanobot.providers.oauth_store._CLAUDE_CLI_CREDENTIALS_PATH", cred_file
    )
    monkeypatch.setattr("nanobot.providers.oauth_store._CLAUDE_CLI_CONFIG_PATH", legacy)

    loaded = store.load()

    assert loaded is not None
    assert loaded.access_token == "new-path"


def test_migrates_from_flat_credentials_next_to_config(
    store: OAuthCredentialStore, monkeypatch, tmp_path: Path
) -> None:
    flat = tmp_path / "oauth_credentials.json"
    flat.write_text(
        json.dumps(
            {
                "access_token": "flat-access",
                "refresh_token": "flat-refresh",
                "expires_at_ms": 1893456000000,
            }
        )
    )
    monkeypatch.setattr(
        "nanobot.providers.oauth_store._flat_credentials_path", lambda: flat
    )

    loaded = store.load()

    assert loaded is not None
    assert loaded.access_token == "flat-access"
    assert loaded.expires_at == 1893456000000


def test_migration_persists_so_next_load_skips_source(
    store: OAuthCredentialStore, monkeypatch, tmp_path: Path
) -> None:
    cred_file = tmp_path / "credentials.json"
    cred_file.write_text(json.dumps({"claudeAiOauth": {"accessToken": "once"}}))
    monkeypatch.setattr(
        "nanobot.providers.oauth_store._CLAUDE_CLI_CREDENTIALS_PATH", cred_file
    )

    assert store.load() is not None
    cred_file.unlink()

    again = store.load()
    assert again is not None
    assert again.access_token == "once"


def test_corrupt_migration_source_is_ignored(
    store: OAuthCredentialStore, monkeypatch, tmp_path: Path
) -> None:
    cred_file = tmp_path / "credentials.json"
    cred_file.write_text("{ not json")
    monkeypatch.setattr(
        "nanobot.providers.oauth_store._CLAUDE_CLI_CREDENTIALS_PATH", cred_file
    )

    assert store.load() is None


# ---------------------------------------------------------------------------
# 过期判定与刷新
# ---------------------------------------------------------------------------


def test_token_within_five_minutes_of_expiry_needs_refresh(
    store: OAuthCredentialStore,
) -> None:
    store.save(_creds(expires_at=_future_ms(120)))

    assert store.needs_refresh() is True


def test_token_with_ample_ttl_does_not_need_refresh(store: OAuthCredentialStore) -> None:
    store.save(_creds(expires_at=_future_ms(3600)))

    assert store.needs_refresh() is False


def test_zero_expiry_env_token_never_needs_refresh(
    store: OAuthCredentialStore, monkeypatch
) -> None:
    """环境变量注入的 token 没有过期信息，不能因此触发无意义的刷新。"""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "env-token")

    assert store.needs_refresh() is False


def test_get_token_refreshes_when_expiring(
    store: OAuthCredentialStore, monkeypatch
) -> None:
    store.save(_creds("stale", expires_at=_future_ms(60)))
    monkeypatch.setattr(
        "nanobot.providers.oauth_store.refresh_anthropic_token",
        lambda refresh: OAuthCredentials("fresh", "refresh-2", _future_ms(3600), "acct-1"),
    )

    token = store.get_token()

    assert token is not None
    assert token.access_token == "fresh"
    assert store.load().access_token == "fresh"


def test_get_token_skips_refresh_when_fresh(
    store: OAuthCredentialStore, monkeypatch
) -> None:
    store.save(_creds("good", expires_at=_future_ms(3600)))

    def _boom(refresh: str) -> OAuthCredentials:
        raise AssertionError("不应触发刷新")

    monkeypatch.setattr("nanobot.providers.oauth_store.refresh_anthropic_token", _boom)

    assert store.get_token().access_token == "good"


def test_force_refresh_bypasses_freshness_check(
    store: OAuthCredentialStore, monkeypatch
) -> None:
    store.save(_creds("good", expires_at=_future_ms(3600)))
    monkeypatch.setattr(
        "nanobot.providers.oauth_store.refresh_anthropic_token",
        lambda refresh: OAuthCredentials("forced", "refresh-2", _future_ms(3600), "acct-1"),
    )

    assert store.get_token(force_refresh=True).access_token == "forced"


def test_refresh_failure_preserves_original_error(
    store: OAuthCredentialStore, monkeypatch
) -> None:
    store.save(_creds("stale", expires_at=_future_ms(60)))

    def _fail(refresh: str) -> OAuthCredentials:
        raise RuntimeError("Token refresh failed: 401 unauthorized")

    monkeypatch.setattr("nanobot.providers.oauth_store.refresh_anthropic_token", _fail)

    with pytest.raises(RuntimeError, match="401 unauthorized"):
        store.get_token(force_refresh=True)


# ---------------------------------------------------------------------------
# refresh token 被判死后回源：指纹闸只认「有人重登过」
# ---------------------------------------------------------------------------


def _fail_with(exc: Exception):
    def _raise(refresh_token: str) -> OAuthCredentials:
        raise exc

    return _raise


def test_dead_refresh_remigrates_when_source_has_new_login(
    store: OAuthCredentialStore, monkeypatch, tmp_path: Path
) -> None:
    """refresh token 被吊销、而 CLI 凭据文件已换新 refresh token → 自动收编。"""
    store.save(_creds("stale", expires_at=_future_ms(60)))
    cred_file = tmp_path / "credentials.json"
    cred_file.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "relogged-access",
                    "refreshToken": "relogged-refresh",
                    "expiresAt": _future_ms(3600),
                    "accountUuid": "uuid-new",
                }
            }
        )
    )
    monkeypatch.setattr(
        "nanobot.providers.oauth_store._CLAUDE_CLI_CREDENTIALS_PATH", cred_file
    )
    monkeypatch.setattr(
        "nanobot.providers.oauth_store.refresh_anthropic_token",
        _fail_with(InvalidGrantError("Token refresh rejected: 400 invalid_grant")),
    )

    token = store.get_token(force_refresh=True)

    assert token is not None
    assert token.access_token == "relogged-access"
    assert store.load().refresh_token == "relogged-refresh"


def test_dead_refresh_does_not_remigrate_same_refresh_token(
    store: OAuthCredentialStore, monkeypatch, tmp_path: Path
) -> None:
    """来源里还是同一个死 refresh token（没人重登）→ 不迁移，如实抛错。"""
    store.save(_creds("stale", expires_at=_future_ms(60)))
    cred_file = tmp_path / "credentials.json"
    cred_file.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "same-access",
                    "refreshToken": "refresh",  # 与 store 里那份相同
                    "expiresAt": _future_ms(3600),
                }
            }
        )
    )
    monkeypatch.setattr(
        "nanobot.providers.oauth_store._CLAUDE_CLI_CREDENTIALS_PATH", cred_file
    )
    monkeypatch.setattr(
        "nanobot.providers.oauth_store.refresh_anthropic_token",
        _fail_with(InvalidGrantError("Token refresh rejected: 400 invalid_grant")),
    )

    with pytest.raises(InvalidGrantError):
        store.get_token(force_refresh=True)


def test_transient_refresh_error_preserves_store_without_remigrating(
    store: OAuthCredentialStore, monkeypatch, tmp_path: Path
) -> None:
    """瞬时失败（网络/5xx）绝不能回源，也不能动 store 里的好 refresh token。"""
    store.save(_creds("stale", expires_at=_future_ms(60)))
    cred_file = tmp_path / "credentials.json"
    cred_file.write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "x", "refreshToken": "other-refresh"}})
    )
    monkeypatch.setattr(
        "nanobot.providers.oauth_store._CLAUDE_CLI_CREDENTIALS_PATH", cred_file
    )
    monkeypatch.setattr(
        "nanobot.providers.oauth_store.refresh_anthropic_token",
        _fail_with(TokenRefreshError("Token refresh failed: 503 upstream")),
    )

    with pytest.raises(TokenRefreshError):
        store.get_token(force_refresh=True)

    # store 未被回源覆盖，原 refresh token 原封不动。
    assert store.load().refresh_token == "refresh"


# ---------------------------------------------------------------------------
# 并发刷新：拿锁后重查，别人刷过就用别人的
# ---------------------------------------------------------------------------


def test_concurrent_refresh_reuses_peer_result(
    store: OAuthCredentialStore, monkeypatch, tmp_path: Path
) -> None:
    """模拟另一进程在本进程等锁期间已完成刷新，本进程不得再刷一次。"""
    store.save(_creds("stale", expires_at=_future_ms(60)))
    calls: list[str] = []

    def _peer_refreshed_meanwhile(lock_path: Path) -> None:
        OAuthCredentialStore(data_dir=tmp_path).save(
            _creds("peer-fresh", expires_at=_future_ms(3600))
        )

    def _refresh(refresh_token: str) -> OAuthCredentials:
        calls.append(refresh_token)
        return OAuthCredentials("self-fresh", "r", _future_ms(3600), "acct-1")

    monkeypatch.setattr("nanobot.providers.oauth_store.refresh_anthropic_token", _refresh)
    monkeypatch.setattr(store, "_on_lock_acquired", _peer_refreshed_meanwhile)

    token = store.get_token()

    assert token.access_token == "peer-fresh"
    assert calls == []


def test_refresh_holds_file_lock(store: OAuthCredentialStore, monkeypatch) -> None:
    """刷新期间外部进程必须拿不到锁。用独立 FileLock 实例模拟外部进程。"""
    store.save(_creds("stale", expires_at=_future_ms(60)))
    blocked: list[bool] = []

    def _refresh(refresh_token: str) -> OAuthCredentials:
        rival = FileLock(str(store.get_token_path()) + ".lock", timeout=0)
        try:
            rival.acquire()
            rival.release()
            blocked.append(False)
        except Timeout:
            blocked.append(True)
        return OAuthCredentials("fresh", "r", _future_ms(3600), "acct-1")

    monkeypatch.setattr("nanobot.providers.oauth_store.refresh_anthropic_token", _refresh)

    store.get_token()

    assert blocked == [True]


# ---------------------------------------------------------------------------
# refresh_anthropic_token 的 HTTP 契约
# ---------------------------------------------------------------------------


def test_refresh_expires_five_minutes_early(monkeypatch) -> None:
    """提前 5 分钟过期，避免拿着将死的 token 发请求。"""

    class _Response:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {
                "access_token": "a",
                "refresh_token": "r",
                "expires_in": 3600,
                "account": {"uuid": "u-1"},
            }

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, *args, **kwargs):
            return _Response()

    monkeypatch.setattr("nanobot.providers.oauth_store.httpx.Client", lambda **kw: _Client())

    before = int(time.time() * 1000)
    creds = refresh_anthropic_token("refresh-token")

    expected = before + 3600 * 1000 - 5 * 60 * 1000
    assert abs(creds.expires_at - expected) < 2000
    assert creds.account_id == "u-1"


def test_refresh_keeps_the_old_token_when_the_response_omits_one(monkeypatch) -> None:
    """不轮换 refresh token 的响应不能把本地凭据写空，否则永远刷不回来。"""

    class _Response:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"access_token": "a", "expires_in": 3600}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, *args, **kwargs):
            return _Response()

    monkeypatch.setattr("nanobot.providers.oauth_store.httpx.Client", lambda **kw: _Client())

    creds = refresh_anthropic_token("old-refresh-token")

    assert creds.refresh_token == "old-refresh-token"


def test_refresh_raises_on_non_200(monkeypatch) -> None:
    class _Response:
        status_code = 401
        text = "unauthorized"

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, *args, **kwargs):
            return _Response()

    monkeypatch.setattr("nanobot.providers.oauth_store.httpx.Client", lambda **kw: _Client())

    with pytest.raises(RuntimeError, match="401"):
        refresh_anthropic_token("refresh-token")


def _client_returning(status: int, text: str):
    class _Response:
        status_code = status

    _Response.text = text

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, *args, **kwargs):
            return _Response()

    return lambda **kw: _Client()


def test_invalid_grant_body_raises_invalid_grant_error(monkeypatch) -> None:
    """400/401 且带 invalid_grant → 真死，抛 InvalidGrantError 触发回源。"""
    monkeypatch.setattr(
        "nanobot.providers.oauth_store.httpx.Client",
        _client_returning(400, '{"error":"invalid_grant"}'),
    )

    with pytest.raises(InvalidGrantError):
        refresh_anthropic_token("refresh-token")


def test_401_without_invalid_grant_is_transient(monkeypatch) -> None:
    """401 但不是 invalid_grant（如临时鉴权抖动）→ 归为瞬时，不回源。"""
    monkeypatch.setattr(
        "nanobot.providers.oauth_store.httpx.Client",
        _client_returning(401, "unauthorized"),
    )

    with pytest.raises(TokenRefreshError) as excinfo:
        refresh_anthropic_token("refresh-token")
    assert not isinstance(excinfo.value, InvalidGrantError)


def test_network_error_is_transient(monkeypatch) -> None:
    """httpx 网络异常 → TokenRefreshError（瞬时），绝不误判为凭据死亡。"""
    import httpx as _httpx

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, *args, **kwargs):
            raise _httpx.ConnectError("dial tcp: connection refused")

    monkeypatch.setattr("nanobot.providers.oauth_store.httpx.Client", lambda **kw: _Client())

    with pytest.raises(TokenRefreshError) as excinfo:
        refresh_anthropic_token("refresh-token")
    assert not isinstance(excinfo.value, InvalidGrantError)


def test_refresh_raises_on_missing_fields(monkeypatch) -> None:
    class _Response:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"access_token": "a"}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, *args, **kwargs):
            return _Response()

    monkeypatch.setattr("nanobot.providers.oauth_store.httpx.Client", lambda **kw: _Client())

    with pytest.raises(RuntimeError, match="missing required fields"):
        refresh_anthropic_token("refresh-token")


class TestRefreshKeepsWhatTheServerOmits:
    def _store(self, tmp_path, monkeypatch, saved):
        from nanobot.providers.oauth_store import OAuthCredentialStore

        store = OAuthCredentialStore()
        monkeypatch.setattr(store, "load", lambda: saved)
        monkeypatch.setattr(store, "save", lambda creds: None)
        monkeypatch.setattr(store, "_lock", lambda: _NullLock())
        monkeypatch.setattr(store, "_on_lock_acquired", lambda path: None)
        return store

    def test_account_id_survives_a_response_without_account(
        self, tmp_path, monkeypatch
    ) -> None:
        from nanobot.providers.oauth_store import OAuthCredentials

        saved = OAuthCredentials("old", "refresh-1", 0, "acct-1")
        store = self._store(tmp_path, monkeypatch, saved)
        monkeypatch.setattr(
            "nanobot.providers.oauth_store.refresh_anthropic_token",
            lambda token: OAuthCredentials("new", token, 9_999_999_999_000, None),
        )

        creds = store.get_token(force_refresh=True)

        assert creds.access_token == "new"
        assert creds.account_id == "acct-1"

    def test_server_account_wins_when_present(self, tmp_path, monkeypatch) -> None:
        from nanobot.providers.oauth_store import OAuthCredentials

        saved = OAuthCredentials("old", "refresh-1", 0, "acct-1")
        store = self._store(tmp_path, monkeypatch, saved)
        monkeypatch.setattr(
            "nanobot.providers.oauth_store.refresh_anthropic_token",
            lambda token: OAuthCredentials("new", token, 9_999_999_999_000, "acct-2"),
        )

        assert store.get_token(force_refresh=True).account_id == "acct-2"


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
