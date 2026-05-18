"""Search tools: grep."""

from __future__ import annotations

import asyncio
import fnmatch
import functools
import json
import os
import re
import shutil
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, TypeVar

from loguru import logger

from nanobot.agent.tools.filesystem import ListDirTool, _FsTool

_DEFAULT_HEAD_LIMIT = 250
T = TypeVar("T")
_TYPE_GLOB_MAP = {
    "py": ("*.py", "*.pyi"),
    "python": ("*.py", "*.pyi"),
    "js": ("*.js", "*.jsx", "*.mjs", "*.cjs"),
    "ts": ("*.ts", "*.tsx", "*.mts", "*.cts"),
    "tsx": ("*.tsx",),
    "jsx": ("*.jsx",),
    "json": ("*.json",),
    "md": ("*.md", "*.mdx"),
    "markdown": ("*.md", "*.mdx"),
    "go": ("*.go",),
    "rs": ("*.rs",),
    "rust": ("*.rs",),
    "java": ("*.java",),
    "sh": ("*.sh", "*.bash"),
    "yaml": ("*.yaml", "*.yml"),
    "yml": ("*.yaml", "*.yml"),
    "toml": ("*.toml",),
    "sql": ("*.sql",),
    "html": ("*.html", "*.htm"),
    "css": ("*.css", "*.scss", "*.sass"),
}


class SearchBackendError(Exception):
    """Raised when an external search backend (rg/grep) fails."""


@dataclass(frozen=True)
class SearchMatch:
    """A single search match from any backend."""
    file: str
    line_no: int
    text: str
    context_before: list[str] | None = None
    context_after: list[str] | None = None


@functools.lru_cache(maxsize=1)
def _rg_available() -> bool:
    return shutil.which("rg") is not None


@functools.lru_cache(maxsize=1)
def _grep_available() -> bool:
    return shutil.which("grep") is not None


async def _run_rg(
    target: Path,
    pattern: str,
    *,
    glob_pattern: str | None = None,
    type_key: str | None = None,
    case_insensitive: bool = False,
    fixed_strings: bool = False,
    context_before: int = 0,
    context_after: int = 0,
    max_filesize: int = 2_000_000,
    timeout: float = 60.0,
) -> list[SearchMatch]:
    """Run ripgrep and return parsed matches."""
    cmd: list[str] = ["rg", "--json", f"--max-filesize={max_filesize}"]
    if case_insensitive:
        cmd.append("-i")
    if fixed_strings:
        cmd.append("-F")
    if context_before:
        cmd.extend(["-B", str(context_before)])
    if context_after:
        cmd.extend(["-A", str(context_after)])
    # Glob / type filtering
    if glob_pattern:
        cmd.extend(["-g", glob_pattern])
    elif type_key:
        globs = _TYPE_GLOB_MAP.get(type_key.strip().lower())
        if globs:
            for g in globs:
                cmd.extend(["-g", g])
        else:
            cmd.extend(["-g", f"*.{type_key}"])
    # Ignore common noise dirs
    for d in ("node_modules", ".git", "__pycache__", ".venv", "venv"):
        cmd.extend(["--glob", f"!{d}/"])
    cmd.extend(["--", pattern, str(target)])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        raise SearchBackendError("ripgrep timed out")
    except FileNotFoundError:
        raise SearchBackendError("rg binary not found")

    # rg exit code: 0 = matches, 1 = no matches, 2+ = error
    if proc.returncode and proc.returncode >= 2:
        raise SearchBackendError(f"rg error (code {proc.returncode}): {stderr.decode(errors='replace')[:500]}")

    matches: list[SearchMatch] = []
    ctx_before_buf: list[str] = []
    for raw_line in stdout.decode(errors="replace").splitlines():
        try:
            obj = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        msg_type = obj.get("type")
        if msg_type == "match":
            data = obj["data"]
            path_text = data.get("path", {}).get("text", "")
            line_number = data.get("line_number", 0)
            line_text = data.get("lines", {}).get("text", "").rstrip("\n")
            matches.append(SearchMatch(
                file=path_text,
                line_no=line_number,
                text=line_text,
                context_before=ctx_before_buf.copy() if ctx_before_buf else None,
            ))
            ctx_before_buf.clear()
        elif msg_type == "context":
            data = obj["data"]
            line_text = data.get("lines", {}).get("text", "").rstrip("\n")
            ctx_before_buf.append(line_text)
    return matches


async def _run_system_grep(
    target: Path,
    pattern: str,
    *,
    glob_pattern: str | None = None,
    type_key: str | None = None,
    case_insensitive: bool = False,
    fixed_strings: bool = False,
    context_before: int = 0,
    context_after: int = 0,
    timeout: float = 60.0,
) -> list[SearchMatch]:
    """Run system grep and return parsed matches."""
    cmd: list[str] = ["grep", "-rn"]
    if case_insensitive:
        cmd.append("-i")
    if fixed_strings:
        cmd.append("-F")
    if context_before:
        cmd.extend(["-B", str(context_before)])
    if context_after:
        cmd.extend(["-A", str(context_after)])
    # Include filters
    if glob_pattern:
        cmd.extend(["--include", glob_pattern])
    elif type_key:
        globs = _TYPE_GLOB_MAP.get(type_key.strip().lower())
        if globs:
            for g in globs:
                cmd.extend(["--include", g])
        else:
            cmd.extend(["--include", f"*.{type_key}"])
    # Exclude common noise dirs
    for d in ("node_modules", ".git", "__pycache__", ".venv", "venv"):
        cmd.extend(["--exclude-dir", d])
    cmd.extend(["--", pattern, str(target)])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        raise SearchBackendError("grep timed out")
    except FileNotFoundError:
        raise SearchBackendError("grep binary not found")

    # grep exit code: 0 = matches, 1 = no matches, 2+ = error
    if proc.returncode and proc.returncode >= 2:
        raise SearchBackendError(f"grep error (code {proc.returncode}): {stderr.decode(errors='replace')[:500]}")

    matches: list[SearchMatch] = []
    for line in stdout.decode(errors="replace").splitlines():
        # Format: file:line_no:content (with -n)
        # Context lines may use file-line_no-content
        parts = line.split(":", 2)
        if len(parts) >= 3:
            try:
                matches.append(SearchMatch(
                    file=parts[0],
                    line_no=int(parts[1]),
                    text=parts[2],
                ))
            except ValueError:
                continue
    return matches


def _format_rg_results(
    matches: list[SearchMatch],
    *,
    mode: str,
    limit: int | None,
    offset: int,
    workspace: Path | None,
    search_root: Path,
    pattern: str,
) -> str:
    """Format SearchMatch list into the output string."""
    if not matches:
        return f"No matches found for pattern '{pattern}' in {search_root}"

    def _rel(filepath: str) -> str:
        p = Path(filepath)
        if workspace:
            with suppress(ValueError):
                return p.relative_to(workspace).as_posix()
        with suppress(ValueError):
            return p.relative_to(search_root).as_posix()
        return filepath

    if mode == "files_with_matches":
        seen: dict[str, float] = {}
        for m in matches:
            if m.file not in seen:
                try:
                    seen[m.file] = Path(m.file).stat().st_mtime
                except OSError:
                    seen[m.file] = 0.0
        ordered = sorted(seen, key=lambda f: (-seen[f], f))
        display = [_rel(f) for f in ordered]
        paged, truncated = _paginate(display, limit, offset)
        result = "\n".join(paged)
        notes: list[str] = []
        pn = _pagination_note(limit, offset, truncated)
        if pn:
            notes.append(pn)
        if notes:
            result += "\n\n" + "\n".join(notes)
        return result

    if mode == "count":
        counts: dict[str, int] = {}
        mtimes: dict[str, float] = {}
        for m in matches:
            counts[m.file] = counts.get(m.file, 0) + 1
            if m.file not in mtimes:
                try:
                    mtimes[m.file] = Path(m.file).stat().st_mtime
                except OSError:
                    mtimes[m.file] = 0.0
        ordered = sorted(counts, key=lambda f: (-mtimes.get(f, 0), f))
        display = [f"{_rel(f)}: {counts[f]}" for f in ordered]
        paged, truncated = _paginate(display, limit, offset)
        result = "\n".join(paged)
        notes = []
        pn = _pagination_note(limit, offset, truncated)
        if pn:
            notes.append(pn)
        notes.append(f"(total matches: {sum(counts.values())} in {len(counts)} files)")
        result += "\n\n" + "\n".join(notes)
        return result

    # content mode
    blocks: list[str] = []
    for m in matches:
        display_path = _rel(m.file)
        block_lines = [f"{display_path}:{m.line_no}"]
        if m.context_before:
            start_no = m.line_no - len(m.context_before)
            for i, ctx in enumerate(m.context_before):
                block_lines.append(f"  {start_no + i}| {ctx}")
        block_lines.append(f"> {m.line_no}| {m.text}")
        if m.context_after:
            for i, ctx in enumerate(m.context_after):
                block_lines.append(f"  {m.line_no + 1 + i}| {ctx}")
        blocks.append("\n".join(block_lines))

    paged, truncated = _paginate(blocks, limit, offset)
    result = "\n\n".join(paged)
    # Truncate oversized output
    if len(result) > 128_000:
        result = result[:128_000]
    notes = []
    pn = _pagination_note(limit, offset, truncated)
    if pn:
        notes.append(pn)
    if notes:
        result += "\n\n" + "\n".join(notes)
    return result


def _normalize_pattern(pattern: str) -> str:
    return pattern.strip().replace("\\", "/")


def _match_glob(rel_path: str, name: str, pattern: str) -> bool:
    normalized = _normalize_pattern(pattern)
    if not normalized:
        return False
    if "/" in normalized or normalized.startswith("**"):
        return PurePosixPath(rel_path).match(normalized)
    return fnmatch.fnmatch(name, normalized)


def _is_binary(raw: bytes) -> bool:
    if b"\x00" in raw:
        return True
    sample = raw[:4096]
    if not sample:
        return False
    non_text = sum(byte < 9 or 13 < byte < 32 for byte in sample)
    return (non_text / len(sample)) > 0.2


def _paginate(items: list[T], limit: int | None, offset: int) -> tuple[list[T], bool]:
    if limit is None:
        return items[offset:], False
    sliced = items[offset : offset + limit]
    truncated = len(items) > offset + limit
    return sliced, truncated


def _pagination_note(limit: int | None, offset: int, truncated: bool) -> str | None:
    if truncated:
        if limit is None:
            return f"(pagination: offset={offset})"
        return f"(pagination: limit={limit}, offset={offset})"
    if offset > 0:
        return f"(pagination: offset={offset})"
    return None


def _matches_type(name: str, file_type: str | None) -> bool:
    if not file_type:
        return True
    lowered = file_type.strip().lower()
    if not lowered:
        return True
    patterns = _TYPE_GLOB_MAP.get(lowered, (f"*.{lowered}",))
    return any(fnmatch.fnmatch(name.lower(), pattern.lower()) for pattern in patterns)


class _SearchTool(_FsTool):
    _IGNORE_DIRS = set(ListDirTool._IGNORE_DIRS)

    def _display_path(self, target: Path, root: Path) -> str:
        if self._workspace:
            with suppress(ValueError):
                return target.relative_to(self._workspace).as_posix()
        return target.relative_to(root).as_posix()

    def _iter_files(self, root: Path) -> Iterable[Path]:
        if root.is_file():
            yield root
            return

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in self._IGNORE_DIRS)
            current = Path(dirpath)
            for filename in sorted(filenames):
                yield current / filename


class GrepTool(_SearchTool):
    """Search file contents using a regex-like pattern."""
    _scopes = {"core", "subagent"}

    _MAX_RESULT_CHARS = 128_000
    _MAX_FILE_BYTES = 2_000_000

    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return (
            "Search file contents with a regex pattern. "
            "Default output_mode is files_with_matches (file paths only); "
            "use content mode for matching lines with context. "
            "Skips binary and files >2 MB. Supports glob/type filtering."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex or plain text pattern to search for",
                    "minLength": 1,
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search in (default '.')",
                },
                "glob": {
                    "type": "string",
                    "description": "Optional file filter, e.g. '*.py' or 'tests/**/test_*.py'",
                },
                "type": {
                    "type": "string",
                    "description": "Optional file type shorthand, e.g. 'py', 'ts', 'md', 'json'",
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Case-insensitive search (default false)",
                },
                "fixed_strings": {
                    "type": "boolean",
                    "description": "Treat pattern as plain text instead of regex (default false)",
                },
                "output_mode": {
                    "type": "string",
                    "enum": ["content", "files_with_matches", "count"],
                    "description": (
                        "content: matching lines with optional context; "
                        "files_with_matches: only matching file paths; "
                        "count: matching line counts per file. "
                        "Default: files_with_matches"
                    ),
                },
                "context_before": {
                    "type": "integer",
                    "description": "Number of lines of context before each match",
                    "minimum": 0,
                    "maximum": 20,
                },
                "context_after": {
                    "type": "integer",
                    "description": "Number of lines of context after each match",
                    "minimum": 0,
                    "maximum": 20,
                },
                "max_matches": {
                    "type": "integer",
                    "description": (
                        "Legacy alias for head_limit in content mode"
                    ),
                    "minimum": 1,
                    "maximum": 1000,
                },
                "max_results": {
                    "type": "integer",
                    "description": (
                        "Legacy alias for head_limit in files_with_matches or count mode"
                    ),
                    "minimum": 1,
                    "maximum": 1000,
                },
                "head_limit": {
                    "type": "integer",
                    "description": (
                        "Maximum number of results to return. In content mode this limits "
                        "matching line blocks; in other modes it limits file entries. "
                        "Default 250"
                    ),
                    "minimum": 0,
                    "maximum": 1000,
                },
                "offset": {
                    "type": "integer",
                    "description": "Skip the first N results before applying head_limit",
                    "minimum": 0,
                    "maximum": 100000,
                },
            },
            "required": ["pattern"],
        }

    @staticmethod
    def _format_block(
        display_path: str,
        lines: list[str],
        match_line: int,
        before: int,
        after: int,
    ) -> str:
        start = max(1, match_line - before)
        end = min(len(lines), match_line + after)
        block = [f"{display_path}:{match_line}"]
        for line_no in range(start, end + 1):
            marker = ">" if line_no == match_line else " "
            block.append(f"{marker} {line_no}| {lines[line_no - 1]}")
        return "\n".join(block)

    async def execute(
        self,
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        type: str | None = None,
        case_insensitive: bool = False,
        fixed_strings: bool = False,
        output_mode: str = "files_with_matches",
        context_before: int = 0,
        context_after: int = 0,
        max_matches: int | None = None,
        max_results: int | None = None,
        head_limit: int | None = None,
        offset: int = 0,
        **kwargs: Any,
    ) -> str:
        try:
            target = self._resolve(path or ".")
            if not target.exists():
                return f"Error: Path not found: {path}"
            if not (target.is_dir() or target.is_file()):
                return f"Error: Unsupported path: {path}"

            # Resolve limit from head_limit / legacy aliases
            if head_limit is not None:
                limit = None if head_limit == 0 else head_limit
            elif output_mode == "content" and max_matches is not None:
                limit = max_matches
            elif output_mode != "content" and max_results is not None:
                limit = max_results
            else:
                limit = _DEFAULT_HEAD_LIMIT

            # Fast path: try rg, then system grep, then fall through to Python
            root = target if target.is_dir() else target.parent
            for backend in ("rg", "grep"):
                if backend == "rg" and not _rg_available():
                    continue
                if backend == "grep" and not _grep_available():
                    continue
                try:
                    runner = _run_rg if backend == "rg" else _run_system_grep
                    matches = await runner(
                        target, pattern,
                        glob_pattern=glob,
                        type_key=type,
                        case_insensitive=case_insensitive,
                        fixed_strings=fixed_strings,
                        context_before=context_before,
                        context_after=context_after,
                    )
                    return _format_rg_results(
                        matches,
                        mode=output_mode,
                        limit=limit,
                        offset=offset,
                        workspace=self._workspace,
                        search_root=root,
                        pattern=pattern,
                    )
                except SearchBackendError as e:
                    logger.debug("Search backend {} failed: {}, trying next", backend, e)
                    continue

            # Fallback: pure Python implementation
            logger.debug("All external search backends unavailable, using pure Python")
            flags = re.IGNORECASE if case_insensitive else 0
            try:
                needle = re.escape(pattern) if fixed_strings else pattern
                regex = re.compile(needle, flags)
            except re.error as e:
                return f"Error: invalid regex pattern: {e}"

            blocks: list[str] = []
            result_chars = 0
            seen_content_matches = 0
            truncated = False
            size_truncated = False
            skipped_binary = 0
            skipped_large = 0
            matching_files: list[str] = []
            counts: dict[str, int] = {}
            file_mtimes: dict[str, float] = {}

            for file_path in self._iter_files(target):
                rel_path = file_path.relative_to(root).as_posix()
                if glob and not _match_glob(rel_path, file_path.name, glob):
                    continue
                if not _matches_type(file_path.name, type):
                    continue

                raw = file_path.read_bytes()
                if len(raw) > self._MAX_FILE_BYTES:
                    skipped_large += 1
                    continue
                if _is_binary(raw):
                    skipped_binary += 1
                    continue
                try:
                    mtime = file_path.stat().st_mtime
                except OSError:
                    mtime = 0.0
                try:
                    content = raw.decode("utf-8")
                except UnicodeDecodeError:
                    skipped_binary += 1
                    continue

                lines = content.splitlines()
                display_path = self._display_path(file_path, root)
                file_had_match = False
                for idx, line in enumerate(lines, start=1):
                    if not regex.search(line):
                        continue
                    file_had_match = True

                    if output_mode == "count":
                        counts[display_path] = counts.get(display_path, 0) + 1
                        continue
                    if output_mode == "files_with_matches":
                        if display_path not in matching_files:
                            matching_files.append(display_path)
                            file_mtimes[display_path] = mtime
                        break

                    seen_content_matches += 1
                    if seen_content_matches <= offset:
                        continue
                    if limit is not None and len(blocks) >= limit:
                        truncated = True
                        break
                    block = self._format_block(
                        display_path,
                        lines,
                        idx,
                        context_before,
                        context_after,
                    )
                    extra_sep = 2 if blocks else 0
                    if result_chars + extra_sep + len(block) > self._MAX_RESULT_CHARS:
                        size_truncated = True
                        break
                    blocks.append(block)
                    result_chars += extra_sep + len(block)
                if output_mode == "count" and file_had_match:
                    if display_path not in matching_files:
                        matching_files.append(display_path)
                        file_mtimes[display_path] = mtime
                if output_mode in {"count", "files_with_matches"} and file_had_match:
                    continue
                if truncated or size_truncated:
                    break

            if output_mode == "files_with_matches":
                if not matching_files:
                    result = f"No matches found for pattern '{pattern}' in {path}"
                else:
                    ordered_files = sorted(
                        matching_files,
                        key=lambda name: (-file_mtimes.get(name, 0.0), name),
                    )
                    paged, truncated = _paginate(ordered_files, limit, offset)
                    result = "\n".join(paged)
            elif output_mode == "count":
                if not counts:
                    result = f"No matches found for pattern '{pattern}' in {path}"
                else:
                    ordered_files = sorted(
                        matching_files,
                        key=lambda name: (-file_mtimes.get(name, 0.0), name),
                    )
                    ordered, truncated = _paginate(ordered_files, limit, offset)
                    lines = [f"{name}: {counts[name]}" for name in ordered]
                    result = "\n".join(lines)
            else:
                if not blocks:
                    result = f"No matches found for pattern '{pattern}' in {path}"
                else:
                    result = "\n\n".join(blocks)

            notes: list[str] = []
            if output_mode == "content" and truncated:
                notes.append(
                    f"(pagination: limit={limit}, offset={offset})"
                )
            elif output_mode == "content" and size_truncated:
                notes.append("(output truncated due to size)")
            elif truncated and output_mode in {"count", "files_with_matches"}:
                notes.append(
                    f"(pagination: limit={limit}, offset={offset})"
                )
            elif output_mode in {"count", "files_with_matches"} and offset > 0:
                notes.append(f"(pagination: offset={offset})")
            elif output_mode == "content" and offset > 0 and blocks:
                notes.append(f"(pagination: offset={offset})")
            if skipped_binary:
                notes.append(f"(skipped {skipped_binary} binary/unreadable files)")
            if skipped_large:
                notes.append(f"(skipped {skipped_large} large files)")
            if output_mode == "count" and counts:
                notes.append(
                    f"(total matches: {sum(counts.values())} in {len(counts)} files)"
                )
            if notes:
                result += "\n\n" + "\n".join(notes)
            return result
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error searching files: {e}"
