"""run() 入口：首行为 /new 的多行消息先清 session，再把余文当新 prompt 处理。"""

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


def _human_msg(content: str, **overrides) -> InboundMessage:
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
async def test_new_prefix_clears_session_then_dispatches_rest(tmp_path):
    loop = _make_loop(tmp_path)
    session = loop.sessions.get_or_create("discord:c1")
    session.add_message("user", "old context")
    loop.sessions.save(session)
    loop._schedule_background = lambda coro: coro.close()

    dispatched: list[InboundMessage] = []

    async def dispatch(msg):
        # /new 必须先于余文处理完成：此刻 session 已清空。
        assert loop.sessions.get_or_create("discord:c1").messages == []
        dispatched.append(msg)

    loop._dispatch = dispatch
    await _run_loop_with(loop, _human_msg("/new\n/khb-intake https://example.com"))

    assert [m.content for m in dispatched] == ["/khb-intake https://example.com"]
    # /new 的回执照常发出。
    out = await asyncio.wait_for(loop.bus.consume_outbound(), timeout=1.0)
    assert out.chat_id == "c1"
    # 账本保留原始合并消息。
    assert [r["content"] for r in _ledger_records(loop)] == [
        "/new\n/khb-intake https://example.com"
    ]


@pytest.mark.asyncio
async def test_new_prefix_is_case_insensitive(tmp_path):
    loop = _make_loop(tmp_path)
    loop._schedule_background = lambda coro: coro.close()
    dispatched: list[str] = []

    async def dispatch(msg):
        dispatched.append(msg.content)

    loop._dispatch = dispatch
    await _run_loop_with(loop, _human_msg("/NEW\ndo the thing"))

    assert dispatched == ["do the thing"]


@pytest.mark.asyncio
async def test_bare_new_with_trailing_blank_lines_stays_plain_command(tmp_path):
    """余文为空时不走拆分路径，等价于普通 /new。"""
    loop = _make_loop(tmp_path)
    loop._schedule_background = lambda coro: coro.close()
    dispatched: list[str] = []

    async def dispatch(msg):
        dispatched.append(msg.content)

    loop._dispatch = dispatch
    await _run_loop_with(loop, _human_msg("/new\n   \n"))

    # raw 经 strip 后就是 "/new"，走原有整条命令路径。
    assert dispatched == ["/new\n   \n"]


@pytest.mark.asyncio
async def test_new_on_later_line_is_not_split(tmp_path):
    loop = _make_loop(tmp_path)
    dispatched: list[str] = []

    async def dispatch(msg):
        dispatched.append(msg.content)

    loop._dispatch = dispatch
    await _run_loop_with(loop, _human_msg("please explain\n/new means reset"))

    assert dispatched == ["please explain\n/new means reset"]
