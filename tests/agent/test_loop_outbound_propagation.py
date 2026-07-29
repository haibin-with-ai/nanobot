"""Outbound messages must inherit the turn they were sent from.

The propagation itself already lives in MessageTool. These tests pin the
contract so a future refactor cannot quietly make proactive sends land in the
wrong chat: bind a request context the way AgentLoop does, and the tool must
default to that channel and chat.
"""

from __future__ import annotations

import pytest

from nanobot.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from nanobot.agent.tools.message import MessageTool


def _request(**overrides) -> RequestContext:
    fields = dict(
        channel="discord",
        chat_id="chat-1",
        message_id="msg-1",
        session_key="discord:chat-1",
        original_user_text="hi",
        runtime=None,
        metadata={},
        sender_id="user-1",
        turn_id="turn-1",
        workspace=None,
    )
    fields.update(overrides)
    return RequestContext(**fields)


def _tool() -> tuple[MessageTool, list]:
    sent: list = []

    async def capture(message) -> None:
        sent.append(message)

    return MessageTool(capture), sent


async def _send(tool: MessageTool, sent: list, **kwargs) -> object:
    await tool.execute(**kwargs)
    return sent[0] if sent else None


@pytest.mark.asyncio
async def test_outbound_inherits_bound_request_context():
    tool, sent = _tool()
    token = bind_request_context(_request())
    try:
        message = await _send(tool, sent, content="hello")
    finally:
        reset_request_context(token)

    assert message is not None
    assert message.channel == "discord"
    assert message.chat_id == "chat-1"


@pytest.mark.asyncio
async def test_explicit_target_still_wins_over_current_turn():
    tool, sent = _tool()
    token = bind_request_context(_request())
    try:
        message = await _send(
            tool, sent, content="hello", channel="telegram", chat_id="chat-2"
        )
    finally:
        reset_request_context(token)

    assert message.channel == "telegram"
    assert message.chat_id == "chat-2"


@pytest.mark.asyncio
async def test_without_a_turn_it_does_not_invent_a_destination():
    tool, sent = _tool()
    message = await _send(tool, sent, content="hello")
    assert message is None or (message.channel, message.chat_id) != ("discord", "chat-1")
