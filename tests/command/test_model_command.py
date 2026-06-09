from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.command.builtin import (
    build_help_text,
    builtin_command_palette,
    cmd_goal,
    cmd_model,
    register_builtin_commands,
)
from nanobot.command.router import CommandContext, CommandRouter
from nanobot.config.schema import ModelPresetConfig, _resolve_tool_config_refs

# Resolve ToolsConfig forward refs so AgentLoop() works in isolated test runs.
_resolve_tool_config_refs()


def _provider(default_model: str, max_tokens: int = 123) -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = default_model
    provider.generation = SimpleNamespace(
        max_tokens=max_tokens,
        temperature=0.1,
        reasoning_effort=None,
    )
    return provider


def _make_loop(tmp_path) -> AgentLoop:
    return AgentLoop(
        bus=MessageBus(),
        provider=_provider("base-model", max_tokens=123),
        workspace=tmp_path,
        model="base-model",
        context_window_tokens=1000,
        model_presets={
            "default": ModelPresetConfig(
                model="base-model",
                max_tokens=123,
                context_window_tokens=1000,
            ),
            "fast": ModelPresetConfig(
                model="openai/gpt-4.1",
                max_tokens=4096,
                context_window_tokens=32_768,
            ),
        },
    )


def _ctx(loop: AgentLoop, raw: str, args: str = "") -> CommandContext:
    msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content=raw)
    return CommandContext(msg=msg, session=None, key=msg.session_key, raw=raw, args=args, loop=loop)


def _ctx_session(loop: AgentLoop, raw: str, args: str = "") -> CommandContext:
    msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content=raw)
    return CommandContext(
        msg=msg, session=MagicMock(), key=msg.session_key, raw=raw, args=args, loop=loop,
    )


def _ctx_real_session(
    loop: AgentLoop, raw: str, args: str = "", metadata: dict | None = None,
) -> CommandContext:
    """A context with a session carrying a real metadata dict.

    `/model` switches are now per-session: they write session.metadata and never
    mutate the global runtime model. Stub out sessions.save so the command can
    persist without a real store.
    """
    loop.sessions.save = lambda session: None  # type: ignore[assignment]
    msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content=raw)
    session = SimpleNamespace(key=msg.session_key, metadata=dict(metadata or {}))
    return CommandContext(
        msg=msg, session=session, key=msg.session_key, raw=raw, args=args, loop=loop,
    )


@pytest.mark.asyncio
async def test_model_command_lists_current_and_available_presets(tmp_path) -> None:
    loop = _make_loop(tmp_path)

    out = await cmd_model(_ctx(loop, "/model"))

    assert "Current model: `base-model`" in out.content
    assert "Current preset: `default`" in out.content
    assert "Available presets: `default`, `fast`" in out.content
    assert "`fast`" in out.content
    assert out.metadata == {"render_as": "text"}


@pytest.mark.asyncio
async def test_model_command_switches_preset_for_session_only(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    ctx = _ctx_real_session(loop, "/model fast", args="fast")

    out = await cmd_model(ctx)

    assert "Switched model preset to `fast` for this session." in out.content
    assert "Model: `openai/gpt-4.1`" in out.content
    # Per-session override is recorded on the session.
    assert ctx.session.metadata["model_preset"] == "fast"
    # The global runtime model is NOT mutated.
    assert loop.model_preset is None
    assert loop.model == "base-model"
    assert loop.context_window_tokens == 1000


@pytest.mark.asyncio
async def test_model_command_default_clears_session_override(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    ctx = _ctx_real_session(
        loop, "/model default", args="default", metadata={"model_preset": "fast"},
    )

    out = await cmd_model(ctx)

    assert "Cleared session model override" in out.content
    assert "model_preset" not in ctx.session.metadata
    # Falls back to the global default for display.
    assert "Current model: `base-model`" in out.content


@pytest.mark.asyncio
async def test_model_command_requires_session_to_switch(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    out = await cmd_model(_ctx(loop, "/model fast", args="fast"))
    assert "No active session." in out.content


@pytest.mark.asyncio
async def test_model_command_unknown_preset_keeps_old_state(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    ctx = _ctx_real_session(loop, "/model missing", args="missing")

    out = await cmd_model(ctx)

    assert "Could not switch model preset" in out.content
    assert "Available presets: `default`, `fast`" in out.content
    # Neither the session nor the global model changed.
    assert "model_preset" not in ctx.session.metadata
    assert loop.model_preset is None
    assert loop.model == "base-model"


@pytest.mark.asyncio
async def test_model_command_does_not_depend_on_my_allow_set(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    assert loop.tools_config.my.allow_set is False

    ctx = _ctx_real_session(loop, "/model fast", args="fast")
    await cmd_model(ctx)

    assert ctx.session.metadata["model_preset"] == "fast"


@pytest.mark.asyncio
async def test_model_command_registered_as_exact_and_prefix(tmp_path) -> None:
    router = CommandRouter()
    register_builtin_commands(router)
    loop = _make_loop(tmp_path)
    ctx = _ctx_real_session(loop, "/model fast", args="fast")

    out = await router.dispatch(ctx)

    assert out is not None
    assert "Switched model preset" in out.content
    assert ctx.session.metadata["model_preset"] == "fast"


def test_model_command_in_help_and_palette() -> None:
    palette = builtin_command_palette()

    assert any(item["command"] == "/model" and item["arg_hint"] == "[preset]" for item in palette)
    assert "/model [preset]" in build_help_text()


@pytest.mark.asyncio
async def test_goal_command_shows_usage_without_args(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    out = await cmd_goal(_ctx(loop, "/goal"))
    assert out is not None
    assert "Usage: /goal" in out.content


@pytest.mark.asyncio
async def test_goal_command_rejects_mid_turn_without_session(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    out = await cmd_goal(_ctx(loop, "/goal do work", args="do work"))
    assert out is not None
    assert "/stop" in out.content


@pytest.mark.asyncio
async def test_goal_command_rewrites_to_agent_prompt(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    ctx = _ctx_session(loop, "/goal audit the repo", args="audit the repo")
    out = await cmd_goal(ctx)
    assert out is None
    assert "audit the repo" in ctx.msg.content
    assert "long_task" in ctx.msg.content
    assert ctx.msg.metadata.get("original_command") == "/goal"
    assert ctx.msg.metadata.get("original_content") == "/goal audit the repo"
    assert isinstance(ctx.msg.metadata.get("goal_started_at"), int | float)


@pytest.mark.asyncio
async def test_goal_command_registered_on_router(tmp_path) -> None:
    router = CommandRouter()
    register_builtin_commands(router)
    loop = _make_loop(tmp_path)
    ctx = _ctx_session(loop, "/goal ship it", args="ship it")
    out = await router.dispatch(ctx)
    assert out is None
    assert "ship it" in ctx.msg.content


def test_goal_command_in_help_and_palette() -> None:
    palette = builtin_command_palette()
    assert any(item["command"] == "/goal" and item["arg_hint"] == "<goal>" for item in palette)
    assert "/goal <goal>" in build_help_text()
