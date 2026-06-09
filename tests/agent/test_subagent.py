"""Tests for SubagentManager."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Pre-import tool configs to resolve forward refs before ToolsConfig is imported
from nanobot.agent.tools.image_generation import ImageGenerationToolConfig  # noqa: F401
from nanobot.agent.tools.self import MyToolConfig  # noqa: F401
from nanobot.agent.tools.shell import ExecToolConfig  # noqa: F401
from nanobot.agent.tools.web import WebFetchConfig, WebSearchConfig, WebToolsConfig  # noqa: F401
from nanobot.config.schema import ToolsConfig

ToolsConfig.model_rebuild()

from nanobot.agent.subagent import SubagentManager
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider


class _FakeSubagentManager:
    """Minimal fake to test _build_subagent_prompt without hitting ToolsConfig."""

    def __init__(self, workspace: Path, disabled_skills: set | None = None) -> None:
        self.workspace = workspace
        self.disabled_skills = disabled_skills or set()


def _make_manager(workspace: Path) -> SubagentManager:
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test"
    return SubagentManager(
        provider=provider,
        workspace=workspace,
        bus=MessageBus(),
        model="test",
        max_tool_result_chars=16_000,
    )


@pytest.mark.asyncio
async def test_subagent_uses_tool_loader():
    """Verify subagent registers tools via ToolLoader, not hard-coded imports."""
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test"
    sm = SubagentManager(
        provider=provider,
        workspace=Path("/tmp"),
        bus=MessageBus(),
        model="test",
        max_tool_result_chars=16_000,
    )
    tools = sm._build_tools()
    assert tools.has("read_file")
    assert tools.has("write_file")
    assert not tools.has("message")
    assert not tools.has("spawn")


@pytest.mark.asyncio
async def test_subagent_build_tools_isolates_file_read_state(tmp_path):
    """Each spawned subagent needs a fresh file-state cache."""
    (tmp_path / "note.txt").write_text("hello\n", encoding="utf-8")
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test"
    sm = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        model="test",
        max_tool_result_chars=16_000,
    )

    first_read = sm._build_tools().get("read_file")
    second_read = sm._build_tools().get("read_file")

    assert first_read is not second_read
    assert (await first_read.execute(path="note.txt")).startswith("1| hello")
    second_result = await second_read.execute(path="note.txt")
    assert second_result.startswith("1| hello")
    assert "File unchanged" not in second_result


def test_subagent_bootstrap_includes_soul_and_tools(tmp_path):
    (tmp_path / "SOUL.md").write_text("Be kind. Be sharp.", encoding="utf-8")
    (tmp_path / "TOOLS.md").write_text("Tool rules.", encoding="utf-8")
    sm = _FakeSubagentManager(tmp_path)
    prompt = SubagentManager._build_subagent_prompt(sm)
    assert "## SOUL.md" in prompt
    assert "Be kind. Be sharp." in prompt
    assert "## TOOLS.md" in prompt
    assert "Tool rules." in prompt


def test_subagent_bootstrap_skips_missing_files(tmp_path):
    (tmp_path / "SOUL.md").write_text("Only soul.", encoding="utf-8")
    sm = _FakeSubagentManager(tmp_path)
    prompt = SubagentManager._build_subagent_prompt(sm)
    assert "## SOUL.md" in prompt
    assert "Only soul." in prompt
    assert "## TOOLS.md" not in prompt


def test_subagent_no_soul_anchor(tmp_path):
    sm = _FakeSubagentManager(tmp_path)
    prompt = SubagentManager._build_subagent_prompt(sm)
    assert "## SOUL.md" not in prompt
    assert "## TOOLS.md" not in prompt


def test_subagent_default_concurrency_limit(tmp_path):
    """Without an explicit value, fall back to AgentDefaults (1)."""
    sm = _make_manager(tmp_path)
    assert sm.max_concurrent_subagents == 1


def test_subagent_concurrency_limit_from_config(tmp_path):
    """An explicit max_concurrent_subagents must be honored, not ignored."""
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test"
    sm = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        model="test",
        max_tool_result_chars=16_000,
        max_concurrent_subagents=4,
    )
    assert sm.max_concurrent_subagents == 4
