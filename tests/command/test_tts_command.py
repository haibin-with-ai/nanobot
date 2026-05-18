"""Tests for the /tts command."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nanobot.bus.events import InboundMessage
from nanobot.command.builtin import cmd_tts, register_builtin_commands
from nanobot.command.router import CommandContext, CommandRouter


def _ctx(raw: str, args: str = "", session: MagicMock | None = None) -> CommandContext:
    msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content=raw)
    return CommandContext(msg=msg, session=session, key=msg.session_key, raw=raw, args=args, loop=MagicMock())


@pytest.mark.asyncio
async def test_tts_command_shows_usage_without_args() -> None:
    session = MagicMock()
    session.metadata = {}
    out = await cmd_tts(_ctx("/tts", session=session))
    assert out is not None
    assert "TTS is currently off." in out.content


@pytest.mark.asyncio
async def test_tts_command_enables() -> None:
    session = MagicMock()
    session.metadata = {}
    out = await cmd_tts(_ctx("/tts on", args="on", session=session))
    assert out is not None
    assert "TTS enabled" in out.content
    assert session.metadata.get("_outbound_tts") is True


@pytest.mark.asyncio
async def test_tts_command_disables() -> None:
    session = MagicMock()
    session.metadata = {"_outbound_tts": True}
    out = await cmd_tts(_ctx("/tts off", args="off", session=session))
    assert out is not None
    assert "TTS disabled" in out.content
    assert "_outbound_tts" not in session.metadata


@pytest.mark.asyncio
async def test_tts_command_no_session() -> None:
    out = await cmd_tts(_ctx("/tts on", args="on"))
    assert out is not None
    assert "No active session" in out.content


@pytest.mark.asyncio
async def test_tts_command_registered_on_router() -> None:
    router = CommandRouter()
    register_builtin_commands(router)

    session = MagicMock()
    session.metadata = {}
    out = await router.dispatch(_ctx("/tts on", args="on", session=session))
    assert out is not None
    assert "TTS enabled" in out.content


@pytest.mark.asyncio
async def test_tts_command_exact_match() -> None:
    router = CommandRouter()
    register_builtin_commands(router)

    session = MagicMock()
    session.metadata = {}
    out = await router.dispatch(_ctx("/tts", session=session))
    assert out is not None
    assert "TTS is currently off." in out.content


@pytest.mark.asyncio
async def test_tts_command_prefix_match() -> None:
    router = CommandRouter()
    register_builtin_commands(router)

    session = MagicMock()
    session.metadata = {}
    out = await router.dispatch(_ctx("/tts on", session=session))
    assert out is not None
    assert "TTS enabled" in out.content
