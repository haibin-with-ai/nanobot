"""Tests for AnthropicProvider OAuth token refresh."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from nanobot.providers.anthropic_provider import AnthropicProvider


def _make_provider(**kwargs):
    with patch("anthropic.AsyncAnthropic"):
        return AnthropicProvider(**kwargs)


class TestEnsureValidToken:
    @pytest.mark.asyncio
    async def test_no_op_when_not_oauth(self):
        p = _make_provider(api_key="sk-test")
        # Should not raise
        await p._ensure_valid_token()
        assert p._auth_mode == "api_key"

    @pytest.mark.asyncio
    async def test_no_op_when_no_credential_store(self):
        p = _make_provider(auth_token="sk-ant-oat-test")
        assert p._credential_store is None
        await p._ensure_valid_token()

    @pytest.mark.asyncio
    async def test_no_op_when_token_not_near_expiry(self):
        store = MagicMock()
        store.load.return_value = MagicMock(
            refresh_token="sk-ant-ort-test",
            expires_at=int(time.time() * 1000) + 3_600_000,  # 1 hour from now
        )
        p = _make_provider(auth_token="sk-ant-oat-test", credential_store=store)
        with patch.object(p, "_update_oauth_token") as mock_update:
            await p._ensure_valid_token()
            mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_refreshes_when_token_near_expiry(self):
        store = MagicMock()
        store.load.return_value = MagicMock(
            refresh_token="sk-ant-ort-test",
            expires_at=int(time.time() * 1000) + 60_000,  # 1 minute from now
        )
        p = _make_provider(auth_token="sk-ant-oat-test", credential_store=store)
        with patch.object(p, "_update_oauth_token") as mock_update:
            await p._ensure_valid_token()
            mock_update.assert_called_once_with("sk-ant-ort-test")

    @pytest.mark.asyncio
    async def test_no_op_when_no_refresh_token(self):
        store = MagicMock()
        store.load.return_value = MagicMock(
            refresh_token="",
            expires_at=int(time.time() * 1000) + 60_000,
        )
        p = _make_provider(auth_token="sk-ant-oat-test", credential_store=store)
        with patch.object(p, "_update_oauth_token") as mock_update:
            await p._ensure_valid_token()
            mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_op_when_store_load_returns_none(self):
        store = MagicMock()
        store.load.return_value = None
        p = _make_provider(auth_token="sk-ant-oat-test", credential_store=store)
        with patch.object(p, "_update_oauth_token") as mock_update:
            await p._ensure_valid_token()
            mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_refresh_failure_does_not_block(self):
        store = MagicMock()
        store.load.return_value = MagicMock(
            refresh_token="sk-ant-ort-test",
            expires_at=int(time.time() * 1000) + 60_000,
        )
        p = _make_provider(auth_token="sk-ant-oat-test", credential_store=store)
        with patch.object(
            p, "_update_oauth_token", side_effect=RuntimeError("network")
        ) as mock_update:
            # Should not raise even though _update_oauth_token fails
            await p._ensure_valid_token()
            mock_update.assert_called_once()


class TestUpdateOAuthToken:
    def test_successful_refresh_updates_state(self):
        store = MagicMock()
        p = _make_provider(auth_token="sk-ant-oat-old", credential_store=store)
        with patch(
            "nanobot.providers.oauth_store.refresh_anthropic_token"
        ) as mock_refresh:
            mock_refresh.return_value = MagicMock(
                access_token="sk-ant-oat-new",
                refresh_token="sk-ant-ort-new",
                expires_at=9999999999000,
            )
            p._update_oauth_token("sk-ant-ort-old")
            assert p._auth_token == "sk-ant-oat-new"
            store.save.assert_called_once()

    def test_failed_refresh_raises_to_caller(self):
        """_update_oauth_token no longer swallows errors; _ensure_valid_token handles them."""
        store = MagicMock()
        p = _make_provider(auth_token="sk-ant-oat-old", credential_store=store)
        with patch(
            "nanobot.providers.oauth_store.refresh_anthropic_token"
        ) as mock_refresh:
            mock_refresh.side_effect = RuntimeError("network")
            with pytest.raises(RuntimeError, match="network"):
                p._update_oauth_token("sk-ant-ort-old")

    def test_chat_calls_ensure_valid_token(self):
        store = MagicMock()
        store.load.return_value = MagicMock(
            refresh_token="",
            expires_at=int(time.time() * 1000) + 3_600_000,
        )
        p = _make_provider(auth_token="sk-ant-oat-test", credential_store=store)
        with patch.object(p, "_ensure_valid_token") as mock_ensure:
            with patch.object(p, "_build_kwargs", return_value={}):
                with patch.object(p, "_client") as mock_client:
                    mock_client.messages.create = MagicMock(return_value=MagicMock())
                    # Run the async chat method
                    import asyncio
                    asyncio.run(p.chat([{"role": "user", "content": "hi"}]))
                    mock_ensure.assert_called_once()

    def test_chat_stream_calls_ensure_valid_token(self):
        store = MagicMock()
        store.load.return_value = MagicMock(
            refresh_token="",
            expires_at=int(time.time() * 1000) + 3_600_000,
        )
        p = _make_provider(auth_token="sk-ant-oat-test", credential_store=store)
        with patch.object(p, "_ensure_valid_token") as mock_ensure:
            with patch.object(p, "_build_kwargs", return_value={}):
                with patch.object(p, "_client") as mock_client:
                    mock_stream = MagicMock()
                    mock_stream.__aenter__ = MagicMock(return_value=mock_stream)
                    mock_stream.__aexit__ = MagicMock(return_value=None)
                    mock_client.messages.stream = MagicMock(return_value=mock_stream)
                    import asyncio
                    asyncio.run(
                        p.chat_stream([{"role": "user", "content": "hi"}])
                    )
                    mock_ensure.assert_called_once()
