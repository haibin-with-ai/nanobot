"""Tests for TraceHook JSONL trace logging."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nanobot.agent.hook import AgentHookContext
from nanobot.agent.hooks import TraceHook
from nanobot.providers.base import LLMResponse


def _make_context(model="test-model", iteration=1, stop_reason="end_turn", usage=None):
    ctx = AgentHookContext(
        iteration=iteration,
        messages=[],
    )
    ctx.model = model
    ctx.stop_reason = stop_reason
    ctx.usage = usage or {"prompt_tokens": 10, "completion_tokens": 5}
    return ctx


@pytest.mark.asyncio
async def test_trace_hook_writes_jsonl(tmp_path):
    trace_file = tmp_path / "llm_logs" / "test.trace.jsonl"
    hook = TraceHook(trace_file=trace_file)

    ctx = _make_context()
    await hook.after_iteration(ctx)

    assert trace_file.exists()
    line = json.loads(trace_file.read_text().strip())
    assert line["v"] == 1
    assert line["model"] == "test-model"
    assert line["iteration"] == 1
    assert line["finish_reason"] == "end_turn"
    assert line["usage"]["prompt_tokens"] == 10


@pytest.mark.asyncio
async def test_trace_hook_appends_multiple(tmp_path):
    trace_file = tmp_path / "llm_logs" / "multi.trace.jsonl"
    hook = TraceHook(trace_file=trace_file)

    await hook.after_iteration(_make_context(iteration=1))
    await hook.after_iteration(_make_context(iteration=2))

    lines = [json.loads(l) for l in trace_file.read_text().strip().split("\n")]
    assert len(lines) == 2
    assert lines[0]["iteration"] == 1
    assert lines[1]["iteration"] == 2


@pytest.mark.asyncio
async def test_trace_hook_creates_parent_dirs(tmp_path):
    trace_file = tmp_path / "deep" / "nested" / "trace.jsonl"
    hook = TraceHook(trace_file=trace_file)

    await hook.after_iteration(_make_context())
    assert trace_file.exists()


@pytest.mark.asyncio
async def test_trace_hook_includes_timestamp(tmp_path):
    trace_file = tmp_path / "ts.trace.jsonl"
    hook = TraceHook(trace_file=trace_file)

    await hook.after_iteration(_make_context())
    line = json.loads(trace_file.read_text().strip())
    assert "timestamp" in line
    assert isinstance(line["timestamp"], float)
