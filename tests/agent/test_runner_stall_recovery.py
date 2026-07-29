"""Stall 分阶段恢复：重试、把超时写回上下文、总量超限才放弃。

历史事故：Phase 1 重试耗尽后主循环直接 break，bot 静默停摆。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.runner_helpers import make_run_spec
from nanobot.config.schema import AgentDefaults
from nanobot.providers.base import LLMProvider, LLMResponse


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
    async def test_a_good_answer_clears_the_counters(self) -> None:
        from nanobot.agent.runner import AgentRunner

        provider = _Provider(
            _stall(), _ok("first"),
            _stall(), _stall(), _stall(), _ok("second"),
        )
        spec = _spec(provider)
        assert (await AgentRunner().run(spec)).final_content == "first"
        assert (await AgentRunner().run(spec)).final_content == "second"


class TestPhaseThreeGivesUp:
    @pytest.mark.asyncio
    async def test_too_many_stalls_raise(self) -> None:
        from nanobot.agent.runner import AgentRunner, ModelStallError

        provider = _Provider(*[_stall() for _ in range(6)])

        with pytest.raises(ModelStallError):
            await AgentRunner().run(_spec(provider))

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
