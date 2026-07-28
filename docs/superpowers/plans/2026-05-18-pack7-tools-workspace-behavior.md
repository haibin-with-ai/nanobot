# Pack7 — Tools and Workspace Operational Behavior Replay Plan

> 历史归档，非当前实现。基座为 ba38f908（2026-05-18），与 upstream/main=3f808d0a 之后的结构不再对应。

## 0. Context

This plan is for the isolated upstream replay worktree only:

```bash
/root/git_code/nanobot/.worktrees/sync-upstream-2026-05-merge
# branch: sync-upstream-2026-05-replay
# replay base: upstream/main ba38f9083291a899d62c9b4b2a7b46429c39b062
```

Do **not** run this pack in the production checkout:

```bash
/root/git_code/nanobot
```

Do **not** implement while reading this plan. This document is the handoff for a later execution agent. It should write code only after turning the tasks below into failing tests.

Pack7 is deliberately narrow: tool-layer and workspace operational behavior. Do not smuggle provider routing, Discord/TTS/transcription UX, runtime/session metadata, command rewrite, subagent trace logging, memory consolidation/pruning, bootstrap, SOUL, or docs migration into it. A sync pack is not a junk drawer.

Checked facts for this planning pass:

- Current worktree branch is `sync-upstream-2026-05-replay`.
- `HEAD` during inspection was `cdc3fa71 docs: add pack6 memory consolidation pruning replay plan`.
- Merge base with `upstream/main` is `ba38f9083291a899d62c9b4b2a7b46429c39b062`.
- `nanobot/workspace/layout.py` is **不存在** in the replay worktree.
- `origin/main:nanobot/workspace/layout.py` exists and is fork-only.
- `nanobot/agent/tools/path_utils.py` exists in the replay worktree.
- `origin/main:nanobot/agent/tools/path_utils.py` is **不存在**.
- `nanobot/utils/path.py` exists in the replay worktree, but it is display-path abbreviation only, not workspace boundary logic.
- `tests/tools/test_shell*` is **不存在** in the replay worktree.
- `tests/agent/test_grep.py` is **不存在** in `origin/main`.
- `tests/tools/test_filesystem_tools.py`, `tests/tools/test_search_tools.py`, `tests/tools/test_message_tool_suppress.py`, `tests/agent/tools/test_subagent_tools.py`, `tests/test_context_documents.py`, `tests/test_api_attachment.py`, and `tests/config/test_config_paths.py` exist in the replay worktree.

## 1. Goal

Replay the fork behavior that production currently depends on for tools and workspace paths onto the new upstream architecture:

1. Preserve correct `restrict_to_workspace` semantics for filesystem, search, shell, message media, and web-adjacent tool paths.
2. Decide what to do with the old `extra_allowed_paths` feature based on the inspected fork history and current upstream structure, not vibes.
3. Restore the `grep` tool's ripgrep-first backend with a pure-Python fallback, while keeping the current tool schema and output format compatible.
4. Preserve `MessageTool` proactive-delivery behavior and final-reply suppression behavior required by current runtime.
5. Keep workspace/temp/output/log operational paths out of `/tmp` and out of the production checkout. Reuse existing upstream path helpers where they exist; add only minimal tool-layer helpers where they do not.
6. Carry over only spawn tool schema/parameter behavior that is operationally a tool contract and not already owned by Pack5.

The implementation agent should end with a small, boring set of changes. If it starts redesigning the tool system, it has lost the plot.

## 2. Non-goals

- No Anthropic OAuth or provider routing. Pack1 owns that.
- No Discord UX, TTS, transcription, sender-name logic, or channel-specific media behavior. Pack2 owns that.
- No runtime/session metadata model, channel names, scope IDs, or session layout migration except where a tool needs a path boundary. Pack3 owns runtime/session metadata.
- No command rewrite, `rtk`, or rewrite hooks in `ExecTool`. Pack4 owns that.
- No subagent model override, TraceHook, `llm_logs`, or trace logging. Pack5 owns that.
- No memory consolidation, dream pruning, context soft trim, or hard clear. Pack6 owns that.
- No bootstrap/SOUL/docs migration. Pack8 owns that.
- No global tool refactor. Only change filesystem/search/message/web/spawn/shell behavior where a fork/upstream difference exists and production depends on it.
- No compatibility shims in the wrong layer. Fix the construction/call site that should pass the correct path/config/context instead of making callees accept random aliases forever.

## 3. Source commits

Inspect these commits before implementing. The important part is the net behavior after later fork commits, not the first commit that introduced a thing.

- `b19d219b feat: add extra_allowed_paths config for restrict_to_workspace mode`
  - Touched `nanobot/agent/loop.py`, `nanobot/agent/tools/shell.py`, `nanobot/cli/commands.py`, `nanobot/config/schema.py`.
  - Added a user-facing `tools.extra_allowed_paths` config and threaded it into filesystem and exec restrictions.
- `e99b4f4c refactor: remove extra_allowed_paths feature (complete)`
  - Touched `nanobot/agent/tools/shell.py`, `nanobot/cli/commands.py`, `nanobot/config/schema.py`.
  - Explicitly removed the `extra_allowed_paths` parameter and schema field because the feature was no longer needed.
  - This is the decisive later fork commit. Pack7 must **not** resurrect `tools.extra_allowed_paths` as a config field.
- `11f6df84 perf(grep): use ripgrep as search backend with Python fallback`
  - Touched only `nanobot/agent/tools/search.py`.
  - Adds `rg` via `shutil.which("rg")`, delegates supported grep modes to ripgrep, and falls back to Python when `rg` is unavailable.
- Current fork `origin/main:nanobot/agent/tools/message.py`
  - Old architecture, but captures net message delivery/suppression semantics: same-target message sends set `_sent_in_turn`; cross-target sends do not; default message id applies only to same target.
- Current fork `origin/main:nanobot/agent/tools/spawn.py`
  - Has `timeout_seconds` and `model` parameters. Model override is Pack5 territory. `timeout_seconds` is a tool schema/parameter operational contract and belongs here if the new upstream `SubagentManager.spawn` already has or should receive a timeout parameter.
- Current fork `origin/main:nanobot/workspace/layout.py`
  - Fork-only workspace layout helper for sessions and `llm_logs`. Pack7 may reference it as evidence of production path expectations, but must not replay `llm_logs` or session layout here.

Observed fork history for `extra_allowed_paths`:

```text
b19d219b feat: add extra_allowed_paths config for restrict_to_workspace mode
e99b4f4c refactor: remove extra_allowed_paths feature (complete)
```

Decision: drop the old public `extra_allowed_paths` feature. Preserve only upstream's current internal `extra_allowed_dirs` constructor mechanism for specific trusted tool paths, such as built-in skills and media, because that is not the same thing as reviving user-configured arbitrary extra roots.

## 4. Files expected to change

Implementation should expect to touch only these files, plus tests:

- `nanobot/agent/tools/search.py`
  - Add ripgrep fast path, fallback dispatch, output formatting parity, error handling, and workspace restriction preservation.
- `nanobot/agent/tools/message.py`
  - Verify or minimally adjust proactive delivery, default channel/chat/message id handling, media path resolution, `deliver`/suppress semantics, and ContextVar turn tracking.
- `nanobot/agent/loop.py`
  - Only if final response suppression or generated-media interaction needs a call-site fix. Do not add runtime metadata or Pack3 fields here.
- `nanobot/agent/tools/spawn.py`
  - Only add non-Pack5 operational schema/parameter behavior, most likely `timeout_seconds`, if `SubagentManager.spawn` can support it or a minimal call-site thread is required.
- `nanobot/agent/subagent.py`
  - Only if needed to accept and enforce `timeout_seconds`; do not touch model override or trace/log behavior.
- `nanobot/agent/tools/filesystem.py`
  - Only for path-boundary bugs discovered by tests; do not add `tools.extra_allowed_paths`.
- `nanobot/agent/tools/path_utils.py`
  - Only for shared path-boundary behavior, media allowance, and clearer policy error text.
- `nanobot/agent/tools/shell.py`
  - Only for `restrict_to_workspace` boundary behavior and workspace/temp/output policy; do not implement command rewrite.
- `nanobot/config/paths.py`
  - Only if a minimal runtime subdir helper is required for tool output/temp behavior. Existing `get_runtime_subdir()`, `get_media_dir()`, `get_logs_dir()`, and `get_workspace_path()` already cover most needs.
- Tests likely to change/add:
  - `tests/tools/test_search_tools.py`
  - `tests/tools/test_message_tool_suppress.py`
  - `tests/tools/test_filesystem_tools.py`
  - new `tests/tools/test_shell_tool.py` if shell boundary coverage is missing
  - `tests/agent/tools/test_subagent_tools.py`
  - `tests/config/test_config_paths.py` only if path helpers change

Files that should **not** be changed by Pack7:

- Provider configs or provider routing files.
- Discord, Feishu, TTS, transcription channel implementation files.
- Command rewrite config/hook files.
- TraceHook, llm log, session layout, memory, dream, pruning files.

## 5. Upstream baseline observations

### 5.1 Search tool baseline

Replay-worktree `nanobot/agent/tools/search.py` currently:

- Implements `GlobTool` and `GrepTool` in pure Python.
- Imports `fnmatch`, `os`, `re`, `Path`, and `PurePosixPath`.
- Has `_DEFAULT_HEAD_LIMIT = 250` and `_TYPE_GLOB_MAP`.
- `GrepTool.execute()` resolves the target through `_FsTool._resolve()`, validates existence and file/dir type, compiles regex or escaped fixed string, then walks files in Python.
- Supports `output_mode` values `files_with_matches`, `count`, and `content`.
- Supports `glob`, `type`, `case_insensitive`, `fixed_strings`, `context_before`, `context_after`, `max_matches`, `max_results`, `head_limit`, and `offset`.
- Enforces `restrict_to_workspace` indirectly through `_FsTool._resolve()` and `resolve_workspace_path()`.
- Current tests cover basic glob, grep pagination/recent-first behavior, binary and large file skipped notes, outside-workspace rejection, and registration in AgentLoop and subagents.

Fork `origin/main:nanobot/agent/tools/search.py` adds:

- `json`, `shutil`, and `subprocess` imports.
- `_RG_BIN = shutil.which("rg")`.
- `GrepTool._build_rg_cmd()` to translate tool args into `rg` args.
- `GrepTool._execute_rg()` to parse ripgrep output for all output modes.
- Fallback to Python implementation when `rg` is unavailable.

### 5.2 Filesystem/path baseline

Replay-worktree `nanobot/agent/tools/filesystem.py` currently:

- Uses shared `nanobot.agent.tools.path_utils.resolve_workspace_path`.
- `_FsTool.__init__()` accepts `workspace`, `allowed_dir`, `extra_allowed_dirs`, and optional `FileStates`.
- `_FsTool.create()` sets `allowed_dir = Path(ctx.workspace)` when `ctx.config.restrict_to_workspace` or `ctx.config.exec.sandbox` is truthy.
- `_FsTool.create()` passes `extra_allowed_dirs=[BUILTIN_SKILLS_DIR]` only when restricted.
- `resolve_workspace_path()` also allows `get_media_dir()` under restriction.
- There is no public `tools.extra_allowed_paths` schema field in the replay worktree.

Fork `origin/main:nanobot/agent/tools/filesystem.py` is older and has a local `_resolve_path()` helper with `extra_allowed_dirs`, media allowance, and no `path_utils.py`. That is useful as behavior history, not as code shape.

### 5.3 Shell baseline

Replay-worktree `nanobot/agent/tools/shell.py` currently:

- `ExecTool.create()` passes `working_dir=ctx.workspace`, `restrict_to_workspace=ctx.config.restrict_to_workspace`, sandbox, path append, and env allow/deny config.
- `ExecTool.execute()` rejects an LLM-supplied `working_dir` outside configured workspace when `restrict_to_workspace` is enabled.
- `_guard_command()` blocks deny patterns, internal/private URLs, `../` traversal, and absolute paths outside the working dir/media dir when restricted.
- Boundary errors append a hard policy note telling the model not to retry with shell tricks.
- It imports `get_media_dir()` but does not know about user-configured `extra_allowed_paths`.

Pack7 may tighten boundary tests. It must not add command rewrite or `rtk` behavior.

### 5.4 Message tool baseline

Replay-worktree `nanobot/agent/tools/message.py` currently:

- Is `ContextAware` and stores channel/chat/message metadata in `ContextVar`s, which is correct for concurrent sessions.
- Tool schema has `content`, `channel`, `chat_id`, `media`, and `buttons`. No explicit `deliver` parameter exists.
- Description explicitly says this is for proactive or cross-channel delivery, not normal current-chat replies.
- On WebSocket/WebUI turns, passing a mismatched explicit `chat_id` is rejected; omitting `chat_id` uses the server conversation id from context.
- Same-target sends inherit the current `message_id`; cross-target sends clear `message_id`.
- Same-target sends set `_sent_in_turn = True` and record same-turn delivered media.
- `AgentLoop._assemble_outbound()` suppresses the final outbound if `MessageTool._sent_in_turn` is true and either there were no pending-user injections or the stop reason is `empty_final_response`.
- Existing `tests/tools/test_message_tool_suppress.py` already covers same-target suppression, no suppression without message tool, injected follow-up empty fallback, progress filtering, turn reset, and schema wording.

Fork `origin/main:nanobot/agent/tools/message.py` is older:

- Uses instance fields rather than ContextVars.
- Sets `_sent_in_turn` only for same-target delivery.
- Carries session TTS metadata, which is Pack2 and not part of this pack.
- Has no `buttons` handling.

Net decision: preserve replay-worktree ContextVar architecture and same-target suppression semantics. Do not port old instance-field code.

### 5.5 Spawn baseline

Replay-worktree `nanobot/agent/tools/spawn.py` currently:

- Tool schema exposes `task` and `label` only.
- It is `ContextAware` and forwards origin channel, origin chat id, session key, and origin message id.
- It enforces `max_concurrent_subagents` before calling manager.

Fork `origin/main:nanobot/agent/tools/spawn.py` exposes:

- `timeout_seconds` parameter.
- `model` parameter.
- `log_dir` context.

Pack7 decision:

- `model` and `log_dir` are Pack5 territory. Do not implement them here.
- `timeout_seconds` is a tool schema/parameter operational behavior. If `SubagentManager.spawn` in the replay worktree has no timeout support, implement the minimal timeout parameter path here and test it. If the manager already has equivalent timeout behavior under a different name, update the `SpawnTool` call site to pass the correct argument, not a compatibility alias inside the manager.

### 5.6 Workspace/path baseline

Replay-worktree `nanobot/config/paths.py` currently:

- `get_data_dir()` returns the active config file's parent.
- `get_runtime_subdir(name)` returns a named runtime directory under the instance data dir.
- `get_media_dir(channel=None)` returns `get_runtime_subdir("media")`, optionally channel-namespaced.
- `get_logs_dir()` returns `get_runtime_subdir("logs")`.
- `get_workspace_path(workspace=None)` returns the explicit workspace or `~/.nanobot/workspace` and ensures it exists.
- `get_legacy_sessions_dir()` remains global for migration fallback.

Fork `origin/main:nanobot/workspace/layout.py` exists but is not present upstream. It has session and `llm_logs` directories. Pack5 already owns `llm_logs`; Pack3 owns session/runtime metadata. Pack7 only needs the narrower rule: tool-created temp/output/media/log paths must come from configured workspace/runtime dirs, never `/tmp`, and must not point at `/root/git_code/nanobot` production checkout during replay.

## 6. Design decisions

### 6.1 `extra_allowed_paths`: drop public config, keep internal trusted allowances

Do not add `tools.extra_allowed_paths` to `ToolsConfig`.

Why:

- The fork introduced it in `b19d219b`.
- The fork later removed it completely in `e99b4f4c` with the explicit commit message `This feature is no longer needed`.
- Replay-worktree upstream already has a better-shaped internal mechanism: `_FsTool(extra_allowed_dirs=...)` and `resolve_workspace_path(..., extra_allowed_dirs=...)`.

Required behavior:

- Under `restrict_to_workspace=True`, filesystem/search tools may access:
  - the configured workspace;
  - the configured media directory from `get_media_dir()`;
  - explicitly passed internal trusted directories such as `BUILTIN_SKILLS_DIR` for read/list use.
- Under `restrict_to_workspace=True`, shell commands may access:
  - their configured workspace working dir;
  - the configured media dir;
  - benign device paths already enumerated by `ExecTool`.
- Do not let arbitrary user config expand the boundary.
- Do not implement a shim that accepts both `extra_allowed_paths` and `extra_allowed_dirs`. That is how dead config comes back as a zombie.

Tests to add/keep:

- `ToolsConfig` should not expose `extra_allowed_paths`.
- filesystem/search reject outside paths when restricted.
- filesystem/search allow configured media dir under restriction.
- filesystem read/list can access `BUILTIN_SKILLS_DIR` only through the existing internal constructor path, not a public config path.
- shell rejects `working_dir` outside workspace and absolute paths outside workspace/media under restriction.

### 6.2 Ripgrep backend for `grep`

Implement a two-path `GrepTool`:

- Resolve and validate the target path through existing `_resolve()` **before** invoking `rg`. This preserves workspace restriction. Do not delegate path boundary checks to ripgrep.
- If `_RG_BIN` is available, use ripgrep for normal search execution.
- If `_RG_BIN` is unavailable, or if ripgrep execution fails in a non-user-error operational way, use the existing Python backend.
- Keep the Python backend as the source of semantic truth. Avoid deleting it.

Ripgrep command requirements:

- Use `shutil.which("rg")` at import time or a small helper that can be monkeypatched in tests.
- Use argument lists with `subprocess.run(...)`, not shell strings.
- Add `--hidden` so behavior matches the existing Python traversal policy for non-ignored hidden files.
- Exclude the same noise directories as Python traversal using `--glob !<dir>/` entries.
- Respect `_MAX_FILE_BYTES`, using ripgrep's `--max-filesize`.
- Respect `case_insensitive` with `-i`.
- Respect `fixed_strings` with `-F`.
- Respect `glob` with `--glob <pattern>`.
- Respect `type` using a generated type definition or by translating `_TYPE_GLOB_MAP` to globs. Prefer the simpler translation that matches the existing Python `_matches_filters()` semantics. Do not depend on ripgrep's built-in type names if they diverge from `_TYPE_GLOB_MAP`.
- For a single file target, run ripgrep against that file. For a directory target, run against that directory.
- Use JSON output for `content` mode so line numbers are parsed robustly.
- Use `--files-with-matches` for `files_with_matches` mode.
- Use `--count` for `count` mode.

Output compatibility requirements:

- `files_with_matches` output remains one display path per line, sorted newest-first where current Python behavior sorts by mtime. If ripgrep order cannot guarantee this, post-process ripgrep paths and sort using file mtimes before pagination.
- `count` output remains `path: count`, and includes `(total matches: N in M files)` notes when counts exist.
- `content` output remains blocks formatted by `_format_block()`:
  - first line `display_path:match_line`
  - context lines `  line| text`
  - match line `> line| text`
- Pagination notes must remain compatible with existing tests: `(pagination: limit=X, offset=Y)` or `(pagination: offset=Y)` as appropriate.
- No-match messages should remain compatible: `No matches found for pattern '...' in ...`.
- Invalid regex should be caught before ripgrep if possible by compiling the Python regex, preserving current `Error: invalid regex pattern: ...` behavior.
- Ripgrep exit code handling:
  - `0`: parse results.
  - `1`: no matches, not an error.
  - anything else: return a clear `Error searching files: ...` only for true user-visible failures, or fallback to Python if the error is backend availability/compatibility. Do not hide invalid pattern errors behind fallback if Python compilation already catches them.
- Binary and large-file notes:
  - Python fallback already reports skipped binary/unreadable and large files.
  - Ripgrep path may not reproduce exact skipped counts. Keep tests focused on fallback for skipped notes by monkeypatching `_RG_BIN = None` or equivalent.

Workspace restriction:

- `GrepTool.execute()` must call `_resolve(path)` before `_execute_rg()`.
- Never pass user-provided unresolved `path` directly to subprocess.
- Display paths should be relative to the search root/workspace using existing `_display_path()` behavior.

### 6.3 MessageTool delivery and suppress semantics

There is no explicit `deliver` parameter in the current replay-worktree `MessageTool` schema. The design decision is to preserve the existing implicit delivery contract, not invent a new flag unless a current failing production test proves it exists.

Required behavior:

- Default: `MessageTool.execute(content=...)` sends a proactive message through `_send_callback` to the context target when `channel` and `chat_id` are omitted and a context exists.
- Same-target delivery means effective `channel == default_channel` and effective `chat_id == default_chat_id`.
- Same-target delivery:
  - inherits `message_id` from context when available;
  - sets `_sent_in_turn = True` only after callback success;
  - records delivered media in the turn media ContextVar;
  - may suppress the assistant's final outbound message through `AgentLoop._assemble_outbound()`.
- Cross-target delivery:
  - never inherits the current `message_id`;
  - does **not** set `_sent_in_turn`;
  - does **not** suppress the assistant's final outbound message.
- Callback errors return `Error sending message: ...` and must not mark `_sent_in_turn`.
- Missing target returns `Error: No target channel/chat specified`.
- Media paths are resolved through `resolve_workspace_path()` with the active workspace, not by reading files or guessing.
- `buttons` remain a tool-layer feature if already present upstream; do not remove them.
- WebSocket/WebUI explicit `chat_id` mismatch protection remains in place. It is not Discord/TTS.

Final response suppress behavior:

- If a same-target message tool call succeeded and there were no pending user-turn injections, return `None` from `_assemble_outbound()` so the runtime does not double-send.
- If a same-target message tool call succeeded during an injected follow-up turn and the model ends with `empty_final_response`, also return `None` rather than emitting an empty fallback.
- If there was no same-target message tool call, always allow normal final replies.
- If a cross-target message tool call happened, allow normal final replies.

Tests should use `RequestContext`, not the old fork signature `set_context(channel, chat_id, ...)`.

### 6.4 Workspace/temp/output/log path strategy

Pack7's path rule is boring and strict:

- Tools must not create or depend on `/tmp` for durable outputs.
- Tools must not write into `/root/git_code/nanobot` production checkout during replay.
- Durable media/attachments go through `get_media_dir()` or an explicit workspace path.
- Runtime logs go through `get_logs_dir()` if tool-layer logs are needed. Do not add `llm_logs`; Pack5 owns that.
- Workspace files go through the configured workspace path from `AgentLoop(workspace=...)` or `get_workspace_path(...)`.
- If a temporary file is needed for a tool operation, prefer a subdirectory under `get_runtime_subdir("tmp")` or the active workspace. Add a small helper in `config/paths.py` only if a real tool needs it; do not add speculative directories.

Current upstream already has `get_runtime_subdir(name)`, `get_media_dir()`, `get_logs_dir()`, and `get_workspace_path()`. Therefore the default implementation plan is: reuse these and add tests documenting that media/log/runtime dirs are config-root-relative, not `/tmp`.

Do not port `origin/main:nanobot/workspace/layout.py` wholesale in Pack7. Its `sessions_dir` is Pack3. Its `llm_logs_dir` is Pack5. Porting it here would be scope theft wearing a fake mustache.

### 6.5 Filesystem/search/shell restriction boundary

The correct boundary is constructed at tool creation time:

- `AgentLoop._register_default_tools()` builds `ToolContext(workspace=str(self.workspace), config=self.tools_config, ...)`.
- `ToolLoader` calls each tool's `create(ctx)`.
- Filesystem/search tools should derive `allowed_dir` from `ctx.workspace` when `ctx.config.restrict_to_workspace` or sandbox requires it.
- Shell should derive its configured working dir from `ctx.workspace` and reject user override outside that root.

Do not fix boundary bugs by making `resolve_workspace_path()` accept a wider variety of paths. The call site should pass the correct `workspace`, `allowed_dir`, and internal `extra_allowed_dirs`.

### 6.6 Spawn tool operational parameters

Add `timeout_seconds` to `SpawnTool` only if the implementation pass confirms `SubagentManager.spawn` can enforce it or can be minimally extended to enforce it.

Required schema if implemented:

- `timeout_seconds`: number, optional, minimum `0`.
- Description: optional wall-clock timeout in seconds; omit to use manager default; `0` or negative disables timeout only if manager semantics support that. If manager does not support disabling timeout, do not claim it.

Explicit non-scope:

- Do not add `model` to the schema in Pack7. Pack5 owns subagent model override.
- Do not add `log_dir` context in Pack7. Pack5 owns subagent trace/log dirs.

## 7. TDD task sequence

### Task 1 — Lock `extra_allowed_paths` decision with tests

1. Add or update tests in `tests/config/test_config_paths.py` or a new config-schema test:
   - instantiate `ToolsConfig()`;
   - assert it has no `extra_allowed_paths` attribute/field;
   - assert `restrict_to_workspace` still exists.
2. Add/extend filesystem tests in `tests/tools/test_filesystem_tools.py`:
   - with restricted tools, reading an outside path returns `Error:`;
   - reading a file under `get_media_dir()` succeeds when restricted, using monkeypatched config path so it does not touch real `~/.nanobot`;
   - if testing built-in skills access, assert the tool's internal `extra_allowed_dirs` allows it only through `ReadFileTool.create(ctx)` under restriction.
3. Run only the new/changed tests for this task.
4. Implement only if tests fail.

Expected implementation:

- Probably no schema implementation, because the field is already absent.
- Possibly small test-only construction adjustments.
- No production code should reintroduce `extra_allowed_paths`.

### Task 2 — Add ripgrep backend behind existing `GrepTool` semantics

1. In `tests/tools/test_search_tools.py`, add tests that monkeypatch the ripgrep backend:
   - ripgrep path is used when `_RG_BIN` or helper returns a fake binary;
   - subprocess receives an argument list, not a shell string;
   - `files_with_matches`, `count`, and `content` modes match existing output shape;
   - `fixed_strings`, `case_insensitive`, `glob`, and `type` are reflected in the command or translated filters;
   - exit code `1` returns no-match output;
   - when `_RG_BIN` is `None`, Python fallback still passes existing tests;
   - invalid regex returns current invalid regex error before invoking subprocess;
   - outside-workspace restricted path never invokes subprocess.
2. Implement `GrepTool._execute_python()` by extracting current Python logic if needed.
3. Implement `GrepTool._execute_rg()` and a small command builder.
4. Keep `_format_block()`, `_matches_filters()`, `_display_path()`, pagination helpers, and ignore-dir behavior as shared semantics.
5. Run `tests/tools/test_search_tools.py` only.

Implementation notes:

- Prefer a helper like `_rg_bin()` over a hard global if that makes monkeypatching cleaner. Keep it tiny.
- Avoid shell execution. Use `subprocess.run(cmd, capture_output=True, text=True, timeout=...)` or equivalent.
- Use existing display path and pagination helpers after parsing ripgrep results.
- If ripgrep cannot provide a semantic exactly, parse its raw paths and normalize through Python post-processing.

### Task 3 — Preserve MessageTool delivery/suppress behavior

1. Extend `tests/tools/test_message_tool_suppress.py`:
   - same-target send suppresses final reply and sets `_sent_in_turn` only on success;
   - cross-target send does not suppress final reply;
   - failed callback does not suppress final reply;
   - same-target media send records delivered media and returns media count;
   - explicit WebSocket `chat_id` mismatch returns the current error and does not call send callback;
   - omitted `channel/chat_id` uses current context target.
2. If tests already cover some items, do not duplicate. Add only missing cases.
3. Implement minimal fixes in `message.py` or `_assemble_outbound()`.
4. Do not port fork TTS metadata.
5. Run `tests/tools/test_message_tool_suppress.py` only.

Implementation notes:

- Use `RequestContext(channel=..., chat_id=..., message_id=..., metadata=...)` for context.
- Preserve ContextVars. Do not revert to fork's instance fields.
- If you need an explicit `deliver` flag because a current production test requires it, document why in a code comment and default it to true for proactive tool calls. Otherwise do not add the parameter.

### Task 4 — Workspace/temp/output path policy tests

1. Add tests around existing path helpers, likely in `tests/config/test_config_paths.py`:
   - `get_media_dir()` follows the active config file parent.
   - `get_logs_dir()` follows the active config file parent.
   - `get_runtime_subdir("tmp")` would resolve under the active config file parent if used.
   - `get_workspace_path(explicit)` resolves explicit workspace and does not default to the repository checkout.
2. Search tool/file/media tests should monkeypatch config path rather than touching real home dirs.
3. Implement only if current helpers fail the tests.
4. Do not add `nanobot/workspace/layout.py` in this pack unless a tool-layer test proves no existing helper can express the needed path. If added, it must exclude sessions and `llm_logs` from Pack7 scope.

### Task 5 — Shell boundary tests without command rewrite

1. Add `tests/tools/test_shell_tool.py` if no shell test file exists.
2. Test only boundary behavior:
   - `restrict_to_workspace=True` rejects `working_dir` outside the configured workspace.
   - `restrict_to_workspace=True` rejects absolute paths outside workspace/media.
   - `restrict_to_workspace=True` allows benign device paths such as `/dev/null`.
   - boundary errors include the hard policy note telling the model not to retry with shell tricks.
3. Do not test or implement command rewrite.
4. Run the new shell test only.

Implementation likely unnecessary because replay-worktree shell already has this behavior. If a test fails, fix `ExecTool.create()` or `ExecTool.execute()` call-site boundary derivation first, not a broad callee shim.

### Task 6 — Spawn timeout parameter only

1. Inspect `nanobot/agent/subagent.py` during implementation to confirm current `SubagentManager.spawn` signature.
2. If timeout support exists:
   - add `timeout_seconds` to `SpawnTool` schema;
   - pass it to the manager using the real manager parameter name;
   - add tests in `tests/agent/tools/test_subagent_tools.py` that the tool forwards it.
3. If timeout support does not exist:
   - add a failing test for manager-level timeout behavior;
   - implement the minimal timeout in `SubagentManager.spawn` or the correct execution call site;
   - then add `SpawnTool` forwarding.
4. Do **not** add `model` in Pack7.
5. Do **not** add `log_dir` in Pack7.

### Task 7 — Regression run for Pack7 files only

Run the smallest relevant test set:

```bash
pytest tests/tools/test_search_tools.py \
       tests/tools/test_message_tool_suppress.py \
       tests/tools/test_filesystem_tools.py \
       tests/tools/test_shell_tool.py \
       tests/agent/tools/test_subagent_tools.py \
       tests/config/test_config_paths.py
```

If `tests/tools/test_shell_tool.py` was not created because all coverage already exists elsewhere, omit it.

Do not run implementation-wide integration suites unless a Pack7 change touches shared agent loop behavior and the local small suite cannot cover it.

## 8. Pack-level verification

Before declaring Pack7 done, verify:

- `grep` with `rg` available uses ripgrep and preserves output shape for all three output modes.
- `grep` without `rg` passes the Python fallback tests.
- `grep` never searches outside workspace when restricted.
- `filesystem` tools reject outside paths when restricted and allow workspace/media/internal trusted paths only.
- `ExecTool` rejects outside `working_dir` and absolute outside paths when restricted.
- No `tools.extra_allowed_paths` config field exists.
- `MessageTool` same-target sends suppress final replies; cross-target sends do not.
- `MessageTool` callback failures do not suppress final replies.
- `MessageTool` media path resolution does not bypass workspace policy.
- Spawn timeout parameter is either implemented and tested, or explicitly left out because manager support does not exist and would exceed Pack7's minimal tool contract.
- No command rewrite, model override, trace logging, `llm_logs`, TTS, transcription, memory pruning, or bootstrap files were changed.

Suggested inspection commands for the implementation agent:

```bash
git status --short
grep -RIn "extra_allowed_paths" nanobot tests | sed -n '1,120p'
grep -RIn "command_rewrite\|rtk\|llm_logs\|subagent_model\|TraceHook" nanobot tests | sed -n '1,120p'
```

Those grep commands are intentionally plain. No shell acrobatics. The previous failure was a safety-guard own goal; don't repeat it.

## 9. Manual smoke check

After tests pass, run manual checks in a disposable workspace, not the production checkout:

1. Create a temp workspace under the replay worktree or a config-root-controlled test directory.
2. Put two text files in it, one matching and one not.
3. Call `GrepTool.execute(pattern="needle", path=".", output_mode="files_with_matches")` with restricted workspace and confirm only workspace-relative paths appear.
4. Call `GrepTool.execute(pattern="needle", path=".", output_mode="content", context_before=1, context_after=1)` and confirm block formatting.
5. Instantiate `MessageTool`, set `RequestContext(channel="cli", chat_id="direct")`, attach an async fake send callback, call `execute(content="hello")`, and confirm `_sent_in_turn` becomes true.
6. Repeat with cross-target `channel/chat_id` and confirm `_sent_in_turn` remains false.
7. Instantiate `ExecTool(working_dir=<workspace>, restrict_to_workspace=True)` and confirm an outside absolute path is blocked with the policy note.

Do not smoke test by running nanobot against real Discord, Feishu, production workspaces, or `/root/git_code/nanobot`.

## 10. Rollback plan

If Pack7 breaks runtime behavior:

1. Revert the Pack7 commit only.
2. If the break is isolated to ripgrep backend, disable the ripgrep fast path by setting the helper to return `None` or reverting only the `_execute_rg()` dispatch while leaving Python grep intact.
3. If the break is isolated to message suppression, revert only the message/loop change and keep search/filesystem changes.
4. If spawn timeout causes instability, remove the `timeout_seconds` schema and forwarding; this is independent from grep/message behavior.
5. Do not reintroduce `extra_allowed_paths` as an emergency workaround. That feature was already removed by the fork. Fix the path boundary or call site that actually failed.

## 11. Completion criteria

Pack7 is complete when:

- The plan's test sequence passes for all files touched by the implementation.
- Ripgrep is used when available and Python fallback remains available.
- `grep` output remains compatible with existing consumers.
- Workspace restriction is enforced consistently across filesystem/search/shell/message media paths.
- Public `extra_allowed_paths` stays removed.
- MessageTool delivery and final-reply suppression semantics are documented by tests.
- Tool-created durable paths use workspace/config-root runtime dirs, not `/tmp` or production checkout paths.
- Spawn timeout behavior is either implemented with tests or explicitly documented as not implemented because no upstream manager support exists in this architecture.
- `git status --short` contains only Pack7 implementation/test files and no production-checkout modifications.

The thing you are really guarding against here is not a missing helper. It is boundary drift: every tool quietly inventing its own idea of where it is allowed to touch. That is how an agent stops being sandboxed and starts being a raccoon with shell access.
