"""Tests for AgentRunner LLM request/response logging."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger

from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.agent.runner import AgentRunner, AgentRunSpec
from nanobot.providers.base import LLMResponse, ToolCallRequest


def _make_spec(**overrides) -> AgentRunSpec:
    defaults = dict(
        model="openai/gpt-4",
        initial_messages=[],
        max_iterations=10,
        session_key="test",
        tools=MagicMock(),
        max_tool_result_chars=1000,
    )
    defaults.update(overrides)
    return AgentRunSpec(**defaults)


class _CaptureHook(AgentHook):
    async def before_iteration(self, context: AgentHookContext) -> None:
        pass


# ---------------------------------------------------------------------------
# _last_message_preview
# ---------------------------------------------------------------------------

def test_last_message_preview_empty():
    assert AgentRunner._last_message_preview([]) == ""


def test_last_message_preview_string_content():
    messages = [{"role": "user", "content": "Hello world"}]
    assert AgentRunner._last_message_preview(messages) == "Hello world"


def test_last_message_preview_newlines_replaced():
    messages = [{"role": "user", "content": "Line 1\nLine 2\n"}]
    assert AgentRunner._last_message_preview(messages) == "Line 1 Line 2"


def test_last_message_preview_truncation():
    messages = [{"role": "user", "content": "x" * 300}]
    preview = AgentRunner._last_message_preview(messages, limit=20)
    assert preview == "x" * 17 + "..."


def test_last_message_preview_list_content_extracts_text():
    messages = [{"role": "user", "content": [{"type": "text", "text": "image desc"}]}]
    assert AgentRunner._last_message_preview(messages) == "image desc"


def test_last_message_preview_list_content_no_text_block():
    messages = [{"role": "user", "content": [{"type": "image_url", "url": "http://x"}]}]
    assert AgentRunner._last_message_preview(messages) == ""


# ---------------------------------------------------------------------------
# _request_model logging
# ---------------------------------------------------------------------------

@pytest.fixture
def log_capture():
    """Capture loguru messages in a list."""
    captured: list[str] = []
    handler_id = logger.add(captured.append, level="INFO", filter="nanobot.agent.runner")
    yield captured
    logger.remove(handler_id)


@pytest.mark.asyncio
async def test_request_model_logs_request_and_response(log_capture):
    runner = AgentRunner(MagicMock())
    runner.provider = MagicMock()
    runner.provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(content="ok", finish_reason="stop")
    )
    spec = _make_spec(model="anthropic/claude-3")
    messages = [{"role": "user", "content": "hi"}]
    hook = _CaptureHook()
    context = AgentHookContext(iteration=1, messages=messages)

    await runner._request_model(spec, messages, hook, context)

    text = "\n".join(log_capture)
    assert "LLM request → model=anthropic/claude-3" in text
    assert "messages=1" in text
    assert "last='hi'" in text
    assert "LLM response ← model=anthropic/claude-3" in text
    assert "finish_reason=stop" in text


@pytest.mark.asyncio
async def test_request_model_logs_tool_calls(log_capture):
    runner = AgentRunner(MagicMock())
    runner.provider = MagicMock()
    runner.provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(
            content="",
            finish_reason="tool_calls",
            tool_calls=[ToolCallRequest(id="c1", name="read_file", arguments={})],
        )
    )
    spec = _make_spec()
    messages = [{"role": "user", "content": "read it"}]
    hook = _CaptureHook()
    context = AgentHookContext(iteration=1, messages=messages)

    await runner._request_model(spec, messages, hook, context)

    text = "\n".join(log_capture)
    assert "LLM tool_calls model=openai/gpt-4 tools=['read_file']" in text


@pytest.mark.asyncio
async def test_request_model_no_tool_calls_no_extra_log(log_capture):
    runner = AgentRunner(MagicMock())
    runner.provider = MagicMock()
    runner.provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(content="ok", finish_reason="stop")
    )
    spec = _make_spec()
    messages = [{"role": "user", "content": "hello"}]
    hook = _CaptureHook()
    context = AgentHookContext(iteration=1, messages=messages)

    await runner._request_model(spec, messages, hook, context)

    text = "\n".join(log_capture)
    assert "LLM tool_calls" not in text


@pytest.mark.asyncio
async def test_pinned_provider_survives_mid_run_live_provider_swap():
    """A run pins its provider; swapping runner.provider mid-run must not
    redirect the in-flight request.

    Regression: a runtime model switch (opus -> gpt-5.5) mutated
    runner.provider to the Codex provider while an output-truncation
    continuation was still in flight with the opus model name, sending opus to
    Codex -> HTTP 400. The pinned spec.provider keeps provider+model paired.
    """
    pinned = MagicMock()
    pinned.chat_with_retry = AsyncMock(
        return_value=LLMResponse(content="ok", finish_reason="stop")
    )
    live = MagicMock()
    live.chat_with_retry = AsyncMock(
        return_value=LLMResponse(content="WRONG", finish_reason="stop")
    )

    runner = AgentRunner(pinned)
    spec = _make_spec(model="anthropic/claude-opus-4-8", provider=pinned)
    messages = [{"role": "user", "content": "continue"}]
    hook = _CaptureHook()
    context = AgentHookContext(iteration=1, messages=messages)

    # Runtime model switch fires mid-run: live provider is replaced.
    runner.provider = live

    await runner._request_model(spec, messages, hook, context)

    pinned.chat_with_retry.assert_awaited_once()
    live.chat_with_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_finalization_retry_silent_no_llm_logs(log_capture):
    """Finalization retry must not emit LLM request/response logs."""
    runner = AgentRunner(MagicMock())
    runner.provider = MagicMock()
    runner.provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(content="retry ok", finish_reason="stop")
    )
    spec = _make_spec()
    messages = [{"role": "user", "content": "hello"}]

    await runner._request_finalization_retry(spec, messages)

    text = "\n".join(log_capture)
    assert "LLM request" not in text
    assert "LLM response" not in text
