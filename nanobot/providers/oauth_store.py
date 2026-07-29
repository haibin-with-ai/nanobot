"""Anthropic Claude Code OAuth 凭据存取与刷新。

凭据有三个历史来源（环境变量、Claude CLI 凭据文件、legacy 配置），
一旦读到就写进本地 storage，之后不再回头读来源。
刷新走文件锁 + 双重检查，形态对齐 xai_oauth.get_xai_oauth_token()。
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from filelock import FileLock
from oauth_cli_kit.models import OAuthToken
from oauth_cli_kit.storage import FileTokenStorage

logger = logging.getLogger(__name__)

_ANTHROPIC_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_ANTHROPIC_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
_CLAUDE_CLI_CONFIG_PATH = Path.home() / ".claude.json"
_CLAUDE_CLI_CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
_CLAUDE_CODE_ENV_KEY = "CLAUDE_CODE_OAUTH_TOKEN"
_APP_NAME = "nanobot"
_TOKEN_FILENAME = "claude-code.json"
_REFRESH_MARGIN_MS = 5 * 60 * 1000
_LOCK_TIMEOUT_S = 30.0


@dataclass
class OAuthCredentials:
    access_token: str
    refresh_token: str = ""
    expires_at: int = 0
    account_id: str | None = None

    def fresh_for(self, min_ttl_ms: int) -> bool:
        """没有过期信息（如环境变量注入）视为长期有效。"""
        if self.expires_at <= 0:
            return True
        return self.expires_at - int(time.time() * 1000) > min_ttl_ms


def _flat_credentials_path() -> Path:
    from nanobot.config.paths import get_config_dir

    return get_config_dir() / "oauth_credentials.json"


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _from_env() -> OAuthCredentials | None:
    token = os.environ.get(_CLAUDE_CODE_ENV_KEY, "").strip()
    return OAuthCredentials(access_token=token) if token else None


def _from_nested(path: Path) -> OAuthCredentials | None:
    """Claude CLI 的 .credentials.json 与 legacy .claude.json 同构。"""
    data = _read_json(path)
    if not isinstance(data, dict):
        return None
    oauth = data.get("claudeAiOauth") or data.get("oauth")
    if not isinstance(oauth, dict):
        return None
    access = oauth.get("accessToken") or oauth.get("access_token")
    if not access:
        return None
    return OAuthCredentials(
        access_token=access,
        refresh_token=oauth.get("refreshToken") or oauth.get("refresh_token") or "",
        expires_at=int(oauth.get("expiresAt") or oauth.get("expires_at") or 0),
        account_id=oauth.get("accountUuid") or oauth.get("account_id"),
    )


def _from_flat(path: Path) -> OAuthCredentials | None:
    data = _read_json(path)
    if not isinstance(data, dict) or not data.get("access_token"):
        return None
    return OAuthCredentials(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", ""),
        expires_at=int(data.get("expires_at_ms") or data.get("expires_at") or 0),
        account_id=data.get("account_id"),
    )


def _to_token(creds: OAuthCredentials) -> OAuthToken:
    return OAuthToken(
        access=creds.access_token,
        refresh=creds.refresh_token,
        expires=creds.expires_at,
        account_id=creds.account_id,
    )


def _from_token(token: OAuthToken) -> OAuthCredentials:
    return OAuthCredentials(
        access_token=token.access,
        refresh_token=token.refresh or "",
        expires_at=token.expires or 0,
        account_id=token.account_id,
    )


class OAuthCredentialStore:
    """本地 storage 优先；读不到时按固定顺序从历史来源迁移一次。"""

    def __init__(
        self,
        token_filename: str = _TOKEN_FILENAME,
        app_name: str = _APP_NAME,
        data_dir: Path | None = None,
    ) -> None:
        self._storage = FileTokenStorage(
            token_filename=token_filename,
            app_name=app_name,
            data_dir=data_dir,
            import_codex_cli=False,
        )

    def get_token_path(self) -> Path:
        return self._storage.get_token_path()

    def save(self, creds: OAuthCredentials) -> None:
        self._storage.save(_to_token(creds))

    def load(self) -> OAuthCredentials | None:
        token = self._storage.load()
        if token and token.access:
            return _from_token(token)
        return self._migrate()

    def _sources(self) -> list[Callable[[], OAuthCredentials | None]]:
        return [
            _from_env,
            lambda: _from_nested(_CLAUDE_CLI_CREDENTIALS_PATH),
            lambda: _from_nested(_CLAUDE_CLI_CONFIG_PATH),
            lambda: _from_flat(_flat_credentials_path()),
        ]

    def _migrate(self) -> OAuthCredentials | None:
        for source in self._sources():
            creds = source()
            if creds:
                self.save(creds)
                logger.info("Migrated Claude Code OAuth credentials into %s", self.get_token_path())
                return creds
        return None

    def needs_refresh(self, min_ttl_ms: int = _REFRESH_MARGIN_MS) -> bool:
        creds = self.load()
        return creds is not None and not creds.fresh_for(min_ttl_ms)

    def _lock(self) -> FileLock:
        return FileLock(str(self.get_token_path()) + ".lock", timeout=_LOCK_TIMEOUT_S)

    def _on_lock_acquired(self, lock_path: Path) -> None:
        """测试钩子：模拟等锁期间另一进程完成了刷新。"""

    def get_token(
        self, force_refresh: bool = False, min_ttl_ms: int = _REFRESH_MARGIN_MS
    ) -> OAuthCredentials | None:
        creds = self.load()
        if creds is None:
            return None
        if not force_refresh and creds.fresh_for(min_ttl_ms):
            return creds
        if not creds.refresh_token:
            return creds
        return self._refresh_locked(creds, force_refresh, min_ttl_ms)

    def _refresh_locked(
        self, creds: OAuthCredentials, force_refresh: bool, min_ttl_ms: int
    ) -> OAuthCredentials:
        with self._lock():
            self._on_lock_acquired(self.get_token_path())
            latest = self.load() or creds
            if latest.access_token != creds.access_token and latest.fresh_for(min_ttl_ms):
                return latest
            if not force_refresh and latest.fresh_for(min_ttl_ms):
                return latest
            refreshed = refresh_anthropic_token(latest.refresh_token or creds.refresh_token)
            self.save(refreshed)
            return refreshed


def refresh_anthropic_token(refresh_token: str) -> OAuthCredentials:
    """用 refresh token 换新 access token；失败保留真实错误文本。"""
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": _ANTHROPIC_CLIENT_ID,
    }
    with httpx.Client(timeout=30.0) as client:
        response = client.post(_ANTHROPIC_TOKEN_URL, json=payload)
    if response.status_code != 200:
        raise RuntimeError(f"Token refresh failed: {response.status_code} {response.text}")
    return _parse_refresh_response(response.json(), refresh_token)


def _parse_refresh_response(data: dict, previous_refresh_token: str = "") -> OAuthCredentials:
    access = data.get("access_token")
    expires_in = data.get("expires_in")
    if not access or not expires_in:
        raise RuntimeError("Token refresh response missing required fields")
    expires_at = int(time.time() * 1000) + int(expires_in) * 1000 - _REFRESH_MARGIN_MS
    account = data.get("account") or {}
    return OAuthCredentials(
        access_token=access,
        refresh_token=data.get("refresh_token") or previous_refresh_token,
        expires_at=expires_at,
        account_id=account.get("uuid") if isinstance(account, dict) else None,
    )
