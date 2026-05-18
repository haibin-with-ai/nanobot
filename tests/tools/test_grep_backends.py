"""Tests for ripgrep / system grep / Python fallback in GrepTool."""

from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.agent.tools.search import (
    GrepTool,
    SearchMatch,
    _format_rg_results,
    _grep_available,
    _rg_available,
)


def _make_grep_tool(tmp_path: Path) -> GrepTool:
    return GrepTool(workspace=tmp_path, allowed_dir=tmp_path)


# ── Backend selection tests ───────────────────────────────────────


@pytest.mark.asyncio
async def test_grep_tool_rg_preferred(tmp_path: Path) -> None:
    """When rg is available, the rg backend is used and returns results."""
    if not _rg_available():
        pytest.skip("rg not installed")
    (tmp_path / "hello.py").write_text("hello world\n")
    tool = _make_grep_tool(tmp_path)
    result = await tool.execute("hello", path=str(tmp_path))
    assert "hello.py" in result


@pytest.mark.asyncio
async def test_grep_tool_grep_fallback(tmp_path: Path, monkeypatch) -> None:
    """When rg is unavailable, system grep is used."""
    (tmp_path / "foo.txt").write_text("findme here\n")
    tool = _make_grep_tool(tmp_path)
    monkeypatch.setattr("nanobot.agent.tools.search._rg_available", lambda: False)
    if not _grep_available():
        pytest.skip("system grep not installed")
    result = await tool.execute("findme", path=str(tmp_path))
    assert "foo.txt" in result


@pytest.mark.asyncio
async def test_grep_tool_python_fallback(tmp_path: Path, monkeypatch) -> None:
    """When both rg and grep are unavailable, pure Python fallback is used."""
    (tmp_path / "bar.txt").write_text("pythonfind\n")
    tool = _make_grep_tool(tmp_path)
    monkeypatch.setattr("nanobot.agent.tools.search._rg_available", lambda: False)
    monkeypatch.setattr("nanobot.agent.tools.search._grep_available", lambda: False)
    result = await tool.execute("pythonfind", path=str(tmp_path))
    assert "bar.txt" in result


# ── Format function tests ────────────────────────────────────────


def test_format_rg_results_files_mode(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text("x")
    matches = [
        SearchMatch(file=str(f), line_no=1, text="x"),
        SearchMatch(file=str(f), line_no=2, text="x"),
    ]
    result = _format_rg_results(
        matches, mode="files_with_matches", limit=10, offset=0,
        workspace=tmp_path, search_root=tmp_path, pattern="x",
    )
    assert "a.py" in result


def test_format_rg_results_count_mode(tmp_path: Path) -> None:
    f = tmp_path / "b.py"
    f.write_text("x\nx\n")
    matches = [
        SearchMatch(file=str(f), line_no=1, text="x"),
        SearchMatch(file=str(f), line_no=2, text="x"),
    ]
    result = _format_rg_results(
        matches, mode="count", limit=10, offset=0,
        workspace=tmp_path, search_root=tmp_path, pattern="x",
    )
    assert "b.py: 2" in result
    assert "total matches: 2" in result


def test_format_rg_results_content_mode() -> None:
    matches = [
        SearchMatch(file="/tmp/c.py", line_no=5, text="hello world"),
    ]
    result = _format_rg_results(
        matches, mode="content", limit=10, offset=0,
        workspace=None, search_root=Path("/tmp"), pattern="hello",
    )
    assert "c.py:5" in result
    assert "> 5| hello world" in result


def test_format_rg_results_empty() -> None:
    result = _format_rg_results(
        [], mode="content", limit=10, offset=0,
        workspace=None, search_root=Path("/tmp"), pattern="nope",
    )
    assert "No matches found" in result


def test_format_rg_results_pagination(tmp_path: Path) -> None:
    f = tmp_path / "d.py"
    f.write_text("\n".join(f"line{i}" for i in range(20)))
    matches = [
        SearchMatch(file=str(f), line_no=i, text=f"line{i}")
        for i in range(20)
    ]
    result = _format_rg_results(
        matches, mode="content", limit=5, offset=3,
        workspace=tmp_path, search_root=tmp_path, pattern="line",
    )
    assert "pagination" in result


# ── Integration with real rg ─────────────────────────────────────


@pytest.mark.asyncio
async def test_grep_tool_content_mode_with_rg(tmp_path: Path) -> None:
    """Integration: content mode with real rg."""
    if not _rg_available():
        pytest.skip("rg not installed")
    (tmp_path / "test.py").write_text("alpha\nbeta\ngamma\ndelta\n")
    tool = _make_grep_tool(tmp_path)
    result = await tool.execute(
        "beta", path=str(tmp_path), output_mode="content",
        context_before=1, context_after=1,
    )
    assert "beta" in result
    assert "test.py" in result


@pytest.mark.asyncio
async def test_grep_tool_count_mode_with_rg(tmp_path: Path) -> None:
    """Integration: count mode with real rg."""
    if not _rg_available():
        pytest.skip("rg not installed")
    (tmp_path / "multi.txt").write_text("foo\nbar\nfoo\nbaz\nfoo\n")
    tool = _make_grep_tool(tmp_path)
    result = await tool.execute(
        "foo", path=str(tmp_path), output_mode="count",
    )
    assert "multi.txt" in result
    assert "3" in result


@pytest.mark.asyncio
async def test_grep_tool_fixed_strings(tmp_path: Path) -> None:
    """Fixed strings mode doesn't interpret regex special chars."""
    if not _rg_available():
        pytest.skip("rg not installed")
    (tmp_path / "re.txt").write_text("hello (world)\nother\n")
    tool = _make_grep_tool(tmp_path)
    result = await tool.execute(
        "(world)", path=str(tmp_path), fixed_strings=True,
    )
    assert "re.txt" in result


@pytest.mark.asyncio
async def test_grep_tool_case_insensitive(tmp_path: Path) -> None:
    if not _rg_available():
        pytest.skip("rg not installed")
    (tmp_path / "case.txt").write_text("Hello World\n")
    tool = _make_grep_tool(tmp_path)
    result = await tool.execute(
        "hello world", path=str(tmp_path), case_insensitive=True,
    )
    assert "case.txt" in result


@pytest.mark.asyncio
async def test_grep_tool_glob_filter(tmp_path: Path) -> None:
    if not _rg_available():
        pytest.skip("rg not installed")
    (tmp_path / "yes.py").write_text("target\n")
    (tmp_path / "no.txt").write_text("target\n")
    tool = _make_grep_tool(tmp_path)
    result = await tool.execute(
        "target", path=str(tmp_path), glob="*.py",
    )
    assert "yes.py" in result
    assert "no.txt" not in result


@pytest.mark.asyncio
async def test_grep_tool_type_filter(tmp_path: Path) -> None:
    if not _rg_available():
        pytest.skip("rg not installed")
    (tmp_path / "code.py").write_text("found\n")
    (tmp_path / "doc.md").write_text("found\n")
    tool = _make_grep_tool(tmp_path)
    result = await tool.execute(
        "found", path=str(tmp_path), type="py",
    )
    assert "code.py" in result
    assert "doc.md" not in result
