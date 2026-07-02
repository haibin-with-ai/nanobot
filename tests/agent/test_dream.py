"""Tests for single-phase Dream memory consolidation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from nanobot.agent.loop import AgentLoop
from nanobot.agent.memory import (
    _HISTORY_ENTRY_PREVIEW_MAX_CHARS,
    MemoryStore,
)
from nanobot.config.schema import DreamConfig, ToolsConfig
from nanobot.utils.gitstore import LineAge

ToolsConfig.model_rebuild()


def _response(*, stop_reason: str = "completed", content: str | None = None):
    return SimpleNamespace(
        metadata={"_stop_reason": stop_reason},
        content=content,
    )


class FakeDreamMemory:
    def __init__(self, built=None, cursor=0):
        self.built = built
        self.cursor = cursor
        self.git = SimpleNamespace(auto_commit=MagicMock(return_value=True))
        self.build_dream_prompt = MagicMock(return_value=built)
        self.get_last_dream_cursor = MagicMock(side_effect=lambda: self.cursor)
        self.set_last_dream_cursor = MagicMock(
            side_effect=lambda value: setattr(self, "cursor", value)
        )


class TestDreamPrompt:
    def test_build_dream_prompt_returns_none_with_no_history(self, tmp_path):
        store = MemoryStore(tmp_path)

        assert store.build_dream_prompt(max_entries=20, annotate_line_ages=False) is None

    def test_build_dream_prompt_contains_inputs_and_target_cursor(self, tmp_path):
        store = MemoryStore(tmp_path)
        store.write_soul("# Soul\nEvie")
        store.write_user("# User\nhaibin")
        store.write_memory("# Memory\n- durable fact")
        store.append_history("first event")
        store.append_history("second event")

        prompt, target_cursor = store.build_dream_prompt(
            max_entries=20,
            annotate_line_ages=False,
        )

        assert target_cursor == 2
        assert "## Target Cursor\n2" in prompt
        assert ".dream_cursor" in prompt
        assert "first event" in prompt
        assert "second event" in prompt
        assert "# Soul\nEvie" in prompt
        assert "# User\nhaibin" in prompt
        assert "# Memory\n- durable fact" in prompt

    def test_build_dream_prompt_caps_oversized_history_entry(self, tmp_path):
        store = MemoryStore(tmp_path)
        huge = "H" * (_HISTORY_ENTRY_PREVIEW_MAX_CHARS * 3)
        store.history_file.write_text(
            json.dumps(
                {
                    "cursor": 1,
                    "timestamp": "2026-07-02 10:00",
                    "content": huge,
                }
            )
            + "\n",
            encoding="utf-8",
        )

        prompt, target_cursor = store.build_dream_prompt(
            max_entries=20,
            annotate_line_ages=False,
        )

        history_section = prompt.split("## Conversation History\n", 1)[1]
        assert target_cursor == 1
        assert len(history_section) < _HISTORY_ENTRY_PREVIEW_MAX_CHARS + 500
        assert huge not in history_section

    def test_build_dream_prompt_annotates_stale_memory_lines(self, tmp_path):
        store = MemoryStore(tmp_path)
        store.write_memory("# Memory\n- old fact\n- fresh fact")
        store.append_history("some event")
        ages = [LineAge(age_days=30), LineAge(age_days=15), LineAge(age_days=5)]

        with patch.object(store.git, "line_ages", return_value=ages):
            prompt, _ = store.build_dream_prompt(max_entries=20, annotate_line_ages=True)

        memory_section = prompt.split("## Current MEMORY.md", 1)[1].split("## Current SOUL.md", 1)[0]
        assert "# Memory  ← 30d" in memory_section
        assert "- old fact  ← 15d" in memory_section
        assert "- fresh fact  ← 5d" not in memory_section

    def test_build_dream_prompt_skips_line_ages_when_disabled(self, tmp_path):
        store = MemoryStore(tmp_path)
        store.write_memory("# Memory\n- old fact")
        store.append_history("some event")

        with patch.object(store.git, "line_ages") as line_ages:
            prompt, _ = store.build_dream_prompt(max_entries=20, annotate_line_ages=False)

        line_ages.assert_not_called()
        memory_section = prompt.split("## Current MEMORY.md", 1)[1].split("## Current SOUL.md", 1)[0]
        assert "←" not in memory_section


class TestDreamHelpers:
    def test_dream_run_completed_true_only_for_completed_marker(self):
        assert MemoryStore.dream_run_completed(_response(stop_reason="completed")) is True
        assert MemoryStore.dream_run_completed(_response(stop_reason="max_iterations")) is False
        assert MemoryStore.dream_run_completed(SimpleNamespace(metadata={})) is False
        assert MemoryStore.dream_run_completed(None) is False

    def test_build_dream_commit_message_includes_response_content_when_present(self):
        assert MemoryStore.build_dream_commit_message("prefix", None) == "prefix"
        assert MemoryStore.build_dream_commit_message("prefix", SimpleNamespace(content="")) == "prefix"
        assert (
            MemoryStore.build_dream_commit_message("prefix", SimpleNamespace(content="  changed memory  "))
            == "prefix\n\nchanged memory"
        )

    def test_prune_dream_sessions_keeps_newest_n(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        kept_other = sessions_dir / "regular_0.jsonl"
        kept_other.write_text("not dream", encoding="utf-8")
        dream_files: list[Path] = []
        for i in range(5):
            path = sessions_dir / f"dream_{i}.jsonl"
            path.write_text(str(i), encoding="utf-8")
            os.utime(path, (i, i))
            dream_files.append(path)

        MemoryStore.prune_dream_sessions(sessions_dir, keep=2)

        assert [p.name for p in dream_files if p.exists()] == ["dream_3.jsonl", "dream_4.jsonl"]
        assert kept_other.exists()


class TestRunDreamOnce:
    async def test_no_entries_does_not_call_process_direct(self, tmp_path):
        memory = FakeDreamMemory(built=None)
        loop = object.__new__(AgentLoop)
        loop.dream_config = DreamConfig(model_override="dream-model")
        loop.context = SimpleNamespace(memory=memory)
        loop.workspace = tmp_path
        loop.process_direct = AsyncMock()

        result = await loop.run_dream_once()

        assert result.did_work is False
        assert result.reason == "no_history"
        loop.process_direct.assert_not_called()
        memory.build_dream_prompt.assert_called_once_with(max_entries=20, annotate_line_ages=True)

    async def test_entries_call_process_direct_once_with_dream_model_preset(self, tmp_path):
        memory = FakeDreamMemory(built=("dream prompt", 7), cursor=7)
        loop = object.__new__(AgentLoop)
        loop.dream_config = DreamConfig(model_override="dream-model")
        loop.context = SimpleNamespace(memory=memory)
        loop.workspace = tmp_path
        loop.process_direct = AsyncMock(return_value=_response(stop_reason="max_iterations"))

        with patch.object(MemoryStore, "dream_session_key", return_value="dream:test"):
            result = await loop.run_dream_once()

        loop.process_direct.assert_awaited_once_with(
            "dream prompt",
            session_key="dream:test",
            channel="cron",
            chat_id="dream",
            media=None,
            on_progress=None,
            on_stream=None,
            on_stream_end=None,
            model_preset="dream-model",
        )
        assert result.did_work is False
        assert result.reason == "not_completed"
        memory.git.auto_commit.assert_not_called()

    async def test_incomplete_response_leaves_cursor_untouched_and_not_committed(self, tmp_path):
        memory = FakeDreamMemory(built=("dream prompt", 7), cursor=3)
        loop = object.__new__(AgentLoop)
        loop.dream_config = DreamConfig(model_override="dream-model")
        loop.context = SimpleNamespace(memory=memory)
        loop.workspace = tmp_path
        loop.process_direct = AsyncMock(return_value=_response(stop_reason="max_iterations", content="done"))

        result = await loop.run_dream_once()

        assert result.did_work is False
        assert result.cursor == 3
        assert result.reason == "not_completed"
        memory.set_last_dream_cursor.assert_not_called()
        memory.git.auto_commit.assert_not_called()

    async def test_completed_response_advances_cursor_and_auto_commits(self, tmp_path):
        memory = FakeDreamMemory(built=("dream prompt", 7), cursor=3)
        loop = object.__new__(AgentLoop)
        loop.dream_config = DreamConfig(model_override="dream-model")
        loop.context = SimpleNamespace(memory=memory)
        loop.workspace = tmp_path
        loop.process_direct = AsyncMock(return_value=_response(stop_reason="completed", content="updated notes"))

        with patch.object(MemoryStore, "prune_dream_sessions") as prune:
            result = await loop.run_dream_once()

        assert result.did_work is True
        assert result.cursor == 7
        assert result.reason == "completed"
        assert result.committed is True
        memory.set_last_dream_cursor.assert_called_once_with(7)
        memory.git.auto_commit.assert_called_once_with(
            "dream: consolidate to cursor 7\n\nupdated notes"
        )
        prune.assert_called_once_with(tmp_path / "sessions")
