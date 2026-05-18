"""Tests for ContextPruner."""

from __future__ import annotations

import pytest

from nanobot.agent.pruner import ContextPruner, _soft_trim
from nanobot.config.schema import ContextPruningConfig


def test_disabled_returns_original():
    config = ContextPruningConfig(enabled=False)
    pruner = ContextPruner(config)
    messages = [{"role": "tool", "content": "x" * 100_000, "tool_call_id": "t1"}]
    result = pruner.prune(messages, context_window_chars=1)
    assert result == messages


def test_pruner_protects_last_n_assistants():
    config = ContextPruningConfig(enabled=True, keep_last_assistants=2)
    pruner = ContextPruner(config)
    messages = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "tool", "content": "x" * 100_000, "tool_call_id": "1"},
        {"role": "assistant", "content": "a2"},
        {"role": "tool", "content": "y" * 100_000, "tool_call_id": "2"},
        {"role": "assistant", "content": "a3"},
        {"role": "tool", "content": "z" * 100_000, "tool_call_id": "3"},
    ]
    result = pruner.prune(messages, context_window_chars=10_000)
    # a2 is the 2nd-to-last assistant, so a2 onward (including tool 2, a3, tool 3) is protected.
    assert result[2]["content"] != messages[2]["content"]   # tool 1 trimmed
    assert result[4]["content"] == messages[4]["content"]   # tool 2 protected
    assert result[6]["content"] == messages[6]["content"]   # tool 3 protected


def test_pruner_does_not_mutate_original_messages():
    config = ContextPruningConfig(enabled=True, keep_last_assistants=0)
    pruner = ContextPruner(config)
    original = [{"role": "tool", "content": "long text", "tool_call_id": "t1"}]
    result = pruner.prune(original, context_window_chars=1)
    assert original[0]["content"] == "long text"
    assert result is not original
    assert result[0] is not original[0]


def test_hard_clear_triggered():
    config = ContextPruningConfig(
        enabled=True,
        keep_last_assistants=0,
        hard_clear=ContextPruningConfig().hard_clear.model_copy(update={"enabled": True, "ratio": 0.1}),
    )
    pruner = ContextPruner(config)
    content = "x" * 10_000
    messages = [{"role": "tool", "content": content, "tool_call_id": "t1"}]
    result = pruner.prune(messages, context_window_chars=50_000)
    # len(content) / 50000 = 0.2 > 0.1, so hard_clear should trigger.
    assert result[0]["content"] == "[10000 characters cleared]"


def test_hard_clear_not_triggered():
    config = ContextPruningConfig(
        enabled=True,
        keep_last_assistants=0,
        hard_clear=ContextPruningConfig().hard_clear.model_copy(update={"enabled": True, "ratio": 0.5}),
    )
    pruner = ContextPruner(config)
    content = "x" * 100
    messages = [{"role": "tool", "content": content, "tool_call_id": "t1"}]
    result = pruner.prune(messages, context_window_chars=50_000)
    # len(content) / 50000 = 0.002 < 0.5, so hard_clear should not trigger.
    assert result[0]["content"] == content


def test_soft_trim_triggered():
    config = ContextPruningConfig(
        enabled=True,
        keep_last_assistants=0,
        soft_trim=ContextPruningConfig().soft_trim.model_copy(update={"enabled": True, "chunk_size": 10, "chunk_count": 3}),
        hard_clear=ContextPruningConfig().hard_clear.model_copy(update={"enabled": False}),
    )
    pruner = ContextPruner(config)
    content = "line1\nline2\nline3\nline4\nline5"
    messages = [{"role": "tool", "content": content, "tool_call_id": "t1"}]
    result = pruner.prune(messages, context_window_chars=50_000)
    # target_size = 10 * 3 = 30, content len = 29, so fits within target without trimming.
    # Wait, 29 <= 30, so no trim. Let's use a longer content.
    content = "line1\nline2\nline3\nline4\nline5\nline6"
    messages = [{"role": "tool", "content": content, "tool_call_id": "t1"}]
    result = pruner.prune(messages, context_window_chars=50_000)
    assert len(result[0]["content"]) <= 30
    assert "line1" in result[0]["content"]
    assert "line6" in result[0]["content"]


def test_soft_trim_single_long_line():
    config = ContextPruningConfig(
        enabled=True,
        keep_last_assistants=0,
        soft_trim=ContextPruningConfig().soft_trim.model_copy(update={"enabled": True, "chunk_size": 10, "chunk_count": 1}),
        hard_clear=ContextPruningConfig().hard_clear.model_copy(update={"enabled": False}),
    )
    pruner = ContextPruner(config)
    content = "a" * 100
    messages = [{"role": "tool", "content": content, "tool_call_id": "t1"}]
    result = pruner.prune(messages, context_window_chars=50_000)
    assert len(result[0]["content"]) <= 10


def test_multimodal_skip():
    config = ContextPruningConfig(enabled=True, keep_last_assistants=0)
    pruner = ContextPruner(config)
    messages = [
        {
            "role": "tool",
            "content": [{"type": "image_url", "image_url": {"url": "http://example.com/img.png"}}],
            "tool_call_id": "t1",
        }
    ]
    result = pruner.prune(messages, context_window_chars=1)
    assert result[0]["content"] == messages[0]["content"]


def test_non_str_tool_content_passthrough():
    config = ContextPruningConfig(enabled=True, keep_last_assistants=0)
    pruner = ContextPruner(config)
    messages = [{"role": "tool", "content": {"key": "value"}, "tool_call_id": "t1"}]
    result = pruner.prune(messages, context_window_chars=1)
    assert result[0]["content"] == {"key": "value"}


def test_hard_clear_priority_over_soft_trim():
    config = ContextPruningConfig(
        enabled=True,
        keep_last_assistants=0,
        hard_clear=ContextPruningConfig().hard_clear.model_copy(update={"enabled": True, "ratio": 0.1}),
        soft_trim=ContextPruningConfig().soft_trim.model_copy(update={"enabled": True, "chunk_size": 10, "chunk_count": 1}),
    )
    pruner = ContextPruner(config)
    content = "x" * 10_000
    messages = [{"role": "tool", "content": content, "tool_call_id": "t1"}]
    result = pruner.prune(messages, context_window_chars=50_000)
    assert result[0]["content"] == "[10000 characters cleared]"


def test_assistant_insufficient_protection():
    config = ContextPruningConfig(enabled=True, keep_last_assistants=5)
    pruner = ContextPruner(config)
    messages = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "tool", "content": "x" * 100_000, "tool_call_id": "1"},
    ]
    result = pruner.prune(messages, context_window_chars=10_000)
    # Only 1 assistant, less than keep_last_assistants=5, all protected.
    assert result[2]["content"] == messages[2]["content"]


def test_soft_trim_helper():
    assert _soft_trim("short", 100) == "short"
    assert len(_soft_trim("a" * 200, 50)) <= 50
    multiline = "first\nsecond\nthird"
    assert _soft_trim(multiline, 15) == "first\n...\nthird"
