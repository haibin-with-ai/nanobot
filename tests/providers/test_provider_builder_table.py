"""构造行为回到 registry：backend -> builder 一张表，factory 只查表调用。

这里锁两件事：
1. registry 里每个 spec 的 backend 都能查到构造器（加 provider 不会静默漏分支）；
2. 代表性 backend 经 _make_provider_core 造出来的对象类型和参数不变。
"""

from __future__ import annotations

from typing import Any

import pytest

from nanobot.config.schema import Config
from nanobot.providers import registry
from nanobot.providers.factory import _make_provider_core
from nanobot.providers.registry import PROVIDERS, builder_for_backend


def _config(providers: dict[str, Any], provider: str, model: str) -> Config:
    return Config.model_validate(
        {
            "providers": providers,
            "modelPresets": {"main": {"provider": provider, "model": model}},
        }
    )


# ---------------------------------------------------------------------------
# 表本身
# ---------------------------------------------------------------------------


def test_every_registered_backend_has_a_builder() -> None:
    missing = sorted({spec.backend for spec in PROVIDERS} - set(registry.BACKEND_BUILDERS))
    assert missing == []


def test_builder_lookup_is_callable_for_every_backend() -> None:
    for spec in PROVIDERS:
        assert callable(builder_for_backend(spec.backend)), spec.name


def test_unknown_backend_falls_back_to_openai_compat_builder() -> None:
    assert builder_for_backend("no-such-backend") is registry.BACKEND_BUILDERS["openai_compat"]


def test_table_covers_the_six_special_backends_plus_default() -> None:
    assert set(registry.BACKEND_BUILDERS) >= {
        "openai_codex",
        "xai_grok",
        "azure_openai",
        "github_copilot",
        "anthropic",
        "bedrock",
        "openai_compat",
    }


# ---------------------------------------------------------------------------
# 每个 backend 造出来的类型与参数
# ---------------------------------------------------------------------------


def test_openai_compat_backend_builds_openai_compat_provider() -> None:
    from nanobot.providers.openai_compat_provider import OpenAICompatProvider

    cfg = _config({"deepseek": {"api_key": "sk-ds"}}, "deepseek", "deepseek-chat")

    provider = _make_provider_core(cfg, preset_name="main")

    assert type(provider) is OpenAICompatProvider
    assert provider.api_key == "sk-ds"
    assert provider.default_model == "deepseek-chat"


def test_openai_codex_backend_builds_codex_provider() -> None:
    from nanobot.providers.openai_codex_provider import OpenAICodexProvider

    cfg = _config(
        {"openaiCodex": {"proxy": "http://127.0.0.1:8080", "extra_body": {"a": 1}}},
        "openai_codex",
        "openai-codex/gpt-5.6-sol",
    )

    provider = _make_provider_core(cfg, preset_name="main")

    assert type(provider) is OpenAICodexProvider
    assert provider.default_model == "openai-codex/gpt-5.6-sol"
    assert provider.proxy == "http://127.0.0.1:8080"
    assert provider._extra_body == {"a": 1}


def test_xai_grok_backend_builds_grok_provider() -> None:
    from nanobot.providers.xai_grok_provider import XAIGrokProvider

    cfg = _config({"xaiGrok": {"proxy": "http://127.0.0.1:9"}}, "xai_grok", "xai-grok/grok-4.5")

    provider = _make_provider_core(cfg, preset_name="main")

    assert type(provider) is XAIGrokProvider
    assert provider.default_model == "xai-grok/grok-4.5"
    assert provider.proxy == "http://127.0.0.1:9"


def test_azure_openai_backend_builds_azure_provider() -> None:
    from nanobot.providers.azure_openai_provider import AzureOpenAIProvider

    cfg = _config(
        {"azureOpenai": {"api_key": "az-key", "api_base": "https://x.openai.azure.com"}},
        "azure_openai",
        "gpt-4o",
    )

    provider = _make_provider_core(cfg, preset_name="main")

    assert type(provider) is AzureOpenAIProvider
    assert provider.api_key == "az-key"
    assert provider.api_base.rstrip("/") == "https://x.openai.azure.com"
    assert provider.default_model == "gpt-4o"


def test_github_copilot_backend_builds_copilot_provider() -> None:
    from nanobot.providers.github_copilot_provider import GitHubCopilotProvider

    cfg = _config({"githubCopilot": {}}, "github_copilot", "github-copilot/gpt-4.1")

    provider = _make_provider_core(cfg, preset_name="main")

    assert type(provider) is GitHubCopilotProvider
    assert provider.default_model == "github-copilot/gpt-4.1"


def test_anthropic_backend_builds_anthropic_provider() -> None:
    from nanobot.providers.anthropic_provider import AnthropicProvider

    cfg = _config({"anthropic": {"api_key": "sk-ant"}}, "anthropic", "claude-opus-4-6")

    provider = _make_provider_core(cfg, preset_name="main")

    assert type(provider) is AnthropicProvider
    assert provider.api_key == "sk-ant"
    assert provider.default_model == "claude-opus-4-6"
    assert provider.product_mode == ""


def test_anthropic_oauth_backend_keeps_claude_code_product_mode(monkeypatch) -> None:
    from nanobot.providers.anthropic_provider import AnthropicProvider
    from nanobot.providers.oauth_store import OAuthCredentials

    monkeypatch.setattr(
        "nanobot.providers.oauth_store.OAuthCredentialStore.get_token",
        lambda self, force_refresh=False, min_ttl_ms=0: OAuthCredentials("tok", "r", 0, "acct"),
    )
    cfg = _config({"anthropicClaudeCode": {}}, "anthropic_claude_code", "claude-opus-4-6")

    provider = _make_provider_core(cfg, preset_name="main")

    assert type(provider) is AnthropicProvider
    assert provider.api_key == "tok"
    assert provider.product_mode == "claude_code"


def test_bedrock_backend_builds_bedrock_provider(monkeypatch) -> None:
    from nanobot.providers.bedrock_provider import BedrockProvider

    monkeypatch.setattr(BedrockProvider, "_make_client", lambda self: object())
    cfg = _config(
        {"bedrock": {"region": "us-east-1"}},
        "bedrock",
        "bedrock/global.anthropic.claude-opus-4-7",
    )

    provider = _make_provider_core(cfg, preset_name="main")

    assert type(provider) is BedrockProvider
    assert provider.region == "us-east-1"
    assert provider.default_model == "bedrock/global.anthropic.claude-opus-4-7"


def test_generation_settings_still_attached_after_table_dispatch() -> None:
    cfg = _config({"deepseek": {"api_key": "sk-ds"}}, "deepseek", "deepseek-chat")

    provider = _make_provider_core(cfg, preset_name="main")

    assert provider.generation is not None


def test_validation_errors_survive_the_table() -> None:
    cfg = _config({"azureOpenai": {"api_key": "az"}}, "azure_openai", "gpt-4o")

    with pytest.raises(ValueError, match="Azure OpenAI requires api_base"):
        _make_provider_core(cfg, preset_name="main")
