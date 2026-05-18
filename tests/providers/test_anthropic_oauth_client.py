"""Tests for AnthropicProvider OAuth mode and product_mode gating."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from nanobot.providers.anthropic_provider import AnthropicProvider


def _make_provider(**kwargs):
    with patch("anthropic.AsyncAnthropic"):
        return AnthropicProvider(**kwargs)


def _build_kwargs(provider: AnthropicProvider, **overrides):
    defaults = dict(
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        model=None,
        max_tokens=4096,
        temperature=0.7,
        reasoning_effort=None,
        tool_choice=None,
        supports_caching=False,
    )
    defaults.update(overrides)
    return provider._build_kwargs(**defaults)


class TestAuthMode:
    def test_auth_mode_api_key_when_no_auth_token(self):
        p = _make_provider(api_key="sk-test")
        assert p._auth_mode == "api_key"
        assert p._auth_token is None

    def test_auth_mode_oauth_when_auth_token_provided(self):
        p = _make_provider(auth_token="sk-ant-oat-test")
        assert p._auth_mode == "oauth"
        assert p._auth_token == "sk-ant-oat-test"

    def test_api_key_and_auth_token_mutually_exclusive(self):
        p = _make_provider(api_key="sk-test", auth_token="sk-ant-oat-test")
        # auth_token takes precedence in _build_client
        assert p._auth_mode == "oauth"

    def test_build_client_uses_api_key(self):
        with patch("anthropic.AsyncAnthropic") as mock_client:
            provider = AnthropicProvider(api_key="sk-test")
            call_kw = mock_client.call_args[1]
            assert call_kw.get("api_key") == "sk-test"
            assert "auth_token" not in call_kw

    def test_build_client_uses_auth_token(self):
        with patch("anthropic.AsyncAnthropic") as mock_client:
            provider = AnthropicProvider(auth_token="sk-ant-oat-test")
            call_kw = mock_client.call_args[1]
            assert call_kw.get("auth_token") == "sk-ant-oat-test"
            assert "api_key" not in call_kw

    def test_build_client_passes_base_url_and_headers(self):
        with patch("anthropic.AsyncAnthropic") as mock_client:
            provider = AnthropicProvider(
                api_key="sk-test",
                api_base="https://custom.example.com",
                extra_headers={"X-Custom": "val"},
            )
            call_kw = mock_client.call_args[1]
            assert call_kw.get("base_url") == "https://custom.example.com"
            assert call_kw.get("default_headers") == {"X-Custom": "val"}

    def test_product_mode_is_none_by_default(self):
        p = _make_provider(api_key="sk-test")
        assert p._product_mode is None

    def test_product_mode_stored(self):
        p = _make_provider(auth_token="sk-ant-oat-test", product_mode="claude_code")
        assert p._product_mode == "claude_code"

    def test_credential_store_stored(self):
        store = MagicMock()
        p = _make_provider(auth_token="sk-ant-oat-test", credential_store=store)
        assert p._credential_store is store


class TestProductModeClaudeCode:
    def test_system_prompt_prepended_as_list(self):
        p = _make_provider(auth_token="sk-test", product_mode="claude_code")
        kw = _build_kwargs(p, messages=[{"role": "system", "content": "custom"}])
        system = kw["system"]
        assert isinstance(system, list)
        assert system[0] == {
            "type": "text",
            "text": "You are Claude Code, Anthropic's official CLI for Claude.",
        }
        assert system[1] == {"type": "text", "text": "custom"}

    def test_system_prompt_prepended_when_existing_list(self):
        p = _make_provider(auth_token="sk-test", product_mode="claude_code")
        kw = _build_kwargs(
            p, messages=[{"role": "system", "content": [{"type": "text", "text": "custom"}]}]
        )
        system = kw["system"]
        assert isinstance(system, list)
        assert system[0]["text"] == "You are Claude Code, Anthropic's official CLI for Claude."
        assert system[1] == {"type": "text", "text": "custom"}

    def test_system_prompt_prepended_when_no_system(self):
        p = _make_provider(auth_token="sk-test", product_mode="claude_code")
        kw = _build_kwargs(p, messages=[{"role": "user", "content": "hello"}])
        system = kw["system"]
        assert isinstance(system, list)
        assert len(system) == 1
        assert system[0]["text"] == "You are Claude Code, Anthropic's official CLI for Claude."

    def test_beta_headers_added(self):
        p = _make_provider(auth_token="sk-test", product_mode="claude_code")
        kw = _build_kwargs(p)
        assert "extra_headers" in kw
        assert "anthropic-beta" in kw["extra_headers"]

    def test_beta_headers_merged_with_existing(self):
        p = _make_provider(
            auth_token="sk-test",
            product_mode="claude_code",
            extra_headers={"X-Custom": "val"},
        )
        kw = _build_kwargs(p)
        assert kw["extra_headers"]["X-Custom"] == "val"
        assert "anthropic-beta" in kw["extra_headers"]

    def test_tool_names_normalized(self):
        p = _make_provider(auth_token="sk-test", product_mode="claude_code")
        tools = [
            {"type": "function", "function": {"name": "python.exec", "parameters": {}}},
            {"type": "function", "function": {"name": "123tool", "parameters": {}}},
        ]
        kw = _build_kwargs(p, tools=tools)
        assert kw["tools"][0]["name"] == "python_exec"
        assert kw["tools"][1]["name"] == "tool_123tool"

    def test_no_claude_code_modifications_when_product_mode_none(self):
        p = _make_provider(api_key="sk-test")
        kw = _build_kwargs(p, messages=[{"role": "system", "content": "custom"}])
        assert kw["system"] == "custom"
        assert "extra_headers" not in kw

    def test_no_tool_normalization_when_product_mode_none(self):
        p = _make_provider(api_key="sk-test")
        tools = [
            {"type": "function", "function": {"name": "python.exec", "parameters": {}}},
        ]
        kw = _build_kwargs(p, tools=tools)
        assert kw["tools"][0]["name"] == "python.exec"
