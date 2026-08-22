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
from dataclasses import dataclass, replace
from pathlib import Path

import httpx
from filelock import FileLock
from oauth_cli_kit.models import OAuthToken
from oauth_cli_kit.storage import FileTokenStorage

logger = logging.getLogger(__name__)


class TokenRefreshError(RuntimeError):
    """刷新失败，但可能是瞬时的（网络、超时、5xx）。

    收到这个错误必须保留现有凭据文件——它也许下一次就成功了，
    删掉等于拿一次瞬时抖动烧掉唯一可用的 refresh token。
    """


class InvalidGrantError(TokenRefreshError):
    """服务端明确判定 refresh token 已死（invalid_grant / 400 / 401）。

    只有这个错误才允许回源重新迁移：refresh token 真被吊销了，
    再重试同一个也没用，唯一出路是看外部来源有没有人重登过。
    """


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
            if creds.fresh_for(0):
                # 还没真过期（含环境变量注入那种没有过期信息的凭据），照常返回。
                return creds
            logger.warning(
                "Claude Code OAuth credentials expired and no refresh token is available; "
                "re-login required (%s)",
                self.get_token_path(),
            )
            return None
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
            dead_refresh = latest.refresh_token or creds.refresh_token
            try:
                refreshed = refresh_anthropic_token(dead_refresh)
            except InvalidGrantError:
                # refresh token 被服务端判死：不删文件，改看外部来源是否已有人重登。
                recovered = self._remigrate_after_dead_refresh(dead_refresh)
                if recovered is not None:
                    return recovered
                raise
            known_account = latest.account_id or creds.account_id
            if not refreshed.account_id and known_account:
                # 刷新响应经常不带 account，别让它把已知的账号信息抹掉。
                refreshed = replace(refreshed, account_id=known_account)
            self.save(refreshed)
            return refreshed

    def _remigrate_after_dead_refresh(
        self, dead_refresh_token: str
    ) -> OAuthCredentials | None:
        """refresh token 被判死后，若外部来源已换新（有人重登过），收编之。

        指纹闸：只认 refresh_token 与刚刷废那个**不同**的来源。相同 = 没人重登，
        迁进来还是死的，直接放弃并报「需重新登录」，绝不空转刷废凭据。
        """
        for source in self._sources():
            creds = source()
            if creds and creds.refresh_token and creds.refresh_token != dead_refresh_token:
                self.save(creds)
                logger.info(
                    "Re-migrated Claude Code OAuth credentials from external source "
                    "after the stored refresh token was rejected (%s)",
                    self.get_token_path(),
                )
                return creds
        logger.warning(
            "Claude Code refresh token rejected (invalid_grant) and no fresher external "
            "credential was found; re-login required (%s)",
            self.get_token_path(),
        )
        return None


def refresh_anthropic_token(refresh_token: str) -> OAuthCredentials:
    """用 refresh token 换新 access token；失败保留真实错误文本。

    区分两类失败：网络/超时/5xx 抛 TokenRefreshError（瞬时，保留凭据重试）；
    400/401 且 invalid_grant 抛 InvalidGrantError（真死，允许回源）。
    """
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": _ANTHROPIC_CLIENT_ID,
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(_ANTHROPIC_TOKEN_URL, json=payload)
    except httpx.HTTPError as exc:
        raise TokenRefreshError(f"Token refresh request failed: {exc}") from exc
    if response.status_code == 200:
        return _parse_refresh_response(response.json(), refresh_token)
    detail = f"{response.status_code} {response.text}"
    if response.status_code in (400, 401) and "invalid_grant" in response.text.lower():
        raise InvalidGrantError(f"Token refresh rejected: {detail}")
    raise TokenRefreshError(f"Token refresh failed: {detail}")


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
