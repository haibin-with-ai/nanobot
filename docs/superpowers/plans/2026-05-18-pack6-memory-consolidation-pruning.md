# Pack6 — Memory / Consolidation / Context Pruning Replay Plan

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

Pack6 is deliberately narrow: memory consolidation controls and transient context pruning before model calls. Do not smuggle provider routing, Discord UX, runtime/session metadata, command rewrite, subagent trace logging, search/workspace tool behavior, bootstrap/SOUL/docs, or llm_logs into it. That is how a sync becomes a junk drawer.

Checked facts for this planning pass:

- Current worktree branch is `sync-upstream-2026-05-replay`.
- `HEAD` during inspection was `6845ba801073db5dfa9a0c9530d8e583d5d69cd0`.
- Merge base with `upstream/main` is `ba38f9083291a899d62c9b4b2a7b46429c39b062`.
- Optional upstream-worktree test file `tests/agent/test_context_pruner.py` is **不存在**.
- Optional fork/origin file `origin/main:nanobot/agent/pruner.py` exists.
- Optional fork/origin test file `origin/main:tests/agent/test_context_pruner.py` exists.
- Optional fork/origin test file `origin/main:tests/agent/test_consolidation_ratio.py` is **不存在**.

## 1. Goal

Replay the fork behavior production currently depends on, but fit it into the new upstream/main architecture instead of resurrecting old call sites:

1. Add `ContextPruningConfig` schema with `softTrim` / `hardClear` knobs from fork commit `b461e878`.
2. Add `ContextPruner` behavior from fork commit `9c0de5b6`: transient soft trimming and hard clearing of oversized tool results before LLM calls.
3. Integrate pruning into the agent execution path that feeds `AgentRunner._request_model()` / provider calls, corresponding to fork commit `25a01a76`, without pruning in provider classes.
4. Preserve configurable `consolidation_ratio` behavior from fork commit `6a38c378` and verify it still flows from config into `Consolidator.maybe_consolidate_by_tokens()`.
5. Compare related fork autocompact/consolidator/memory commits against upstream baseline and only replay behavior still missing after upstream’s architecture rewrite.

The resulting pack should be independently testable with targeted memory/pruning tests and should not require any production workspace or Dream-managed memory files.

## 2. Non-goals

- Pack1: no Anthropic OAuth, provider routing, provider snapshot, or fallback model work.
- Pack2: no Discord transcription, TTS, attachment, typing, UX, or channel delivery work.
- Pack3: no runtime/session metadata data model changes; do not change JSONL session metadata semantics.
- Pack4: no command rewrite, `rtk`, or `/new` command behavior changes unless a test proves a consolidation API call currently fails inside Pack6 scope. Even then, prefer fixing the Pack6 call point, not broad command rewrites.
- Pack5: no subagent trace/logging/model override/llm_logs work.
- Pack7: no grep/search/message/workspace tool behavior changes.
- Pack8: no bootstrap, SOUL, docs, or identity text changes.
- Do not edit long-term memory files such as `MEMORY.md`, `SOUL.md`, `USER.md`, `history.jsonl`, or Dream output under a real workspace.
- Do not implement session JSONL metadata, llm_logs, or runtime metadata persistence here.

## 3. Source commits

Use these as behavior references, not as blind cherry-picks:

- `b461e878 feat(config): add ContextPruningConfig schema (softTrim/hardClear)`
  - Touched: `nanobot/config/schema.py`, `tests/config/test_config_migration.py`.
  - Important behavior: config model names and camelCase aliases: `contextPruning`, `softTrim`, `hardClear`, `keepLastAssistants`, `minPrunableToolChars`.
- `9c0de5b6 feat(agent): add ContextPruner with softTrim/hardClear for tool results`
  - Touched: `nanobot/agent/pruner.py`, `tests/agent/test_context_pruner.py`.
  - Important behavior: pruning is transient, only string tool result content is mutated, image-list content is skipped.
- `25a01a76 feat(agent): integrate ContextPruner into AgentLoop._run_agent_loop`
  - Touched: old-architecture `nanobot/agent/loop.py`, `nanobot/cli/commands.py`.
  - Old integration pruned `messages` immediately before each provider chat call. New architecture routes model calls through `AgentRunner.run()`, so integrate at the runner boundary, not by copying old loop code.
- `6a38c378 feat(memory): configurable consolidation trigger ratio`
  - Touched: `nanobot/agent/loop.py`, `nanobot/agent/memory.py`, `nanobot/config/schema.py`, `nanobot/nanobot.py`, and tests.
  - Current worktree already contains most of this behavior. Treat this pack as verification plus gap-filling, not duplication.

Related fork commits identified by `git log origin/main -- nanobot/agent/memory.py nanobot/agent/autocompact.py nanobot/agent/context.py tests/agent/test_consolidator.py tests/agent/test_loop_consolidation_tokens.py tests/agent/test_context_pruner.py tests/agent/test_consolidation_ratio.py`:

- `401d1f57 fix(dream): allow LLM to retry on tool errors instead of failing immediately`
- `c3b4ebae refactor(agent): move internal prompts into packaged templates`
- `04419326 fix(memory): migrate legacy HISTORY.md even when history.jsonl is empty`
- `0a3a60a7 refactor(memory): simplify Dream config naming and rename gitstore module`
- `6e896249 feat(memory): harden legacy history migration and Dream UX`
- `d436a1d6 feat: integrate Jinja2 templating for agent responses and memory consolidation`
- `7e0c1967 fix(memory): repair Dream follow-up paths and move GitStore to utils`
- `ab026c51 refactor: extract memory consolidation to MemoryStore, slim down AgentLoop`
- `94c21fc2 feat: redesign memory system — two-layer architecture with grep-based retrieval`

Baseline inspection shows upstream already has a new, substantially richer `MemoryStore`, `Consolidator`, `Dream`, `AutoCompact`, prompt template rendering, `GitStore`, legacy migration, and retry-tolerant Dream/tool execution structure. Do not replay these related commits wholesale. Only add tests if inspection during execution reveals a concrete missing production-critical behavior.

## 4. Files expected to change

Expected production-code files:

- `nanobot/config/schema.py`
  - Add `SoftTrimConfig`, `HardClearConfig`, `ContextPruningConfig` near other agent/default config models.
  - Add `context_pruning: ContextPruningConfig = Field(default_factory=ContextPruningConfig)` to `AgentDefaults`.
  - Preserve Pydantic v2 alias behavior using existing `Base` model conventions.
- `nanobot/agent/pruner.py`
  - New module containing `ContextPruner` and small helpers.
- `nanobot/agent/runner.py`
  - Add an optional pruning config/pruner field to `AgentRunSpec` or `AgentRunner` and call it in the existing context-governance pipeline immediately before `_request_model()`.
  - Prefer `AgentRunSpec.context_pruning` over global mutable runner state, because pruning is a per-agent/default config concern and subagents may later have different runtime limits.
- `nanobot/agent/loop.py`
  - Store config-derived pruning settings on `AgentLoop`.
  - Pass them into `AgentRunSpec` in `_run_agent_loop()`.
  - Ensure `from_config()` wires `config.agents.defaults.context_pruning`.

Expected tests:

- `tests/agent/test_context_pruner.py`
  - New file, adapted from fork tests to upstream message shapes.
- `tests/agent/test_loop_runner_context_pruning.py` or additions to `tests/agent/test_runner_governance.py`
  - Prove integration occurs before provider call and persisted/session messages are not mutated.
- `tests/config/test_context_pruning_config.py` or additions to an existing config schema test file
  - Prove aliases/defaults/validation.
- Existing `tests/agent/test_consolidation_ratio.py`
  - Keep and run. Add only if a missing edge is found.

Files checked but not expected to change unless a failing Pack6 test proves a gap:

- `nanobot/agent/memory.py`
- `nanobot/agent/autocompact.py`
- `nanobot/agent/context.py`
- `nanobot/session/manager.py`

## 5. Upstream baseline observations

Checked in the isolated worktree:

- `nanobot/config/schema.py`
  - Uses Pydantic v2.
  - `Base` has `alias_generator=to_camel` and `populate_by_name=True`.
  - `AgentDefaults` already has:
    - `session_ttl_minutes` default `0`, `ge=0`, validation aliases `idleCompactAfterMinutes` and `sessionTtlMinutes`, serialization alias `idleCompactAfterMinutes`.
    - `max_messages` default `120`, `ge=0`.
    - `consolidation_ratio` default `0.5`, `ge=0.1`, `le=0.95`, validation alias `consolidationRatio`, serialization alias `consolidationRatio`.
    - `dream: DreamConfig`.
  - It does **not** contain `ContextPruningConfig`, `SoftTrimConfig`, `HardClearConfig`, `context_pruning`, `softTrim`, or `hardClear`.
- `nanobot/agent/memory.py`
  - Contains `MemoryStore`, `Consolidator`, and `Dream` in one module.
  - `Consolidator.__init__()` already accepts `consolidation_ratio: float = 0.5`.
  - `maybe_consolidate_by_tokens()` calculates `budget = self._input_token_budget` and `target = int(budget * self.consolidation_ratio)`.
  - It loops until estimated prompt tokens fall under the ratio target, archives old legal chunks, persists `_last_summary`, and respects `replay_max_messages`.
  - Dream already uses `AgentRunner`, packaged prompt templates, `GitStore`, and a fixed stale threshold source of truth.
- `nanobot/agent/autocompact.py`
  - Exists upstream. Fork/origin did **not** have `nanobot/agent/autocompact.py` at `origin/main`.
  - Provides idle-session compaction with `session_ttl_minutes`, `_last_summary` persistence, and `prepare_session()` summary injection.
  - This is upstream-absorbed behavior; do not backport old fork structures over it.
- `nanobot/agent/context.py`
  - Builds system prompt and runtime context.
  - Uses `MemoryStore` only for loading bootstrap/memory files and recent history context.
  - No pruning logic belongs here. Context building should remain about assembling prompts, not per-call truncation of tool results.
- `nanobot/agent/loop.py`
  - New upstream architecture has a state-machine turn flow and delegates model/tool iteration to `AgentRunner`.
  - `from_config()` already passes `consolidation_ratio=defaults.consolidation_ratio` and `max_messages=defaults.max_messages`.
  - `_state_build()` calls `self.consolidator.maybe_consolidate_by_tokens(ctx.session, replay_max_messages=self._max_messages)` before building prompt messages.
  - `_state_save()` schedules background `maybe_consolidate_by_tokens()` after saving the turn.
  - `_run_agent_loop()` creates `AgentRunSpec(...)` around lines inspected at `751-777` and passes context window, block limit, max tool result chars, retry mode, callbacks, and timeout. This is the practical handoff point to the runner.
- `nanobot/agent/runner.py`
  - Already performs context governance inside `AgentRunner.run()` before each model call:
    1. `_drop_orphan_tool_results(messages)`
    2. `_backfill_missing_tool_results(messages_for_model)`
    3. `_microcompact(messages_for_model)`
    4. `_apply_tool_result_budget(spec, messages_for_model)`
    5. `_snip_history(spec, messages_for_model)`
    6. repair orphan/backfill again
  - This pipeline is the new equivalent of old `AgentLoop._run_agent_loop` pre-provider preparation.
  - `_apply_tool_result_budget()` currently normalizes/truncates tool result content using `max_tool_result_chars`.
  - `_snip_history()` estimates tokens and drops old non-system messages while preserving legal starts.
  - There is no `ContextPruner` integration yet.
- `nanobot/session/manager.py`
  - Session loading/saving owns persistent JSONL-ish session history and metadata such as `_last_summary`.
  - It has role-boundary helpers such as `get_history()`, `retain_recent_legal_suffix()`, and `clear()`.
  - Pack6 must not change session metadata schema or start storing pruning markers in JSONL.
- Tests:
  - `tests/agent/test_context_pruner.py` is **不存在** in the worktree.
  - `tests/agent/test_consolidation_ratio.py` exists and already covers ratio propagation, validation, and archive count behavior with deterministic mocked estimates.
  - `tests/agent/test_consolidator.py`, `tests/agent/test_loop_consolidation_tokens.py`, `tests/agent/test_auto_compact.py`, `tests/agent/test_autocompact_unit.py`, `tests/agent/test_context_builder.py`, `tests/agent/test_context_prompt_cache.py`, and `tests/agent/test_dream.py` exist.

Fork/origin reference observations:

- `origin/main:nanobot/agent/pruner.py` exists and implements the production pruning semantics.
- `origin/main:nanobot/agent/autocompact.py` is **不存在**.
- `origin/main:tests/agent/test_context_pruner.py` exists.
- `origin/main:tests/agent/test_consolidation_ratio.py` is **不存在**.
- Fork `ContextPruner` is old-architecture and mutates a message list used in `AgentLoop`; port semantics, not placement.

## 6. Design decisions

### 6.1 ContextPruningConfig schema

Add these models to `nanobot/config/schema.py`:

```python
class SoftTrimConfig(Base):
    max_chars: int = Field(default=4000, ge=1)
    head_chars: int = Field(default=1500, ge=0)
    tail_chars: int = Field(default=1500, ge=0)

class HardClearConfig(Base):
    enabled: bool = True
    placeholder: str = "[Old tool result content cleared]"
    ratio: float = Field(default=0.5, ge=0.0, le=1.0)

class ContextPruningConfig(Base):
    enabled: bool = False
    keep_last_assistants: int = Field(default=3, ge=0)
    min_prunable_tool_chars: int = Field(default=50_000, ge=0)
    soft_trim: SoftTrimConfig = Field(default_factory=SoftTrimConfig)
    hard_clear: HardClearConfig = Field(default_factory=HardClearConfig)
```

Then add to `AgentDefaults`:

```python
context_pruning: ContextPruningConfig = Field(default_factory=ContextPruningConfig)
```

Alias and serialization requirements:

- Existing `Base` already converts snake_case to camelCase for aliases. Therefore:
  - `context_pruning` serializes to `contextPruning`.
  - `keep_last_assistants` serializes to `keepLastAssistants`.
  - `min_prunable_tool_chars` serializes to `minPrunableToolChars`.
  - `soft_trim` serializes to `softTrim`.
  - `hard_clear` serializes to `hardClear`.
- Because `populate_by_name=True`, both snake_case and camelCase should validate.
- Do not add legacy aliases unless a test proves an existing production config uses a different spelling. Random compatibility aliases are just sweeping glass under the rug.
- `enabled` defaults to `False`; this preserves upstream behavior unless production config opts in.
- Validation should reject negative sizes and out-of-range `hard_clear.ratio`.
- Add one cross-field model validator only if needed: if `head_chars + tail_chars > max_chars`, either allow it and simply skip trimming when no savings would occur, or reject it. Prefer allowing it to avoid surprising config failures; the pruner can no-op if the trim would not shrink content.

### 6.2 ContextPruner behavior boundary

Create `nanobot/agent/pruner.py` with a small `ContextPruner` class:

```python
class ContextPruner:
    def __init__(self, config: ContextPruningConfig): ...
    def prune(self, messages: list[dict[str, Any]], *, context_window_chars: int) -> list[dict[str, Any]]: ...
```

Semantics to preserve:

- If `config.enabled` is false, return the original messages unchanged.
- Count total prunable tool chars across messages where:
  - `role == "tool"`
  - `content` is `str`
- If total prunable chars `< min_prunable_tool_chars`, return unchanged.
- Determine a protection boundary from the last `keep_last_assistants` assistant messages. Tool results at or after that boundary are protected.
  - If `keep_last_assistants == 0`, protect nothing because the operator explicitly asked to allow old and recent tool results to be pruned.
  - If there are fewer assistant messages than requested, preserve the fork behavior: protect all messages, no pruning. This avoids destroying short active exchanges.
- Skip non-tool messages entirely.
- Skip tool content that is not a string.
- Skip list content containing image blocks (`type` in `("image_url", "image")`). Do not try to trim multimodal content.
- `hardClear` has priority over `softTrim`.
  - If `hard_clear.enabled` and `len(content) / context_window_chars > hard_clear.ratio`, replace content with `hard_clear.placeholder`.
  - Guard `context_window_chars <= 0`: hardClear should not trigger on division by zero; fall back to softTrim checks.
- `softTrim` applies when `len(content) > soft_trim.max_chars`.
  - Replace content with a deterministic head/tail string, for example:
    `content[:head] + "\n...[trimmed {n} chars]...\n" + content[-tail:]`.
  - Keep the marker stable enough for tests. Do not include timestamps or token counts.
  - If `head_chars + tail_chars >= len(content)`, no-op.
- Return a new list / copied message dicts only when at least one message changes. Do not mutate the caller’s persistent `messages` list.
- Preserve all message identity fields other than `content`: `role`, `tool_call_id`, `name`, `id`, `tool_calls`, provider-specific fields.

The invariant is not “make the context smaller at any cost.” The invariant is “only shrink large tool payloads while preserving the conversation grammar.” Like cutting cargo weight from a plane: remove crates from the hold, not bolts from the wings.

### 6.3 Tool call/result pairing and role grammar

The pruner must not:

- Delete any message.
- Reorder any message.
- Remove `tool_call_id`.
- Remove assistant `tool_calls`.
- Convert a `tool` message into a `user` or `assistant` message.
- Trim user intent or assistant natural-language answers.
- Create an orphan tool result.
- Break Anthropic/OpenAI role alternation expectations.

Since it only edits string `content` on existing `role == "tool"` messages, it should preserve pairing by construction. Add tests that fail if the list length, roles, and tool IDs change.

### 6.4 Integration point

Do **not** add pruning to provider classes. Provider classes should serialize/send model requests; they should not decide which conversation content gets amputated.

Do **not** put pruning in `ContextBuilder`. ContextBuilder assembles prompt context and runtime memory. It does not know per-call tool result age after multiple tool iterations.

Use the current `AgentRunner.run()` context governance pipeline. The integration should happen after existing repair/compaction/budget steps have produced `messages_for_model`, and before `_request_model()` sends the request.

Recommended order inside the `try` block before model call:

```python
messages_for_model = self._drop_orphan_tool_results(messages)
messages_for_model = self._backfill_missing_tool_results(messages_for_model)
messages_for_model = self._microcompact(messages_for_model)
messages_for_model = self._apply_tool_result_budget(spec, messages_for_model)
messages_for_model = self._snip_history(spec, messages_for_model)
messages_for_model = self._drop_orphan_tool_results(messages_for_model)
messages_for_model = self._backfill_missing_tool_results(messages_for_model)
messages_for_model = self._apply_context_pruning(spec, messages_for_model)
```

Rationale:

- Repair first, so the pruner sees legal tool pairs.
- Existing `_apply_tool_result_budget()` and `_microcompact()` get first chance to do upstream-native reductions.
- `_snip_history()` may remove whole old messages; pruning after snip prevents trimming messages that will be dropped anyway.
- Final repair before pruning catches snip-created orphans.
- Pruning only edits content and cannot create orphans, so another repair after pruning should not be necessary.

Add to `AgentRunSpec`:

```python
context_pruning: ContextPruningConfig | None = None
```

Use a helper in `AgentRunner`:

```python
def _apply_context_pruning(self, spec: AgentRunSpec, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cfg = spec.context_pruning
    if cfg is None or not cfg.enabled:
        return messages
    context_window = spec.context_window_tokens or 0
    return ContextPruner(cfg).prune(messages, context_window_chars=context_window * 4)
```

The `* 4` char heuristic matches the fork integration (`context_window_tokens * 4`). Keep it explicit and documented as a character approximation, not a token counter.

In `AgentLoop.__init__()`, add `context_pruning: ContextPruningConfig | None = None` and store it as `self.context_pruning = context_pruning or ContextPruningConfig()`.

In `AgentLoop.from_config()`, pass `context_pruning=defaults.context_pruning`.

In `_run_agent_loop()`, pass `context_pruning=self.context_pruning` into `AgentRunSpec(...)`.

### 6.5 Consolidation ratio behavior

Current worktree already has the intended ratio model:

- Schema field: `AgentDefaults.consolidation_ratio`
  - default `0.5`
  - legal range `0.1 <= ratio <= 0.95`
  - camelCase alias `consolidationRatio`
- Loop wiring:
  - `AgentLoop.from_config()` passes `defaults.consolidation_ratio`.
  - `AgentLoop.__init__()` passes it into `Consolidator(...)`.
- Consolidator logic:
  - `maybe_consolidate_by_tokens()` computes a safe input budget.
  - It targets `int(budget * consolidation_ratio)` after consolidation.
  - It loops using mocked/deterministic token estimates in tests.

Do not change default or range unless a targeted test proves current behavior diverges from production. Keep `0.5` as the default because that is fork source behavior and current worktree behavior.

If execution finds the ratio not wired due to later plan commits changing code, restore the above path. Do not introduce a second ratio on `DreamConfig`, `AutoCompact`, or `SessionManager`.

### 6.6 Autocompact, Dream, and long-term memory boundary

Upstream has absorbed or superseded most older fork memory work:

- `AutoCompact` is upstream-native and absent from fork/origin path inspected.
- `MemoryStore` already handles file I/O and legacy history migration.
- `Dream` already works through prompt templates and `GitStore` and should own long-term memory file editing.
- `ContextBuilder` reads memory files into prompt context but should not mutate them.

Pack6 must keep three layers separate:

1. Runtime session history: `Session.messages`, JSONL session file, `last_consolidated`, `_last_summary` metadata.
2. Consolidation archive: `MemoryStore.append_history()` / `history.jsonl` summaries and raw archive fallbacks.
3. Long-term Dream-managed files: `MEMORY.md`, `SOUL.md`, `USER.md` and any Dream edits through `GitStore`.

Context pruning touches only an in-memory `messages_for_model` list on the hot path. It does not save to session. It does not archive. It does not edit Dream files. If a test has to assert persistence, it should compare the original `messages` list after `AgentRunner.run()` with the provider-captured pruned request.

## 7. TDD task sequence

### Task 1 — Schema tests for context pruning

Write failing tests first.

Add `tests/config/test_context_pruning_config.py` or extend an existing config schema test file with:

1. Default values:
   - `AgentDefaults().context_pruning.enabled is False`
   - `keep_last_assistants == 3`
   - `min_prunable_tool_chars == 50_000`
   - `soft_trim.max_chars == 4000`
   - `soft_trim.head_chars == 1500`
   - `soft_trim.tail_chars == 1500`
   - `hard_clear.enabled is True`
   - `hard_clear.placeholder == "[Old tool result content cleared]"`
   - `hard_clear.ratio == 0.5`
2. CamelCase input validation:
   - `AgentDefaults.model_validate({"contextPruning": {"enabled": True, "keepLastAssistants": 1, "minPrunableToolChars": 10, "softTrim": {"maxChars": 100, "headChars": 20, "tailChars": 30}, "hardClear": {"enabled": False, "ratio": 0.25}}})`
3. Snake_case input validation also works because `populate_by_name=True`.
4. Serialization with `model_dump(by_alias=True)` emits `contextPruning`, `softTrim`, `hardClear`, `keepLastAssistants`, and `minPrunableToolChars`.
5. Validation rejects:
   - negative `keep_last_assistants`
   - negative `min_prunable_tool_chars`
   - `hard_clear.ratio < 0`
   - `hard_clear.ratio > 1`

Then implement schema.

### Task 2 — Unit tests for ContextPruner

Create `tests/agent/test_context_pruner.py`, adapted from `origin/main:tests/agent/test_context_pruner.py` but tightened for upstream invariants.

Test cases:

1. Disabled config returns the same object or equal unchanged list.
2. Below `min_prunable_tool_chars` returns unchanged.
3. Soft trim:
   - Use `min_prunable_tool_chars=0`, `keep_last_assistants=0`, `soft_trim.max_chars=100`, `head_chars=20`, `tail_chars=20`, `hard_clear.enabled=False`.
   - Assert output content starts with first 20 chars, ends with last 20 chars, includes deterministic `trimmed` marker, and is shorter than original.
4. Hard clear priority:
   - Use `hard_clear.enabled=True`, `ratio=0.1`, `context_window_chars=1000`, content length > 100.
   - Assert content equals placeholder even if softTrim would also apply.
5. Recent assistant protection:
   - Build several assistant/tool pairs.
   - With `keep_last_assistants=2`, assert tool results after the boundary are unchanged and older ones are pruned.
6. Too few assistants protection:
   - With `keep_last_assistants=3` but only one assistant, assert no pruning.
7. Image/list content skip:
   - Tool content as list containing `{"type": "image_url", ...}` remains unchanged.
8. Non-string content skip:
   - Dict/list without image or numeric content remains unchanged.
9. Pairing preservation:
   - Roles list unchanged.
   - `tool_call_id` list unchanged.
   - Assistant `tool_calls` unchanged.
   - Message count unchanged.
10. Original list not mutated when pruning happens.
11. `context_window_chars <= 0` does not crash and does not hardClear by division; softTrim may still apply.

Then implement `nanobot/agent/pruner.py`.

### Task 3 — Runner integration tests

Add tests in `tests/agent/test_runner_governance.py` or a new `tests/agent/test_loop_runner_context_pruning.py`.

Use a fake provider whose chat method captures messages and returns a simple `LLMResponse(content="done", tool_calls=[])`.

Test 1: pruning is applied before provider call.

- Build `AgentRunner(provider)` and `AgentRunSpec` directly, or `AgentLoop` if that is simpler.
- Initial messages should include:
  - system message
  - user message
  - assistant message with a valid `tool_calls` entry
  - matching `tool` message with oversized string content
  - user follow-up
- Set `context_pruning.enabled=True`, `min_prunable_tool_chars=0`, `keep_last_assistants=0`, softTrim values small, hardClear disabled.
- Run the runner.
- Assert captured provider request has trimmed tool content.
- Assert the original `initial_messages` list still has the full oversized content.

Test 2: disabled config preserves request behavior.

- Same setup but `enabled=False`.
- Assert provider captured full content except for existing upstream `_apply_tool_result_budget()` behavior. To avoid interference, set `max_tool_result_chars` larger than the content.

Test 3: role grammar survives integration.

- Capture provider request and assert role sequence still contains assistant/tool pair legally.
- Assert `tool_call_id` still matches assistant `tool_calls[0]["id"]`.

Test 4: AgentLoop.from_config wiring.

- Build `Config.model_validate({"agents": {"defaults": {"contextPruning": {"enabled": True, "minPrunableToolChars": 0}}}})` with workspace pointed at `tmp_path` if needed.
- `AgentLoop.from_config(config, bus=..., provider=fake_provider)` should produce a loop whose `context_pruning.enabled is True`.
- Avoid running a full loop unless necessary.

Then implement integration in `AgentRunSpec`, `AgentRunner`, and `AgentLoop`.

### Task 4 — Consolidation ratio regression check

Do not rewrite the consolidator first. Tests already exist; trust them until they fail.

Run or inspect `tests/agent/test_consolidation_ratio.py` and add only the missing regression if needed:

- Config alias `consolidationRatio` validation/serialization.
- `AgentLoop.from_config()` actually passes the configured ratio into `loop.consolidator.consolidation_ratio`.
- `maybe_consolidate_by_tokens()` uses target `budget * ratio`, not the full budget.

If adding a test, keep token counting deterministic:

- Monkeypatch `estimate_session_prompt_tokens()` to return a controlled sequence.
- Monkeypatch `estimate_message_tokens()` if chunk sizing needs control.
- Do not depend on real `tiktoken` counts or provider-specific tokenizers.

Only modify code if these tests fail. Current baseline suggests no production-code change is needed for this task.

### Task 5 — Memory/autocompact boundary regression tests only if needed

Run targeted existing tests before adding new ones:

- `tests/agent/test_auto_compact.py`
- `tests/agent/test_autocompact_unit.py`
- `tests/agent/test_consolidator.py`
- `tests/agent/test_loop_consolidation_tokens.py`
- `tests/agent/test_dream.py`

If a failure is caused by Pack6 integration, fix Pack6 integration. Do not port old fork `MemoryConsolidator` APIs into upstream unless a concrete current caller still requires it. Especially avoid adding compatibility aliases in the called layer just to hide a wrong caller; fix the caller.

## 8. Pack-level verification

During implementation, run only targeted tests. Do not run broad implementation test suites as part of this planning-only task.

Recommended verification commands for the later execution agent:

```bash
cd /root/git_code/nanobot/.worktrees/sync-upstream-2026-05-merge

python3 -m pytest \
  tests/config/test_context_pruning_config.py \
  tests/agent/test_context_pruner.py \
  tests/agent/test_runner_governance.py \
  tests/agent/test_consolidation_ratio.py

python3 -m pytest \
  tests/agent/test_consolidator.py \
  tests/agent/test_loop_consolidation_tokens.py \
  tests/agent/test_auto_compact.py \
  tests/agent/test_autocompact_unit.py \
  tests/agent/test_context_builder.py \
  tests/agent/test_context_prompt_cache.py \
  tests/agent/test_dream.py

python3 -m compileall nanobot/agent nanobot/config nanobot/session
```

If `tests/config/test_context_pruning_config.py` is not created because schema tests were placed elsewhere, substitute the actual path. Don’t be cute and claim “all tests pass” after running the wrong filename.

Token-counting test strategy:

- Use mocked estimates for consolidation thresholds.
- Keep content sizes char-based for pruner tests.
- Avoid real model/provider calls.
- Avoid relying on `tiktoken` exact counts for pass/fail thresholds.
- Assert exact archive counts only when the estimate sequence is fully controlled.
- For pruning markers, assert stable substrings and preserved head/tail, not incidental whitespace from unrelated serializers.

## 9. Manual smoke check

After targeted tests pass, do a no-network, no-production smoke check in the worktree only:

1. Instantiate `AgentDefaults` from a small dict containing `contextPruning` and print/inspect `model_dump(by_alias=True)`.
2. Instantiate `ContextPruner` with `enabled=True` and run it on a hand-built assistant/tool pair.
3. Confirm original messages are unchanged and pruned messages preserve roles and `tool_call_id`.
4. Instantiate `AgentLoop.from_config()` with a fake provider and confirm:
   - `loop.context_pruning.enabled` matches config.
   - `loop.consolidator.consolidation_ratio` matches config.

Do not point the smoke check at `/root/git_code/nanobot` or a real production workspace. Use `tmp_path` in tests or a scratch directory under the worktree if manual.

## 10. Rollback plan

If Pack6 implementation causes failures or uncertain behavior:

1. Revert the Pack6 code/test commit only.
2. Remove:
   - `nanobot/agent/pruner.py`
   - `ContextPruningConfig` / nested config fields from `schema.py`
   - `context_pruning` additions to `AgentRunSpec`, `AgentRunner`, and `AgentLoop`
   - new context pruning tests
3. Leave existing upstream consolidation/autocompact/memory files untouched unless they were changed by Pack6.
4. Re-run the pre-Pack6 targeted consolidation tests to confirm baseline behavior is restored.

Because context pruning defaults to disabled, partial rollback should be easy. If it isn’t, the implementation coupled pruning into the wrong layer.

## 11. Completion criteria

Pack6 is complete when all are true:

- `ContextPruningConfig`, `SoftTrimConfig`, and `HardClearConfig` exist with defaults and camelCase serialization/validation documented above.
- `AgentDefaults.context_pruning` exists and defaults to disabled.
- `ContextPruner` exists and only trims/clears string tool result content.
- Pruning never deletes messages, never changes roles, never removes tool IDs, and never mutates the persisted/original message list.
- Pruning is applied in the agent/runner pre-provider path, not in provider classes and not in `ContextBuilder`.
- `AgentLoop.from_config()` passes `contextPruning` into runtime execution.
- Existing `consolidation_ratio` behavior remains intact: default `0.5`, range `0.1..0.95`, alias `consolidationRatio`, and target `budget * ratio` in `maybe_consolidate_by_tokens()`.
- Existing autocompact/consolidator/Dream tests still pass under targeted verification.
- No Dream-managed long-term memory files are edited.
- No session JSONL metadata model or llm_logs behavior is changed.
- The production checkout `/root/git_code/nanobot` remains untouched.
