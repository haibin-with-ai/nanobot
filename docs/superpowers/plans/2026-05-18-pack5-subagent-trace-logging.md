# Pack5 — Subagent Model Override + Trace / LLM Logging Replay Plan

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

This pack is deliberately narrow: subagent model/runtime separation plus trace/LLM request-response logging. Do not smuggle session metadata, command rewrite, memory pruning, Discord, or provider-routing work into it. 把别的 pack 顺手塞进来，就是把迁移计划写成垃圾桶。

Upstream baseline inspection was performed in the isolated worktree. `nanobot/workspace/layout.py` was checked with `test -f` and is **不存在** in the current worktree. `nanobot/utils/llm_runtime.py` exists. For fork/origin comparison, optional paths were checked with `git cat-file -e`: `origin/main:nanobot/workspace/layout.py` exists; `origin/main:nanobot/utils/llm_runtime.py` and `origin/main:nanobot/providers/factory.py` are **不存在**.

## 1. Goal

Replay the fork behavior that production depends on:

1. Add explicit subagent default model configuration to `AgentDefaults`:
   - `subagent_model: str | None = None`
   - `subagent_reasoning_effort: str | None = None`
   - `subagent_max_tokens: int | None = None`
2. Keep zero-change behavior when unset: subagents use the same provider/model/generation defaults as the main agent.
3. When configured, build an independent subagent provider/runtime so background agents can use a cheaper/faster model without mutating the main agent provider.
4. Let `spawn` accept a per-spawn `model` override, resolve aliases against configured model candidates, create an isolated provider/runner for that spawn, and pass the resolved model into `AgentRunSpec`.
5. Carry `reasoning_effort` and `max_tokens` into subagent `AgentRunSpec`.
6. Add `AgentHookContext.model` and have the runner set it for every iteration so hooks and trace logs can distinguish main/subagent/model.
7. Add a `TraceHook` that writes JSONL request/response/tool-call records.
8. Give the main agent and every spawned subagent independent LLM log files under an explicit `llm_logs` directory.
9. Add concise loguru LLM request/response/tool-call logs for main and subagent agents with privacy-preserving previews.
10. Preserve hook composition compatibility with Pack4 while not implementing command rewrite here.

## 2. Non-goals

Do not include any of the following:

- Anthropic OAuth/provider routing. That is Pack1.
- Discord UX, TTS, transcription, or voice state behavior. That is Pack2.
- JSONL session message metadata or runtime session metadata schema changes. That is Pack3. Pack5 may write LLM trace files; it must not alter session message data models.
- Command rewrite / `rtk`. That is Pack4. Pack5 may rely on `AgentHook` and `CompositeHook` and may mention hook ordering; it must not design or implement `CommandRewriteHook`.
- Memory, consolidation, context pruning, or auto-compact behavior. That is Pack6.
- Grep/search/message/search workspace tool behavior. That is Pack7. Touch `spawn` only for the `model` parameter and log-dir propagation.
- Bootstrap, SOUL, broad docs, or general persona docs. That is Pack8.

## 3. Source commits

Replay behavior from these fork commits. Use them as behavior sources, not as a command to resurrect old topology.

1. `083902c3 feat(config): add subagent_model/reasoning_effort/max_tokens fields`
   - touched `nanobot/config/schema.py`, `tests/config/test_subagent_model_schema.py`.
   - Adds the three optional `AgentDefaults` fields. Defaults are all `None`.
2. `0aa55f57 feat(subagent): accept reasoning_effort and max_tokens overrides`
   - touched `nanobot/agent/subagent.py`, `tests/agent/test_subagent_model_override.py`.
   - `SubagentManager` stores generation overrides and passes them to `AgentRunSpec`.
3. `92a5c899 feat(loop): build independent provider for subagent when configured`
   - touched `nanobot/agent/loop.py`, `tests/agent/test_subagent_model_override.py`.
   - Configured `subagent_model` creates a separate provider/runner from the main agent provider.
4. `f0296b79 test(provider): verify _make_single_provider works for subagent model`
   - touched `tests/test_make_provider_fallback.py`.
   - In current upstream, provider creation moved to `nanobot/providers/factory.py`; keep the provider test but target the current factory API.
5. `e96865e4 feat(hook): expose model field on AgentHookContext + runner fills it + trace hook writes it`
   - touched `nanobot/agent/hook.py`, `nanobot/agent/runner.py`, `tests/agent/test_trace_hook_model_field.py`.
6. `e7c78354 feat(subagent): per-spawn TraceHook + independent llm_logs file`
   - touched `nanobot/agent/subagent.py`, `tests/agent/test_subagent_trace.py`.
7. `3bf4e69b feat(spawn): propagate layout.llm_logs_dir through tool to manager`
   - touched `nanobot/agent/loop.py`, `nanobot/agent/tools/spawn.py`, `tests/agent/test_spawn_tool_log_dir.py`.
   - Current upstream has no `nanobot/workspace/layout.py`; implement the same intent without pretending that module exists.
8. `0a38f353 feat(logging): LLM req/resp + toolcall logs for main and subagent agents`
   - touched `docs/plans/2026-04-21-subagent-runtime-parity.md`, `nanobot/agent/loop.py`, `nanobot/agent/runner.py`, `nanobot/agent/subagent.py`, `nanobot/config/schema.py`, `tests/agent/test_subagent_parity.py`, `tests/agent/test_subagent_timeout.py`.
   - Keep logging and timeout parity only where it intersects Pack5. Do not replay old pruner/context-window fields if Pack6 owns them.
9. `1bf703ef feat(logging): show last message preview in LLM request log`
   - touched `nanobot/agent/runner.py`, `nanobot/templates/agent/subagent_system.md`.
   - In Pack5, do not edit subagent prompt unless a failing test proves current template must change for logging.
10. `d32655ec fix(logging): improve LLM request log — drop tools count, show last message content preview`
    - touched `nanobot/agent/runner.py`.
11. `10db29bc refactor(logging): close I/O boundary in _request_model, eliminate double logging`
    - touched `nanobot/agent/runner.py`, `nanobot/agent/subagent.py`.
12. `82e66e95 fix(logging): LLM request preview — collapse to single line, truncate at 256 chars`
    - touched `nanobot/agent/runner.py`.
13. `6e2e9860 feat(spawn): support per-spawn model override with alias resolution`
    - touched `nanobot/agent/loop.py`, `nanobot/agent/subagent.py`, `nanobot/agent/tools/spawn.py`.

Source commit touched-file list was inspected with `git show --name-only` in the isolated worktree.

## 4. Files expected to change

Expected production files:

- `nanobot/config/schema.py`
  - Add the three optional subagent fields to `AgentDefaults` near main model/generation defaults:
    - `subagent_model: str | None = None`
    - `subagent_reasoning_effort: str | None = None`
    - `subagent_max_tokens: int | None = None`
  - Current upstream already has `model_preset`, `model`, `provider`, `max_tokens`, `context_window_tokens`, `temperature`, `reasoning_effort`, `fallback_models`, and `fallback_cooldown_s`.
  - Current upstream has `Base` with camelCase aliasing, so JSON config can use `subagentModel`, `subagentReasoningEffort`, `subagentMaxTokens` while Python uses snake_case.

- `nanobot/providers/factory.py`
  - Current upstream file exists in the replay branch, but `origin/main` did not have it. Use current branch architecture.
  - Add or expose a helper for building a single independent provider for an arbitrary model without mutating global/default provider state. Preferred shape:
    - `build_provider_snapshot(config, preset=ModelPresetConfig(...))` for configured subagent defaults, if sufficient.
    - Or add a private helper `build_provider_for_model(config, model: str, *, generation_from: ModelPresetConfig | None = None) -> ProviderSnapshot` only if tests show existing factory calls cannot construct a snapshot from an ad-hoc model.
  - Do **not** resurrect `nanobot.nanobot._make_single_provider`; current upstream uses provider factory snapshots.

- `nanobot/agent/hook.py`
  - Add `model: str | None = None` to `AgentHookContext`.
  - Add `TraceHook` if current branch does not already have it after earlier packs. Current upstream worktree inspection shows no `TraceHook` in `hook.py`; origin/fork version had one.
  - Keep `AgentHook.__init__(reraise: bool = False)` and `CompositeHook` exception behavior from current upstream. Do not overwrite it with the older fork shape.
  - `TraceHook` should accept `log_path: Path | str | None`, expose `set_log_path(path)`, and write JSON Lines.

- `nanobot/agent/runner.py`
  - Set `AgentHookContext(model=spec.model)` when constructing per-iteration context.
  - Add `_last_message_preview(messages) -> tuple[str, str]` helper.
  - Move loguru LLM request/response logging into `_request_model`, the I/O boundary, not scattered around callers.
  - Ensure `_request_finalization_retry` either calls `_request_model` with a hook/context or performs matching minimal logging once. Avoid double logging.
  - Log tool-call summaries after `context.tool_calls` is known, before execution, without dumping complete arguments.
  - Do not change runner semantics unrelated to logging.

- `nanobot/agent/subagent.py`
  - Extend `SubagentManager.__init__` with:
    - `reasoning_effort: str | None = None`
    - `max_tokens: int | None = None`
    - `config: Config | None = None` for alias resolution/provider creation, or a smaller resolver callback if that keeps dependencies cleaner.
    - `llm_logs_dir: Path | None = None`
    - `hooks: list[AgentHook] | None = None` only if Pack4 has added hook propagation; otherwise leave this as the Pack4 integration seam.
  - Store `self.reasoning_effort` and `self.max_tokens`; pass them to `AgentRunSpec`.
  - Add `_resolve_model(alias: str) -> tuple[LLMProvider, str]` using current provider factory, not old `nanobot.nanobot._make_single_provider`.
  - Extend `spawn(..., model: str | None = None, log_dir: Path | None = None, ...)`.
  - For each spawn, create a per-subagent trace file and compose hooks as `CompositeHook([*extra_hooks, _SubagentHook(...), trace_hook])` unless Pack4 tests require rewrite before trace. The invariant is: cross-cutting mutation hooks run before trace so trace sees final tool calls; subagent status hook still records events.
  - If per-spawn model override is present, run that spawn with a new `AgentRunner(provider_override)` and `model_override`. Do not mutate `self.provider`, `self.runner`, or the main loop provider.

- `nanobot/agent/tools/spawn.py`
  - Add `model` to the tool schema via `StringSchema`.
  - Current upstream `SpawnTool` is `ContextAware` and uses `RequestContext`; preserve that design. Do not revert to the older fork’s plain `set_context(channel, chat_id, log_dir)` only shape.
  - Add a contextvar for `llm_logs_dir` only if `RequestContext` or tool setup can provide it. Otherwise have `SpawnTool` call manager default `llm_logs_dir` and keep `log_dir` optional.
  - Pass `model=model` into `SubagentManager.spawn()`.

- `nanobot/agent/tools/context.py` or whichever current file defines `RequestContext`
  - Change only if needed to pass `llm_logs_dir` from loop to context-aware tools.
  - If changed, add `llm_logs_dir: Path | None = None` as an optional field. Do not add session metadata fields.

- `nanobot/agent/loop.py`
  - Build a subagent provider/runtime from config defaults during `AgentLoop.__init__` / `from_config` wiring.
  - Pass `subagent_provider`, `subagent_model`, `subagent_reasoning_effort`, `subagent_max_tokens`, `config`, and `llm_logs_dir` into `SubagentManager`.
  - On runtime model switch (`_apply_provider_snapshot` currently calls `self.subagents.set_provider(provider, model)`), do not clobber a configured independent subagent provider. Add an explicit flag like `self._subagent_uses_main_provider: bool`. If true, keep current behavior; if false, leave subagent runtime alone.
  - Add main `TraceHook` to `_extra_hooks` only once and set its log path per session/turn. If the project already has trace injection after Pack4, compose rather than replace.
  - Derive `llm_logs_dir` by the path strategy below; do not import a nonexistent workspace layout module.

- `nanobot/nanobot.py`
  - Only adjust if the programmatic facade needs to pass config/provider factory data for subagent defaults. Current upstream `Nanobot.from_config()` calls `AgentLoop.from_config()`; prefer keeping all wiring in `AgentLoop.from_config()`.

Expected test files:

- `tests/config/test_subagent_model_schema.py` — create or replay.
- `tests/agent/test_subagent_model_override.py` — create or extend.
- `tests/agent/test_trace_hook_model_field.py` — create.
- `tests/agent/test_subagent_trace.py` — create.
- `tests/agent/test_spawn_tool_log_dir.py` — create if log-dir propagation touches spawn/tool context.
- `tests/agent/test_subagent_parity.py` — only logging/runtime parity tests that do not involve Pack6 pruning.
- `tests/providers/test_subagent_provider_factory.py` or extend existing provider factory tests. Current upstream has `tests/providers/test_github_copilot_routing.py`; `tests/test_make_provider_fallback.py` exists in source commit but is not the current provider factory location.
- Existing tests to preserve: `tests/agent/test_subagent.py`, `tests/agent/test_subagent_lifecycle.py`, `tests/agent/tools/test_subagent_tools.py`, `tests/agent/test_hook_composite.py`, `tests/config/test_model_presets.py`, `tests/config/test_config_migration.py`.

## 5. Upstream baseline observations

Checked in the isolated worktree:

- Current branch is `sync-upstream-2026-05-replay`, ahead of `upstream/main` by plan commits. Merge base and `upstream/main` are `ba38f9083291a899d62c9b4b2a7b46429c39b062`.
- `nanobot/agent/hook.py` currently has `AgentHookContext` without `model`, `AgentHook`, `CompositeHook`, and `SDKCaptureHook`. It has no `TraceHook` in this worktree baseline.
- `nanobot/agent/runner.py` already has `AgentRunSpec` with `model`, `temperature`, `max_tokens`, `reasoning_effort`, `session_key`, `provider_retry_mode`, and `llm_timeout_s`. `_request_model()` is the right I/O boundary. It currently does not log request/response previews.
- `nanobot/agent/subagent.py` currently creates `self.runner = AgentRunner(provider)`, stores `self.model`, builds tools with `ToolLoader`, and runs `AgentRunSpec(model=self.model, ...)`. It does not carry subagent `reasoning_effort`, `max_tokens`, per-spawn model overrides, or trace files.
- `nanobot/agent/tools/spawn.py` currently has schema params `task` and `label` only, is `ContextAware`, stores origin/session in `ContextVar`s, enforces concurrency, and calls `SubagentManager.spawn(...)` without model/log-dir.
- `nanobot/agent/loop.py` currently passes the main provider/model directly into `SubagentManager`. `_apply_provider_snapshot()` updates main provider/runner and then calls `self.subagents.set_provider(provider, model)`. This must become conditional when subagent has an independent provider.
- `nanobot/config/schema.py` currently has `ModelPresetConfig` and `AgentDefaults.model_preset`. This did not exist in older fork commits, so provider creation should use presets/snapshots where possible.
- `nanobot/providers/factory.py` currently exists and exposes `ProviderSnapshot`, `_make_provider_core`, `make_provider`, `provider_signature`, `build_provider_snapshot`, and `load_provider_snapshot`. `origin/main:nanobot/providers/factory.py` was checked with `git cat-file -e` and is **不存在** because this file is already a replay-branch change.
- `nanobot/nanobot.py` is a thin SDK facade and should probably remain thin.
- Optional current files: `nanobot/workspace/layout.py` was checked with `test -f` and is **不存在**. Do not `read_file` it. `nanobot/utils/llm_runtime.py` exists and defines `LLMRuntime`, `LLMRuntimeResolver`, `static_llm_runtime`; use it only if it actually simplifies provider/model pairing.
- Fork/origin optional files: `origin/main:nanobot/workspace/layout.py` exists and had `WorkspaceLayout.llm_logs_dir`; `origin/main:nanobot/utils/llm_runtime.py` is **不存在**.
- Existing tests found: `tests/agent/test_subagent.py`, `tests/agent/test_subagent_lifecycle.py`, `tests/agent/tools/test_subagent_tools.py`, `tests/agent/test_hook_composite.py`, `tests/providers/test_github_copilot_routing.py`, `tests/config/test_config_migration.py`, `tests/config/test_config_paths.py`, `tests/config/test_dream_config.py`, `tests/config/test_env_interpolation.py`, `tests/config/test_model_presets.py`.
- No `tests/tools/test_spawn*` file was found with the inspected glob.

## 6. Design decisions

### 6.1 Subagent default model config data model

Add these fields to `AgentDefaults`:

```python
subagent_model: str | None = None
subagent_reasoning_effort: str | None = None
subagent_max_tokens: int | None = None
```

Defaults are all `None`.

Relationship to main agent:

- `subagent_model is None`: subagent uses the main loop provider and `self.model`. `subagent_reasoning_effort` should not independently trigger a new provider. Generation settings in `AgentRunSpec` remain inherited/default unless explicitly set by fields below.
- `subagent_model is not None`: subagent manager receives an independent provider built for that model, `self.model` becomes the configured subagent model, and main provider remains untouched.
- `subagent_reasoning_effort is None`: pass `None` into subagent `AgentRunSpec`; provider default behavior applies.
- `subagent_reasoning_effort is not None`: pass it into `AgentRunSpec.reasoning_effort` for every subagent run.
- `subagent_max_tokens is None`: pass `None` unless current provider factory requires copying a preset’s max tokens. Do not silently use main `max_tokens` in `AgentRunSpec` if the existing runner/provider already injects provider generation defaults. The fork comment said “None → inherit main agent max_tokens”, but current upstream has `ModelPresetConfig` and provider generation defaults; verify with a test. If the execution agent finds that `None` does **not** inherit main max tokens in current architecture, set it explicitly from the effective default preset and document that in code.
- `subagent_max_tokens is not None`: pass it into `AgentRunSpec.max_tokens` for every subagent run.

Do not add a nested `SubagentConfig` unless broader config refactor is already present. This pack replays fork production fields.

### 6.2 Independent provider lifecycle

Provider lifecycle must be boring:

- At `AgentLoop.from_config()` / `AgentLoop.__init__` time, inspect `config.agents.defaults.subagent_model`.
- If unset, pass the main provider to `SubagentManager` and set `self._subagent_uses_main_provider = True`.
- If set, build a new provider snapshot for that model using current provider factory. Pass `snapshot.provider` and `snapshot.model` to `SubagentManager`; set `self._subagent_uses_main_provider = False`.
- If building the configured subagent provider fails, log a warning and fall back to main provider/model. Keep startup alive. This matches fork production behavior.
- On main model switch / provider snapshot apply, update subagent manager only when `self._subagent_uses_main_provider` is true. If false, main model switches must not mutate subagent provider or model.
- Per-spawn model override creates a temporary provider and a temporary `AgentRunner(provider_override)` inside that spawn. It must not assign to `self.provider`, `self.runner`, or `self.model`.
- No provider should be reused across main/subagent by accidental mutation. If a provider has mutable `generation` settings, creating an independent provider is mandatory for configured subagent model and per-spawn override.

### 6.3 Per-spawn model override and alias resolution

Add `model` to `SpawnTool` schema:

```python
model=StringSchema(
    "Optional LLM model to use for this subagent. Supports partial aliases "
    "from configured model candidates. Uses the default subagent model if omitted."
)
```

Execution flow:

1. LLM calls `spawn(task=..., label=..., model=...)`.
2. `SpawnTool.execute()` receives `model: str | None` and passes it unchanged to `SubagentManager.spawn(model=model, ...)`.
3. `SubagentManager.spawn()` stores the requested override for that task and schedules `_run_subagent(..., model_override_request=model)`.
4. `_run_subagent()` resolves before building `AgentRunSpec`:
   - If no override: use `self.runner` and `self.model`.
   - If override: call `_resolve_model(override)` and create `AgentRunner(provider_override)`.
5. Pass `model=resolved_model` to `AgentRunSpec`.

Alias resolution policy:

- Candidate list should include:
  - `config.agents.defaults.model`
  - `config.agents.defaults.subagent_model` when set
  - all `config.agents.defaults.fallback_models`
  - all `config.model_presets[name].model` values if `model_presets` exists in current schema
- Remove duplicates while preserving order.
- Exact match wins.
- If no exact match, substring/contains match is allowed only when it produces exactly one candidate.
- Multiple substring matches raise `ValueError("Ambiguous model alias ...")` and the subagent should announce failure cleanly.
- No match means treat the input as a full model name and ask provider factory to build it. If provider factory rejects it, the subagent should announce failure cleanly.
- Alias resolution must return `(provider, full_model_name)` and should be covered by unit tests.

### 6.4 AgentHookContext.model and TraceHook

`AgentHookContext.model` is not configuration. It is per-iteration observability state.

The runner must construct context like:

```python
context = AgentHookContext(
    iteration=iteration,
    messages=messages,
    model=spec.model,
)
```

Uses:

- `TraceHook` writes `model` into every JSONL entry.
- Loguru request/response lines use `spec.model` directly; hooks can use `context.model` without needing the run spec.
- Subagent traces prove whether a run used default subagent model, main model fallback, or per-spawn override.

`TraceHook` JSONL entry shape should be stable and sparse:

```json
{
  "type": "iteration",
  "iteration": 1,
  "model": "anthropic/claude-...",
  "messages": ["sanitized message summaries, not raw secret dumps"],
  "response": {
    "finish_reason": "tool_calls",
    "usage": {"prompt_tokens": 1, "completion_tokens": 2},
    "content_preview": "...",
    "tool_calls": [{"name": "read_file", "id": "...", "arguments_preview": "..."}]
  },
  "tool_events": [{"name": "read_file", "status": "ok", "detail": "..."}],
  "elapsed_ms": 123
}
```

Do not store full raw message content if it may include secrets. TraceHook can keep sanitized messages, but it should truncate content. The fork stored more; current replay should bias toward safe observability.

### 6.5 LLM logs path strategy

The fork had `WorkspaceLayout.llm_logs_dir`. Current upstream worktree does not have `nanobot/workspace/layout.py`. Therefore Pack5 must use a minimal path strategy instead of inventing imports:

- Main log directory default: `workspace / "llm_logs"`.
- Ensure directory exists with `mkdir(parents=True, exist_ok=True)` lazily when first writing.
- Main file name:
  - If a session key is available: sanitize it to filesystem-safe characters and write `main-{sanitized_session_key}.jsonl`.
  - If no session key: `main-sdk-default.jsonl` or `main-cli-direct.jsonl` depending available context. Keep tests tolerant by checking prefix/suffix, not exact timestamp.
- Subagent file name: `subagent-{task_id}.jsonl` under the same `llm_logs_dir`.
- If later packs add a layout object, the only integration point should be an optional `llm_logs_dir: Path | None` passed into `AgentLoop`/`SubagentManager`/`SpawnTool`; do not spread path construction across tools.
- If `RequestContext` can carry a per-channel log directory after Pack3/Discord integration, `SpawnTool` may pass that directory into `SubagentManager.spawn(log_dir=...)`. Otherwise manager default is sufficient.

Main and each subagent must use independent files. Do not write all spawned agents into the main file; debugging concurrent subagents becomes soup.

### 6.6 LLM request/response/toolcall logging boundary

All loguru LLM request/response lines belong in `AgentRunner._request_model()` because that is where provider I/O happens. Anything else is duplicate noise.

Request log:

```text
LLM request → model=<model> messages=<count> last=<role>: <preview>
```

For subagents, use a label if `AgentRunSpec` gains one or if current code already has it after other packs:

```text
Subagent <task_id> LLM request → model=<model> messages=<count> last=<role>: <preview>
```

If `AgentRunSpec` does not have `log_label`, add `log_label: str | None = None` only for logging. Do not attach session metadata.

Response log:

```text
LLM response ← model=<model> finish_reason=<finish_reason> usage=<usage_dict> elapsed_ms=<ms>
```

Tool-call log:

```text
Tool calls requested: read_file, grep, edit_file
```

or with label:

```text
Subagent <task_id> tool calls requested: read_file, grep
```

Logging refinements to implement:

- Last message preview collapses whitespace to a single line: `" ".join(text.split())`.
- Last message preview truncates to 256 characters.
- For tool messages, include `name: preview` but not full tool output.
- For list content, prefer the first text block. If no text block, stringify and truncate.
- Drop noisy `tools=<count>` from request logs. It is not useful and made log lines noisy.
- Do not dump full arguments, full message chains, full tool outputs, API keys, headers, or provider config.
- `_request_model()` must time the provider call and log exactly one response line per request. Finalization retry should not double-log the same request.

### 6.7 Hook composition and Pack4 relationship

Pack4 owns command rewrite. Pack5 owns trace/logging. They meet only through `AgentHook` composition.

Expected ordering when both packs exist:

1. Command rewrite or other mutation hooks.
2. Subagent status hook (`_SubagentHook`) if needed for status updates.
3. TraceHook.

The reason is simple: trace logs should show the final tool-call arguments that will actually execute. Pack5 should not implement command rewrite and should not import `rtk`. It may write tests proving `CompositeHook` preserves order if existing Pack4 tests do not already cover it.

## 7. TDD task sequence

Do these as red-green-refactor. No implementation without a failing test first.

### Task 1 — Config schema for subagent defaults

Tests first:

- Create `tests/config/test_subagent_model_schema.py`.
- Assert default `AgentDefaults()` has:
  - `subagent_model is None`
  - `subagent_reasoning_effort is None`
  - `subagent_max_tokens is None`
- Assert snake_case and camelCase input both parse:
  - `subagent_model` / `subagentModel`
  - `subagent_reasoning_effort` / `subagentReasoningEffort`
  - `subagent_max_tokens` / `subagentMaxTokens`

Implementation:

- Add fields to `AgentDefaults` in `nanobot/config/schema.py`.
- Do not touch provider code yet.

### Task 2 — Runner context carries model

Tests first:

- Create `tests/agent/test_trace_hook_model_field.py` with a fake provider returning a simple `LLMResponse`.
- Use a custom hook whose `before_iteration` or `after_iteration` captures `context.model`.
- Run `AgentRunner.run(AgentRunSpec(model="test/model", ...))` and assert captured model is `"test/model"`.

Implementation:

- Add `model` field to `AgentHookContext`.
- Set it when runner constructs context.

### Task 3 — TraceHook writes model-aware JSONL safely

Tests first:

- In `tests/agent/test_trace_hook_model_field.py` or `tests/agent/test_subagent_trace.py`, instantiate `TraceHook(tmp_path / "trace.jsonl")`.
- Run a one-iteration fake provider.
- Assert file exists and contains one JSON line with `model`, `iteration`, response finish reason, and usage.
- Include a long last message and assert trace/log preview truncation if TraceHook stores previews.

Implementation:

- Add `TraceHook` to `nanobot/agent/hook.py`.
- Use JSONL append mode.
- Create parent directory lazily.
- Keep sanitization helpers private and small.

### Task 4 — LLM request/response logging in runner

Tests first:

- Add runner logging tests using pytest `caplog` or loguru capture conventions already used in repo.
- Assert request line contains model, message count, and last message preview.
- Assert preview is one line and `len(preview) <= 256`.
- Assert request line does not include `tools=` count.
- Assert response line contains finish reason, usage, and elapsed.
- Assert tool-call log names tools but does not dump full argument JSON.

Implementation:

- Add `_last_message_preview()` helper to runner.
- Add request/response logging inside `_request_model()`.
- Add tool-call summary after response parsing / before tool execution.
- Keep `_request_finalization_retry()` from producing duplicate logs. If adding a shared helper is cleaner, do it.

### Task 5 — Subagent generation overrides

Tests first:

- Extend/create `tests/agent/test_subagent_model_override.py`.
- Instantiate `SubagentManager(reasoning_effort="low", max_tokens=1234)` with a fake runner/provider.
- Call `_run_subagent()` or `spawn()` with controlled fake runner so test does not hit real LLM.
- Assert `AgentRunSpec.reasoning_effort == "low"` and `max_tokens == 1234`.

Implementation:

- Add constructor fields to `SubagentManager`.
- Pass fields into `AgentRunSpec`.
- Keep existing subagent tests passing: tool loader isolation, no `message`, no `spawn` inside subagent tools.

### Task 6 — Independent configured subagent provider lifecycle

Tests first:

- In `tests/agent/test_subagent_model_override.py`, build a config where main model and subagent model differ.
- Monkeypatch current provider factory helper to return distinct fake providers/snapshots.
- Assert `AgentLoop.from_config()` or lower-level constructor gives `SubagentManager` the subagent provider/model when `subagent_model` is set.
- Assert when unset, subagent manager receives main provider/model.
- Assert `_apply_provider_snapshot()` updates subagent only when using main provider; it does not override configured independent subagent provider.
- Add a provider-factory unit test equivalent to `f0296b79` using current `nanobot/providers/factory.py`, not old `_make_single_provider`.

Implementation:

- Add provider creation helper only if existing `build_provider_snapshot(config, preset=...)` cannot express an ad-hoc model.
- Wire config through `AgentLoop.from_config()` / `__init__`.
- Add `self._subagent_uses_main_provider` flag.
- Make `SubagentManager.set_provider(provider, model)` continue to exist for main-provider mode.

### Task 7 — Spawn tool accepts model override

Tests first:

- Create/extend `tests/agent/tools/test_subagent_tools.py` or a new focused test.
- Assert `SpawnTool.parameters` includes `model` and `required == ["task"]`.
- Mock manager and call `execute(task="x", model="haiku")`; assert manager receives `model="haiku"`.
- Preserve concurrency-limit behavior.

Implementation:

- Add `model` schema field.
- Add `model` parameter to `execute()`.
- Pass through to manager.
- Preserve `ContextAware` and contextvars.

### Task 8 — Per-spawn alias resolution and isolated runner

Tests first:

- In `tests/agent/test_subagent_model_override.py`, create config candidates:
  - default model `anthropic/claude-opus-4-5`
  - subagent model `openrouter/anthropic/claude-3-haiku`
  - fallback model `openrouter/google/gemini-2.5-pro`
  - model preset model if current schema supports it
- Assert exact alias returns exact candidate.
- Assert unique substring like `haiku` returns the haiku model.
- Assert ambiguous substring raises `ValueError`.
- Assert unknown full name calls provider factory with that name.
- Assert per-spawn override uses a temporary runner/provider and leaves manager default `runner`, `provider`, and `model` unchanged.

Implementation:

- Add `_model_candidates()` helper if needed.
- Add `_resolve_model()` using current provider factory.
- In `_run_subagent()`, branch to override runner/model only for that run.
- On resolution error, announce error and mark status error rather than crashing the main loop.

### Task 9 — LLM log directory and per-spawn trace files

Tests first:

- Create `tests/agent/test_subagent_trace.py`.
- Instantiate manager with `llm_logs_dir=tmp_path / "llm_logs"`.
- Run a fake subagent and assert file `subagent-<task_id>.jsonl` exists.
- Assert two subagents write two distinct files.
- Assert TraceHook entry includes task’s resolved model.
- If touching `RequestContext`, add `tests/agent/test_spawn_tool_log_dir.py` proving `SpawnTool` passes context log dir to manager; if not touching it, test manager default path.

Implementation:

- Add `llm_logs_dir` to manager.
- Add helper `_subagent_log_path(task_id, log_dir=None)`.
- Compose per-spawn `TraceHook(log_path)`.
- Add main loop log-dir computation `workspace / "llm_logs"` and pass it to manager.
- Add main TraceHook only if main trace logging is required by tests; set path based on active session key before runner call.

### Task 10 — Main TraceHook file

Tests first:

- Add a loop-level test with temp workspace and fake provider.
- Process one direct message.
- Assert `workspace/llm_logs/main-*.jsonl` exists and contains `model`.
- Assert adding hooks from SDK still composes with TraceHook; SDK capture should still collect messages/tools.

Implementation:

- In `AgentLoop.__init__`, create `self._trace_hook = TraceHook()` and include it in `_extra_hooks`, or maintain a separate internal hook list so SDK hook replacement in `Nanobot.run()` does not accidentally drop trace logging.
- Before `AgentRunner.run()`, set the trace hook path using session key.
- Ensure no duplicate TraceHook is added on repeated calls.

### Task 11 — Regression pass against existing subagent/hook tests

Run targeted tests only; do not run implementation/integration suites that hit real providers.

- `pytest tests/config/test_subagent_model_schema.py`
- `pytest tests/agent/test_trace_hook_model_field.py`
- `pytest tests/agent/test_subagent_model_override.py`
- `pytest tests/agent/test_subagent_trace.py`
- `pytest tests/agent/test_subagent.py tests/agent/test_subagent_lifecycle.py tests/agent/tools/test_subagent_tools.py tests/agent/test_hook_composite.py`
- Provider factory test added for this pack.

Do not mark the pack complete until these pass.

## 8. Pack-level verification

After implementation, run:

```bash
cd /root/git_code/nanobot/.worktrees/sync-upstream-2026-05-merge
pytest \
  tests/config/test_subagent_model_schema.py \
  tests/config/test_model_presets.py \
  tests/agent/test_trace_hook_model_field.py \
  tests/agent/test_subagent_model_override.py \
  tests/agent/test_subagent_trace.py \
  tests/agent/test_subagent.py \
  tests/agent/test_subagent_lifecycle.py \
  tests/agent/tools/test_subagent_tools.py \
  tests/agent/test_hook_composite.py \
  tests/providers/test_subagent_provider_factory.py
```

If `tests/providers/test_subagent_provider_factory.py` is not created because an existing provider test was extended, replace that path with the actual test file.

Also run static import checks for touched modules:

```bash
python -m py_compile \
  nanobot/config/schema.py \
  nanobot/agent/hook.py \
  nanobot/agent/runner.py \
  nanobot/agent/subagent.py \
  nanobot/agent/tools/spawn.py \
  nanobot/agent/loop.py \
  nanobot/providers/factory.py
```

Do not run real provider integration tests. Do not run implementation smoke tests that call paid/remote LLMs.

## 9. Manual smoke check

Manual smoke is for the later execution agent after tests pass, using fake/local providers where possible:

1. Create a temporary workspace.
2. Configure main model as one fake model and `subagent_model` as another fake model.
3. Run a direct agent turn that calls `spawn` with no override.
4. Confirm logs:
   - `workspace/llm_logs/main-<session>.jsonl` exists.
   - `workspace/llm_logs/subagent-<task_id>.jsonl` exists.
   - main trace entries show main model.
   - subagent trace entries show configured subagent model.
5. Run a spawn with `model="haiku"` where alias resolution is unique.
6. Confirm that subagent trace shows resolved full model and that the manager default provider/model still equals configured subagent provider/model after the run.
7. Review loguru output manually:
   - request preview is single-line and truncated.
   - no full tool outputs or full arguments appear.
   - no noisy `tools=<count>` appears.

## 10. Rollback plan

If Pack5 causes regressions:

1. Remove subagent config fields from `AgentDefaults` and associated tests.
2. Revert `AgentHookContext.model` and `TraceHook` additions if they are the failure source.
3. Remove runner logging helpers and restore `_request_model()` to previous behavior.
4. Restore `SubagentManager` to using a single provider/runner passed by `AgentLoop`.
5. Remove `model` from `SpawnTool` schema and execution path.
6. Delete Pack5-specific tests.

Rollback must not touch Pack1/Pack2/Pack3/Pack4 plan/code. In particular, do not remove `CompositeHook` or hook infrastructure that Pack4 uses.

## 11. Completion criteria

Pack5 is complete when all of these are true:

- `AgentDefaults` exposes `subagent_model`, `subagent_reasoning_effort`, and `subagent_max_tokens`, all defaulting to `None`, with camelCase parsing supported by existing aliasing.
- Unconfigured subagents still use the main provider/model and still update when main provider switches.
- Configured subagents use an independent provider/model and are not mutated by main provider switches.
- Per-spawn `model` override flows from `SpawnTool` schema to manager to runner, supports exact/unique alias resolution, rejects ambiguous aliases cleanly, and does not mutate default manager/main provider state.
- Subagent `reasoning_effort` and `max_tokens` reach `AgentRunSpec`.
- `AgentHookContext.model` is populated for main and subagent runs.
- `TraceHook` writes JSONL with model-aware entries.
- Main agent and each subagent write to independent LLM log files under a real directory. With no workspace layout module, the fallback is `workspace/llm_logs`.
- LLM request/response/toolcall loguru lines exist, are emitted at the runner I/O boundary, have one-line 256-character previews, omit noisy tool counts, and do not dump full sensitive content.
- Pack4 hook composition remains possible; Pack5 does not implement command rewrite or import `rtk`.
- Targeted tests listed in Pack-level verification pass.
