"""Line-age annotation on the Dream prompt.

MEMORY.md only grows. Without knowing which lines have sat untouched for
weeks, the consolidation run has no basis for pruning and keeps adding.
Ages are shown only past a threshold, so fresh lines stay unmarked and the
annotation itself does not become noise.
"""

from __future__ import annotations

import pytest

from nanobot.agent import memory as memory_module
from nanobot.agent.memory import MemoryStore
from nanobot.utils.gitstore import GitStoreError, LineAge


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path)
    s.write_memory("# Memory\n- old fact\n- fresh fact")
    s.append_history("something happened")
    return s


def _fake_ages(monkeypatch, store, ages):
    def line_ages(self, file_path: str):
        assert file_path == "memory/MEMORY.md"
        return ages

    monkeypatch.setattr(type(store._git), "line_ages", line_ages)


class TestLineAgeAnnotation:
    def test_lines_past_the_threshold_carry_their_age(self, store, monkeypatch):
        threshold = memory_module._STALE_THRESHOLD_DAYS
        _fake_ages(monkeypatch, store, [
            LineAge(age_days=0), LineAge(age_days=threshold + 30), LineAge(age_days=1),
        ])
        prompt, _ = store.build_dream_prompt()
        assert f"- old fact  ← {threshold + 30}d" in prompt

    def test_fresh_lines_stay_unmarked(self, store, monkeypatch):
        _fake_ages(monkeypatch, store, [
            LineAge(age_days=0), LineAge(age_days=1), LineAge(age_days=2),
        ])
        prompt, _ = store.build_dream_prompt()
        assert "←" not in prompt
        assert "- old fact" in prompt

    def test_threshold_boundary_is_not_marked(self, store, monkeypatch):
        threshold = memory_module._STALE_THRESHOLD_DAYS
        _fake_ages(monkeypatch, store, [
            LineAge(age_days=0), LineAge(age_days=threshold), LineAge(age_days=0),
        ])
        prompt, _ = store.build_dream_prompt()
        assert "←" not in prompt

    def test_blank_lines_are_never_annotated(self, tmp_path, monkeypatch):
        store = MemoryStore(tmp_path)
        store.write_memory("- a\n\n- b")
        store.append_history("x")
        _fake_ages(monkeypatch, store, [
            LineAge(age_days=999), LineAge(age_days=999), LineAge(age_days=999),
        ])
        prompt, _ = store.build_dream_prompt()
        assert "\n  ← 999d" not in prompt
        assert prompt.count("← 999d") == 2

    def test_no_git_history_leaves_content_intact(self, store, monkeypatch):
        _fake_ages(monkeypatch, store, [])
        prompt, _ = store.build_dream_prompt()
        assert "- old fact" in prompt
        assert "←" not in prompt

    def test_count_mismatch_skips_annotation(self, store, monkeypatch):
        """Blame lagging behind an unstaged edit must not shift ages onto wrong lines."""
        _fake_ages(monkeypatch, store, [LineAge(age_days=999)])
        prompt, _ = store.build_dream_prompt()
        assert "- old fact" in prompt
        assert "←" not in prompt

    def test_blame_failure_degrades_instead_of_killing_the_run(self, store, monkeypatch):
        def boom(self, file_path: str):
            raise GitStoreError("blame exploded")

        monkeypatch.setattr(type(store._git), "line_ages", boom)
        prompt, _ = store.build_dream_prompt()
        assert "- old fact" in prompt

    def test_annotation_survives_real_git_history(self, tmp_path):
        """End to end against an actual repo, no mocks."""
        store = MemoryStore(tmp_path)
        store.write_memory("# Memory\n- committed line")
        store._git.init()
        store._git.auto_commit("seed")
        store.append_history("x")
        prompt, _ = store.build_dream_prompt()
        assert "- committed line" in prompt


class TestDreamTemplateContract:
    def test_template_states_the_stale_threshold(self, store):
        store.append_history("x")
        prompt, _ = store.build_dream_prompt()
        assert str(memory_module._STALE_THRESHOLD_DAYS) in prompt

    def test_template_guards_against_short_session_noise(self, store):
        prompt, _ = store.build_dream_prompt()
        assert "Process noise" in prompt

    def test_template_forbids_touching_the_cursor(self, store):
        prompt, _ = store.build_dream_prompt()
        assert "memory/.dream_cursor" in prompt

    def test_template_warns_that_embedded_files_may_be_truncated(self, store):
        prompt, _ = store.build_dream_prompt()
        assert "truncated" in prompt
