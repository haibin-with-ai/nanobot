"""凭据策略：OAuth 与 API key 的差异只在一个对象里分叉。

过去 product_mode == "claude_code" 这句话在三处复述：客户端 kwargs 的字段名、
system 首块的身份注入、凭据刷新。加第四处差异就会有第四个 if。
"""

from unittest.mock import patch

import pytest

from nanobot.providers.anthropic_provider import _CLAUDE_CODE_IDENTITY, AnthropicProvider


@pytest.fixture
def oauth() -> AnthropicProvider:
    return AnthropicProvider(api_key="tok", product_mode="claude_code")


@pytest.fixture
def api_key() -> AnthropicProvider:
    return AnthropicProvider(api_key="sk-test")


class TestCredentialStrategy:
    def test_oauth_uses_bearer_field_and_injects_identity(self, oauth: AnthropicProvider) -> None:
        assert oauth._client_kwargs()["auth_token"] == "tok"
        assert "api_key" not in oauth._client_kwargs()

        system = oauth._credential.decorate_system("You are Evie.")

        assert system[0]["text"] == _CLAUDE_CODE_IDENTITY

    def test_api_key_uses_api_key_field_and_leaves_system_alone(
        self, api_key: AnthropicProvider
    ) -> None:
        assert api_key._client_kwargs()["api_key"] == "sk-test"
        assert "auth_token" not in api_key._client_kwargs()
        assert api_key._credential.decorate_system("You are Evie.") == "You are Evie."

    @pytest.mark.asyncio
    async def test_api_key_credential_never_refreshes(self, api_key: AnthropicProvider) -> None:
        """API key 模式不该去碰 OAuth store，更不该重建 client。"""
        with patch.object(AnthropicProvider, "_rebuild_client") as rebuild:
            assert await api_key._refresh_credentials() is False
        rebuild.assert_not_called()
        assert api_key.api_key == "sk-test"

    @pytest.mark.asyncio
    async def test_oauth_credential_refresh_swaps_key_and_client(
        self, oauth: AnthropicProvider
    ) -> None:
        class _Creds:
            access_token = "fresh-token"

        class _Store:
            def get_token(self, force_refresh: bool = False):
                assert force_refresh is True
                return _Creds()

        with (
            patch("nanobot.providers.oauth_store.OAuthCredentialStore", _Store),
            patch.object(AnthropicProvider, "_rebuild_client") as rebuild,
        ):
            assert await oauth._refresh_credentials() is True

        assert oauth.api_key == "fresh-token"
        rebuild.assert_called_once_with("fresh-token")

    def test_build_kwargs_routes_system_through_the_credential(
        self, oauth: AnthropicProvider, api_key: AnthropicProvider
    ) -> None:
        args = dict(
            messages=[
                {"role": "system", "content": "You are Evie."},
                {"role": "user", "content": "hi"},
            ],
            tools=None,
            model="claude-sonnet-4-6",
            max_tokens=256,
            temperature=0.3,
            reasoning_effort=None,
            tool_choice=None,
            supports_caching=False,
        )
        oauth_kwargs = oauth._build_kwargs(**args)
        plain_kwargs = api_key._build_kwargs(**args)

        assert oauth_kwargs["system"][0]["text"] == _CLAUDE_CODE_IDENTITY
        assert plain_kwargs["system"] == "You are Evie."
