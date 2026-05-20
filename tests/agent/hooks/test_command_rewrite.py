"""Tests for CommandRewriteHook."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.hooks.rewrite import CommandRewriteHook
from nanobot.providers.base import ToolCallRequest


@dataclass
class _FakeHookContext:
    tool_calls: list[ToolCallRequest] = field(default_factory=list)


def _make_tool_call(name: str, arguments: dict[str, Any]) -> ToolCallRequest:
    return ToolCallRequest(
        id="tc_1",
        name=name,
        arguments=dict(arguments),
    )


@pytest.fixture
def enabled_hook() -> CommandRewriteHook:
    return CommandRewriteHook(enabled=True, verbose=False, timeout=5.0, binary_path="rtk")


@pytest.fixture
def verbose_hook() -> CommandRewriteHook:
    return CommandRewriteHook(enabled=True, verbose=True, timeout=5.0, binary_path="rtk")


@pytest.mark.anyio
async def test_disabled_no_modification() -> None:
    hook = CommandRewriteHook(enabled=False)
    tc = _make_tool_call("exec", {"command": "ls"})
    ctx = _FakeHookContext(tool_calls=[tc])
    await hook.before_execute_tools(ctx)  # type: ignore[arg-type]
    assert tc.arguments["command"] == "ls"


@pytest.mark.anyio
async def test_non_exec_tool_unchanged(enabled_hook: CommandRewriteHook) -> None:
    tc = _make_tool_call("list_dir", {"command": "ls"})
    ctx = _FakeHookContext(tool_calls=[tc])
    await enabled_hook.before_execute_tools(ctx)  # type: ignore[arg-type]
    assert tc.arguments["command"] == "ls"


@pytest.mark.anyio
async def test_exec_empty_command_unchanged(enabled_hook: CommandRewriteHook) -> None:
    tc = _make_tool_call("exec", {"command": ""})
    ctx = _FakeHookContext(tool_calls=[tc])
    await enabled_hook.before_execute_tools(ctx)  # type: ignore[arg-type]
    assert tc.arguments["command"] == ""


@pytest.mark.anyio
async def test_exec_none_command_unchanged(enabled_hook: CommandRewriteHook) -> None:
    tc = _make_tool_call("exec", {"command": None})
    ctx = _FakeHookContext(tool_calls=[tc])
    await enabled_hook.before_execute_tools(ctx)  # type: ignore[arg-type]
    assert tc.arguments["command"] is None


@pytest.mark.anyio
async def test_exec_list_command_unchanged(enabled_hook: CommandRewriteHook) -> None:
    tc = _make_tool_call("exec", {"command": ["ls", "-la"]})
    ctx = _FakeHookContext(tool_calls=[tc])
    await enabled_hook.before_execute_tools(ctx)  # type: ignore[arg-type]
    assert tc.arguments["command"] == ["ls", "-la"]


@pytest.mark.anyio
async def test_rewrite_exit_0_replaces_command(enabled_hook: CommandRewriteHook) -> None:
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"rewritten-cmd\n", b""))
    proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc) as mock_exec:
        tc = _make_tool_call("exec", {"command": "original-cmd"})
        ctx = _FakeHookContext(tool_calls=[tc])
        await enabled_hook.before_execute_tools(ctx)  # type: ignore[arg-type]

    assert tc.arguments["command"] == "rewritten-cmd"
    # Verify rtk rewrite is called with the command as a positional arg
    mock_exec.assert_called_once()
    args = mock_exec.call_args[0]
    assert args[0] == "rtk"
    assert args[1] == "rewrite"
    assert args[2] == "original-cmd"


@pytest.mark.anyio
async def test_rewrite_exit_3_replaces_command(enabled_hook: CommandRewriteHook) -> None:
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"rewritten-cmd\r\n", b""))
    proc.returncode = 3

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc):
        tc = _make_tool_call("exec", {"command": "original-cmd"})
        ctx = _FakeHookContext(tool_calls=[tc])
        await enabled_hook.before_execute_tools(ctx)  # type: ignore[arg-type]

    assert tc.arguments["command"] == "rewritten-cmd"


@pytest.mark.anyio
async def test_rewrite_exit_0_empty_stdout_preserves_command(enabled_hook: CommandRewriteHook) -> None:
    """Exit 0 with empty stdout should not replace with empty string."""
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc):
        tc = _make_tool_call("exec", {"command": "original-cmd"})
        ctx = _FakeHookContext(tool_calls=[tc])
        await enabled_hook.before_execute_tools(ctx)  # type: ignore[arg-type]

    assert tc.arguments["command"] == "original-cmd"


@pytest.mark.anyio
async def test_rewrite_exit_1_preserves_command(enabled_hook: CommandRewriteHook) -> None:
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b"error"))
    proc.returncode = 1

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc):
        tc = _make_tool_call("exec", {"command": "original-cmd"})
        ctx = _FakeHookContext(tool_calls=[tc])
        await enabled_hook.before_execute_tools(ctx)  # type: ignore[arg-type]

    assert tc.arguments["command"] == "original-cmd"


@pytest.mark.anyio
async def test_rewrite_timeout_preserves_command(enabled_hook: CommandRewriteHook) -> None:
    proc = MagicMock()
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
    proc.kill = MagicMock()
    proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc):
        tc = _make_tool_call("exec", {"command": "original-cmd"})
        ctx = _FakeHookContext(tool_calls=[tc])
        await enabled_hook.before_execute_tools(ctx)  # type: ignore[arg-type]

    assert tc.arguments["command"] == "original-cmd"
    proc.kill.assert_called_once()


@pytest.mark.anyio
async def test_rewrite_binary_not_found_preserves_command(enabled_hook: CommandRewriteHook) -> None:
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, side_effect=FileNotFoundError):
        tc = _make_tool_call("exec", {"command": "original-cmd"})
        ctx = _FakeHookContext(tool_calls=[tc])
        await enabled_hook.before_execute_tools(ctx)  # type: ignore[arg-type]

    assert tc.arguments["command"] == "original-cmd"


@pytest.mark.anyio
async def test_verbose_logs_rewrite(verbose_hook: CommandRewriteHook, caplog: Any) -> None:
    import logging

    from loguru import logger

    # loguru writes to stderr by default; intercept at DEBUG level
    logger.remove()
    handler_id = logger.add(logging.StreamHandler(), level="DEBUG")

    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"rewritten-cmd\n", b""))
    proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc):
        tc = _make_tool_call("exec", {"command": "original-cmd"})
        ctx = _FakeHookContext(tool_calls=[tc])
        await verbose_hook.before_execute_tools(ctx)  # type: ignore[arg-type]

    logger.remove(handler_id)
    assert tc.arguments["command"] == "rewritten-cmd"
