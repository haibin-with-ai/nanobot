"""Claude Code 的 login/logout 必须挂进上游那套 OAuth 注册表。

`provider login` 只认 _LOGIN_HANDLERS 里的 key，registry 注册了 spec 但没注册
handler，命令会直接报 unknown provider——两张表必须同时有条目。
"""

from __future__ import annotations

from nanobot.cli import commands


def test_login_handler_registered() -> None:
    assert "anthropic_claude_code" in commands._LOGIN_HANDLERS


def test_logout_handler_registered() -> None:
    assert "anthropic_claude_code" in commands._LOGOUT_HANDLERS


def test_display_name_present() -> None:
    assert commands._PROVIDER_DISPLAY["anthropic_claude_code"] == "Anthropic Claude Code"


def test_default_model_present() -> None:
    """_set_oauth_provider_as_main 直接下标取值，缺条目就是 KeyError。"""
    assert "anthropic_claude_code" in commands._OAUTH_PROVIDER_DEFAULT_MODELS


def test_default_model_is_prefixed() -> None:
    model = commands._OAUTH_PROVIDER_DEFAULT_MODELS["anthropic_claude_code"]

    assert model.startswith("anthropic-claude-code/")


def test_resolve_oauth_provider_accepts_dashed_name() -> None:
    spec = commands._resolve_oauth_provider("anthropic-claude-code")

    assert spec.name == "anthropic_claude_code"


def test_logout_targets_claude_code_token_file(monkeypatch) -> None:
    deleted: list[tuple] = []
    monkeypatch.setattr(
        commands, "_delete_oauth_files", lambda path, label: deleted.append((path, label))
    )

    commands._LOGOUT_HANDLERS["anthropic_claude_code"]()

    path, label = deleted[0]
    assert path.name == "claude-code.json"
    assert label == "Anthropic Claude Code"
