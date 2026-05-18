# Pack4 — Command Rewrite Hook / rtk Migration Replay Plan

## 0. Context

This plan is for the isolated upstream replay worktree only:

```bash
/root/git_code/nanobot/.worktrees/sync-upstream-2026-05-merge
# branch: sync-upstream-2026-05-replay
# replay base: upstream/main ba38f908
```

Do **not** run this pack in the production checkout:

```bash
/root/git_code/nanobot
```

Do **not** implement while reading this plan. This document is the handoff for a later execution agent. It should write code only after turning the tasks below into failing tests.

The branch already contains Pack1/Pack2/Pack3 plan commits. Pack4 must replay only command rewrite / `rtk` behavior that the fork depends on in production. The architectural point is the whole pack: command rewrite is a cross-cutting tool-call argument transformation and belongs in `AgentHook`/`CommandRewriteHook`, not inside `ExecTool` / `ShellTool`.

A dumb replay would resurrect the old `ExecTool(rtk_enabled=...)` path from `2713688e`. Do not do that. That commit is behavioral archaeology, not architecture guidance.

## 1. Goal

Replay fork behavior for command rewrite onto the current upstream runner/hook architecture:

1. Add config schema for command rewrite:
   - `tools.command_rewrite.enabled: bool = False`
   - `tools.command_rewrite.verbose: bool = False`
   - `tools.command_rewrite.timeout: float = 5.0`
2. Add `CommandRewriteHook`, exported from `nanobot.agent.hooks`, which implements `AgentHook.before_execute_tools()`.
3. When enabled, before tool execution, rewrite only `exec` tool calls with a string `arguments["command"]` by running:

   ```bash
   rtk rewrite '<original command>'
   ```

4. Mutate the existing `ToolCallRequest.arguments["command"]` in place so the runner, tool execution, tool result messages, and later callbacks all see the rewritten command.
5. Treat `rtk` exit code `0` as successful rewrite when stdout is non-empty.
6. Treat `rtk` exit code `3` as successful rewrite when stdout is non-empty. This is required for `rtk` 0.37+ contract drift.
7. Treat any other exit code, timeout, missing binary, empty stdout, or exception as fail-safe passthrough: preserve the original command and continue the run.
8. Inject the hook into the main `AgentLoop` when config enables it.
9. Propagate the same hook semantics into subagent runs so `spawn`-created agents rewrite commands the same way as the main loop.
10. Document the hook architecture and user-facing config enough that future agents do not put this back into `ExecTool`.

## 2. Non-goals

Do not include any of the following:

- Anthropic OAuth/provider routing. That is Pack1.
- Discord UX/TTS/transcription. That is Pack2.
- Session/runtime metadata. That is Pack3.
- Subagent trace files, `llm_logs`, model override, or trace/logging UX. That is Pack5. Pack4 may pass hooks into subagent runner; it must not design trace logging.
- Memory, consolidation, context pruning. That is Pack6.
- Grep/search/message/workspace tool behavior. That is Pack7. Mention shell/exec only to prove rewrite is not embedded in `ExecTool`.
- Bootstrap/SOUL/general docs. That is Pack8. Pack4 docs are limited to command rewrite config and hook architecture.

## 3. Source commits

Replay behavior from these fork commits. Do not blindly replay old file structure when upstream has moved on.

1. `2713688e feat: add rtk rewrite support to ExecTool`
   - touched `nanobot/agent/loop.py`, `nanobot/agent/tools/shell.py`, `nanobot/config/schema.py`, `tests/tools/test_rtk_rewrite.py`.
   - Use only as the original behavior source: `rtk rewrite` turns a shell command into a compressed command before execution. Do **not** keep the `ExecTool`-embedded design.
2. `88cd2924 feat(config): add CommandRewriteConfig schema`
   - touched `nanobot/config/schema.py`, `tests/config/test_command_rewrite_config.py`.
3. `47392fb5 feat(hooks): add CommandRewriteHook for cross-cutting tool argument rewrite`
   - touched `nanobot/agent/hooks/__init__.py`, `nanobot/agent/hooks/rewrite.py`, `tests/agent/hooks/__init__.py`, `tests/agent/hooks/test_command_rewrite.py`.
4. `1fad5206 feat(loop): inject CommandRewriteHook into main agent loop`
   - touched `nanobot/agent/loop.py`, `nanobot/cli/commands.py`, `nanobot/nanobot.py`, `tests/agent/test_loop_rewrite_hook_injection.py`.
5. `094c7223 feat(loop): propagate CommandRewriteHook into subagent runs`
   - touched `nanobot/agent/loop.py`, `tests/agent/test_loop_rewrite_hook_injection.py`.
6. `d24cdec0 refactor(exec): remove rtk rewrite from ExecTool`
   - touched `nanobot/agent/loop.py`, `nanobot/agent/tools/shell.py`, `nanobot/config/schema.py`, `tests/tools/test_rtk_rewrite.py`.
   - This is the guardrail commit: Pack4 must end with no `rtk` rewrite knobs or helper methods in `ExecTool`.
7. `63375b87 docs: document rtk -> commandRewrite migration`
   - touched `CHANGELOG.md`, `nanobot/agent/loop.py`.
   - Reuse the migration idea only where it fits current docs; do not write broad changelog noise unless project convention demands it.
8. `8cb53e21 docs(hooks): add architecture notes for cross-cutting hooks`
   - touched `nanobot/agent/hooks/CLAUDE.md`.
9. `f6070e4d fix(hooks): accept rtk 0.37+ exit code 3 as successful rewrite`
   - touched `nanobot/agent/hooks/rewrite.py`, `tests/agent/hooks/test_command_rewrite.py`.
   - Required. Exit code `3` is not failure for this hook.

Source commit touched-file list was inspected with `git show --stat --name-only` in the isolated worktree.

## 4. Files expected to change

Expected production files:

- `nanobot/config/schema.py`
  - Add `CommandRewriteConfig(Base)` near tool config classes.
  - Add `command_rewrite: CommandRewriteConfig = Field(default_factory=CommandRewriteConfig)` to `ToolsConfig`.
  - Make sure camelCase aliasing works automatically via `Base`, so JSON may use `commandRewrite` while Python uses `command_rewrite`.
  - With upstream's lazy tool-config rebuild, `CommandRewriteConfig` lives directly in this module and does not need lazy import handling.

- `nanobot/agent/hooks/__init__.py` — currently **不存在** in upstream baseline.
  - Create package export file.
  - Export `CommandRewriteHook` in `__all__`.

- `nanobot/agent/hooks/rewrite.py` — currently **不存在** in upstream baseline.
  - Implement `CommandRewriteHook`.
  - Keep it tool-agnostic except for selecting supported shell tool-call names. For this pack, supported name is `"exec"`; if execution discovers upstream has an alias named `"shell"`, include it only if a test proves the provider emits that tool name. Do not speculate.

- `nanobot/agent/hooks/CLAUDE.md` — currently **不存在** in upstream baseline.
  - Add short architecture notes for hook directory.
  - Must explicitly say cross-cutting argument rewrite belongs in hooks, not tool internals.

- `nanobot/agent/loop.py`
  - Import `CommandRewriteHook` and `CommandRewriteConfig`.
  - Extend `AgentLoop.__init__` with `command_rewrite_config: CommandRewriteConfig | None = None` or derive it cleanly from `tools_config` if that is less invasive in current upstream.
  - If config is enabled, construct one `CommandRewriteHook` and append it to `_extra_hooks`.
  - Store it as `self._command_rewrite_hook: CommandRewriteHook | None` for tests and for subagent propagation.
  - Pass the configured hook list or hook object to `SubagentManager` so subagent `AgentRunSpec.hook` has the same rewrite behavior.
  - In `from_config()`, pass `config.tools.command_rewrite` into `AgentLoop.__init__` or ensure `tools_config=config.tools` is enough and tested. Prefer explicit handoff because it makes config wiring visible.

- `nanobot/agent/subagent.py`
  - Add a constructor parameter such as `hooks: list[AgentHook] | None = None` or `extra_hooks: list[AgentHook] | None = None`.
  - Store a copy.
  - When running a subagent, compose `_SubagentHook(task_id, status)` with extra hooks using existing `CompositeHook`. If no extra hooks exist, keep current behavior.
  - Do not add trace logging, per-subagent log dirs, model override, or Pack5 fields.

Possible production file depending on current wiring:

- `nanobot/nanobot.py`
  - Fork source touched this around SDK hook behavior. Current upstream `Nanobot.from_config()` already calls `AgentLoop.from_config(config, ...)`, so command rewrite should be loaded automatically once `AgentLoop.from_config()` wires config. Do not change this file unless a test proves SDK-created loops bypass config.

Probably not production files:

- `nanobot/agent/tools/shell.py`
  - Should remain free of `rtk`, `rewrite`, `rtk_enabled`, `rtk_verbose`, `rtk_timeout`, or `_rtk_rewrite`.
  - A test may inspect `ExecToolConfig` and `ExecTool.__init__` behavior, but production edit here should be unnecessary.

- `nanobot/cli/commands.py`
  - Fork source touched this. Current upstream CLI appears to build loops through shared config/loop helpers. Do not edit unless a failing config-injection test proves an interactive/direct CLI path constructs `AgentLoop` manually and bypasses `from_config()`.

Expected tests:

- `tests/config/test_command_rewrite_config.py`
  - Config defaults and alias behavior.

- `tests/agent/hooks/test_command_rewrite.py`
  - Hook mutation, filtering, fail-safe, exit code 0/3 semantics.

- `tests/agent/test_loop_rewrite_hook_injection.py`
  - Main loop injection and subagent propagation.

- `tests/agent/test_hook_composite.py`
  - Likely already enough for `CompositeHook.before_execute_tools`; add a narrow test only if missing coverage around ordering/mutation.

- `tests/tools/test_shell.py` or a focused new test under `tests/tools/`
  - Guard that `ExecTool` has no embedded `rtk` rewrite behavior. Do not resurrect old `tests/tools/test_rtk_rewrite.py` as `ExecTool` tests; if a file with that name exists from old work, delete or rewrite it to assert absence.

Reference files inspected for this plan and status:

- Existing upstream baseline files inspected:
  - `nanobot/agent/hook.py`
  - `nanobot/agent/loop.py`
  - `nanobot/agent/runner.py`
  - `nanobot/agent/subagent.py`
  - `nanobot/agent/tools/shell.py`
  - `nanobot/agent/tools/spawn.py`
  - `nanobot/config/schema.py`
  - `nanobot/nanobot.py`
  - `nanobot/cli/commands.py`
  - `tests/agent/test_hook_composite.py`
  - `tests/agent/test_subagent.py`
  - `tests/agent/test_subagent_lifecycle.py`
  - `tests/agent/tools/test_subagent_tools.py`
  - `tests/config/test_config_paths.py`, `test_dream_config.py`, `test_config_migration.py`, `test_env_interpolation.py`, `test_model_presets.py`
- Upstream baseline paths checked and **不存在**:
  - `tests/agent/hooks/*`
  - `nanobot/agent/hooks/rewrite.py`
  - `nanobot/agent/hooks/__init__.py`
  - `nanobot/agent/hooks/CLAUDE.md`
- Fork versions inspected via `git show origin/main:<path>`:
  - `nanobot/agent/hooks/rewrite.py`
  - `nanobot/agent/hooks/__init__.py`
  - `nanobot/agent/hooks/CLAUDE.md`
  - `nanobot/agent/hook.py`
  - `nanobot/agent/loop.py`
  - `nanobot/agent/runner.py`
  - `nanobot/agent/subagent.py`
  - `nanobot/agent/tools/shell.py`
  - `nanobot/config/schema.py`
  - `nanobot/nanobot.py`
  - `tests/agent/hooks/test_command_rewrite.py`
  - `tests/agent/test_hook_composite.py`
  - `tests/agent/test_loop_rewrite_hook_injection.py`
  - `tests/tools/test_rtk_rewrite.py` at `2713688e` and `d24cdec0`.

## 5. Upstream baseline observations

These observations are from the isolated worktree on `sync-upstream-2026-05-replay`, base `upstream/main ba38f908`, after Pack1/2/3 plan commits only.

### 5.1 Hook/runner baseline

`nanobot/agent/hook.py` already has the needed lifecycle surface:

- `AgentHookContext` includes:
  - `iteration`
  - `messages`
  - `response`
  - `usage`
  - `tool_calls: list[ToolCallRequest]`
  - `tool_results`
  - `tool_events`
  - streaming/final/error fields.
- `AgentHook.before_execute_tools(context)` exists.
- `CompositeHook.before_execute_tools(context)` calls each hook in order.
- `CompositeHook.finalize_content()` pipes transformed content through each hook.

`nanobot/agent/runner.py` already calls the hook at the right seam:

```python
context.tool_calls = list(response.tool_calls)
...
await hook.before_execute_tools(context)
results, new_events, fatal_error = await self._execute_tools(
    spec,
    response.tool_calls,
    ...
)
```

`context.tool_calls` is a new list but contains the same `ToolCallRequest` objects as `response.tool_calls`. Therefore a hook that mutates `tc.arguments["command"]` in place changes what `_execute_tools()` receives. No runner change is needed unless tests uncover that a future upstream copy/deepcopy breaks this assumption.

This is exactly the seam Pack4 should use. It is the turnstile at the stadium gate, not the hot-dog stand inside the stadium. Command rewrite changes who gets through the execution boundary; `ExecTool` merely cooks the command it is given.

### 5.2 Agent loop baseline

`nanobot/agent/loop.py` already accepts generic hooks:

```python
hooks: list[AgentHook] | None = None
...
self._extra_hooks: list[AgentHook] = hooks or []
```

During a turn it composes:

```python
hook: AgentHook = (
    CompositeHook([loop_hook] + self._extra_hooks) if self._extra_hooks else loop_hook
)
```

`AgentLoop.from_config()` currently forwards common config into `AgentLoop.__init__`, including `tools_config=config.tools`, but it does not yet have `command_rewrite_config` because schema does not exist. The clean replay is to add an explicit config parameter and pass `config.tools.command_rewrite` from `from_config()`.

`AgentLoop.__init__` constructs `SubagentManager` at lines around the current `self.subagents = SubagentManager(...)` call. That constructor currently receives provider/workspace/bus/model/tools_config/runtime limits, but no hooks. Pack4 must add a narrow hook propagation path here.

### 5.3 Subagent baseline

`nanobot/agent/subagent.py` currently:

- imports `AgentHook`, `AgentHookContext`, `AgentRunner`, `AgentRunSpec`.
- builds a `SubagentManager` with its own `AgentRunner(provider)`.
- in `_run_subagent_inner()` calls:

```python
result = await self.runner.run(AgentRunSpec(
    initial_messages=messages,
    tools=tools,
    model=self.model,
    max_iterations=self.max_iterations,
    max_tool_result_chars=self.max_tool_result_chars,
    hook=_SubagentHook(task_id, status),
    ...
))
```

So subagents currently get only `_SubagentHook`. Pack4 must compose `_SubagentHook` with the command rewrite hook. Use the existing `CompositeHook`; do not invent a new subagent hook protocol.

### 5.4 Shell/ExecTool baseline

`nanobot/agent/tools/shell.py` currently defines:

```python
class ExecToolConfig(Base):
    enable: bool = True
    timeout: int = 60
    path_append: str = ""
    sandbox: str = ""
    allowed_env_keys: list[str] = Field(default_factory=list)
    allow_patterns: list[str] = Field(default_factory=list)
    deny_patterns: list[str] = Field(default_factory=list)
```

`ExecTool` executes the given command, handles cwd/env/path/sandbox/policy/timeout/truncation. No `rtk` or rewrite code was found in the upstream baseline.

Keep it that way. `ExecTool` is a shell execution primitive. If it grows `rtk_enabled` again, every future shell-like tool must either duplicate that wart or behave differently. Hooks solve the common boundary once.

### 5.5 Config baseline

`nanobot/config/schema.py` has `Base` with camelCase aliases and `ToolsConfig` with lazy defaults for tool-specific config classes:

```python
class ToolsConfig(Base):
    web: WebToolsConfig = Field(default_factory=lambda: _lazy_default(...))
    exec: ExecToolConfig = Field(default_factory=lambda: _lazy_default(...))
    my: MyToolConfig = Field(default_factory=lambda: _lazy_default(...))
    image_generation: ImageGenerationToolConfig = Field(default_factory=lambda: _lazy_default(...))
    restrict_to_workspace: bool = False
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)
    ssrf_whitelist: list[str] = Field(default_factory=list)
```

Add `command_rewrite` here. Because `CommandRewriteConfig` is in the same file and has no circular tool import, it can use `Field(default_factory=CommandRewriteConfig)`.

### 5.6 Fork implementation baseline

Fork `origin/main:nanobot/agent/hooks/rewrite.py` implements:

- `CommandRewriteHook(enabled=False, verbose=False, timeout=5.0, path_append="")`
- `before_execute_tools()` loops over `context.tool_calls`
- filters `tc.name != "exec"`
- reads `tc.arguments.get("command")`
- skips non-string or blank commands
- calls `asyncio.create_subprocess_exec("rtk", "rewrite", command, stdout=PIPE, stderr=DEVNULL, env=env)`
- appends `path_append` to `PATH` when present
- waits with `asyncio.wait_for(proc.communicate(), timeout=self._timeout)`
- accepts `proc.returncode in (0, 3)` and non-empty stdout
- logs debug if verbose and actual command changed
- catches exceptions and returns original command.

Fork `origin/main:nanobot/config/schema.py` had `CommandRewriteConfig` with `enabled`, `verbose`, and `timeout`. `path_append` is not in config; fork injection passed `exec_config.path_append` into the hook so `rtk` can be found through the same PATH extension already used by shell execution.

## 6. Design decisions

### 6.1 Why not rewrite inside ExecTool/ShellTool

Do not put `rtk` in `ExecTool`.

`ExecTool` owns execution mechanics: process spawn, cwd, env allowlist, path append, sandbox, timeout, output capture, and truncation. Command rewrite is not execution. It is policy applied to the model's tool-call arguments before any tool sees them.

Putting rewrite in `ExecTool` creates three bad couplings:

1. **Tool-specific policy leak**: a global decision about command compression becomes a private behavior of one tool.
2. **Future duplication**: if upstream later has `shell`, `bash`, MCP shell, or remote exec tools, each one needs its own `rtk` knobs and edge cases.
3. **Bad observability seam**: runner hooks, checkpoints, and subagent runner see the pre-rewrite arguments while the tool secretly executes different arguments. A hook mutates the `ToolCallRequest` before execution, so the execution boundary has one truth.

The correct layer is `AgentHook.before_execute_tools()`: after the LLM has produced tool calls, before the runner dispatches them. That is the narrowest seam that is still above individual tools.

### 6.2 Config schema contract

Add:

```python
class CommandRewriteConfig(Base):
    """Cross-cutting tool-argument rewrite hook configuration."""

    enabled: bool = False
    verbose: bool = False
    timeout: float = 5.0
```

Attach to tools:

```python
command_rewrite: CommandRewriteConfig = Field(default_factory=CommandRewriteConfig)
```

Defaults mean disabled. A default config must not spawn `rtk`, must not alter commands, and must not add a hook to `_extra_hooks`.

CamelCase config must work through `Base` aliases:

```json
{
  "tools": {
    "commandRewrite": {
      "enabled": true,
      "verbose": true,
      "timeout": 2.5
    }
  }
}
```

Python API uses `config.tools.command_rewrite`.

`path_append` is intentionally not a `CommandRewriteConfig` field in the source behavior. The hook accepts a constructor-only `path_append` value supplied from `ExecToolConfig.path_append` during injection. This keeps user config DRY: if `rtk` lives in a custom bin dir, the same path extension used for shell execution also lets the hook find `rtk`.

### 6.3 Hook behavior contract

`CommandRewriteHook.before_execute_tools()`:

- if disabled, return immediately and do not spawn subprocesses.
- iterate over `context.tool_calls`.
- only process calls where `tc.name == "exec"`.
- read `command = tc.arguments.get("command")`.
- skip if command is not a `str`.
- skip if command is blank/whitespace.
- call `_rewrite(command)`.
- if returned string is non-empty and different from original, assign:

```python
tc.arguments["command"] = rewritten
```

- if verbose, debug-log the old/new command only when changed.

Non-shell tools must not be modified. For example `read_file`, `grep`, `web_fetch`, `spawn`, and MCP tools keep their arguments exactly as supplied. Do not walk nested dictionaries looking for `command` keys; that would eventually rewrite the wrong thing.

### 6.4 rtk process contract

Run:

```python
proc = await asyncio.create_subprocess_exec(
    "rtk", "rewrite", command,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.DEVNULL,
    env=env,
)
stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
```

Semantics:

- exit code `0` + non-empty stdout => successful rewrite.
- exit code `3` + non-empty stdout => successful rewrite. `rtk` 0.37+ uses this for successful rewrite despite older docs.
- exit code `1` => no rewrite available; passthrough.
- any other exit code, even with stdout => failure; passthrough.
- empty stdout under any code => passthrough.
- timeout, `FileNotFoundError`, subprocess error, decode error, or any exception => passthrough.

Decode stdout with default text decoding as fork did (`stdout.decode().strip()` is enough). Do not include stderr in tool output or user-visible messages; rewrite failure is not a tool failure.

### 6.5 Main loop injection

In `AgentLoop.__init__`, after `self._extra_hooks` is initialized and after the effective exec config is known:

```python
self._extra_hooks: list[AgentHook] = list(hooks or [])
self._command_rewrite_hook: CommandRewriteHook | None = None
if command_rewrite_config and command_rewrite_config.enabled:
    self._command_rewrite_hook = CommandRewriteHook(
        enabled=True,
        verbose=command_rewrite_config.verbose,
        timeout=command_rewrite_config.timeout,
        path_append=_tc.exec.path_append,
    )
    self._extra_hooks.append(self._command_rewrite_hook)
```

Order matters mildly. Existing caller-provided hooks should run before command rewrite only if they are meant to observe raw model arguments; fork appended the rewrite hook to `_extra_hooks`. Preserve that unless tests show source did the opposite. The important invariant is that `CommandRewriteHook` runs before `_execute_tools()`.

If `command_rewrite_config` is `None` or `enabled=False`, do not create the hook. Tests should assert both `_extra_hooks` and `_command_rewrite_hook` reflect that.

### 6.6 Subagent hook propagation

Main loop and subagents must share the same rewrite semantics. A spawned subagent uses a separate `AgentRunner`, so main-loop `_extra_hooks` do not magically apply.

Add a narrow propagation path:

```python
self.subagents = SubagentManager(
    ...,
    hooks=self._extra_hooks,
)
```

Inside `SubagentManager`:

```python
self._extra_hooks = list(hooks or [])
```

When building the subagent run spec:

```python
base_hook = _SubagentHook(task_id, status)
hook = CompositeHook([base_hook] + self._extra_hooks) if self._extra_hooks else base_hook
...
hook=hook
```

This includes `CommandRewriteHook` and any pre-existing loop extra hooks. If execution worries about sharing one hook instance concurrently across multiple subagents, inspect hook state. `CommandRewriteHook` is configuration-only and has no per-call mutable state, so sharing is safe. Do not add cloning machinery.

Do not add Pack5 trace hooks, log dirs, or model override while touching this seam.

### 6.7 Docs scope

Create `nanobot/agent/hooks/CLAUDE.md` with only local architecture notes:

- directory tree;
- `rewrite.py` responsibility;
- hook vs tool boundary;
- how to add future cross-cutting hooks;
- fail-safe rule for external dependencies.

If adding user-facing docs, keep it to command rewrite config. Do not rewrite general bootstrap docs or SOUL docs.

## 7. TDD task sequence

Run this pack with strict TDD. Write the failing test first, then the minimum implementation for that task, then the focused test command. Do not run implementation tests before code exists just to fish around; this sequence is the contract.

### Task 1 — Config schema defaults and aliases

Add `tests/config/test_command_rewrite_config.py`.

Test cases:

1. `CommandRewriteConfig()` defaults:
   - `enabled is False`
   - `verbose is False`
   - `timeout == 5.0`
2. `ToolsConfig()` has `command_rewrite` and it is default-disabled.
3. snake_case construction works:

   ```python
   ToolsConfig(command_rewrite={"enabled": True, "verbose": True, "timeout": 2.5})
   ```

4. camelCase construction works:

   ```python
   ToolsConfig(commandRewrite={"enabled": True})
   ```

5. Full `Config` model accepts JSON-style `tools.commandRewrite` if current tests already have a helper for raw dict config. If that is awkward, `ToolsConfig` alias coverage is sufficient because `Base` owns alias behavior.

Implementation:

- add `CommandRewriteConfig` in `nanobot/config/schema.py`.
- add `command_rewrite` to `ToolsConfig`.

Focused verification:

```bash
pytest tests/config/test_command_rewrite_config.py -q
```

### Task 2 — Prove ExecTool has no embedded rewrite seam

Add a guard test, preferably in `tests/tools/test_shell.py` if it exists, otherwise create `tests/tools/test_shell_no_rtk_rewrite.py`.

Test intent:

- `ExecToolConfig` must not expose old rewrite config fields:
  - no `rtk_enabled`
  - no `rtk_verbose`
  - no `rtk_timeout`
  - no `command_rewrite`
- `ExecTool` must not have old private helper `_rtk_rewrite`.
- `ExecTool.execute(command="echo ok")` must not call `asyncio.create_subprocess_exec("rtk", "rewrite", ...)` before running the shell command. Prefer structural assertions over brittle subprocess mocking if existing shell tests are complex.

Example structural test:

```python
from nanobot.agent.tools.shell import ExecTool, ExecToolConfig


def test_exec_tool_has_no_rtk_rewrite_api():
    fields = ExecToolConfig.model_fields
    assert "rtk_enabled" not in fields
    assert "rtk_verbose" not in fields
    assert "rtk_timeout" not in fields
    assert "command_rewrite" not in fields
    assert not hasattr(ExecTool, "_rtk_rewrite")
```

This is not busywork. It prevents the obvious architectural regression: someone sees `2713688e` and shoves the feature back into `ExecTool` because it is easier.

Focused verification:

```bash
pytest tests/tools/test_shell_no_rtk_rewrite.py -q
# or the specific test in tests/tools/test_shell.py
```

### Task 3 — CommandRewriteHook core behavior

Create `nanobot/agent/hooks/__init__.py`, `nanobot/agent/hooks/rewrite.py`, and `tests/agent/hooks/test_command_rewrite.py`.

Use helpers like the fork test:

```python
def _exec_tc(command: str, tid: str = "call_1") -> ToolCallRequest:
    return ToolCallRequest(id=tid, name="exec", arguments={"command": command})


def _ctx(tool_calls: list[ToolCallRequest]) -> AgentHookContext:
    return AgentHookContext(iteration=0, messages=[], tool_calls=tool_calls)


def _mock_proc(stdout: bytes = b"", returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    return proc
```

Test cases:

1. disabled hook does not call subprocess and preserves command.
2. enabled hook rewrites one `exec` command on exit `0` with stdout.
3. enabled hook rewrites multiple `exec` calls independently.
4. non-`exec` tools are ignored and subprocess is not called.
5. `exec` missing `command` arg is ignored.
6. `exec` command arg is non-string is ignored.
7. blank command is ignored.
8. subprocess spawn error fails safe and preserves original.
9. exit code `1` with no stdout preserves original.
10. exit code `3` with stdout rewrites.
11. unexpected exit code `2` with stdout preserves original.
12. timeout preserves original.
13. `verbose=True` logs on actual change. Keep this non-brittle; assert no exception or capture loguru debug if existing tests already do that.
14. `path_append` is appended to `PATH` in subprocess env when passed. This is important because config does not expose a separate command-rewrite path.

Implementation notes:

- `CommandRewriteHook` should inherit `AgentHook`.
- `__slots__ = ("_enabled", "_verbose", "_timeout", "_path_append")` is fine but not mandatory.
- Catch broad exceptions inside `_rewrite()` because fail-safe is the feature.
- Do not import or call `ExecTool`.

Focused verification:

```bash
pytest tests/agent/hooks/test_command_rewrite.py -q
```

### Task 4 — Composite hook ordering/mutation check if needed

Inspect `tests/agent/test_hook_composite.py` first. Upstream already has composite hook tests and an empty-hooks test that calls `before_execute_tools`. If there is no test proving hooks run in order for `before_execute_tools`, add one.

Test idea:

```python
@pytest.mark.asyncio
async def test_composite_before_execute_tools_ordering_and_shared_context():
    seen = []
    class H1(AgentHook):
        async def before_execute_tools(self, context):
            context.tool_calls[0].arguments["command"] = "one"
            seen.append("h1")
    class H2(AgentHook):
        async def before_execute_tools(self, context):
            seen.append(context.tool_calls[0].arguments["command"])
    ...
    assert seen == ["h1", "one"]
```

Only add this if current coverage is missing. Do not churn this file for aesthetics.

Focused verification:

```bash
pytest tests/agent/test_hook_composite.py -q
```

### Task 5 — Main AgentLoop injection

Add `tests/agent/test_loop_rewrite_hook_injection.py`.

Test setup can mimic fork tests but adapt to current upstream constructor requirements. Current `AgentLoop` can be built with a mocked bus/provider:

```python
bus = MagicMock()
provider = MagicMock()
provider.get_default_model.return_value = "dummy-model"
provider.generation = MagicMock(max_tokens=4096)
loop = AgentLoop(
    bus=bus,
    provider=provider,
    workspace=tmp_path,
    tools_config=ToolsConfig(
        exec=ExecToolConfig(enable=False, path_append="/custom/bin"),
        command_rewrite=CommandRewriteConfig(enabled=True, verbose=True, timeout=2.5),
    ),
    command_rewrite_config=CommandRewriteConfig(enabled=True, verbose=True, timeout=2.5),
)
```

Exact constructor call may be simpler after implementation. The tests should assert behavior, not constructor trivia.

Test cases:

1. `CommandRewriteConfig()` disabled => no `CommandRewriteHook` in `loop._extra_hooks`, `loop._command_rewrite_hook is None`.
2. `command_rewrite_config=None` => no hook.
3. enabled config => exactly one `CommandRewriteHook` in `_extra_hooks`, and `loop._command_rewrite_hook` points to it.
4. Hook receives `verbose`, `timeout`, and `path_append` from config/exec config. Prefer behavioral test for `path_append` through hook internals if no public attributes exist; direct private attribute assertion is acceptable in wiring tests if source did the same.
5. `AgentLoop.from_config(config, ...)` wires `config.tools.command_rewrite` into the loop. Build a minimal `Config` object or use existing config test helpers.
6. If caller passes custom `hooks=[...]` and command rewrite is enabled, both custom hooks and rewrite hook remain in `_extra_hooks`. This prevents overwriting SDK/loop hooks.

Implementation:

- import `CommandRewriteHook` and `CommandRewriteConfig` in `loop.py`.
- define effective `_tc = tools_config or ToolsConfig()` before hook injection if needed, so `exec.path_append` is available.
- append hook only when enabled.
- update `from_config()` to pass `command_rewrite_config=config.tools.command_rewrite`.

Focused verification:

```bash
pytest tests/agent/test_loop_rewrite_hook_injection.py -q
```

### Task 6 — Subagent propagation, without Pack5 trace creep

Extend `tests/agent/test_loop_rewrite_hook_injection.py` or add a focused subagent test.

Test cases:

1. Enabled main loop passes the rewrite hook into `loop.subagents`.
2. A `SubagentManager` constructed with `hooks=[rewrite_hook]` composes `_SubagentHook` and rewrite hook in `AgentRunSpec.hook`.

Possible test shape without running a real LLM:

- Create `SubagentManager` with a fake provider and fake runner.
- Monkeypatch or fake `manager.runner.run` to capture the `AgentRunSpec` and return a minimal `AgentRunResult`.
- Call the internal `_run_subagent_inner(...)` if public `spawn()` is too asynchronous/noisy. Existing `tests/agent/test_subagent.py` and `test_subagent_lifecycle.py` should guide the least ugly approach.
- Assert captured `spec.hook` is a `CompositeHook` when extra hooks exist.
- Then manually call `await spec.hook.before_execute_tools(ctx)` on an `exec` tool call with `asyncio.create_subprocess_exec` patched and assert command rewrite occurs. This proves semantics, not just object plumbing.

Implementation:

- Add `hooks: list[AgentHook] | None = None` to `SubagentManager.__init__`.
- Store `self._extra_hooks = list(hooks or [])`.
- Compose at run-site:

```python
base_hook = _SubagentHook(task_id, status)
hook: AgentHook = CompositeHook([base_hook] + self._extra_hooks) if self._extra_hooks else base_hook
```

- Pass `hook=hook` into `AgentRunSpec`.
- In `AgentLoop.__init__`, pass `hooks=self._extra_hooks` to `SubagentManager` after command rewrite injection has happened.

Do not add:

- log dirs,
- `subagent_trace`,
- `llm_logs`,
- model override,
- `runner_override` unless current upstream tests already use it and no production change is needed.

Focused verification:

```bash
pytest tests/agent/test_loop_rewrite_hook_injection.py tests/agent/test_subagent.py tests/agent/test_subagent_lifecycle.py -q
```

### Task 7 — Docs

Create `nanobot/agent/hooks/CLAUDE.md`.

Minimum content:

```markdown
# Agent Hooks

## Structure

- `__init__.py` exports hook implementations.
- `rewrite.py` owns `CommandRewriteHook`, a cross-cutting tool-call argument rewrite hook.

## Decision

Command rewrite runs in `AgentHook.before_execute_tools()`, not inside `ExecTool`.
...
```

Also add a short user-facing config note only if the repo has an obvious current config doc location. If no obvious doc exists, keep docs to `CLAUDE.md` and do not scatter config snippets into unrelated docs.

Focused verification:

```bash
test -f nanobot/agent/hooks/CLAUDE.md
```

### Task 8 — Integration/absence search

After implementation, search for regressions:

```bash
rg "rtk|command_rewrite|CommandRewrite|_rtk_rewrite|rtk_enabled|rtk_verbose" nanobot tests
```

Expected:

- `rtk` appears in `nanobot/agent/hooks/rewrite.py`, hook tests, and docs.
- `command_rewrite` / `CommandRewriteConfig` appears in schema, loop wiring, tests.
- No `rtk_enabled`, `rtk_verbose`, `rtk_timeout`, `_rtk_rewrite` in production code.
- No `rtk` in `nanobot/agent/tools/shell.py`.

If `rg` finds `rtk` in `ExecTool`/`shell.py`, stop. That is architecture regression, not an implementation detail.

## 8. Pack-level verification

When all tasks are implemented, run focused tests first:

```bash
pytest \
  tests/config/test_command_rewrite_config.py \
  tests/agent/hooks/test_command_rewrite.py \
  tests/agent/test_loop_rewrite_hook_injection.py \
  tests/agent/test_hook_composite.py \
  tests/tools/test_shell_no_rtk_rewrite.py \
  -q
```

If the shell absence test is placed in an existing file, replace the last path with that file/test node.

Then run nearby regression tests that should remain stable:

```bash
pytest \
  tests/agent/test_subagent.py \
  tests/agent/test_subagent_lifecycle.py \
  tests/agent/tools/test_subagent_tools.py \
  tests/config/test_config_paths.py \
  tests/config/test_env_interpolation.py \
  -q
```

Finally run static absence checks:

```bash
rg "_rtk_rewrite|rtk_enabled|rtk_verbose|rtk_timeout" nanobot tests && exit 1 || true
rg "rtk" nanobot/agent/tools/shell.py && exit 1 || true
```

Do not run broad implementation test suites as part of this planning task. The execution agent can run these after implementing.

## 9. Manual smoke check

After tests pass, do a manual smoke check in a disposable config/workspace, not production.

1. Ensure `rtk` is available or create a temporary fake `rtk` earlier in `PATH`:

```bash
mkdir -p /tmp/nanobot-pack4-bin
cat >/tmp/nanobot-pack4-bin/rtk <<'SH'
#!/usr/bin/env bash
if [ "$1" = "rewrite" ]; then
  printf 'echo rewritten-by-rtk\n'
  exit 3
fi
exit 2
SH
chmod +x /tmp/nanobot-pack4-bin/rtk
```

2. Build a minimal loop or hook-only script that constructs:

```python
CommandRewriteHook(enabled=True, timeout=1.0, path_append="/tmp/nanobot-pack4-bin")
```

3. Feed it:

```python
ToolCallRequest(id="c1", name="exec", arguments={"command": "echo original"})
```

4. Call `before_execute_tools()` and verify:

```python
tc.arguments["command"] == "echo rewritten-by-rtk"
```

5. Change fake `rtk` to `exit 2` with stdout and verify passthrough.

6. If doing an end-to-end agent smoke, use a fake/stub provider or existing test harness. Do not call real LLMs and do not use production config.

## 10. Rollback plan

If Pack4 breaks behavior after implementation:

1. Remove hook injection in `AgentLoop.__init__` and `from_config()` first. With schema still present but hook not injected, default runtime behavior returns to no rewrite.
2. Remove subagent hook propagation if it caused lifecycle regressions.
3. Keep `CommandRewriteConfig` only if config compatibility is needed temporarily; otherwise remove it from `ToolsConfig` and delete the config tests.
4. Delete `nanobot/agent/hooks/rewrite.py`, `nanobot/agent/hooks/__init__.py`, and `nanobot/agent/hooks/CLAUDE.md` if the whole pack is rolled back.
5. Never roll back by moving `rtk` logic into `ExecTool`. That is not rollback; that is reintroducing the bug-shaped architecture Pack4 is supposed to kill.

Because default `enabled=False`, a safe emergency mitigation is config-only: set `tools.commandRewrite.enabled=false` or remove the config block. Hook failures already pass through original commands.

## 11. Completion criteria

Pack4 is complete only when all are true:

- `CommandRewriteConfig` exists with exactly the intended public fields and defaults:
  - `enabled=False`
  - `verbose=False`
  - `timeout=5.0`
- `ToolsConfig.command_rewrite` defaults to disabled and accepts camelCase `commandRewrite`.
- `CommandRewriteHook` exists under `nanobot/agent/hooks/rewrite.py` and is exported by `nanobot.agent.hooks`.
- Hook rewrites only `exec` tool calls with string non-blank `arguments["command"]`.
- Hook mutates the `ToolCallRequest.arguments["command"]` in place before runner tool execution.
- Non-shell tools are not modified, even if they contain a `command` key.
- `rtk` exit code `0` with stdout rewrites.
- `rtk` exit code `3` with stdout rewrites.
- `rtk` exit code `1`, exit code `2`, other non-`0/3`, empty stdout, timeout, missing binary, and exceptions all preserve the original command.
- Main `AgentLoop` injects the hook only when config enables it.
- Subagent runs receive equivalent hook behavior through `SubagentManager` composition.
- No Pack5 trace/logging/model override concepts were introduced.
- `ExecTool` and `ExecToolConfig` contain no `rtk` rewrite API or implementation.
- Focused config, hook, loop injection, subagent propagation, composite-hook, and shell absence tests pass.
- Hook architecture docs exist and explicitly state why command rewrite belongs in hooks rather than tools.
