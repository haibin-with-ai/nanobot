"""Tests for runner timing accumulation in AgentRunResult."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.runner import AgentRunner, AgentRunSpec
from nanobot.providers.base import LLMResponse


@pytest.mark.asyncio
async def test_runner_timing_accumulated():
    """elapsed_ms and llm_elapsed_ms are accumulated across provider calls."""
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="done",
        tool_calls=[],
        usage={},
    ))

    runner = AgentRunner(provider)
    spec = AgentRunSpec(
        initial_messages=[{"role": "user", "content": "hi"}],
        tools=MagicMock(),
        model="test-model",
        max_iterations=10,
        max_tool_result_chars=1000,
    )
    spec.tools.get_definitions = MagicMock(return_value=[])

    with patch.object(runner, "_request_model", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = (
            LLMResponse(content="done", tool_calls=[], usage={}),
            50,
        )
        with patch.object(runner, "_request_finalization_retry", new_callable=AsyncMock) as mock_retry:
            mock_retry.return_value = (
                LLMResponse(content="done", tool_calls=[], usage={}),
                30,
            )
            result = await runner.run(spec)

    # Since the normal response is non-empty, finalization retry should not be called
    assert mock_req.call_count == 1
    assert mock_retry.call_count == 0
    assert result.llm_elapsed_ms == 50
    assert result.elapsed_ms >= 0


@pytest.mark.asyncio
async def test_runner_finalization_retry_adds_timing():
    """_request_finalization_retry adds its own timing to llm_elapsed_ms."""
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    call_count = {"n": 0}

    async def chat_with_retry(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMResponse(content="", tool_calls=[], usage={})
        return LLMResponse(content="done", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry

    runner = AgentRunner(provider)
    spec = AgentRunSpec(
        initial_messages=[{"role": "user", "content": "hi"}],
        tools=MagicMock(),
        model="test-model",
        max_iterations=10,
        max_tool_result_chars=1000,
    )
    spec.tools.get_definitions = MagicMock(return_value=[])

    with patch.object(runner, "_request_model", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = (
            LLMResponse(content="", tool_calls=[], usage={}),
            40,
        )
        with patch.object(runner, "_request_finalization_retry", new_callable=AsyncMock) as mock_retry:
            mock_retry.return_value = (
                LLMResponse(content="done", tool_calls=[], usage={}),
                25,
            )
            result = await runner.run(spec)

    assert mock_req.call_count == 2
    assert mock_retry.call_count == 1
    assert result.llm_elapsed_ms == 105
    assert result.elapsed_ms >= 0


@pytest.mark.asyncio
async def test_runner_default_timing_is_zero():
    """AgentRunResult defaults timing fields to 0."""
    from nanobot.agent.runner import AgentRunResult

    result = AgentRunResult(final_content="hi", messages=[])
    assert result.elapsed_ms == 0
    assert result.llm_elapsed_ms == 0
