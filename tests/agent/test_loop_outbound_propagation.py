"""Tests for generic _outbound_* metadata propagation in AgentLoop."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.message import MessageTool
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.session.manager import Session


def _make_loop(tmp_path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(max_tokens=4096, temperature=0.1, reasoning_effort=None)
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        context_window_tokens=128_000,
    )


def _mk_turn_context(
    loop: AgentLoop,
    session: Session | None,
    final_content: str = "hello",
) -> MagicMock:
    ctx = MagicMock()
    ctx.msg = InboundMessage(channel="test", sender_id="user", chat_id="chat1", content="hi")
    ctx.session_key = "test:chat1"
    ctx.session = session
    ctx.final_content = final_content
    ctx.all_messages = []
    ctx.stop_reason = "ok"
    ctx.had_injections = False
    ctx.generated_media = []
    ctx.on_stream = None
    ctx.turn_latency_ms = 100
    return ctx


@pytest.mark.asyncio
async def test_state_respond_propagates_outbound_metadata(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    session = Session(key="test:chat1")
    session.metadata["_outbound_tts"] = True
    session.metadata["_outbound_other"] = "value"
    session.metadata["other_key"] = "ignored"

    ctx = _mk_turn_context(loop, session)
    await loop._state_respond(ctx)

    assert ctx.outbound is not None
    assert ctx.outbound.metadata.get("_outbound_tts") is True
    assert ctx.outbound.metadata.get("_outbound_other") == "value"
    assert "other_key" not in ctx.outbound.metadata


@pytest.mark.asyncio
async def test_state_respond_no_session_no_crash(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    ctx = _mk_turn_context(loop, None)
    await loop._state_respond(ctx)
    assert ctx.outbound is not None


@pytest.mark.asyncio
async def test_state_respond_assemble_returns_none(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    session = Session(key="test:chat1")
    session.metadata["_outbound_tts"] = True

    ctx = _mk_turn_context(loop, session)
    with patch.object(loop, "_assemble_outbound", return_value=None):
        await loop._state_respond(ctx)

    assert ctx.outbound is None


@pytest.mark.asyncio
async def test_state_build_propagates_outbound_metadata(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    session = Session(key="test:chat1")
    session.metadata["_outbound_tts"] = True
    session.metadata["_outbound_custom"] = 42
    session.metadata["unrelated"] = "foo"

    ctx = MagicMock()
    ctx.msg = InboundMessage(channel="test", sender_id="user", chat_id="chat1", content="hi")
    ctx.session_key = "test:chat1"
    ctx.session = session
    ctx.pending_summary = None

    # Use the already-registered message tool from _register_default_tools
    message_tool = loop.tools.get("message")
    assert message_tool is not None, "message tool should be registered by default"

    await loop._state_build(ctx)

    default_meta = message_tool._default_metadata.get()
    assert default_meta.get("_outbound_tts") is True
    assert default_meta.get("_outbound_custom") == 42
    assert "unrelated" not in default_meta


@pytest.mark.asyncio
async def test_state_build_no_session_no_crash(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    ctx = MagicMock()
    ctx.msg = InboundMessage(channel="test", sender_id="user", chat_id="chat1", content="hi")
    ctx.session_key = "test:chat1"
    ctx.session = Session(key="test:chat1")  # empty session, no _outbound_ keys
    ctx.pending_summary = None

    await loop._state_build(ctx)
