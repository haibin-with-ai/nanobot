"""Tests for GitStore — line_ages() and core git operations."""

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from nanobot.utils.gitstore import GitStore, GitStoreError


@pytest.fixture
def git(tmp_path):
    """Create an initialized GitStore with tracked MEMORY.md."""
    g = GitStore(tmp_path, tracked_files=["MEMORY.md", "SOUL.md"])
    g.init()
    return g


class TestLineAges:
    def test_returns_empty_when_not_initialized(self, tmp_path):
        """line_ages should return [] if the git repo is not initialized."""
        git = GitStore(tmp_path, tracked_files=["MEMORY.md"])
        assert git.line_ages("MEMORY.md") == []

    def test_returns_empty_for_missing_file(self, git):
        """line_ages should return [] for a file that doesn't exist."""
        assert git.line_ages("SOUL.md") == []

    def test_returns_empty_for_empty_file(self, git, tmp_path):
        """line_ages should return [] for an empty tracked file."""
        (tmp_path / "SOUL.md").write_text("", encoding="utf-8")
        git.auto_commit("empty soul")
        assert git.line_ages("SOUL.md") == []

    def test_one_age_per_line(self, git, tmp_path):
        """line_ages should return one entry per line in the file."""
        content = "# Memory\n\n## Section A\n- item 1\n"
        (tmp_path / "MEMORY.md").write_text(content, encoding="utf-8")
        git.auto_commit("initial")
        ages = git.line_ages("MEMORY.md")
        assert len(ages) == len(content.splitlines())

    def test_fresh_lines_have_age_zero(self, git, tmp_path):
        """Lines committed today should have age_days=0."""
        (tmp_path / "MEMORY.md").write_text("## A\n- x\n", encoding="utf-8")
        git.auto_commit("initial")
        ages = git.line_ages("MEMORY.md")
        assert all(a.age_days == 0 for a in ages)

    def test_age_differentiates_across_days(self, git, tmp_path):
        """Lines committed today should show correct age when 'now' is mocked forward."""
        (tmp_path / "MEMORY.md").write_text("## A\n- x\n", encoding="utf-8")
        git.auto_commit("initial")

        future_now = datetime.now(tz=timezone.utc) + timedelta(days=30)
        with patch("nanobot.utils.gitstore.datetime") as mock_dt:
            mock_dt.now.return_value = future_now
            mock_dt.fromtimestamp = datetime.fromtimestamp
            ages = git.line_ages("MEMORY.md")

        assert len(ages) == 2
        assert all(a.age_days == 30 for a in ages)

    def test_annotate_failure_is_explicit(self, git, tmp_path):
        (tmp_path / "MEMORY.md").write_text("important\n", encoding="utf-8")
        git.auto_commit("initial")

        with patch("subprocess.run", side_effect=OSError("broken repo")):
            with pytest.raises(GitStoreError, match="annotation failed"):
                git.line_ages("MEMORY.md")

    def test_partial_edit_only_updates_changed_lines(self, git, tmp_path):
        """Only modified lines should reflect the new commit's timestamp."""
        now = datetime(2026, 5, 1, tzinfo=timezone.utc)
        old = now - timedelta(days=30)

        (tmp_path / "MEMORY.md").write_text(
            "# Memory\n\n## A\n- old\n\n## B\n- keep\n", encoding="utf-8"
        )
        with patch("dulwich.worktree.time.time", return_value=old.timestamp()):
            git.auto_commit("commit1")

        # Only modify section A
        (tmp_path / "MEMORY.md").write_text(
            "# Memory\n\n## A\n- new\n\n## B\n- keep\n", encoding="utf-8"
        )
        with patch("dulwich.worktree.time.time", return_value=now.timestamp()):
            git.auto_commit("commit2")

        with patch("nanobot.utils.gitstore.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.fromtimestamp = datetime.fromtimestamp
            ages = git.line_ages("MEMORY.md")

        lines = (tmp_path / "MEMORY.md").read_text(encoding="utf-8").splitlines()
        assert len(ages) == len(lines)
        age_by_line = {line: age.age_days for line, age in zip(lines, ages, strict=True)}
        assert age_by_line["- new"] == 0
        assert age_by_line["- keep"] == 30


class TestSummarizeWorkingTree:
    """Ground-truth diff summary used to keep Dream audit records honest."""

    def test_empty_when_not_initialized(self, tmp_path):
        git = GitStore(tmp_path, tracked_files=["MEMORY.md"])
        assert git.summarize_working_tree(["MEMORY.md"]) == ""

    def test_empty_when_no_changes(self, git):
        assert git.summarize_working_tree(["MEMORY.md", "SOUL.md"]) == ""

    def test_summarizes_real_change(self, git, tmp_path):
        (tmp_path / "MEMORY.md").write_text("# Memory\n- new fact\n", encoding="utf-8")
        summary = git.summarize_working_tree(["MEMORY.md"])
        assert "MEMORY.md: +2 -0" in summary
        assert "new fact" in summary
        assert "1 file changed, 2 insertions(+), 0 deletions(-)" in summary

    def test_only_reports_requested_paths(self, git, tmp_path):
        # MEMORY.md changes, but we only ask about the unchanged SOUL.md.
        (tmp_path / "MEMORY.md").write_text("changed\n", encoding="utf-8")
        assert git.summarize_working_tree(["SOUL.md"]) == ""

    def test_counts_additions_and_removals(self, git, tmp_path):
        (tmp_path / "MEMORY.md").write_text("# M\n- keep\n- new\n", encoding="utf-8")
        summary = git.summarize_working_tree(["MEMORY.md"])
        assert "MEMORY.md: +3 -0" in summary

    def test_detects_deletion(self, git, tmp_path):
        # File removed from the working tree (must have content first; the
        # fixture's tracked files start empty, so an empty-file delete is a no-op).
        (tmp_path / "MEMORY.md").write_text("has content\n", encoding="utf-8")
        git.auto_commit("add content")
        (tmp_path / "MEMORY.md").unlink()
        summary = git.summarize_working_tree(["MEMORY.md"])
        assert summary  # a removal is still a change
        assert "deletion" in summary

    def test_non_utf8_file_marked_binary_without_replacement_chars(self, git, tmp_path):
        # Invalid UTF-8 must not leak replacement chars into the audit record.
        (tmp_path / "MEMORY.md").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\x00\x01")
        summary = git.summarize_working_tree(["MEMORY.md"])
        assert "MEMORY.md: binary or non-UTF-8 file changed" in summary
        assert "\ufffd" not in summary  # no U+FFFD replacement chars leaked


class TestNestedRepoProtection:
    """Regression tests for GitHub issue #2980: nested repo protection."""

    def test_init_refuses_inside_git_repo(self, tmp_path):
        """init() should detect it's inside an existing git repo and refuse."""
        project = tmp_path / "project"
        project.mkdir()
        (project / ".git").mkdir()

        workspace = project / "workspace"
        workspace.mkdir()

        g = GitStore(workspace, tracked_files=["MEMORY.md"])
        result = g.init()

        assert result is False
        assert not (workspace / ".git").is_dir()

    def test_init_preserves_existing_gitignore(self, tmp_path):
        """init() should preserve existing .gitignore entries and append new ones."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        existing = "*.pyc\n__pycache__/\n"
        (workspace / ".gitignore").write_text(existing, encoding="utf-8")

        g = GitStore(workspace, tracked_files=["MEMORY.md"])
        result = g.init()

        assert result is True
        gitignore = (workspace / ".gitignore").read_text(encoding="utf-8")
        assert "*.pyc" in gitignore
        assert "__pycache__/" in gitignore
        assert "!MEMORY.md" in gitignore
        assert "!.gitignore" in gitignore

    def test_init_no_gitignore_creates_new(self, tmp_path):
        """init() should create .gitignore with Dream content when none exists."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        g = GitStore(workspace, tracked_files=["MEMORY.md"])
        result = g.init()

        assert result is True
        gitignore = (workspace / ".gitignore").read_text(encoding="utf-8")
        expected = g._build_gitignore()
        assert gitignore == expected

    def test_init_gitignore_merge_idempotent(self, tmp_path):
        """init() should not duplicate Dream entries already in .gitignore."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        # Pre-existing .gitignore that already has some Dream entries
        existing = "*.pyc\n/*\n!MEMORY.md\n"
        (workspace / ".gitignore").write_text(existing, encoding="utf-8")

        g = GitStore(workspace, tracked_files=["MEMORY.md"])
        result = g.init()

        assert result is True
        gitignore = (workspace / ".gitignore").read_text(encoding="utf-8")
        # No duplicate lines
        lines = gitignore.splitlines()
        assert lines.count("/*") == 1
        assert lines.count("!MEMORY.md") == 1
        # Existing entry preserved, new Dream entries appended
        assert "*.pyc" in gitignore
        assert "!.gitignore" in gitignore

    def test_init_outside_git_repo_works_normally(self, tmp_path):
        """init() should succeed and create .git when not inside a git repo."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        g = GitStore(workspace, tracked_files=["MEMORY.md"])
        result = g.init()

        assert result is True
        assert (workspace / ".git").is_dir()

    def test_staging_paths_are_absolute_from_workspace(self, tmp_path, monkeypatch):
        """Git operations should not depend on the process working directory."""
        from dulwich import porcelain

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        monkeypatch.chdir(tmp_path)

        git = GitStore(workspace, tracked_files=["MEMORY.md"])

        with patch.object(porcelain, "add", wraps=porcelain.add) as mock_add:
            assert git.init() is True
            assert len(git.log()) == 1

            (workspace / "MEMORY.md").write_text("updated\n", encoding="utf-8")
            assert git.auto_commit("update memory") is not None
            assert len(git.log()) == 2

        assert len(mock_add.call_args_list) == 2
        for call in mock_add.call_args_list:
            staging_paths = [Path(path) for path in call.kwargs["paths"]]
            assert all(path.is_absolute() for path in staging_paths)
            assert all(path.is_relative_to(workspace) for path in staging_paths)

    def test_staging_paths_preserve_symlinks(self, tmp_path):
        """Absolute staging paths should still identify the tracked symlink itself."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        target = tmp_path / "shared-memory.md"
        target.write_text("shared\n", encoding="utf-8")
        link = workspace / "MEMORY.md"
        try:
            link.symlink_to(target)
        except OSError as exc:
            pytest.skip(f"symlinks unavailable: {exc}")

        git = GitStore(workspace, tracked_files=["MEMORY.md"])

        staging_path = Path(git._staging_paths("MEMORY.md")[0])
        assert staging_path == link.absolute()
        assert staging_path.is_symlink()

    def test_init_refuses_inside_git_worktree(self, tmp_path):
        """init() should refuse when the parent checkout is a git worktree."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "README.md").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "-m",
                "init",
            ],
            check=True,
        )
        subprocess.run(["git", "-C", str(repo), "branch", "wt-branch"], check=True)

        worktree = tmp_path / "worktree"
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-q", str(worktree), "wt-branch"],
            check=True,
        )
        assert (worktree / ".git").is_file()

        workspace = worktree / "workspace"
        workspace.mkdir()

        g = GitStore(workspace, tracked_files=["MEMORY.md"])
        result = g.init()

        assert result is False
        assert not (workspace / ".git").exists()


class TestLineAgesPerCommit:
    """行龄必须逐行反映各自最后修改的那次提交，不是整文件一个时间。"""

    @staticmethod
    def _commit_at(repo: Path, days_ago: int, message: str) -> None:
        stamp = datetime.now(tz=timezone.utc) - timedelta(days=days_ago)
        iso = stamp.strftime("%Y-%m-%dT%H:%M:%S+0000")
        env = {
            "GIT_COMMITTER_DATE": iso,
            "GIT_AUTHOR_DATE": iso,
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "PATH": "/usr/bin:/bin",
            "HOME": str(repo),
        }
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
        subprocess.run(
            ["git", "commit", "-m", message, "--no-gpg-sign"],
            cwd=repo,
            check=True,
            env=env,
            capture_output=True,
        )

    def test_old_and_new_lines_get_distinct_ages(self, git, tmp_path):
        (tmp_path / "MEMORY.md").write_text("old line\n", encoding="utf-8")
        self._commit_at(tmp_path, 40, "old")
        (tmp_path / "MEMORY.md").write_text("old line\nnew line\n", encoding="utf-8")
        self._commit_at(tmp_path, 0, "new")

        ages = git.line_ages("MEMORY.md")

        assert len(ages) == 2
        assert ages[0].age_days >= 39
        assert ages[1].age_days == 0

    def test_untouched_line_keeps_its_original_age(self, git, tmp_path):
        """改第二行不该让第一行跟着变新。"""
        (tmp_path / "MEMORY.md").write_text("keep\nedit me\n", encoding="utf-8")
        self._commit_at(tmp_path, 30, "base")
        (tmp_path / "MEMORY.md").write_text("keep\nedited\n", encoding="utf-8")
        self._commit_at(tmp_path, 0, "touch second")

        ages = git.line_ages("MEMORY.md")

        assert ages[0].age_days >= 29
        assert ages[1].age_days == 0

    def test_path_with_spaces(self, tmp_path):
        g = GitStore(tmp_path, tracked_files=["my notes.md"])
        g.init()
        (tmp_path / "my notes.md").write_text("a\nb\n", encoding="utf-8")
        self._commit_at(tmp_path, 5, "spaced")

        ages = g.line_ages("my notes.md")

        assert len(ages) == 2
        assert all(a.age_days >= 4 for a in ages)

    def test_untracked_file_returns_empty(self, git, tmp_path):
        """从未进过 git 的文件没有行龄可言。"""
        (tmp_path / "NOTES.md").write_text("never committed\n", encoding="utf-8")

        assert git.line_ages("NOTES.md") == []

    def test_uncommitted_lines_are_blamed_as_today(self, git, tmp_path):
        """blame 看工作区而非 HEAD：行号必须对齐调用方手上的文件内容。"""
        (tmp_path / "MEMORY.md").write_text("committed\n", encoding="utf-8")
        self._commit_at(tmp_path, 20, "base")
        (tmp_path / "MEMORY.md").write_text("committed\ndraft\n", encoding="utf-8")

        ages = git.line_ages("MEMORY.md")

        assert len(ages) == 2
        assert ages[0].age_days >= 19
        assert ages[1].age_days == 0

    @pytest.mark.parametrize(
        "message",
        [
            "a" * 32 + " tail of a normal commit summary",
            "这是一个包含非 ASCII 字符的提交摘要",
            "subject\n\nbody line one\nbody line two",
        ],
        ids=["long-summary", "non-ascii-summary", "multiline-message"],
    )
    def test_commit_metadata_cannot_be_mistaken_for_blame_header(
        self, git, tmp_path, message
    ):
        (tmp_path / "MEMORY.md").write_text("remember this\n", encoding="utf-8")
        self._commit_at(tmp_path, 0, message)

        ages = git.line_ages("MEMORY.md")

        assert len(ages) == 1
        assert ages[0].age_days == 0

    def test_blame_timeout_reports_path_and_limit(self, git, tmp_path):
        (tmp_path / "MEMORY.md").write_text("important\n", encoding="utf-8")
        git.auto_commit("initial")
        tracked = subprocess.CompletedProcess([], 0, stdout="MEMORY.md\n", stderr="")

        with patch(
            "nanobot.utils.gitstore.subprocess.run",
            side_effect=[tracked, subprocess.TimeoutExpired("git blame", 30)],
        ) as run:
            with pytest.raises(
                GitStoreError, match=r"MEMORY\.md.*30.*seconds"
            ):
                git.line_ages("MEMORY.md")

        assert run.call_args_list[1].kwargs["timeout"] == 30

    def test_blame_failure_is_not_faked_as_success(self, git, tmp_path):
        """blame 挂了要抛错，不能返回空列表让调用方以为文件没内容。"""
        (tmp_path / "MEMORY.md").write_text("a\n", encoding="utf-8")
        git.auto_commit("initial")

        with patch("subprocess.run", side_effect=OSError("git missing")):
            with pytest.raises(GitStoreError):
                git.line_ages("MEMORY.md")
