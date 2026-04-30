"""Tests for the lightweight Consolidator — append-only to HISTORY.md."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from nanobot.agent.memory import Consolidator, MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path)


@pytest.fixture
def mock_provider():
    p = MagicMock()
    p.chat_with_retry = AsyncMock()
    return p


@pytest.fixture
def consolidator(store, mock_provider):
    sessions = MagicMock()
    sessions.save = MagicMock()
    return Consolidator(
        store=store,
        provider=mock_provider,
        model="test-model",
        sessions=sessions,
        context_window_tokens=1000,
        build_messages=MagicMock(return_value=[]),
        get_tool_definitions=MagicMock(return_value=[]),
        max_completion_tokens=100,
    )


class TestConsolidatorSummarize:
    async def test_summarize_appends_to_history(self, consolidator, mock_provider, store):
        """Consolidator should call LLM to summarize, then append to HISTORY.md."""
        mock_provider.chat_with_retry.return_value = MagicMock(
            content="User fixed a bug in the auth module."
        )
        messages = [
            {"role": "user", "content": "fix the auth bug"},
            {"role": "assistant", "content": "Done, fixed the race condition."},
        ]
        result = await consolidator.archive(messages)
        assert result is True
        entries = store.read_unprocessed_history(since_cursor=0)
        assert len(entries) == 1

    async def test_summarize_raw_dumps_on_llm_failure(self, consolidator, mock_provider, store):
        """On LLM failure, raw-dump messages to HISTORY.md."""
        mock_provider.chat_with_retry.side_effect = Exception("API error")
        messages = [{"role": "user", "content": "hello"}]
        result = await consolidator.archive(messages)
        assert result is True  # always succeeds
        entries = store.read_unprocessed_history(since_cursor=0)
        assert len(entries) == 1
        assert "[RAW]" in entries[0]["content"]

    async def test_summarize_skips_empty_messages(self, consolidator):
        result = await consolidator.archive([])
        assert result is False


class TestConsolidatorTokenBudget:
    async def test_prompt_below_threshold_does_not_consolidate(self, consolidator):
        """No consolidation when tokens are within budget."""
        session = MagicMock()
        session.last_consolidated = 0
        session.messages = [{"role": "user", "content": "hi"}]
        session.key = "test:key"
        consolidator.estimate_session_prompt_tokens = MagicMock(return_value=(100, "tiktoken"))
        consolidator.archive = AsyncMock(return_value=True)
        await consolidator.maybe_consolidate_by_tokens(session)
        consolidator.archive.assert_not_called()


class TestConsolidatorTriggerRatio:
    async def test_ratio_0_5_lowers_trigger_and_does_not_fire(self, consolidator):
        """With ratio=0.5 on a 1000-token window, budget=400; estimated=300 stays idle."""
        consolidator.trigger_ratio = 0.5
        consolidator.context_window_tokens = 1000
        consolidator.max_completion_tokens = 100
        consolidator._SAFETY_BUFFER = 0

        session = MagicMock()
        session.last_consolidated = 0
        session.key = "test:key"
        session.messages = [
            {"role": "user", "content": "x" * 200},
            {"role": "assistant", "content": "y" * 200},
        ]
        consolidator.estimate_session_prompt_tokens = MagicMock(return_value=(300, "test"))
        consolidator.archive = AsyncMock(return_value=True)

        await consolidator.maybe_consolidate_by_tokens(session)
        # 300 < budget(400) -> should NOT consolidate
        consolidator.archive.assert_not_called()

    async def test_ratio_0_5_fires_when_estimated_exceeds_budget(self, consolidator):
        """With ratio=0.5, consolidation fires when estimated >= budget."""
        consolidator.trigger_ratio = 0.5
        consolidator.context_window_tokens = 1000
        consolidator.max_completion_tokens = 100
        consolidator._SAFETY_BUFFER = 0

        session = MagicMock()
        session.last_consolidated = 0
        session.key = "test:key"
        session.messages = [
            {"role": "user", "content": "x" * 400},
            {"role": "assistant", "content": "y" * 400},
            {"role": "user", "content": "z" * 400},
        ]
        # budget = 500 - 100 = 400; target = 200
        consolidator.estimate_session_prompt_tokens = MagicMock(return_value=(450, "test"))
        consolidator.archive = AsyncMock(return_value=True)

        await consolidator.maybe_consolidate_by_tokens(session)
        # 450 >= 400 -> fire; 450 > 200 -> need consolidation
        consolidator.archive.assert_called_once()

    async def test_ratio_zero_treated_as_one(self, consolidator):
        """ratio=0 should be treated as 1.0 (backward compatible)."""
        consolidator.trigger_ratio = 0.0
        consolidator.context_window_tokens = 1000
        consolidator.max_completion_tokens = 100
        consolidator._SAFETY_BUFFER = 0

        session = MagicMock()
        session.last_consolidated = 0
        session.key = "test:key"
        session.messages = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ]
        # budget = 1000 - 100 = 900
        consolidator.estimate_session_prompt_tokens = MagicMock(return_value=(950, "test"))
        consolidator.archive = AsyncMock(return_value=True)

        await consolidator.maybe_consolidate_by_tokens(session)
        consolidator.archive.assert_called_once()

    async def test_negative_budget_returns_early(self, consolidator):
        """If ratio is so low that budget <= 0, return without archiving."""
        consolidator.trigger_ratio = 0.05
        consolidator.context_window_tokens = 1000
        consolidator.max_completion_tokens = 100
        consolidator._SAFETY_BUFFER = 0

        session = MagicMock()
        session.last_consolidated = 0
        session.key = "test:key"
        session.messages = [{"role": "user", "content": "hi"}]
        # trigger_threshold = 50; budget = 50 - 100 = -50 -> <= 0 -> return early
        consolidator.estimate_session_prompt_tokens = MagicMock(return_value=(1000, "test"))
        consolidator.archive = AsyncMock(return_value=True)

        await consolidator.maybe_consolidate_by_tokens(session)
        consolidator.archive.assert_not_called()
