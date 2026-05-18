"""Tests for Anthropic OAuth credential store."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nanobot.providers.oauth_store import (
    OAuthCredentialStore,
    OAuthCredentials,
    refresh_anthropic_token,
)


class TestOAuthCredentials:
    def test_basic_creation(self):
        creds = OAuthCredentials(
            access_token="sk-ant-oat-test",
            refresh_token="sk-ant-ort-test",
            expires_at=1234567890000,
            account_id="user@example.com",
        )
        assert creds.access_token == "sk-ant-oat-test"
        assert creds.refresh_token == "sk-ant-ort-test"
        assert creds.expires_at == 1234567890000
        assert creds.account_id == "user@example.com"


@pytest.fixture(autouse=True)
def _clear_claude_env(monkeypatch):
    """Ensure CLAUDE_CODE_OAUTH_TOKEN is unset for all tests in this module."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)


class TestOAuthCredentialStore:
    def test_load_returns_none_when_empty(self, tmp_path: Path):
        store = OAuthCredentialStore(token_filename="test.json", app_name="test-app")
        # Override the path so it points into tmp_path
        store._storage._data_dir = tmp_path
        assert store.load() is None

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        store = OAuthCredentialStore(token_filename="test.json", app_name="test-app")
        store._storage._data_dir = tmp_path

        creds = OAuthCredentials(
            access_token="sk-ant-oat-abc",
            refresh_token="sk-ant-ort-abc",
            expires_at=9999999999000,
            account_id="abc",
        )
        store.save(creds)

        loaded = store.load()
        assert loaded is not None
        assert loaded.access_token == "sk-ant-oat-abc"
        assert loaded.refresh_token == "sk-ant-ort-abc"
        assert loaded.expires_at == 9999999999000
        assert loaded.account_id == "abc"

    def test_migration_from_env_var(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-env")
        store = OAuthCredentialStore(token_filename="test.json", app_name="test-app")
        store._storage._data_dir = tmp_path

        loaded = store.load()
        assert loaded is not None
        assert loaded.access_token == "sk-ant-oat-env"
        assert loaded.refresh_token == ""

    def test_migration_from_claude_json(self, tmp_path: Path, monkeypatch):
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(
            json.dumps(
                {
                    "claudeAiOauth": {
                        "accessToken": "sk-ant-oat-file",
                        "refreshToken": "sk-ant-ort-file",
                        "expiresAt": 12345,
                        "accountUuid": "acc-uuid",
                    }
                }
            )
        )
        monkeypatch.setattr(
            "nanobot.providers.oauth_store._CLAUDE_CLI_CONFIG_PATH",
            claude_json,
        )

        store = OAuthCredentialStore(token_filename="test.json", app_name="test-app")
        store._storage._data_dir = tmp_path

        loaded = store.load()
        assert loaded is not None
        assert loaded.access_token == "sk-ant-oat-file"
        assert loaded.refresh_token == "sk-ant-ort-file"
        assert loaded.expires_at == 12345
        assert loaded.account_id == "acc-uuid"

    def test_local_store_takes_precedence_over_migration(
        self, tmp_path: Path, monkeypatch
    ):
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-env")
        store = OAuthCredentialStore(token_filename="test.json", app_name="test-app")
        store._storage._data_dir = tmp_path

        # Save local credentials first
        local = OAuthCredentials(
            access_token="sk-ant-oat-local",
            refresh_token="sk-ant-ort-local",
            expires_at=1111111111000,
        )
        store.save(local)

        loaded = store.load()
        assert loaded is not None
        assert loaded.access_token == "sk-ant-oat-local"


class TestRefreshAnthropicToken:
    def test_successful_refresh(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "sk-ant-oat-new",
            "refresh_token": "sk-ant-ort-new",
            "expires_in": 3600,
            "account": {"uuid": "acc-123", "email_address": "user@example.com"},
        }

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            result = refresh_anthropic_token("sk-ant-ort-old")

            assert result.access_token == "sk-ant-oat-new"
            assert result.refresh_token == "sk-ant-ort-new"
            assert result.account_id == "acc-123"
            # expires_at should be roughly now + 3600 seconds (in ms)
            expected = int(time.time() * 1000 + 3600 * 1000)
            assert abs(result.expires_at - expected) < 5000

            call_args = mock_client.post.call_args
            assert call_args[1]["data"]["grant_type"] == "refresh_token"
            assert call_args[1]["data"]["refresh_token"] == "sk-ant-ort-old"
            assert (
                call_args[1]["data"]["client_id"]
                == "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
            )

    def test_refresh_failure_raises(self):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            with pytest.raises(RuntimeError, match="Token refresh failed: 401"):
                refresh_anthropic_token("bad-token")

    def test_refresh_missing_fields_raises(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "only-access"}

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            with pytest.raises(RuntimeError, match="missing required fields"):
                refresh_anthropic_token("old-token")
