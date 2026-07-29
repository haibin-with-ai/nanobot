"""Tests for ContextBuilder — system prompt and message assembly."""

from pathlib import Path

import pytest

from nanobot.agent.context import ContextBuilder
from nanobot.runtime_context import RuntimeContextBlock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _builder(tmp_path: Path, **kw) -> ContextBuilder:
    return ContextBuilder(workspace=tmp_path, **kw)


# ---------------------------------------------------------------------------
# _merge_message_content (static)
# ---------------------------------------------------------------------------


class TestMergeMessageContent:
    def test_str_plus_str(self):
        result = ContextBuilder._merge_message_content("hello", "world")
        assert result == "hello\n\nworld"

    def test_empty_left_plus_str(self):
        result = ContextBuilder._merge_message_content("", "world")
        assert result == "world"

    def test_list_plus_list(self):
        left = [{"type": "text", "text": "a"}]
        right = [{"type": "text", "text": "b"}]
        result = ContextBuilder._merge_message_content(left, right)
        assert len(result) == 2
        assert result[0]["text"] == "a"
        assert result[1]["text"] == "b"

    def test_str_plus_list(self):
        right = [{"type": "text", "text": "b"}]
        result = ContextBuilder._merge_message_content("hello", right)
        assert len(result) == 2
        assert result[0]["text"] == "hello"
        assert result[1]["text"] == "b"

    def test_list_plus_str(self):
        left = [{"type": "text", "text": "a"}]
        result = ContextBuilder._merge_message_content(left, "world")
        assert len(result) == 2
        assert result[0]["text"] == "a"
        assert result[1]["text"] == "world"

    def test_none_plus_str(self):
        result = ContextBuilder._merge_message_content(None, "hello")
        assert result == [{"type": "text", "text": "hello"}]

    def test_str_plus_none(self):
        result = ContextBuilder._merge_message_content("hello", None)
        assert result == [{"type": "text", "text": "hello"}]

    def test_none_plus_none(self):
        result = ContextBuilder._merge_message_content(None, None)
        assert result == []

    def test_list_items_not_dicts_wrapped(self):
        result = ContextBuilder._merge_message_content(["raw_item"], None)
        assert result == [{"type": "text", "text": "raw_item"}]


# ---------------------------------------------------------------------------
# _load_bootstrap_files
# ---------------------------------------------------------------------------


class TestLoadBootstrapFiles:
    def test_no_bootstrap_files(self, tmp_path):
        builder = _builder(tmp_path)
        assert builder._load_bootstrap_files() == ""

    def test_empty_bootstrap_files(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("\n", encoding="utf-8")
        builder = _builder(tmp_path)
        assert builder._load_bootstrap_files() == ""

    def test_agents_md(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Be helpful.", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "## AGENTS.md" in result
        assert "Be helpful." in result

    def test_multiple_bootstrap_files(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Rules.", encoding="utf-8")
        (tmp_path / "SOUL.md").write_text("Soul.", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "## AGENTS.md" in result
        assert "## SOUL.md" in result
        assert "Rules." in result
        assert "Soul." in result

    def test_all_bootstrap_files_enter_context_in_required_order(self, tmp_path):
        expected_files = ["SOUL.md", "AGENTS.md", "USER.md", "TOOLS.md"]
        for name in expected_files:
            (tmp_path / name).write_text(f"unique content from {name}", encoding="utf-8")

        result = _builder(tmp_path)._load_bootstrap_files()

        assert ContextBuilder.BOOTSTRAP_FILES == expected_files
        headings = [f"## {name}" for name in expected_files]
        assert [result.index(heading) for heading in headings] == sorted(
            result.index(heading) for heading in headings
        )
        for name in expected_files:
            assert f"unique content from {name}" in result

    def test_utf8_content(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("用中文回复", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "用中文回复" in result

    def test_selected_project_supplies_only_agents_file(self, tmp_path):
        agent_home = tmp_path / "agent-home"
        project = tmp_path / "project"
        agent_home.mkdir()
        project.mkdir()
        (agent_home / "AGENTS.md").write_text("global project rules", encoding="utf-8")
        (agent_home / "SOUL.md").write_text("global soul", encoding="utf-8")
        (agent_home / "USER.md").write_text("global user", encoding="utf-8")
        (project / "AGENTS.md").write_text("selected project rules", encoding="utf-8")
        (project / "SOUL.md").write_text("project soul collision", encoding="utf-8")
        (project / "USER.md").write_text("project user collision", encoding="utf-8")

        result = ContextBuilder(agent_home).build_system_prompt(
            workspace=project,
            include_memory_recent_history=False,
        )

        assert "selected project rules" in result
        assert "global project rules" not in result
        assert "global soul" in result
        assert "global user" in result
        assert "project soul collision" not in result
        assert "project user collision" not in result

    def test_selected_project_without_agents_does_not_fall_back(self, tmp_path):
        agent_home = tmp_path / "agent-home"
        project = tmp_path / "project"
        agent_home.mkdir()
        project.mkdir()
        (agent_home / "AGENTS.md").write_text("default workspace rules", encoding="utf-8")

        result = ContextBuilder(agent_home).build_system_prompt(
            workspace=project,
            include_memory_recent_history=False,
        )

        assert "default workspace rules" not in result

    def test_unmodified_agents_and_user_templates_are_skipped(self, tmp_path):
        from nanobot.utils.helpers import sync_workspace_templates

        sync_workspace_templates(tmp_path, silent=True)

        result = ContextBuilder(tmp_path)._load_bootstrap_files()

        assert "## AGENTS.md" not in result
        assert "## USER.md" not in result
        assert "## SOUL.md" in result

    def test_customized_user_template_is_loaded(self, tmp_path):
        from nanobot.utils.helpers import sync_workspace_templates

        sync_workspace_templates(tmp_path, silent=True)
        (tmp_path / "USER.md").write_text("User prefers Chinese.", encoding="utf-8")

        result = ContextBuilder(tmp_path)._load_bootstrap_files()

        assert "## USER.md" in result
        assert "User prefers Chinese." in result


# ---------------------------------------------------------------------------
# _is_template_content (static)
# ---------------------------------------------------------------------------


class TestIsTemplateContent:
    def test_nonexistent_template_returns_false(self):
        assert ContextBuilder._is_template_content("anything", "nonexistent/path.md") is False

    def test_content_matching_template(self):
        from importlib.resources import files as pkg_files
        tpl = pkg_files("nanobot") / "templates" / "memory" / "MEMORY.md"
        if not tpl.is_file():
            pytest.skip("MEMORY.md template not bundled")
        original = tpl.read_text(encoding="utf-8")
        assert ContextBuilder._is_template_content(original, "memory/MEMORY.md") is True

    def test_modified_content_returns_false(self):
        from importlib.resources import files as pkg_files
        tpl = pkg_files("nanobot") / "templates" / "memory" / "MEMORY.md"
        if not tpl.is_file():
            pytest.skip("MEMORY.md template not bundled")
        assert ContextBuilder._is_template_content("totally different", "memory/MEMORY.md") is False


# ---------------------------------------------------------------------------
# Bundled bootstrap templates
# ---------------------------------------------------------------------------


class TestBundledToolContract:
    def test_tool_contract_balances_general_and_coding_workflows(self):
        from importlib.resources import files as pkg_files

        tpl = pkg_files("nanobot") / "templates" / "agent" / "tool_contract.md"
        content = tpl.read_text(encoding="utf-8")

        assert "## General Tool Contract" in content
        assert "Use the narrowest structured tool" in content
        assert "Do not use `exec` as a universal workaround" in content
        assert "## File and Coding Workflows" in content
        assert "apply_patch" in content
        assert "acceptance criteria into concrete checks" in content
        assert "visual evidence reaches the model" in content
        assert "clear user request as authorization" in content
        assert "Never invent missing records or measurements" in content
        assert "scientific fitting" not in content
        assert "## Web and External Information" in content
        assert "## Messaging and Media" in content
        assert "## Scheduling and Background Work" in content
        assert "pure coding" not in content.lower()

    def test_tool_contract_is_injected_without_workspace_file(self, tmp_path):
        builder = _builder(tmp_path)
        prompt = builder.build_system_prompt()

        assert "# Tool Usage Notes" in prompt
        assert "## General Tool Contract" in prompt
        assert "Do not use `exec` as a universal workaround" in prompt


# ---------------------------------------------------------------------------
# _build_user_content
# ---------------------------------------------------------------------------


class TestBuildUserContent:
    def test_no_media_returns_string(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder._build_user_content("hello", None)
        assert result == "hello"

    def test_empty_media_returns_string(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder._build_user_content("hello", [])
        assert result == "hello"

    def test_nonexistent_media_file_returns_string(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder._build_user_content("hello", ["/nonexistent/image.png"])
        assert result == "hello"

    def test_non_image_file_returns_string(self, tmp_path):
        txt = tmp_path / "doc.txt"
        txt.write_text("not an image", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._build_user_content("hello", [str(txt)])
        assert result == "hello"

    def test_valid_image_returns_list(self, tmp_path):
        png = tmp_path / "test.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        builder = _builder(tmp_path)
        result = builder._build_user_content("hello", [str(png)])
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["type"] == "image_url"
        assert result[0]["image_url"]["url"].startswith("data:image/png;base64,")
        assert result[1]["type"] == "text"
        assert result[1]["text"] == "hello"

    def test_image_meta_includes_path(self, tmp_path):
        png = tmp_path / "test.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        builder = _builder(tmp_path)
        result = builder._build_user_content("hello", [str(png)])
        assert "_meta" in result[0]
        assert "path" in result[0]["_meta"]


# ---------------------------------------------------------------------------
# build_system_prompt
# ---------------------------------------------------------------------------


class TestBuildSystemPrompt:
    def test_returns_nonempty_string(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder.build_system_prompt()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_identity_section(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder.build_system_prompt()
        assert "workspace" in result.lower() or "python" in result.lower()

    def test_selected_project_identity_keeps_agent_data_in_agent_workspace(self, tmp_path):
        agent_home = tmp_path / "agent-home"
        project = tmp_path / "project"
        agent_home.mkdir()
        project.mkdir()

        result = ContextBuilder(agent_home)._get_identity(workspace=project)

        assert f"current project workspace is at: {project.resolve()}" in result
        assert f"agent workspace is at: {agent_home.resolve()}" in result
        assert f"{agent_home.resolve()}/SOUL.md" in result
        assert f"{project.resolve()}/SOUL.md" not in result

    def test_includes_bootstrap_files(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Be helpful and concise.", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder.build_system_prompt()
        assert "Be helpful and concise." in result

    def test_includes_session_summary(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder.build_system_prompt(session_summary="Previous chat about Python.")
        assert "Previous chat about Python." in result
        assert "[Archived Context Summary]" in result

    def test_sections_separated_by_separator(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Rules.", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder.build_system_prompt(session_summary="Summary.")
        assert "\n\n---\n\n" in result

    def test_no_bootstrap_no_summary(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder.build_system_prompt()
        assert "## AGENTS.md" not in result
        assert "[Archived Context Summary]" not in result


# ---------------------------------------------------------------------------
# build_messages
# ---------------------------------------------------------------------------


class TestBuildMessages:
    def test_optional_arguments_are_keyword_only(self, tmp_path):
        builder = _builder(tmp_path)

        with pytest.raises(TypeError):
            builder.build_system_prompt(["legacy-skill"])
        with pytest.raises(TypeError):
            builder.build_messages([], "hello", ["legacy-skill"])

    def test_basic_empty_history(self, tmp_path):
        builder = _builder(tmp_path)
        messages = builder.build_messages([], "hello")
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "hello" in str(messages[1]["content"])

    def test_runtime_context_is_not_injected_by_default(self, tmp_path):
        builder = _builder(tmp_path)
        messages = builder.build_messages([], "hello", channel="cli")
        user_msg = str(messages[-1]["content"])
        assert user_msg == "hello"

    def test_explicit_runtime_context_blocks_are_appended(self, tmp_path):
        builder = _builder(tmp_path)
        messages = builder.build_messages(
            [],
            "please use @zoom tonight",
            runtime_context_blocks=[
                RuntimeContextBlock(
                    source="cli_apps",
                    content="CLI App Attachment: @zoom (installed; tool=run_cli_app).",
                ),
            ],
        )
        user_msg = str(messages[-1]["content"])

        assert "CLI App Attachment: @zoom" in user_msg
        assert "tool=run_cli_app" in user_msg
        assert user_msg.index("please use @zoom tonight") < user_msg.index(
            "CLI App Attachment: @zoom"
        )
        assert messages[-1]["_meta"]["runtime_context"]["sources"] == ["cli_apps"]

    def test_consecutive_same_role_merged(self, tmp_path):
        builder = _builder(tmp_path)
        history = [{"role": "user", "content": "previous user message"}]
        messages = builder.build_messages(history, "new message")
        assert len(messages) == 2  # system + merged user
        assert "previous user message" in str(messages[1]["content"])
        assert "new message" in str(messages[1]["content"])

    def test_different_role_appended(self, tmp_path):
        builder = _builder(tmp_path)
        history = [{"role": "assistant", "content": "previous response"}]
        messages = builder.build_messages(history, "new message")
        assert len(messages) == 3  # system + assistant + user

    def test_media_with_history(self, tmp_path):
        png = tmp_path / "img.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        builder = _builder(tmp_path)
        history = [{"role": "assistant", "content": "see this"}]
        messages = builder.build_messages(history, "check image", media=[str(png)])
        user_msg = messages[-1]["content"]
        assert isinstance(user_msg, list)
        assert any(b.get("type") == "image_url" for b in user_msg)


class TestSoulFirstAndAnchor:
    """SOUL must outrank AGENTS/USER, and its core must survive to the prompt tail."""

    def _workspace(self, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "SOUL.md").write_text(
            "# Soul\n\n" + ("persona detail line\n" * 60) + "\n## Prime Directive\n\n"
            "haibin's time is the scarcest resource.\n",
            encoding="utf-8",
        )
        (tmp_path / "USER.md").write_text("# User\n\nlives in GMT+8\n", encoding="utf-8")
        (tmp_path / "AGENTS.md").write_text("# Agents\n\nreminder rules\n", encoding="utf-8")
        (tmp_path / "TOOLS.md").write_text("# Tools\n\ntmp file rules\n", encoding="utf-8")
        return tmp_path

    def test_bootstrap_order_is_soul_agents_user_tools(self, tmp_path):
        builder = ContextBuilder(self._workspace(tmp_path))
        text = builder._load_bootstrap_files(tmp_path)
        positions = [text.index(f"## {name}") for name in ("SOUL.md", "AGENTS.md", "USER.md", "TOOLS.md")]
        assert positions == sorted(positions)

    def test_soul_anchor_is_appended_once_and_stays_small(self, tmp_path):
        workspace = self._workspace(tmp_path)
        builder = ContextBuilder(workspace)
        prompt = builder.build_system_prompt(include_memory_recent_history=False)
        soul = (workspace / "SOUL.md").read_text(encoding="utf-8")

        anchor = builder._build_soul_anchor(workspace)
        assert anchor, "expected a soul anchor"
        assert "Prime Directive" in anchor
        assert "haibin's time is the scarcest resource." in anchor
        assert len(anchor) <= len(soul) * 0.30
        assert prompt.rstrip().endswith(anchor.rstrip())
        # The anchor repeats the directive on purpose; it must not repeat itself.
        assert prompt.count("# Remember") == 1
        assert prompt.count("## Prime Directive") == 2
        assert "persona detail line" not in anchor

    def test_missing_soul_degrades_without_anchor(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("# Agents\n\nrules\n", encoding="utf-8")
        builder = ContextBuilder(tmp_path)
        assert builder._build_soul_anchor(tmp_path) == ""
        prompt = builder.build_system_prompt(include_memory_recent_history=False)
        assert "Prime Directive" not in prompt

    def test_anchor_reads_the_workspace_it_is_given(self, tmp_path):
        """The override argument must not be silently ignored."""
        other = self._workspace(tmp_path / "other")
        builder = ContextBuilder(tmp_path / "empty")
        assert builder._build_soul_anchor() == ""
        assert "scarcest resource" in builder._build_soul_anchor(other)

    def test_soul_without_prime_directive_yields_no_anchor(self, tmp_path):
        (tmp_path / "SOUL.md").write_text("# Soul\n\njust a voice section\n", encoding="utf-8")
        builder = ContextBuilder(tmp_path)
        assert builder._build_soul_anchor(tmp_path) == ""


class TestDiscordFormatHint:
    def test_discord_hint_forbids_pipe_tables(self, tmp_path):
        builder = ContextBuilder(tmp_path)
        identity = builder._get_identity(channel="discord", workspace=tmp_path)
        assert "Format Hint" in identity
        assert "|" in identity and "table" in identity.lower()

    def test_non_messaging_channel_keeps_upstream_hints(self, tmp_path):
        builder = ContextBuilder(tmp_path)
        assert "Format Hint" in builder._get_identity(channel="email", workspace=tmp_path)
        assert "## External Content" in builder._get_identity(channel="cli", workspace=tmp_path)
