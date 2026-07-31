"""Stall 状态机：一个计数器、一次判定、一条显式的墙钟信号。

三条历史坏味道：两个计数器各自演化、`stall_gave_up` 跨分支布尔把状态机
撕成两半、墙钟超时靠嗅探 provider 的错误措辞。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger

from agent.runner_helpers import make_run_spec
from nanobot.config.schema import AgentDefaults
from nanobot.providers.base import LLMProvider, LLMResponse


def _stall() -> LLMResponse:
    return LLMResponse(
        content="Error calling LLM: stream stalled",
        finish_reason="error",
        error_kind="timeout",
    )


def _provider_worded_timeout() -> LLMResponse:
    """provider 自己写的超时文案，恰好和 runner 的墙钟文案同一个前缀。"""
    return LLMResponse(
        content="Error calling LLM: timed out after 9s",
        finish_reason="error",
        error_kind="timeout",
    )


def _ok(content: str = "done") -> LLMResponse:
    return LLMResponse(content=content, finish_reason="stop", tool_calls=[])


class _Provider:
    def __init__(self, *responses: LLMResponse) -> None:
        self._responses = list(responses)
        self.seen: list[list[dict]] = []

    async def chat_with_retry(self, *, messages, **_kwargs) -> LLMResponse:
        self.seen.append([dict(message) for message in messages])
        if not self._responses:
            return _ok("fallback done")
        return self._responses.pop(0)


def _spec(provider, **kwargs):
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(return_value="")
    real = MagicMock(spec=LLMProvider)
    real.chat_with_retry = provider.chat_with_retry
    real.model_attempt_budget = 1
    return make_run_spec(
        real,
        initial_messages=kwargs.pop("initial_messages", [{"role": "user", "content": "hi"}]),
        tools=tools,
        model="test-model",
        max_iterations=kwargs.pop("max_iterations", 12),
        max_tool_result_chars=AgentDefaults().max_tool_result_chars,
        **kwargs,
    )


def _stall_notices(messages: list[dict]) -> list[dict]:
    return [m for m in messages if "超时中断" in str(m.get("content") or "")]


class TestOneCounterDecidesEveryPhase:
    @pytest.mark.parametrize(
        "stalls,expected",
        [(1, "retry"), (2, "retry"), (3, "notice_retry"), (4, "give_up"), (5, "give_up")],
    )
    def test_the_verdict_follows_the_stall_count_alone(self, stalls, expected) -> None:
        from nanobot.agent.runner import AgentRunner

        verdict = AgentRunner._stall_verdict(
            stalls, out_of_iterations=False, out_of_time=False
        )
        assert verdict == expected

    @pytest.mark.parametrize("limit", ["out_of_iterations", "out_of_time"])
    def test_a_spent_budget_gives_up_on_the_first_stall(self, limit) -> None:
        from nanobot.agent.runner import AgentRunner

        kwargs = {"out_of_iterations": False, "out_of_time": False, limit: True}
        assert AgentRunner._stall_verdict(1, **kwargs) == "give_up"


class TestTheStallNoticeNeverLiesAboutLanding:
    @pytest.mark.asyncio
    async def test_a_notice_that_cannot_be_attached_is_reported(self) -> None:
        """续跑的会话以 assistant 收尾时，Phase 2 唯一的动作会落空，日志得说出来。"""
        from nanobot.agent.runner import AgentRunner

        warnings: list[str] = []
        sink_id = logger.add(lambda message: warnings.append(str(message)), level="WARNING")
        try:
            provider = _Provider(_stall(), _stall(), _stall(), _ok())
            result = await AgentRunner().run(
                _spec(
                    provider,
                    initial_messages=[
                        {"role": "user", "content": "hi"},
                        {"role": "assistant", "content": "上一轮我已经答过了"},
                    ],
                )
            )
        finally:
            logger.remove(sink_id)

        assert result.final_content == "done"
        assert _stall_notices(provider.seen[-1]) == [], "这一路本来就贴不上提示"
        assert any("stall notice" in line.lower() for line in warnings), warnings

    @pytest.mark.asyncio
    async def test_a_notice_that_lands_is_not_reported_as_dropped(self) -> None:
        from nanobot.agent.runner import AgentRunner

        warnings: list[str] = []
        sink_id = logger.add(lambda message: warnings.append(str(message)), level="WARNING")
        try:
            provider = _Provider(_stall(), _stall(), _stall(), _ok())
            await AgentRunner().run(_spec(provider))
        finally:
            logger.remove(sink_id)

        assert _stall_notices(provider.seen[-1]), "模型必须看见上一轮超时"
        assert not any("stall notice" in line.lower() for line in warnings), warnings


class TestWallTimeoutIsAnExplicitSignal:
    @pytest.mark.asyncio
    async def test_provider_wording_does_not_pass_for_the_runners_own_wall_clock(self) -> None:
        """runner 不许靠 provider 的错误措辞判断墙钟超时。"""
        from nanobot.agent.runner import AgentRunner

        provider = _Provider(_provider_worded_timeout())
        result = await AgentRunner().run(_spec(provider, max_iterations=1))

        assert result.stop_reason == "error"
        assert result.final_content != "Error calling LLM: timed out after 9s"
        assert "本轮放弃" in result.final_content

    @pytest.mark.asyncio
    async def test_the_runners_own_wall_timeout_keeps_its_own_message(self) -> None:
        from nanobot.agent.runner import AgentRunner

        wall = LLMResponse(
            content="Error calling LLM: timed out after 300s",
            finish_reason="error",
            error_kind="wall_timeout",
        )
        provider = _Provider(wall)
        result = await AgentRunner().run(_spec(provider, max_iterations=1))

        assert result.stop_reason == "error"
        assert result.final_content == "Error calling LLM: timed out after 300s"
