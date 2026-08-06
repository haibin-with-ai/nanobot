"""run() 入口闸：真人消息先落账，落账失败即阻断本轮。"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.bus.events import InboundMessage


def _make_loop(tmp_path):
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.providers.base import GenerationSettings

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = GenerationSettings()
    return AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)


def _human_msg(content: str = "hello", **overrides) -> InboundMessage:
    fields = dict(
        channel="discord",
        sender_id="u1",
        chat_id="c1",
        content=content,
        metadata={"message_id": "m1", "reply_to": None},
    )
    fields.update(overrides)
    return InboundMessage(**fields)


async def _run_loop_with(loop, msg: InboundMessage) -> None:
    """Feed one message through the real run() loop, then stop."""
    loop._connect_mcp = AsyncMock()
    loop.close_mcp = AsyncMock()
    loop.auto_compact.check_expired = MagicMock()
    calls = 0

    async def feed_once_then_stop():
        nonlocal calls
        calls += 1
        if calls == 1:
            return msg
        loop.stop()
        raise asyncio.TimeoutError()

    loop.bus.consume_inbound = feed_once_then_stop
    await loop.run()
    await asyncio.sleep(0)


def _ledger_records(loop) -> list[dict]:
    path = loop.raw_ledger.path
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.mark.asyncio
async def test_human_message_lands_in_ledger_before_dispatch(tmp_path):
    loop = _make_loop(tmp_path)
    dispatched = False

    async def dispatch(_msg):
        nonlocal dispatched
        assert [r["content"] for r in _ledger_records(loop)] == ["hello"]
        dispatched = True

    loop._dispatch = dispatch
    await _run_loop_with(loop, _human_msg())

    assert dispatched is True


@pytest.mark.asyncio
async def test_command_message_lands_in_ledger(tmp_path):
    loop = _make_loop(tmp_path)

    async def dispatch(_msg):
        assert [r["content"] for r in _ledger_records(loop)] == ["/new"]

    loop._dispatch = dispatch
    await _run_loop_with(loop, _human_msg(content="/new"))


@pytest.mark.asyncio
async def test_ledger_write_failure_blocks_processing(tmp_path):
    loop = _make_loop(tmp_path)
    loop._dispatch = AsyncMock()
    loop.raw_ledger.append = MagicMock(side_effect=OSError("disk full"))

    await _run_loop_with(loop, _human_msg())

    loop._dispatch.assert_not_called()
    out = await asyncio.wait_for(loop.bus.consume_outbound(), timeout=1.0)
    assert "未归档" in out.content
    assert out.channel == "discord"
    assert out.chat_id == "c1"


@pytest.mark.asyncio
async def test_ledger_write_failure_blocks_priority_command(tmp_path):
    loop = _make_loop(tmp_path)
    loop.commands.dispatch_priority = AsyncMock()
    loop.raw_ledger.append = MagicMock(side_effect=OSError("disk full"))

    await _run_loop_with(loop, _human_msg(content="/stop"))

    loop.commands.dispatch_priority.assert_not_called()
    out = await asyncio.wait_for(loop.bus.consume_outbound(), timeout=1.0)
    assert "未归档" in out.content


@pytest.mark.asyncio
async def test_automation_turn_skips_ledger(tmp_path):
    from nanobot.cron.session_turns import CRON_TRIGGER_META

    loop = _make_loop(tmp_path)
    loop._dispatch = AsyncMock()
    msg = _human_msg(metadata={CRON_TRIGGER_META: {"job_id": "j1", "message": "run"}})

    await _run_loop_with(loop, msg)

    assert _ledger_records(loop) == []
    loop._dispatch.assert_called_once()


@pytest.mark.asyncio
async def test_process_direct_records_human_message_before_model(tmp_path):
    from nanobot.providers.base import LLMResponse

    loop = _make_loop(tmp_path)
    loop._connect_mcp = AsyncMock()
    loop.provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="ok", usage={}))
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=None)

    result = await loop.process_direct(
        "from sdk",
        session_key="sdk:test",
        channel="sdk",
        chat_id="test",
        sender_id="human",
    )

    assert result is not None
    assert [r["content"] for r in _ledger_records(loop)] == ["from sdk"]
    loop.provider.chat_with_retry.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_direct_write_failure_returns_error_without_model(tmp_path):
    loop = _make_loop(tmp_path)
    loop._connect_mcp = AsyncMock()
    loop.provider.chat_with_retry = AsyncMock()
    loop.raw_ledger.append = MagicMock(side_effect=OSError("disk full"))

    result = await loop.process_direct(
        "/new",
        session_key="sdk:test",
        channel="sdk",
        chat_id="test",
        sender_id="human",
    )

    assert result is not None
    assert "未归档" in result.content
    loop.provider.chat_with_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_command_survives_session_clear_in_ledger(tmp_path):
    loop = _make_loop(tmp_path)
    session = loop.sessions.get_or_create("discord:c1")
    session.add_message("user", "old context")
    loop.sessions.save(session)
    loop._schedule_background = lambda coro: coro.close()

    await _run_loop_with(loop, _human_msg(content="/new"))

    assert loop.sessions.get_or_create("discord:c1").messages == []
    assert [r["content"] for r in _ledger_records(loop)] == ["/new"]


@pytest.mark.asyncio
async def test_non_os_write_failure_still_blocks_processing(tmp_path):
    """编码失败同样是写入失败：阻断本轮而不是击穿 run() 主循环。"""
    loop = _make_loop(tmp_path)
    loop._dispatch = AsyncMock()

    await _run_loop_with(loop, _human_msg(content="bad \ud800 surrogate"))

    loop._dispatch.assert_not_called()
    out = await asyncio.wait_for(loop.bus.consume_outbound(), timeout=1.0)
    assert "未归档" in out.content


@pytest.mark.asyncio
async def test_process_direct_can_declare_internal_turn(tmp_path):
    """Dream/heartbeat 这类系统提示由调用点声明，不进真人账本。"""
    from nanobot.providers.base import LLMResponse

    loop = _make_loop(tmp_path)
    loop._connect_mcp = AsyncMock()
    loop.provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="ok", usage={}))
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=None)

    result = await loop.process_direct(
        "internal system prompt",
        session_key="heartbeat",
        channel="discord",
        chat_id="c1",
        record_raw_message=False,
    )

    assert result is not None
    assert _ledger_records(loop) == []


@pytest.mark.asyncio
async def test_recorded_marker_does_not_leak_to_outbound_metadata(tmp_path):
    """内部去重状态不属于渠道协议。"""
    from nanobot.providers.base import LLMResponse

    loop = _make_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=None)
    loop.provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="ok", usage={}))
    msg = _human_msg()
    loop.raw_ledger.append(msg)

    response = await loop._process_message(msg)

    assert response is not None
    assert "_raw_ledger_recorded" not in response.metadata
    assert "_raw_ledger_recorded" not in msg.metadata
