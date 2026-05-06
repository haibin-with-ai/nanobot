"""Search tools: grep and glob."""

from __future__ import annotations

import fnmatch
import os
import json
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, TypeVar

from nanobot.agent.tools.filesystem import ListDirTool, _FsTool

_DEFAULT_HEAD_LIMIT = 250
T = TypeVar("T")
_RG_BIN: str | None = shutil.which("rg")

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
            try:
                return target.relative_to(self._workspace).as_posix()
            except ValueError:
                pass
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

    def _iter_entries(
        self,
        root: Path,
        *,
        include_files: bool,
        include_dirs: bool,
    ) -> Iterable[Path]:
        if root.is_file():
            if include_files:
                yield root
            return

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in self._IGNORE_DIRS)
            current = Path(dirpath)
            if include_dirs:
                for dirname in dirnames:
                    yield current / dirname
            if include_files:
                for filename in sorted(filenames):
                    yield current / filename


class GlobTool(_SearchTool):
    """Find files matching a glob pattern."""

    @property
    def name(self) -> str:
        return "glob"

    @property
    def description(self) -> str:
        return (
            "Find files matching a glob pattern. "
            "Simple patterns like '*.py' match by filename recursively."
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
                    "description": "Glob pattern to match, e.g. '*.py' or 'tests/**/test_*.py'",
                    "minLength": 1,
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search from (default '.')",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Legacy alias for head_limit",
                    "minimum": 1,
                    "maximum": 1000,
                },
                "head_limit": {
                    "type": "integer",
                    "description": "Maximum number of matches to return (default 250)",
                    "minimum": 0,
                    "maximum": 1000,
                },
                "offset": {
                    "type": "integer",
                    "description": "Skip the first N matching entries before returning results",
                    "minimum": 0,
                    "maximum": 100000,
                },
                "entry_type": {
                    "type": "string",
                    "enum": ["files", "dirs", "both"],
                    "description": "Whether to match files, directories, or both (default files)",
                },
            },
            "required": ["pattern"],
        }

    async def execute(
        self,
        pattern: str,
        path: str = ".",
        max_results: int | None = None,
        head_limit: int | None = None,
        offset: int = 0,
        entry_type: str = "files",
        **kwargs: Any,
    ) -> str:
        try:
            root = self._resolve(path or ".")
            if not root.exists():
                return f"Error: Path not found: {path}"
            if not root.is_dir():
                return f"Error: Not a directory: {path}"

            if head_limit is not None:
                limit = None if head_limit == 0 else head_limit
            elif max_results is not None:
                limit = max_results
            else:
                limit = _DEFAULT_HEAD_LIMIT
            include_files = entry_type in {"files", "both"}
            include_dirs = entry_type in {"dirs", "both"}
            matches: list[tuple[str, float]] = []
            for entry in self._iter_entries(
                root,
                include_files=include_files,
                include_dirs=include_dirs,
            ):
                rel_path = entry.relative_to(root).as_posix()
                if _match_glob(rel_path, entry.name, pattern):
                    display = self._display_path(entry, root)
                    if entry.is_dir():
                        display += "/"
                    try:
                        mtime = entry.stat().st_mtime
                    except OSError:
                        mtime = 0.0
                    matches.append((display, mtime))

            if not matches:
                return f"No paths matched pattern '{pattern}' in {path}"

            matches.sort(key=lambda item: (-item[1], item[0]))
            ordered = [name for name, _ in matches]
            paged, truncated = _paginate(ordered, limit, offset)
            result = "\n".join(paged)
            if note := _pagination_note(limit, offset, truncated):
                result += f"\n\n{note}"
            return result
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error finding files: {e}"


class GrepTool(_SearchTool):
    """Search file contents using a regex-like pattern."""
    _MAX_RESULT_CHARS = 128_000
    _MAX_FILE_BYTES = 2_000_000

    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return (
            "Search file contents with a regex-like pattern. "
            "Supports optional glob filtering, structured output modes, "
            "type filters, pagination, and surrounding context lines."
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

    # ---- ripgrep fast path ------------------------------------------------

    def _build_rg_cmd(
        self,
        pattern: str,
        target: Path,
        *,
        glob_filter: str | None,
        type_filter: str | None,
        case_insensitive: bool,
        fixed_strings: bool,
        output_mode: str,
    ) -> list[str]:
        """Translate grep-tool parameters into an rg command line."""
        assert _RG_BIN is not None
        cmd: list[str] = [
            _RG_BIN,
            "--hidden",
            "--max-filesize", str(self._MAX_FILE_BYTES),
        ]
        for d in self._IGNORE_DIRS:
            cmd.extend(["--glob", f"!{d}/"])
        if case_insensitive:
            cmd.append("-i")
        if fixed_strings:
            cmd.append("-F")
        # File type filter (AND-ed with glob via --type-add/--type)
        if type_filter:
            lowered = type_filter.strip().lower()
            pats = _TYPE_GLOB_MAP.get(lowered, (f"*.{lowered}",))
            for p in pats:
                cmd.extend(["--type-add", f"custom:{p}"])
            cmd.extend(["--type", "custom"])
        if glob_filter:
            cmd.extend(["--glob", glob_filter])
        # Output mode
        if output_mode == "files_with_matches":
            cmd.append("-l")
        elif output_mode == "count":
            cmd.append("-c")
        else:  # content – use JSON for reliable parsing
            cmd.append("--json")
        cmd.extend(["--", pattern, str(target)])
        return cmd

    def _run_rg(
        self,
        pattern: str,
        target: Path,
        display_root: str,
        *,
        glob_filter: str | None,
        type_filter: str | None,
        case_insensitive: bool,
        fixed_strings: bool,
        output_mode: str,
        context_before: int,
        context_after: int,
        limit: int | None,
        offset: int,
    ) -> str:
        """Run ripgrep and return formatted output matching the Python path."""
        cmd = self._build_rg_cmd(
            pattern, target,
            glob_filter=glob_filter, type_filter=type_filter,
            case_insensitive=case_insensitive, fixed_strings=fixed_strings,
            output_mode=output_mode,
        )
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode == 2:
            raise RuntimeError(proc.stderr.strip())
        stdout = proc.stdout
        root = target if target.is_dir() else target.parent

        # ---------- files_with_matches ----------
        if output_mode == "files_with_matches":
            paths = [p.strip() for p in stdout.splitlines() if p.strip()]
            if not paths:
                return f"No matches found for pattern '{pattern}' in {display_root}"
            entries: list[tuple[str, float]] = []
            for p in paths:
                fp = Path(p)
                display = self._display_path(fp, root)
                try:
                    mtime = fp.stat().st_mtime
                except OSError:
                    mtime = 0.0
                entries.append((display, mtime))
            entries.sort(key=lambda item: (-item[1], item[0]))
            ordered = [name for name, _ in entries]
            paged, trunc = _paginate(ordered, limit, offset)
            result = "\n".join(paged)
            if note := _pagination_note(limit, offset, trunc):
                result += f"\n\n{note}"
            return result

        # ---------- count ----------
        if output_mode == "count":
            if not stdout.strip():
                return f"No matches found for pattern '{pattern}' in {display_root}"
            counts: dict[str, int] = {}
            file_mtimes: dict[str, float] = {}
            matching_files: list[str] = []
            for line in stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                sep = line.rfind(":")
                if sep < 0:
                    continue
                fpath_str, cnt_str = line[:sep], line[sep + 1 :]
                try:
                    cnt = int(cnt_str)
                except ValueError:
                    continue
                fp = Path(fpath_str)
                display = self._display_path(fp, root)
                counts[display] = cnt
                matching_files.append(display)
                try:
                    file_mtimes[display] = fp.stat().st_mtime
                except OSError:
                    file_mtimes[display] = 0.0
            if not counts:
                return f"No matches found for pattern '{pattern}' in {display_root}"
            ordered_files = sorted(
                matching_files,
                key=lambda n: (-file_mtimes.get(n, 0.0), n),
            )
            paged, trunc = _paginate(ordered_files, limit, offset)
            lines_out = [f"{name}: {counts[name]}" for name in paged]
            result = "\n".join(lines_out)
            notes: list[str] = []
            if trunc:
                notes.append(f"(pagination: limit={limit}, offset={offset})")
            elif offset > 0:
                notes.append(f"(pagination: offset={offset})")
            if counts:
                notes.append(
                    f"(total matches: {sum(counts.values())} in {len(counts)} files)"
                )
            if notes:
                result += "\n\n" + "\n".join(notes)
            return result

        # ---------- content (--json) ----------
        match_records: list[tuple[Path, int]] = []
        for raw_line in stdout.splitlines():
            if not raw_line.strip():
                continue
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if record.get("type") == "match":
                fpath = Path(record["data"]["path"]["text"])
                lno = record["data"]["line_number"]
                match_records.append((fpath, lno))

        if not match_records:
            return f"No matches found for pattern '{pattern}' in {display_root}"

        blocks: list[str] = []
        result_chars = 0
        seen = 0
        truncated = False
        size_truncated = False
        file_cache: dict[str, list[str]] = {}

        for fpath, lno in match_records:
            seen += 1
            if seen <= offset:
                continue
            if limit is not None and len(blocks) >= limit:
                truncated = True
                break
            key = str(fpath)
            if key not in file_cache:
                try:
                    file_cache[key] = fpath.read_text("utf-8").splitlines()
                except Exception:
                    continue
            display = self._display_path(fpath, root)
            block = self._format_block(
                display, file_cache[key], lno, context_before, context_after,
            )
            extra_sep = 2 if blocks else 0
            if result_chars + extra_sep + len(block) > self._MAX_RESULT_CHARS:
                size_truncated = True
                break
            blocks.append(block)
            result_chars += extra_sep + len(block)

        result = (
            "\n\n".join(blocks)
            if blocks
            else f"No matches found for pattern '{pattern}' in {display_root}"
        )
        notes: list[str] = []
        if truncated:
            notes.append(f"(pagination: limit={limit}, offset={offset})")
        elif size_truncated:
            notes.append("(output truncated due to size)")
        elif offset > 0 and blocks:
            notes.append(f"(pagination: offset={offset})")
        if notes:
            result += "\n\n" + "\n".join(notes)
        return result

    # ---- main entry point -------------------------------------------------

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

            flags = re.IGNORECASE if case_insensitive else 0
            try:
                needle = re.escape(pattern) if fixed_strings else pattern
                regex = re.compile(needle, flags)
            except re.error as e:
                return f"Error: invalid regex pattern: {e}"

            if head_limit is not None:
                limit = None if head_limit == 0 else head_limit
            elif output_mode == "content" and max_matches is not None:
                limit = max_matches
            elif output_mode != "content" and max_results is not None:
                limit = max_results
            else:
                limit = _DEFAULT_HEAD_LIMIT

            # Fast path: use ripgrep when available
            if _RG_BIN:
                try:
                    return self._run_rg(
                        pattern, target, path or ".",
                        glob_filter=glob, type_filter=type,
                        case_insensitive=case_insensitive,
                        fixed_strings=fixed_strings,
                        output_mode=output_mode,
                        context_before=context_before,
                        context_after=context_after,
                        limit=limit, offset=offset,
                    )
                except Exception:
                    pass  # Fall through to Python implementation

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
            root = target if target.is_dir() else target.parent

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
