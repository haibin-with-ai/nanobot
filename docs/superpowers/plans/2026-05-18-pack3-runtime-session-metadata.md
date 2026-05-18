# Pack3 — Runtime Identity and Session Metadata Replay Plan

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

Do **not** implement while reading this plan. This document is the handoff for a later execution agent.

The replay branch already contains Pack1 and Pack2 plan commits. Pack3 must replay only production-critical runtime identity and session metadata behavior. It must not drift into provider routing, Discord transcription/TTS, command rewrite, subagent trace logs, memory pruning, workspace layout, or bootstrap/SOUL changes.

Previous attempt failure: it went looking at an out-of-scope workspace-layout module. That area is not Pack3 scope. Do not inspect or change workspace layout/session-path modules outside `nanobot/session/manager.py`. Pack3 uses the upstream global `SessionManager` JSONL path model already present in `nanobot/session/manager.py`.

## 1. Goal

Replay the fork's production runtime/session metadata behavior onto upstream `main` architecture:

1. Discord runtime context includes `channel_name`.
2. Runtime session metadata and per-assistant `model` are written to JSONL session logs.
3. `provider_name` is not written to runtime context or persisted runtime metadata.
4. Token usage is recorded per assistant message.
5. LLM response timing is recorded per assistant message.
6. Runtime context includes `sender_name` when the inbound channel provides it.
7. Persisted user messages record `sender_id` and `sender_name`.
8. `/new` clears conversation messages but preserves runtime metadata.
9. Session identity is stable across dates under upstream's current session file model.

The output must remain compatible with upstream's existing JSONL session format:

- first line: `_type == "metadata"`, including `key`, timestamps, `metadata`, and `last_consolidated`;
- subsequent lines: message objects.

Pack3 is message/session JSONL metadata only. It does **not** add full LLM request/response logs.

## 2. Non-goals

Do not include any of the following:

- Anthropic Claude Code OAuth or provider routing. That is Pack1.
- Discord TTS, voice transcription, mention filter, or Discord UX behavior unrelated to runtime identity metadata. That is Pack2.
- Command rewrite, `rtk`, command architecture migration. That is Pack4.
- Subagent trace/logging, `llm_logs`, or workspace layout. That is Pack5.
- Memory, consolidation behavior beyond preserving/clearing metadata correctly, or context pruning. That is Pack6.
- Grep/search/message tool workspace behavior. That is Pack7. `MessageTool` may be mentioned only as an existing caller affected by metadata pass-through; do not change it for this pack unless a test proves session metadata is broken at that call boundary.
- Bootstrap files, SOUL docs, or general docs cleanup. That is Pack8.

## 3. Source commits

Replay behavior, not old architecture, from these fork commits:

1. `ecbd6c5a feat: include channel_name in runtime context for Discord`
   - touched `nanobot/agent/context.py`, `nanobot/agent/loop.py`, `nanobot/channels/discord.py`.
2. `d19396da feat(session): add runtime context and model to JSONL session logs`
   - touched `nanobot/agent/loop.py`, `nanobot/cli/commands.py`.
3. `956d06f9 refactor(session): remove provider_name from runtime context`
   - touched `nanobot/agent/loop.py`, `nanobot/cli/commands.py`.
4. `a0817fac feat(session): record token usage (input/output/cache) per assistant message`
   - touched `nanobot/agent/loop.py`, `nanobot/agent/runner.py`, `nanobot/providers/openai_compat_provider.py`.
5. `1dbe6e34 feat(session): record LLM response timing per assistant message`
   - touched `nanobot/agent/loop.py`, `nanobot/agent/runner.py`.
6. `bc8845e9 feat: include sender_name in runtime context for Discord`
   - touched `nanobot/agent/context.py`, `nanobot/agent/loop.py`, `nanobot/channels/discord.py`.
7. `6f210ba0 feat(session): record sender_id and sender_name on user messages`
   - touched `nanobot/agent/loop.py`, `nanobot/channels/telegram.py`.
8. `f94bb61e fix(session): preserve runtime metadata on /new clear`
   - touched `nanobot/session/manager.py`.
9. `0fc40b97 fix(session): decouple session identity from date to prevent cross-day context loss`
   - touched session manager plus workspace-layout test/code paths in the fork. For Pack3, only the session-manager behavior is in scope.
   - For Pack3, use only the session-identity principle. Do not touch or inspect workspace layout.

## 4. Files expected to change

Expected production files:

- `nanobot/bus/events.py`
  - likely add `sender_name: str | None = None` to `InboundMessage`, unless execution chooses to keep `sender_name` only in `metadata`. Prefer explicit field if the resulting call-site churn stays small; upstream already has explicit `sender_id`.
- `nanobot/channels/base.py`
  - if `InboundMessage.sender_name` is added, extend `_handle_message(..., sender_name: str | None = None)` and populate the dataclass field.
- `nanobot/channels/discord.py`
  - enrich inbound metadata with `channel_name` and `sender_name`.
  - for `discord.py`, use already-available `message.channel` / slash-command channel objects; do not reintroduce the fork's raw websocket REST channel-name cache unless upstream lacks the information at the call site.
- `nanobot/channels/telegram.py`
  - if in upstream, set `sender_name` from Telegram user data so persisted user messages work outside Discord too. Source commit did this.
  - If the file or exact Telegram user fields differ, record the mismatch in the execution notes and keep the test limited to current upstream shape.
- `nanobot/agent/context.py`
  - extend `ContextBuilder.build_messages()` and `_build_runtime_context()` to include `channel_name` and `sender_name`.
- `nanobot/agent/loop.py`
  - persist session runtime metadata on each turn.
  - pass `channel_name` and `sender_name` into context building.
  - record user-message sender metadata.
  - pass per-turn usage/timing/model into `_save_turn()` and assistant messages.
- `nanobot/agent/runner.py`
  - expose per-run cumulative `usage`, total elapsed milliseconds, and LLM-only elapsed milliseconds from the `AgentRunResult` boundary.
  - preferably record timing around `_call_provider()` so all provider call modes are covered once.
- `nanobot/providers/openai_compat_provider.py`
  - verify whether upstream already preserves detailed usage fields. If not, replay the source commit's detail extraction for nested usage fields. Keep this limited to usage extraction; do not alter provider routing.
- `nanobot/session/manager.py`
  - make `Session.clear()` preserve `metadata["runtime"]` while clearing conversation-only metadata.
  - verify current session filenames are not date-coupled. If already date-free, only add regression tests and do not change production code.
- `nanobot/command/builtin.py`
  - likely no direct code change if `Session.clear()` owns runtime preservation. Add tests around `/new` through command or session manager as appropriate.

Expected tests:

- `tests/agent/test_loop_save_turn.py`
  - add unit tests for stripped runtime context, persisted sender metadata, assistant model/usage/timing metadata.
- `tests/channels/test_discord_channel.py`
  - add or adapt tests proving Discord inbound metadata includes `channel_name` and `sender_name`.
- `tests/agent/test_session_manager_history.py`, `tests/session/test_session_fsync.py`, or a new focused session test under `tests/session/`
  - test `Session.clear()` preserves runtime metadata.
  - test `SessionManager.safe_key()` / `_get_session_path()` stays date-independent if no current test already covers it.
- `tests/command/*`
  - only if testing `/new` through command routing is easier than direct `Session.clear()` tests. Do not rewrite commands.

Files inspected for this plan that may or may not change:

- `nanobot/channels/manager.py`: no obvious Pack3 change expected unless channel construction needs to pass identity fields. If no change is needed, leave it alone.
- `nanobot/config/schema.py`: no Pack3 config changes expected. Leave it alone unless a test proves a runtime field must be configurable, which would be suspicious scope creep.
- `nanobot/cli/commands.py`: fork source touched it for provider name. Upstream replay should avoid it unless current upstream direct CLI path bypasses `AgentLoop` runtime persistence. Inspect before editing.

## 5. Upstream baseline observations

These are from the current worktree, branch `sync-upstream-2026-05-replay`, ahead of `upstream/main` only by plan commits.

### 5.1 Session manager baseline

`nanobot/session/manager.py` currently has:

- `Session` fields: `key`, `messages`, `created_at`, `updated_at`, `metadata`, `last_consolidated`.
- JSONL save writes the metadata header:

```python
{
    "_type": "metadata",
    "key": session.key,
    "created_at": session.created_at.isoformat(),
    "updated_at": session.updated_at.isoformat(),
    "metadata": session.metadata,
    "last_consolidated": session.last_consolidated,
}
```

- `Session.clear()` currently does:

```python
self.messages = []
self.last_consolidated = 0
self.updated_at = datetime.now()
self.metadata.pop("_last_summary", None)
```

This means it already preserves most metadata by default, including future `metadata["runtime"]`, but it is not explicit. Pack3 should make the runtime-preservation contract explicit and test it. Do not convert it into the fork's older `self.metadata = {}; keep runtime` form unless a failing test demands it; upstream now has other metadata such as web UI title/session keys and goal state that may need deliberate handling.

- Session paths are already date-independent:

```python
return self.sessions_dir / f"{self.safe_key(key)}.jsonl"
```

There is no current date prefix in `_get_session_path()`. The `0fc40b97` workspace-layout part is therefore already satisfied for upstream's global sessions, and Pack3 should only add a regression test if useful.

### 5.2 Context builder baseline

`nanobot/agent/context.py` currently has:

- `_build_runtime_context(channel, chat_id, timezone=None, sender_id=None, supplemental_lines=None)`.
- Runtime context includes:
  - `Current Time`
  - `Channel`
  - `Chat ID`
  - `Sender ID`
  - supplemental goal-state lines
  - closing `[/Runtime Context]` marker.
- `build_messages()` accepts `sender_id` but not `channel_name` or `sender_name`.
- Runtime context is appended to the current user message and later stripped before persistence by `AgentLoop._save_turn()`.

Pack3 should add `channel_name` and `sender_name` here. It should not add provider information.

### 5.3 Agent loop baseline

`nanobot/agent/loop.py` currently has:

- `TurnContext` with fields for `turn_latency_ms`, `all_messages`, `stop_reason`, etc.
- `_build_initial_messages()` passes `channel`, runtime `chat_id`, `sender_id`, and `session.metadata` to `ContextBuilder.build_messages()`.
- `_state_run()` calls `_run_agent_loop()` and currently unpacks:

```python
final_content, tools_used, all_msgs, stop_reason, had_injections = result
```

- `_run_agent_loop()` sets `self._last_usage = result.usage` but returns only final content, tools, messages, stop reason, and injection flag.
- `_state_save()` calculates `ctx.turn_latency_ms`, stores it in websocket pending latency, then calls:

```python
self._save_turn(ctx.session, skip_msgs, ctx.save_skip, turn_latency_ms=ctx.turn_latency_ms)
```

- `_save_turn()` currently attaches `latency_ms` to the last assistant message if `turn_latency_ms` is provided.
- `_save_turn()` strips runtime context out of persisted user content.
- `_persist_user_message_early()` exists and may persist the triggering user before the LLM run, especially for media. This is a trap: sender metadata must be recorded both in early-persisted user messages and in `_save_turn()` for normal text turns.

### 5.4 Agent runner baseline

`nanobot/agent/runner.py` currently has:

- `AgentRunResult` with `final_content`, `messages`, `tools_used`, `usage`, `stop_reason`, `error`, `tool_events`, `had_injections`.
- `AgentRunner.run()` accumulates response usage through `_usage_dict()` and `_accumulate_usage()`.
- `_call_provider()` centralizes all provider calls:
  - streaming via `chat_stream_with_retry()`;
  - progress streaming via `chat_stream_with_retry()`;
  - non-streaming via `chat_with_retry()`.
- `_call_provider()` is the cleanest boundary to measure LLM-only elapsed time.

Pack3 should not scatter timers around every provider call branch if a single `_call_provider()` timer can cover them. Good taste: one chokepoint, not three almost-identical stopwatches taped to the plumbing.

### 5.5 Discord baseline

`nanobot/channels/discord.py` is upstream's `discord.py` implementation, not the fork's raw websocket implementation.

Observed inbound path:

- `DiscordBotClient.on_message()` delegates to `DiscordChannel._handle_discord_message()`.
- `_handle_discord_message()` builds `sender_id`, `channel_id`, `content`, `metadata`, downloads attachments, then calls `BaseChannel._handle_message(...)`.
- Current metadata includes fields like `message_id`, `guild_id`, `channel_id`, `is_dm`, and thread/context IDs.
- It does not currently surface `channel_name` or `sender_name` in the grep output inspected for this plan.
- Slash-command path also calls `_handle_message(...)` and should get the same metadata shape if possible.

Use `message.channel.name` when present for `channel_name`. For DM channels, omit it unless `discord.py` exposes a meaningful name; do not invent fake labels.

For `sender_name`, prefer guild nick/display name over global username:

1. `message.author.display_name` if present and non-empty;
2. `message.author.global_name` if present;
3. `message.author.name`;
4. fallback to `sender_id` only if a string is required.

The runtime context should omit `Sender Name` when no name is known; persisted `sender_id` is still mandatory for user messages.

### 5.6 Base channel and inbound message baseline

`nanobot/bus/events.py` has `InboundMessage(channel, sender_id, chat_id, content, timestamp, media, metadata, session_key_override)`. There is no first-class `sender_name` field.

`nanobot/channels/base.py` has `_handle_message(sender_id, chat_id, content, media=None, metadata=None, session_key=None, is_dm=False)`, builds `InboundMessage`, and publishes it.

Pack3 can choose between:

- explicit `InboundMessage.sender_name`, with `BaseChannel._handle_message(sender_name=...)`; or
- keep `sender_name` in `metadata` only.

Decision below: prefer explicit field for the in-memory event and mirror it in metadata for backwards compatibility with existing tests/callers.

### 5.7 Tests baseline

Existing relevant tests found:

- `tests/agent/test_loop_save_turn.py`: exists and already covers runtime-context stripping from persisted user messages.
- `tests/channels/test_discord_channel.py`: exists and tests upstream `discord.py` shape.
- `tests/session/__init__.py` exists; no `tests/session/test_manager.py` or `tests/session/test_session_manager.py` found.
- Existing session tests include:
  - `tests/agent/test_session_atomic.py`
  - `tests/agent/test_session_delete.py`
  - `tests/agent/test_session_manager_history.py`
  - `tests/session/test_goal_state.py`
  - `tests/session/test_session_fsync.py`
- Existing command tests include:
  - `tests/command/test_builtin_dream.py`
  - `tests/command/test_model_command.py`
  - `tests/command/test_router_dispatchable.py`

If a referenced test file does not exist during execution, record "does not exist" in execution notes and place new tests in the nearest existing test module. Do not fail just because the fork used a different test path.

## 6. Design decisions

### 6.1 Data model: session record vs messages vs runtime-only context

#### Session record metadata, persisted in JSONL header

Persist under `session.metadata["runtime"]`:

```python
{
    "model": self.model,
    "context_window": self.context_window_tokens,
    "channel": msg.channel,
    "chat_id": self._runtime_chat_id(msg),
    "channel_name": msg.metadata.get("channel_name"),  # include only when truthy
    "sender_id": msg.sender_id,                         # include current turn sender
    "sender_name": resolved_sender_name,                # include only when truthy
}
```

Rules:

- `model` is the effective model used by `AgentLoop` for the turn.
- `context_window` is `self.context_window_tokens`.
- `channel` is the inbound channel name, e.g. `discord`, `telegram`, `websocket`, `cli`.
- `chat_id` must match the ID shown to the model in runtime context. Use `self._runtime_chat_id(msg)` where the context builder uses it, not blindly `msg.chat_id`, because upstream has thread/context ID handling.
- `channel_name`, `sender_name` are optional and omitted if unavailable.
- `sender_id` in session runtime metadata reflects the latest turn's sender. That is acceptable because the header is runtime state, not a historical actor table.
- Do not store `provider` or `provider_name` here.

Keep unrelated existing session metadata intact:

- web UI metadata/title keys;
- goal-state metadata;
- consolidation summary metadata;
- runtime checkpoint/pending-turn metadata, except where existing code intentionally clears it.

#### User message fields, persisted as message JSONL lines

For the user message representing the current inbound turn, persist:

```python
{
    "role": "user",
    "content": "...",        # runtime context stripped
    "timestamp": "...",
    "sender_id": msg.sender_id,
    "sender_name": resolved_sender_name,  # optional
}
```

Rules:

- `sender_id` is mandatory for real inbound user turns if available from `InboundMessage`.
- `sender_name` is optional.
- Only attach to the triggering user turn, not historical user messages copied through `history`, and not tool-injected user messages unless they are actual inbound follow-ups from the same channel. Avoid stamping every user role blindly in a way that marks synthetic subagent/user blocks as the human sender.
- Cover both normal `_save_turn()` persistence and `_persist_user_message_early()`.
- Runtime context text must not be persisted as content.

#### Assistant message fields, persisted as message JSONL lines

For the final assistant message of a turn, persist:

```python
{
    "role": "assistant",
    "content": "...",
    "timestamp": "...",
    "model": self.model,
    "usage": {
        "prompt_tokens": 123,
        "completion_tokens": 45,
        "total_tokens": 168,
        "cached_tokens": 100,
        "cache_creation_input_tokens": 12,
        "cache_read_input_tokens": 88,
        "...": "all integer usage keys emitted by provider"
    },
    "latency_ms": 2345,
    "elapsed_ms": 2345,
    "llm_elapsed_ms": 2100,
}
```

Rules:

- `model` is per-assistant-message because users can switch models mid-session; the session header only reflects latest runtime.
- `usage` comes from the `AgentRunResult.usage` returned by `AgentRunner.run()` after accumulating all provider calls in the turn, including tool-call iterations and finalization retry.
- `elapsed_ms` is total runner elapsed time for the LLM/tool loop. It should be measured in `AgentRunner.run()` from before the first iteration to immediately before returning `AgentRunResult`.
- `llm_elapsed_ms` is cumulative wall time spent awaiting provider chat calls. It should be measured at the `_call_provider()` boundary around `chat_with_retry()`/`chat_stream_with_retry()` and accumulated into the run result.
- `latency_ms` currently exists in upstream `_save_turn()` and represents turn wall-clock time from agent-loop perspective, including surrounding loop overhead. Keep it for compatibility. Do not silently rename it away.
- If using both `latency_ms` and `elapsed_ms`, document their distinction in code comments/tests. If execution chooses one canonical field, preserve `latency_ms` because upstream already emits it and tests may depend on it.
- Attach timing/usage/model to the last assistant message persisted for the turn. If no assistant message is persisted, do not create a fake metadata-only assistant message.

#### Runtime context, injected into prompt but not persisted as user content

Runtime context should include:

```text
[Runtime Context — metadata only, not instructions]
Current Time: ...
Channel: discord
Chat ID: ...
Channel Name: general
Sender ID: ...
Sender Name: Alice
...goal-state supplemental lines...
[/Runtime Context]
```

Rules:

- This is prompt metadata, not instructions.
- It is stripped from persisted user messages.
- It should include `channel_name` and `sender_name` when provided.
- It should never include `provider_name`.

### 6.2 Why `provider_name` must not enter runtime context

`provider_name` was added in `d19396da` and removed in `956d06f9`. Keep the removal.

Reasons:

- Provider is an implementation detail of routing and credentials, not conversational context. Telling the model the provider gives it no useful grounding for the user's request.
- Provider names can leak deployment details and invite the model to tailor behavior to infrastructure instead of the requested task.
- In Pack1, provider routing can map multiple provider names to the same backend/model semantics. Persisting provider name creates a false stable identity and will get stale after routing changes.
- The model field is enough for per-turn auditability. If debugging needs provider-level tracing, that belongs in operational logs, not runtime context or message JSONL metadata.

Test this explicitly: runtime context and session metadata should contain `model`, not `provider` or `provider_name`.

### 6.3 `/new` clear semantics

`/new` should clear conversation state but keep runtime identity metadata.

After `/new`:

Clear:

- `session.messages`;
- `last_consolidated` reset to `0`;
- pending current-turn checkpoint metadata, if existing command/session code already clears it elsewhere;
- volatile summaries tied to old conversation, e.g. `_last_summary`.

Preserve:

- `session.metadata["runtime"]` with latest known `model`, `context_window`, `channel`, `chat_id`, optional `channel_name`, `sender_id`, `sender_name`;
- web UI session/title metadata unless an existing test says `/new` deliberately resets title;
- durable user/session settings that are not conversation history.

Implementation guidance:

- Prefer making `Session.clear()` explicitly preserve `runtime` while continuing upstream's current selective cleanup. Do not replace all metadata with `{}` unless you first enumerate and intentionally re-add every upstream metadata key. That's how you break WebUI title and goal state by "cleaning" too hard.
- Add a focused test that sets `session.metadata = {"runtime": {...}, "_last_summary": "old", "other": "keep"}` and verifies after `clear()`:
  - messages are empty;
  - `runtime` remains;
  - `_last_summary` is gone;
  - decide and assert what happens to `other` based on current upstream semantics. Current upstream preserves it; keep that unless there is a strong reason not to.

### 6.4 Session identity decoupled from date

Upstream current `SessionManager._get_session_path(key)` already maps a key directly to:

```python
sessions_dir / f"{safe_key(key)}.jsonl"
```

No date component is present. Therefore:

- Do not implement any workspace-layout sequence/date migration.
- Do not introduce date-based session filenames.
- Add a regression test only if it is cheap and local: freeze/monkeypatch date is not needed; assert `_get_session_path("discord:123")` is stable and contains no ISO date prefix.

### 6.5 Usage extraction details

`LLMResponse.usage` is the data boundary between providers and runner. Pack3 should preserve every integer-like field in `LLMResponse.usage`, not a handpicked subset.

OpenAI-compatible usage can include nested detail fields. If upstream provider still only returns:

```python
prompt_tokens
completion_tokens
total_tokens
```

then extend extraction to include nested details such as:

- `prompt_tokens_details.cached_tokens`
- `completion_tokens_details.reasoning_tokens`
- Anthropic-compatible cache fields if present through gateways:
  - `cache_creation_input_tokens`
  - `cache_read_input_tokens`

Flatten names should be stable and boring. Example:

```python
{
    "prompt_tokens": 100,
    "completion_tokens": 20,
    "total_tokens": 120,
    "cached_tokens": 80,
    "reasoning_tokens": 5,
}
```

If upstream already has richer extraction, do not touch provider code. Add a test only around runner/session persistence.

### 6.6 Timing boundary

Use two timing concepts:

- `AgentLoop` turn wall latency:
  - already calculated in `_state_save()` as `ctx.turn_latency_ms` from `ctx.turn_wall_started_at`;
  - persists as `latency_ms` on assistant message;
  - includes more than provider calls.
- `AgentRunner` run timing:
  - `elapsed_ms`: total time inside runner from start of `AgentRunner.run()` to return;
  - `llm_elapsed_ms`: sum of awaited provider call durations, measured in `_call_provider()`.

The source commit used `elapsed_ms` and `llm_elapsed_ms` on `AgentRunResult`. Upstream already has `latency_ms`. Preserve both if possible. If this feels redundant, it is still production metadata, not user-facing API; don't make a semantic diet in a migration pack unless tests force it.

## 7. TDD task sequence

Use TDD. Do not implement without a failing test first. Keep each task small enough to review.

### Task 1 — Runtime context accepts channel and sender names

Failing tests:

Add to `tests/agent/test_loop_save_turn.py` or a new context-specific test file:

1. `ContextBuilder._build_runtime_context(...)` includes `Channel Name: general` when `channel_name="general"`.
2. It includes `Sender Name: Alice` when `sender_name="Alice"`.
3. It includes `Sender ID` as it already does.
4. It does not include `provider` or `provider_name` anywhere.

Expected implementation:

- In `nanobot/agent/context.py`, extend:

```python
def _build_runtime_context(..., sender_id=None, channel_name=None, sender_name=None, supplemental_lines=None)
def build_messages(..., sender_id=None, channel_name=None, sender_name=None, ...)
```

- Add runtime lines after channel/chat and sender ID, for stable readability:

```python
if channel_name:
    lines.append(f"Channel Name: {channel_name}")
if sender_id:
    lines.append(f"Sender ID: {sender_id}")
if sender_name:
    lines.append(f"Sender Name: {sender_name}")
```

- Preserve the closing `[/Runtime Context]` marker.

Targeted test command for later execution agent:

```bash
python3 -m pytest tests/agent/test_loop_save_turn.py -k 'runtime_context or save_turn'
```

Do not run this while writing the plan.

### Task 2 — Discord and channel inbound identity metadata

Failing tests:

Add tests in `tests/channels/test_discord_channel.py` using the existing fake message style in that file:

1. Discord message in a guild channel publishes inbound metadata containing:
   - `channel_name` from `message.channel.name`;
   - `sender_name` from `message.author.display_name` or equivalent.
2. Slash-command path, if currently tested or easy to instantiate, carries the same fields.
3. DM path does not crash when channel has no `name`; it may omit `channel_name`.

Expected implementation:

- In `nanobot/channels/discord.py`, derive:

```python
channel_name = getattr(message.channel, "name", None)
sender_name = (
    getattr(message.author, "display_name", None)
    or getattr(message.author, "global_name", None)
    or getattr(message.author, "name", None)
)
```

- Add truthy values to metadata.
- Pass `sender_name` explicitly to `_handle_message()` if Task 3 adds that parameter.
- Keep thread metadata (`parent_channel_id`, `context_chat_id`, `thread_id`) unchanged.
- Do not reintroduce the fork's raw Discord REST cache unless upstream's `discord.py` objects cannot provide names in tests.

Targeted test command:

```bash
python3 -m pytest tests/channels/test_discord_channel.py -k 'metadata or channel_name or sender_name'
```

### Task 3 — First-class `sender_name` on inbound messages

Failing tests:

Add a unit test around `BaseChannel._handle_message()` or `InboundMessage` construction:

- calling `_handle_message(sender_id="42", chat_id="99", content="hi", sender_name="Alice")` publishes an `InboundMessage` with:
  - `sender_id == "42"`;
  - `sender_name == "Alice"` if explicit field is added;
  - `metadata["sender_name"] == "Alice"` for compatibility.

Expected implementation:

- In `nanobot/bus/events.py`, add:

```python
sender_name: str | None = None
```

near `sender_id`, after checking dataclass defaults order. Because `sender_name` has a default, place it after non-default fields or keep it after `content` with default. Example:

```python
channel: str
sender_id: str
chat_id: str
content: str
sender_name: str | None = None
```

- In `nanobot/channels/base.py`, add `_handle_message(..., sender_name: str | None = None, ...)`.
- Merge sender name into metadata only when truthy:

```python
meta = dict(metadata or {})
if sender_name and "sender_name" not in meta:
    meta["sender_name"] = sender_name
```

- Construct `InboundMessage(..., sender_name=sender_name, metadata=meta, ...)`.

Risk note: adding a dataclass field is usually low-risk, but positional construction of `InboundMessage` elsewhere could be affected if callers pass more than four positional args. Grep for `InboundMessage(` and adjust only broken call sites. Prefer keyword args in tests and new code.

### Task 4 — Runtime session metadata persisted on every real turn

Failing tests:

In `tests/agent/test_loop_save_turn.py` or a new `tests/agent/test_runtime_session_metadata.py`:

1. Build a minimal `AgentLoop`, `Session`, and `InboundMessage` with metadata:

```python
metadata={"channel_name": "general", "sender_name": "Alice"}
```

After the build/save path that execution chooses, assert:

```python
session.metadata["runtime"] == {
    "model": "test-model",
    "context_window": <loop context window>,
    "channel": "discord",
    "chat_id": "123" or runtime chat id,
    "channel_name": "general",
    "sender_id": "u1",
    "sender_name": "Alice",
}
```

2. Assert no `provider` or `provider_name` key exists in `session.metadata["runtime"]`.

Expected implementation:

- Add a helper in `AgentLoop`, for example:

```python
def _update_runtime_metadata(self, session: Session, msg: InboundMessage) -> None:
    runtime = {
        "model": self.model,
        "context_window": self.context_window_tokens,
        "channel": msg.channel,
        "chat_id": self._runtime_chat_id(msg),
        "sender_id": msg.sender_id,
    }
    if channel_name := (msg.metadata or {}).get("channel_name"):
        runtime["channel_name"] = channel_name
    sender_name = getattr(msg, "sender_name", None) or (msg.metadata or {}).get("sender_name")
    if sender_name:
        runtime["sender_name"] = sender_name
    session.metadata["runtime"] = runtime
```

- Call it after `ctx.session` is loaded/restored and before `ctx.initial_messages` is built, likely in `_state_build()` or the existing build state handler. This makes `session.metadata` available both to JSONL save and to `ContextBuilder` if needed later.
- Do not add `provider_name` to `AgentLoop.__init__`.
- Do not edit config schema for this.

### Task 5 — Pass channel/sender names into prompt context

Failing tests:

Add to a loop/context integration test:

- `_build_initial_messages()` with `InboundMessage(channel="discord", sender_id="u1", chat_id="c1", content="hi", sender_name="Alice", metadata={"channel_name": "general"})` produces a final user message whose content contains:
  - `Channel Name: general`
  - `Sender ID: u1`
  - `Sender Name: Alice`
  - no provider fields.

Expected implementation:

- In `AgentLoop._build_initial_messages()`, pass:

```python
channel_name=msg.metadata.get("channel_name"),
sender_name=getattr(msg, "sender_name", None) or msg.metadata.get("sender_name"),
```

- In any secondary `context.build_messages()` call path, especially the older/system/proactive path around current line ~1056, pass the same fields if `msg` is an inbound user message. If the path is system/subagent and no channel names exist, omit.

### Task 6 — Persist sender metadata on user messages

Failing tests:

1. Normal save path:
   - call `_save_turn()` with messages containing a current user message with runtime context and assistant response;
   - pass `sender_id="u1"`, `sender_name="Alice"` or use a `TurnContext`/wrapper if implementation chooses;
   - assert persisted user message contains `sender_id` and `sender_name` and content is stripped of runtime context.

2. Early media persistence path:
   - call `_persist_user_message_early()` with `InboundMessage(..., media=[...], sender_name="Alice")` where media path can be a placeholder matching existing tests;
   - assert the persisted user message contains sender fields.

Expected implementation:

- Extend `_save_turn()` signature carefully:

```python
def _save_turn(..., turn_latency_ms=None, model=None, usage=None, elapsed_ms=None, llm_elapsed_ms=None, sender_id=None, sender_name=None)
```

or pass a small metadata object. Do not make callers guess positional order; use keyword-only args.

- In `_save_turn()`, attach sender fields only to the first new user message in the saved slice that represents the current inbound turn. The fork used `first_user_seen`; upstream has early persistence and injections, so use `ctx.user_persisted_early` and `save_skip` carefully.
- If `ctx.user_persisted_early` is true, do not stamp sender metadata on later synthetic user messages from the same turn unless they are actual inbound follow-ups. Keep this simple and covered by tests.
- In `_persist_user_message_early()`, include `sender_id=msg.sender_id` and optional `sender_name` in the message dict/session add call.

### Task 7 — AgentRunner returns usage and timing at the right boundary

Failing tests:

Add tests near existing runner/loop tests, using a fake provider that sleeps or monkeypatches monotonic/perf counter:

1. `AgentRunner.run()` returns `AgentRunResult.usage` with cumulative integer fields across multiple responses.
2. `AgentRunResult.elapsed_ms` is present and non-negative.
3. `AgentRunResult.llm_elapsed_ms` is present and non-negative.
4. With monkeypatched timer around `_call_provider()`, `llm_elapsed_ms` increases by expected amount.

Expected implementation:

- Extend `AgentRunResult`:

```python
elapsed_ms: int = 0
llm_elapsed_ms: int = 0
```

- In `AgentRunner.run()`:
  - record `run_started = time.monotonic()` at top;
  - keep `llm_elapsed_ms = 0` local;
  - either have `_call_provider()` return `(response, elapsed_ms)` or store elapsed in a local accumulator via a tiny helper.

Preferred clean shape:

```python
response, call_elapsed_ms = await self._timed_call_provider(spec, messages_for_model, tools, hook, context)
llm_elapsed_ms += call_elapsed_ms
```

where `_timed_call_provider()` wraps existing `_call_provider()`. This avoids mutating runner instance state and keeps tests deterministic.

- At return, set:

```python
elapsed_ms=max(0, int((time.monotonic() - run_started) * 1000)),
llm_elapsed_ms=llm_elapsed_ms,
```

- Include finalization retry provider call in `llm_elapsed_ms`. If `_request_finalization_retry()` bypasses `_call_provider()`, time it separately or route it through the same timing helper.

### Task 8 — Persist assistant model, usage, and timing

Failing tests:

In `tests/agent/test_loop_save_turn.py`:

1. `_save_turn()` called with an assistant message and keyword args:

```python
model="test-model"
usage={"prompt_tokens": 10, "completion_tokens": 2, "cached_tokens": 7}
turn_latency_ms=123
elapsed_ms=111
llm_elapsed_ms=99
```

assert last assistant message has:

```python
"model": "test-model"
"usage": {"prompt_tokens": 10, "completion_tokens": 2, "cached_tokens": 7}
"latency_ms": 123
"elapsed_ms": 111
"llm_elapsed_ms": 99
```

2. If usage is empty, either omit `usage` or persist `{}` consistently. Prefer omit empty `usage` to reduce noise; if upstream tests expect `_last_usage`, keep result usage internal.

Expected implementation:

- Extend `TurnContext` to carry:

```python
usage: dict[str, int] = field(default_factory=dict)
elapsed_ms: int | None = None
llm_elapsed_ms: int | None = None
```

- Change `_run_agent_loop()` return to include usage/timing, or better return `AgentRunResult` directly to avoid tuple arity creep. Minimal migration path:

```python
return result.final_content, result.tools_used, result.messages, result.stop_reason, result.had_injections, result.usage, result.elapsed_ms, result.llm_elapsed_ms
```

But that's tuple soup. If changing to return `AgentRunResult`, update only local call sites and tests. Do not refactor unrelated code.

- In `_state_run()`, store result metadata in `ctx`.
- In `_state_save()`, call `_save_turn()` with:

```python
model=self.model,
usage=ctx.usage,
elapsed_ms=ctx.elapsed_ms,
llm_elapsed_ms=ctx.llm_elapsed_ms,
sender_id=ctx.msg.sender_id,
sender_name=ctx.msg.sender_name or ctx.msg.metadata.get("sender_name"),
```

- In `_save_turn()`, attach assistant metadata to `last_assistant_idx` after the loop, where `latency_ms` is already attached. Sanitize usage values to ints and skip non-numeric values.

### Task 9 — Provider usage detail extraction, only if baseline lacks it

First inspect current `nanobot/providers/openai_compat_provider.py` around usage extraction.

Failing test only if needed:

- A fake OpenAI response usage object/dict containing nested details produces `LLMResponse.usage` with flattened integer fields.

Expected implementation if needed:

- Extend usage extraction helper to preserve all top-level integer fields and selected nested details.
- Do not change provider selection, model names, OAuth, headers, or retry behavior.

If current upstream already preserves rich usage fields, record "no production change needed" in execution notes and rely on Task 8 persistence tests.

### Task 10 — `/new` preserves runtime metadata and date-independent session identity stays fixed

Failing tests:

1. `Session.clear()` preserves runtime:

```python
session = Session(key="discord:c1")
session.messages = [{"role": "user", "content": "old"}]
session.metadata = {
    "runtime": {"model": "m", "channel": "discord", "chat_id": "c1"},
    "_last_summary": "old summary",
    "other": "keep",
}
session.last_consolidated = 1
session.clear()
assert session.messages == []
assert session.last_consolidated == 0
assert session.metadata["runtime"]["model"] == "m"
assert "_last_summary" not in session.metadata
assert session.metadata["other"] == "keep"
```

2. Optional command-level test for `/new`:
   - call `cmd_new()` with a loop/session fake;
   - verify saved session keeps runtime.

3. Date-independent identity regression:

```python
mgr = SessionManager(tmp_path)
path = mgr._get_session_path("discord:123")
assert path.name == "discord_123.jsonl" or path.name.endswith("discord_123.jsonl")
assert not re.match(r"\d{4}-\d{2}-\d{2}_", path.name)
```

Expected implementation:

- If current `Session.clear()` already passes the test, production change can be limited to a comment making runtime preservation explicit.
- Do not touch workspace layout.

## 8. Pack-level verification

Later execution agent should run targeted tests only for this pack, not full implementation suites while planning.

Suggested targeted commands after implementation:

```bash
python3 -m pytest tests/agent/test_loop_save_turn.py
python3 -m pytest tests/channels/test_discord_channel.py
python3 -m pytest tests/session/test_session_fsync.py tests/session/test_goal_state.py tests/agent/test_session_manager_history.py
python3 -m pytest tests/command/test_router_dispatchable.py tests/command/test_model_command.py
```

If new tests are placed in a new file, include that file in the targeted command list.

Also run static smoke checks for touched files:

```bash
python3 -m compileall nanobot/bus/events.py nanobot/channels/base.py nanobot/channels/discord.py nanobot/agent/context.py nanobot/agent/loop.py nanobot/agent/runner.py nanobot/session/manager.py
```

Do not run provider live calls. Do not run Discord live bot tests. Do not run implementation tests while writing this plan.

## 9. Manual smoke check

After tests pass in the later implementation session, do a manual local smoke check in the worktree, not production:

1. Start a direct or fake-channel turn using a temporary workspace and a fake provider.
2. Send an inbound Discord-shaped message with:
   - `sender_id="u1"`
   - `sender_name="Alice"`
   - `chat_id="c1"`
   - `metadata={"channel_name": "general"}`
3. Inspect the resulting session JSONL:
   - first line metadata contains `metadata.runtime.model`, `context_window`, `channel`, `chat_id`, `channel_name`, `sender_id`, `sender_name`;
   - no `provider` or `provider_name` exists;
   - user line has `sender_id` and `sender_name`, and no runtime context block in `content`;
   - assistant line has `model`, `usage` if provider returned usage, and timing fields.
4. Invoke `/new` against the same session or call `Session.clear()` through command path.
5. Re-open JSONL:
   - messages are cleared;
   - metadata header still has `runtime`;
   - session file path did not change due to date.

If doing a real Discord gateway smoke later in deployment, verify the model sees channel/sender names by asking it to identify the current Discord channel/user from metadata. Do not include that in Pack3 automated tests.

## 10. Rollback plan

Pack3 should be a small commit later. Rollback is straightforward:

1. Revert the Pack3 implementation commit.
2. Keep Pack1/Pack2 commits untouched.
3. Remove any new Pack3-only tests if the revert does not automatically remove them.
4. Verify existing baseline tests still pass:

```bash
python3 -m pytest tests/agent/test_loop_save_turn.py tests/channels/test_discord_channel.py
```

Data compatibility notes:

- Adding extra JSONL fields to message objects is forward-compatible with current loader because it reads message dicts as opaque records.
- If rollback happens after sessions with new fields exist, old code should ignore unknown message keys. Do not write a migration.
- Removing `metadata.runtime` persistence only loses latest runtime audit metadata; it should not block session loading.

## 11. Completion criteria

Pack3 is complete when all of these are true:

1. Runtime context contains `channel_name` and `sender_name` when provided.
2. Runtime context and session runtime metadata contain no `provider` or `provider_name`.
3. Session JSONL metadata header includes `metadata.runtime` with latest model/context/channel/chat/sender identity.
4. Persisted user messages for real inbound turns include `sender_id` and optional `sender_name`.
5. Persisted user message content does not include the runtime context block.
6. Persisted assistant messages include per-turn `model`.
7. Persisted assistant messages include cumulative token `usage` when the provider returns usage, preserving cache/detail fields where available.
8. Persisted assistant messages include response timing: existing `latency_ms` plus runner-level `elapsed_ms`/`llm_elapsed_ms` if implemented as planned.
9. `/new` clears messages and conversation summaries while preserving `metadata.runtime`.
10. Session identity remains date-independent in upstream's current `SessionManager` model.
11. Targeted Pack3 tests pass.
12. No production checkout files under `/root/git_code/nanobot` were touched.
13. No workspace-layout or `llm_logs` code was touched for this pack.

## 12. Known uncertainty points for executor

- Upstream `openai_compat_provider.py` may already preserve some detailed usage fields. Inspect before editing; skip provider changes if it already does the right thing.
- The best fake objects for `tests/channels/test_discord_channel.py` depend on existing helper style in that test file. Use existing fixtures rather than inventing a parallel fake Discord framework.
- `InboundMessage.sender_name` is the cleaner data model, but if it causes widespread positional-constructor churn, it is acceptable to keep `sender_name` in `metadata` only and document that decision in the implementation commit. Do not add compatibility shims in the wrong place just to hide bad call sites.
- Upstream currently preserves most metadata in `Session.clear()`. Be careful not to make `/new` more destructive while trying to replay the fork's older `metadata = {}` behavior.
