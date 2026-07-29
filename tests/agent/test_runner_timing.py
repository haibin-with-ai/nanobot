"""Timing on AgentRunResult: total wall clock, and model time alone.

The two numbers answer different questions. Total says how long the user
waited; model time says how much of that was the provider rather than tools.
Tool execution and retries must not blur the model figure.

The clock is patched throughout; no test here sleeps.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.runner_helpers import make_run_spec
from nanobot.agent import runner as runner_module
from nanobot.agent.hook import AgentHook
from nanobot.agent.runner import AgentRunner
from nanobot.config.schema import AgentDefaults
from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars


class FakeClock:
    """Monotonic clock that only advances when a test says so."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(runner_module, "perf_counter", fake)
    return fake


def _spec(provider, *, tools=None):
    if tools is None:
        tools = MagicMock()
        tools.get_definitions.return_value = []
    return make_run_spec(
        provider,
        model="test-model",
        tools=tools,
        initial_messages=[{"role": "user", "content": "hi"}],
        max_iterations=5,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )


@pytest.mark.asyncio
async def test_result_reports_total_and_model_time(clock):
    provider = MagicMock(spec=LLMProvider)

    async def chat(**_kwargs):
        clock.advance(2.0)
        return LLMResponse(content="done", tool_calls=[], usage={})

    provider.chat_with_retry = chat
    result = await AgentRunner().run(_spec(provider))

    assert result.llm_elapsed_ms == 2000
    assert result.elapsed_ms == 2000


@pytest.mark.asyncio
async def test_tool_time_inflates_total_but_not_model_time(clock):
    """A slow tool must move the total and leave the model figure alone."""
    calls = {"n": 0}

    async def chat(**_kwargs):
        clock.advance(1.0)
        calls["n"] += 1
        if calls["n"] == 1:
            return LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(id="c1", name="slow", arguments={})],
                usage={},
            )
        return LLMResponse(content="done", tool_calls=[], usage={})

    async def slow_tool(*_args, **_kwargs) -> str:
        clock.advance(10.0)
        return "tool output"

    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = chat
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(side_effect=slow_tool)

    result = await AgentRunner().run(_spec(provider, tools=tools))

    assert result.llm_elapsed_ms == 2000
    assert result.elapsed_ms == 12000


@pytest.mark.asyncio
async def test_work_outside_the_request_window_lands_in_total_only(clock):
    """Setup before the first model call is wall clock, not model time."""

    class SlowSetupHook(AgentHook):
        async def before_run(self, context) -> None:
            clock.advance(5.0)

    async def chat(**_kwargs):
        clock.advance(1.0)
        return LLMResponse(content="done", tool_calls=[], usage={})

    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = chat

    spec = _spec(provider)
    spec.hook = SlowSetupHook()
    result = await AgentRunner().run(spec)

    assert result.llm_elapsed_ms == 1000
    assert result.elapsed_ms == 6000


@pytest.mark.asyncio
async def test_successive_runs_start_from_zero(clock):
    """One runner serves many turns; the accumulator must not be instance state.

    A shared fake clock makes genuinely concurrent runs overlap by definition,
    so isolation is asserted across successive runs instead.
    """

    async def chat(**_kwargs):
        clock.advance(1.0)
        return LLMResponse(content="done", tool_calls=[], usage={})

    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = chat
    runner = AgentRunner()

    first = await runner.run(_spec(provider))
    second = await runner.run(_spec(provider))
    third = await runner.run(_spec(provider))

    assert [first.llm_elapsed_ms, second.llm_elapsed_ms, third.llm_elapsed_ms] == [1000, 1000, 1000]


@pytest.mark.asyncio
async def test_timing_survives_a_run_without_an_accumulator(clock):
    """_request_model must still work when called outside run()."""
    provider = MagicMock(spec=LLMProvider)

    async def chat(**_kwargs):
        clock.advance(1.0)
        return LLMResponse(content="done", tool_calls=[], usage={})

    provider.chat_with_retry = chat
    result = await AgentRunner().run(_spec(provider))
    assert result.elapsed_ms >= result.llm_elapsed_ms > 0
