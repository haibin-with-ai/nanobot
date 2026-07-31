"""Claude Code 凭据事实必须只有一个来源。

schema validator 与 CLI 登录流程共用 nanobot/config/claude_credentials.py，
CLI 不再各自写死 provider 键 / 默认 model / token 文件名 / OAuth 端点。
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nanobot.config.claude_credentials import (
    CLAUDE_CODE_ALIASES,
    CLAUDE_CODE_DEFAULT_MODEL,
    CLAUDE_CODE_PROVIDER_KEY,
    CLAUDE_CODE_TOKEN_FILENAME,
    claude_code_oauth_provider_kwargs,
    normalize_claude_provider_key,
)
from nanobot.config.schema import Config


def test_normalize_maps_every_alias_to_canonical_key() -> None:
    for alias in CLAUDE_CODE_ALIASES:
        assert normalize_claude_provider_key(alias) == CLAUDE_CODE_PROVIDER_KEY
    assert normalize_claude_provider_key("openai") is None
    assert normalize_claude_provider_key(None) is None


def test_token_filename_matches_oauth_store() -> None:
    from nanobot.providers.oauth_store import _TOKEN_FILENAME

    assert CLAUDE_CODE_TOKEN_FILENAME == _TOKEN_FILENAME


@pytest.mark.parametrize("alias", sorted(CLAUDE_CODE_ALIASES))
def test_schema_normalizes_provider_aliases(alias: str) -> None:
    config = Config.model_validate({"agents": {"defaults": {"provider": alias}}})
    assert config.agents.defaults.provider == CLAUDE_CODE_PROVIDER_KEY


def test_schema_accepts_what_the_cli_login_writes() -> None:
    """CLI 写出的那组 agents.defaults 必须原样通过 schema，别自己拒自己。"""
    from nanobot.cli import commands

    defaults = commands._oauth_agent_defaults(CLAUDE_CODE_PROVIDER_KEY, None)
    config = Config.model_validate({"agents": {"defaults": dict(defaults)}})

    assert config.agents.defaults.provider == CLAUDE_CODE_PROVIDER_KEY
    assert config.agents.defaults.model == CLAUDE_CODE_DEFAULT_MODEL
    assert config.agents.defaults.model_preset is None


def test_login_writes_normalized_defaults(tmp_path: Path) -> None:
    from nanobot.cli import commands
    from nanobot.config.loader import get_config_path, set_config_path

    original = get_config_path()
    config_path = tmp_path / "config.json"
    try:
        commands._set_oauth_provider_as_main(
            CLAUDE_CODE_PROVIDER_KEY, model=None, config_path=str(config_path)
        )
    finally:
        set_config_path(original)

    written = json.loads(config_path.read_text(encoding="utf-8"))["agents"]["defaults"]
    assert written["provider"] == CLAUDE_CODE_PROVIDER_KEY
    assert written["model"] == CLAUDE_CODE_DEFAULT_MODEL
    assert written.get("modelPreset", written.get("model_preset")) is None


def test_login_flow_builds_oauth_config_from_the_single_source(monkeypatch) -> None:
    """真跑一次登录路径，用 spy 接住它交给 oauth_cli_kit 的 provider 配置。"""
    import oauth_cli_kit

    from nanobot.cli import commands

    captured: dict[str, object] = {}

    def _spy(*, print_fn, prompt_fn, provider):
        captured["provider"] = provider
        return SimpleNamespace(access="tok", account_id="acct")

    monkeypatch.setattr(oauth_cli_kit, "login_oauth_interactive", _spy)
    monkeypatch.setattr(commands.console, "print", lambda *a, **k: None)

    commands._login_anthropic_claude_code()

    provider = captured["provider"]
    expected = claude_code_oauth_provider_kwargs(provider.client_id)
    assert provider.token_filename == expected["token_filename"] == CLAUDE_CODE_TOKEN_FILENAME
    assert provider.authorize_url == expected["authorize_url"]
    assert provider.token_url == expected["token_url"]
    assert provider.scope == expected["scope"]
