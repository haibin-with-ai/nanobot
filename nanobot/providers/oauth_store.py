"""OAuth credential store for Anthropic Claude Code."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from oauth_cli_kit.models import OAuthToken
from oauth_cli_kit.storage import FileTokenStorage

_ANTHROPIC_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_ANTHROPIC_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
_CLAUDE_CLI_CONFIG_PATH = Path.home() / ".claude.json"
_CLAUDE_CLI_CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
_CLAUDE_CODE_ENV_KEY = "CLAUDE_CODE_OAUTH_TOKEN"


@dataclass
class OAuthCredentials:
    """OAuth token credentials for Anthropic Claude Code."""

    access_token: str
    refresh_token: str
    expires_at: int  # milliseconds since epoch
    account_id: str | None = None


class OAuthCredentialStore:
    """File-based credential store with migration from Claude CLI."""

    def __init__(
        self,
        token_filename: str = "claude-code.json",
        app_name: str = "nanobot",
    ):
        self._storage = FileTokenStorage(
            token_filename=token_filename,
            app_name=app_name,
            import_codex_cli=False,
        )

    def load(self) -> OAuthCredentials | None:
        """Load credentials from storage, falling back to Claude CLI migration."""
        token = self._storage.load()
        if token:
            return _token_to_credentials(token)

        migrated = self._try_migrate_from_claude_cli()
        if migrated:
            self.save(migrated)
            return migrated

        return None

    def save(self, credentials: OAuthCredentials) -> None:
        """Persist credentials to storage."""
        self._storage.save(_credentials_to_token(credentials))

    def get_token_path(self) -> Path:
        return self._storage.get_token_path()

    # ------------------------------------------------------------------
    # Migration from Claude CLI
    # ------------------------------------------------------------------

    @staticmethod
    def _try_migrate_from_claude_cli() -> OAuthCredentials | None:
        """Best-effort import from Claude CLI storage.

        Checks (in order):
        1. CLAUDE_CODE_OAUTH_TOKEN environment variable
        2. ~/.claude/.credentials.json (Claude CLI v2.1+)
        3. ~/.claude.json config file (legacy)
        """
        env_token = os.environ.get(_CLAUDE_CODE_ENV_KEY)
        if env_token:
            return OAuthCredentials(
                access_token=env_token,
                refresh_token="",
                expires_at=0,
                account_id=None,
            )

        # Claude CLI v2.1+ stores credentials in ~/.claude/.credentials.json
        for config_path in (_CLAUDE_CLI_CREDENTIALS_PATH, _CLAUDE_CLI_CONFIG_PATH):
            if not config_path.exists():
                continue
            try:
                data = json.loads(config_path.read_text())
                oauth_data = data.get("claudeAiOauth") or data.get("oauth")
                if isinstance(oauth_data, dict):
                    access = oauth_data.get("accessToken") or oauth_data.get("access_token")
                    refresh = oauth_data.get("refreshToken") or oauth_data.get("refresh_token")
                    expires = oauth_data.get("expiresAt") or oauth_data.get("expires_at")
                    if access:
                        return OAuthCredentials(
                            access_token=access,
                            refresh_token=refresh or "",
                            expires_at=expires or 0,
                            account_id=oauth_data.get("accountUuid")
                            or oauth_data.get("account_id"),
                        )
            except Exception:
                pass

        return None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _token_to_credentials(token: OAuthToken) -> OAuthCredentials:
    return OAuthCredentials(
        access_token=token.access,
        refresh_token=token.refresh,
        expires_at=token.expires,
        account_id=token.account_id,
    )


def _credentials_to_token(credentials: OAuthCredentials) -> OAuthToken:
    return OAuthToken(
        access=credentials.access_token,
        refresh=credentials.refresh_token,
        expires=credentials.expires_at,
        account_id=credentials.account_id,
    )


def refresh_anthropic_token(refresh_token: str) -> OAuthCredentials:
    """Refresh an Anthropic OAuth access token.

    Raises RuntimeError on non-200 responses.
    """
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": _ANTHROPIC_CLIENT_ID,
    }
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            _ANTHROPIC_TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if response.status_code != 200:
        raise RuntimeError(
            f"Token refresh failed: {response.status_code} {response.text}"
        )

    payload: dict[str, Any] = response.json()
    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    expires_in = payload.get("expires_in")
    if not access or not refresh or expires_in is None:
        raise RuntimeError("Token refresh response missing required fields")

    # Expire 5 minutes early to avoid using a nearly-expired token (aligned with pi-mono)
    expires_at = int(time.time() * 1000 + int(expires_in) * 1000 - 5 * 60 * 1000)
    account = payload.get("account", {})
    account_id = account.get("uuid") or account.get("email_address")

    return OAuthCredentials(
        access_token=access,
        refresh_token=refresh,
        expires_at=expires_at,
        account_id=account_id,
    )
