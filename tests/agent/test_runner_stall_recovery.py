"""Stall 分阶段恢复：重试、把超时写回上下文、总量超限才放弃。

历史事故：Phase 1 重试耗尽后主循环直接 break，bot 静默停摆。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.runner_helpers import make_run_spec
from nanobot.config.schema import AgentDefaults
from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


def _stall() -> LLMResponse:
    return LLMResponse(
        content="Error calling LLM: stream stalled",
        finish_reason="error",
        error_kind="timeout",
    )


def _server_error() -> LLMResponse:
    return LLMResponse(content="boom", finish_reason="error", error_kind="server_error")


def _ok(content: str = "done") -> LLMResponse:
    return LLMResponse(content=content, finish_reason="stop", tool_calls=[])


def _tool_call() -> LLMResponse:
    """一次成功的工具调用，让同一次 run 能继续往下跑。"""
    return LLMResponse(
        content="",
        finish_reason="tool_calls",
        tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={"path": "."})],
    )


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
    real.model_attempt_budget = getattr(provider, "model_attempt_budget", 1)
    return make_run_spec(
        real,
        initial_messages=[{"role": "user", "content": "hi"}],
        tools=tools,
        model="test-model",
        max_iterations=kwargs.pop("max_iterations", 12),
        max_tool_result_chars=AgentDefaults().max_tool_result_chars,
        **kwargs,
    )


def _stall_notices(messages: list[dict]) -> list[dict]:
    return [m for m in messages if "超时" in str(m.get("content") or "")]


class TestPhaseTwoRetries:
    @pytest.mark.asyncio
    async def test_a_single_stall_is_retried_silently(self) -> None:
        from nanobot.agent.runner import AgentRunner

        provider = _Provider(_stall(), _ok())
        result = await AgentRunner().run(_spec(provider))

        assert result.final_content == "done"
        assert len(provider.seen) == 2
        assert _stall_notices(provider.seen[-1]) == []

    @pytest.mark.asyncio
    async def test_exhausted_retries_write_the_stall_into_context(self) -> None:
        from nanobot.agent.runner import AgentRunner

        provider = _Provider(_stall(), _stall(), _stall(), _ok())
        result = await AgentRunner().run(_spec(provider))

        assert result.final_content == "done"
        assert len(provider.seen) == 4
        assert _stall_notices(provider.seen[-1]), "模型必须看见上一轮超时"

    @pytest.mark.asyncio
    async def test_a_good_turn_clears_the_counters_inside_one_run(self) -> None:
        """同一次 run 内：成功一轮后计数必须归零，否则第 4 次超时会提前放弃。"""
        from nanobot.agent.runner import AgentRunner

        provider = _Provider(
            _stall(),
            _tool_call(),
            _stall(), _stall(), _stall(),
            _ok("second"),
        )

        result = await AgentRunner().run(_spec(provider))

        assert result.final_content == "second"
        assert result.stop_reason != "error"
        assert len(provider.seen) == 6


class TestPhaseThreeGivesUp:
    @pytest.mark.asyncio
    async def test_too_many_stalls_end_the_run_as_an_error(self) -> None:
        """放弃要走正常错误出口：不抛异常，上下文照常保留。"""
        from nanobot.agent.runner import _MAX_TOTAL_STALLS, AgentRunner

        provider = _Provider(*[_stall() for _ in range(6)])

        result = await AgentRunner().run(_spec(provider))

        assert result.stop_reason == "error"
        assert "放弃" in result.final_content
        assert len(provider.seen) == _MAX_TOTAL_STALLS
        assert result.messages and result.messages[0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_giving_up_does_not_blame_the_wrong_thing(self) -> None:
        from nanobot.agent.runner import AgentRunner

        result = await AgentRunner().run(_spec(_Provider(*[_stall() for _ in range(6)])))

        assert "超时" not in result.final_content or "放弃" in result.final_content
        assert result.error == result.final_content

    @pytest.mark.asyncio
    async def test_plain_errors_never_reach_the_stall_machinery(self) -> None:
        from nanobot.agent.runner import AgentRunner

        provider = _Provider(_server_error())
        result = await AgentRunner().run(_spec(provider))

        assert result.stop_reason == "error"
        assert len(provider.seen) == 1


class TestOuterTimeoutCoversTheWholeModelChain:
    """外层墙钟预算要按候选模型数放大，否则慢的主模型会把备用模型一起掐掉。"""

    @pytest.mark.asyncio
    async def test_slow_primary_does_not_cancel_the_fallback(self) -> None:
        import asyncio

        from nanobot.agent.runner import AgentRunner

        class _Chain:
            model_attempt_budget = 2

            def __init__(self) -> None:
                self.seen: list[list[dict]] = []

            async def chat_with_retry(self, *, messages, **_kwargs) -> LLMResponse:
                self.seen.append([dict(m) for m in messages])
                if len(self.seen) == 1:
                    await asyncio.sleep(0.25)
                    return _ok("primary late")
                return _ok("fallback ok")

        provider = _Chain()
        spec = _spec(provider, llm_timeout_s=0.2)

        result = await AgentRunner().run(spec)

        assert result.final_content == "primary late"

    @pytest.mark.asyncio
    async def test_a_long_chain_does_not_multiply_the_wall(self) -> None:
        """预算只放大一倍：链再长也不能让用户干等 N 倍时间。"""
        import asyncio

        from nanobot.agent.runner import AgentRunner

        class _LongChain:
            model_attempt_budget = 6

            def __init__(self) -> None:
                self.calls = 0

            async def chat_with_retry(self, *, messages, **_kwargs) -> LLMResponse:
                self.calls += 1
                await asyncio.sleep(5)
                return _ok("never")

        provider = _LongChain()
        loop = asyncio.get_running_loop()
        started = loop.time()

        result = await AgentRunner().run(
            _spec(provider, llm_timeout_s=0.2, max_iterations=1)
        )

        # 6 个候选若各给一份预算就是 1.2 秒起步；封顶后单次调用只等 0.4 秒。
        assert loop.time() - started < 1.0
        assert result.stop_reason == "error"


class TestRefusalKeepsItsWords:
    """拒答是模型说的话，要原样进历史，别写成占位符。"""

    @pytest.mark.asyncio
    async def test_refusal_text_lands_in_history(self) -> None:
        from nanobot.agent.runner import AgentRunner

        refusal = LLMResponse(
            content="我不能帮你做这个。",
            finish_reason="error",
            error_kind="refusal",
        )
        result = await AgentRunner().run(_spec(_Provider(refusal)))

        assert result.final_content == "我不能帮你做这个。"
        assert result.messages[-1]["role"] == "assistant"
        assert "我不能帮你做这个。" in result.messages[-1]["content"]

    @pytest.mark.asyncio
    async def test_other_errors_still_use_the_placeholder(self) -> None:
        from nanobot.agent.runner import AgentRunner

        boom = LLMResponse(content="500 boom", finish_reason="error", error_kind="server_error")
        result = await AgentRunner().run(_spec(_Provider(boom)))

        assert result.messages[-1]["content"] != "500 boom"
