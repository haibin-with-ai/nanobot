"""Claude Code product mode：registry spec、config 字段、token 注入、身份 system block。

这四件事任何一件缺失都会让 OAuth 链路整体失效，而且失败点离原因很远：
schema 少字段是启动即崩，身份 block 缺失是握手成功但请求被拒。
"""

from __future__ import annotations

from typing import Any

import pytest

from nanobot.config.schema import Config, ProvidersConfig
from nanobot.providers.anthropic_provider import (
    _CLAUDE_CODE_IDENTITY,
    AnthropicProvider,
)
from nanobot.providers.registry import find_by_name

# ---------------------------------------------------------------------------
# registry spec
# ---------------------------------------------------------------------------


def test_spec_registered() -> None:
    spec = find_by_name("anthropic_claude_code")

    assert spec is not None
    assert spec.backend == "anthropic"
    assert spec.is_oauth is True
    assert spec.env_key == ""


def test_spec_carries_identity_header() -> None:
    spec = find_by_name("anthropic_claude_code")

    headers = dict(spec.default_extra_headers)
    assert "anthropic-beta" in headers
    assert "oauth-2025-04-20" in headers["anthropic-beta"]


def test_spec_does_not_restore_expired_beta() -> None:
    spec = find_by_name("anthropic_claude_code")

    assert "token-efficient-tools-2025-02-19" not in dict(spec.default_extra_headers).get(
        "anthropic-beta", ""
    )


def test_spec_matches_claude_code_keyword() -> None:
    spec = find_by_name("anthropic_claude_code")

    assert any("claude-code" in kw or "claude_code" in kw for kw in spec.keywords)


# ---------------------------------------------------------------------------
# config schema —— registry 注册了 spec 就必须有对应字段
# ---------------------------------------------------------------------------


def test_providers_config_has_field() -> None:
    assert "anthropic_claude_code" in ProvidersConfig.model_fields


def test_camel_case_key_does_not_raise() -> None:
    """schema.convert_extra_providers 会对落进 model_extra 又能被 registry
    命中的 key 抛 ValueError。字段缺失时这行配置直接让进程起不来。"""
    config = Config.model_validate(
        {"providers": {"anthropicClaudeCode": {"api_base": "https://api.anthropic.com"}}}
    )

    assert config.providers.anthropic_claude_code.api_base == "https://api.anthropic.com"


def test_snake_case_key_also_accepted() -> None:
    config = Config.model_validate(
        {"providers": {"anthropic_claude_code": {"api_base": "https://api.anthropic.com"}}}
    )

    assert config.providers.anthropic_claude_code.api_base == "https://api.anthropic.com"


def test_field_absent_stays_empty() -> None:
    config = Config.model_validate({"providers": {}})

    assert config.providers.anthropic_claude_code.api_key is None


# ---------------------------------------------------------------------------
# token 注入：OAuth 走 auth_token，不是 api_key
# ---------------------------------------------------------------------------


class _SpyAnthropic:
    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        type(self).last_kwargs = kwargs


@pytest.fixture
def spy_client(monkeypatch) -> type[_SpyAnthropic]:
    import anthropic

    _SpyAnthropic.last_kwargs = {}
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _SpyAnthropic)
    return _SpyAnthropic


def test_claude_code_mode_uses_auth_token(spy_client) -> None:
    AnthropicProvider(api_key="oauth-token", product_mode="claude_code")

    assert spy_client.last_kwargs.get("auth_token") == "oauth-token"
    assert "api_key" not in spy_client.last_kwargs


def test_default_mode_uses_api_key(spy_client) -> None:
    AnthropicProvider(api_key="sk-ant-xxx")

    assert spy_client.last_kwargs.get("api_key") == "sk-ant-xxx"
    assert "auth_token" not in spy_client.last_kwargs


def test_base_url_normalization_survives(spy_client) -> None:
    AnthropicProvider(
        api_key="t", api_base="https://api.anthropic.com/v1", product_mode="claude_code"
    )

    assert spy_client.last_kwargs["base_url"] == "https://api.anthropic.com"


# ---------------------------------------------------------------------------
# 身份 system block —— 与 HTTP header 是两条独立链路
# ---------------------------------------------------------------------------


@pytest.fixture
def claude_code(spy_client) -> AnthropicProvider:
    return AnthropicProvider(api_key="t", product_mode="claude_code")


@pytest.fixture
def plain(spy_client) -> AnthropicProvider:
    return AnthropicProvider(api_key="t")


def test_identity_prepended_to_string_system(claude_code) -> None:
    system = claude_code._credential.decorate_system("You are Evie.")

    assert system[0]["text"] == _CLAUDE_CODE_IDENTITY
    assert system[1]["text"] == "You are Evie."


def test_identity_prepended_to_list_system(claude_code) -> None:
    system = claude_code._credential.decorate_system([{"type": "text", "text": "You are Evie."}])

    assert system[0]["text"] == _CLAUDE_CODE_IDENTITY
    assert len(system) == 2


def test_identity_injected_into_empty_system(claude_code) -> None:
    system = claude_code._credential.decorate_system("")

    assert system[0]["text"] == _CLAUDE_CODE_IDENTITY
    assert len(system) == 1


def test_identity_not_duplicated(claude_code) -> None:
    once = claude_code._credential.decorate_system("You are Evie.")
    twice = claude_code._credential.decorate_system(once)

    assert [b["text"] for b in twice].count(_CLAUDE_CODE_IDENTITY) == 1


def test_identity_absent_in_default_mode(plain) -> None:
    assert plain._credential.decorate_system("You are Evie.") == "You are Evie."


def test_identity_text_is_exact(claude_code) -> None:
    """字符串必须逐字一致，Anthropic 侧按精确匹配识别。"""
    assert _CLAUDE_CODE_IDENTITY == "You are Claude Code, Anthropic's official CLI for Claude."


def test_cache_control_marker_preserved(claude_code) -> None:
    """注入不能顶掉尾块上的 cache_control。"""
    original = [{"type": "text", "text": "big prompt", "cache_control": {"type": "ephemeral"}}]

    system = claude_code._credential.decorate_system(original)

    assert system[-1]["cache_control"] == {"type": "ephemeral"}
