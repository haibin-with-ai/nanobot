# Plan: Configurable Memory Consolidation Trigger Ratio

## Goal

Add a `consolidation_trigger_ratio` config so that memory consolidation can fire well before the context window is exhausted. For a 1M-token model, the user wants consolidation to start at ~400k instead of waiting until ~1M.

**Design choice: Option B (aggressive).** Both the trigger threshold and the target are derived from `context_window_tokens * ratio`, not from the full window.

---

## Files Changed

| File | What |
|------|------|
| `nanobot/config/schema.py` | Add `consolidation_trigger_ratio: float = 1.0` to `AgentDefaults` |
| `nanobot/nanobot.py` | Pass `defaults.consolidation_trigger_ratio` into `AgentLoop` |
| `nanobot/agent/loop.py` | Accept `consolidation_trigger_ratio` and forward it to `Consolidator` |
| `nanobot/agent/memory.py` | `Consolidator` uses `trigger_ratio` to compute `trigger_threshold` |
| `tests/agent/test_consolidator.py` | Unit tests for `Consolidator` with custom ratio |
| `tests/agent/test_loop_consolidation_tokens.py` | Integration tests for end-to-end trigger behavior |

---

## Design Notes

Current `Consolidator.maybe_consolidate_by_tokens` computes:

```python
budget = context_window_tokens - max_completion - SAFETY_BUFFER
target = budget // 2
```

- `budget` = the watermark where consolidation **starts**.
- `target` = the watermark where consolidation **stops** (after enough old messages are archived).

With `trigger_ratio = 1.0` this is unchanged. With `trigger_ratio = 0.4` on a 1M window:

```python
trigger_threshold = 1_000_000 * 0.4 = 400_000
budget  = 400_000 - max_completion - 1024  # ~383k
target  = budget // 2                       # ~191k
```

So consolidation starts at ~383k and runs until the active window drops below ~191k. This keeps the live context lean and leaves the 1M window as headroom for single-turn spikes.

**Guardrails:**
- If `trigger_ratio <= 0`, treat it as `1.0` (disabled / backward compatible).
- If `budget <= 0` after applying the ratio, return early to avoid a no-op or negative-budget loop.

---

## Task 1 — Schema: add `consolidation_trigger_ratio`

**File:** `nanobot/config/schema.py`

Insert one field into `AgentDefaults`, near `context_window_tokens`:

```python
context_window_tokens: int = 0  # 0 = auto-detect at startup
context_block_limit: int | None = None
consolidation_trigger_ratio: float = 1.0  # 0.0-1.0; budget = context_window * ratio
```

No migration needed — Pydantic default handles existing configs.

**Verify:**
```bash
cd ~/git_code/nanobot
python -c "from nanobot.config.schema import AgentDefaults; d=AgentDefaults(); print(d.consolidation_trigger_ratio)"
# expected: 1.0
```

---

## Task 2 — Bootstrap: pass ratio into AgentLoop

**File:** `nanobot/nanobot.py`

In the `AgentLoop(...)` constructor call, add one kwarg:

```python
loop = AgentLoop(
    ...
    context_window_tokens=ctx_tokens,
    context_block_limit=defaults.context_block_limit,
    max_tool_result_chars=defaults.max_tool_result_chars,
    consolidation_trigger_ratio=defaults.consolidation_trigger_ratio,
    ...
)
```

**Verify:** `python -m py_compile nanobot/nanobot.py` exits 0.

---

## Task 3 — AgentLoop: accept and forward ratio

**File:** `nanobot/agent/loop.py`

1. Add parameter to `__init__` signature (line ~176):

```python
def __init__(
    self,
    bus: MessageBus,
    provider: LLMProvider,
    workspace: Path,
    model: str | None = None,
    max_iterations: int = 50,
    timeout_seconds: float = 600,
    context_window_tokens: int = 65_536,
    context_block_limit: int | None = None,
    max_tool_result_chars: int | None = None,
    consolidation_trigger_ratio: float = 1.0,
    ...
):
```

2. Store it (near line ~212):

```python
self.context_window_tokens = context_window_tokens
self.context_block_limit = context_block_limit
self.max_tool_result_chars = max_tool_result_chars or self._TOOL_RESULT_MAX_CHARS
self.consolidation_trigger_ratio = consolidation_trigger_ratio
```

3. Forward to `Consolidator` (line ~325):

```python
self.memory_consolidator = MemoryConsolidator(
    store=self._memory_store,
    provider=provider,
    model=self.model,
    sessions=self.sessions,
    context_window_tokens=context_window_tokens,
    build_messages=self.context.build_messages,
    get_tool_definitions=self.tools.get_definitions,
    max_completion_tokens=provider.generation.max_tokens,
    trigger_ratio=self.consolidation_trigger_ratio,
)
```

**Verify:** `python -m py_compile nanobot/agent/loop.py` exits 0.

---

## Task 4 — Consolidator: use trigger ratio

**File:** `nanobot/agent/memory.py`

1. Update `Consolidator.__init__` (line ~362):

```python
def __init__(
    self,
    store: MemoryStore,
    provider: LLMProvider,
    model: str,
    sessions: SessionManager,
    context_window_tokens: int,
    build_messages: Callable[..., list[dict[str, Any]]],
    get_tool_definitions: Callable[[], list[dict[str, Any]]],
    max_completion_tokens: int = 4096,
    trigger_ratio: float = 1.0,
):
    ...
    self.trigger_ratio = max(0.0, trigger_ratio)
```

2. Rewrite the top of `maybe_consolidate_by_tokens` (line ~460):

```python
async def maybe_consolidate_by_tokens(self, session: Session) -> None:
    if not session.messages or self.context_window_tokens <= 0:
        return

    # Ratio guard: <= 0 means "disabled / use full window"
    ratio = self.trigger_ratio if self.trigger_ratio > 0 else 1.0
    trigger_threshold = int(self.context_window_tokens * ratio)

    budget = trigger_threshold - self.max_completion_tokens - self._SAFETY_BUFFER
    if budget <= 0:
        return

    target = budget // 2
    ...
```

3. Update the two debug/info log lines that print `self.context_window_tokens` to print `trigger_threshold` instead, so the logs reflect the actual threshold in use:

```python
logger.debug(
    "Token consolidation idle {}: {}/{} via {}",
    session.key, estimated, trigger_threshold, source,
)
```

```python
logger.info(
    "Token consolidation round {} for {}: {}/{} via {}, chunk={} msgs",
    round_num, session.key, estimated, trigger_threshold, source, len(chunk),
)
```

**Verify:** `python -m py_compile nanobot/agent/memory.py` exits 0.

---

## Task 5 — Unit tests for Consolidator

**File:** `tests/agent/test_consolidator.py`

Add a new test class at the bottom:

```python
class TestConsolidatorTriggerRatio:
    async def test_ratio_below_one_lowers_trigger(self, consolidator):
        """With ratio=0.5, consolidation fires at half the window."""
        consolidator.trigger_ratio = 0.5
        consolidator.context_window_tokens = 1000
        # budget = 500 - 100 - 1024 = -624 -> returns early because budget <= 0
        # To make this testable, we need a larger window or smaller completion.
        # Let's override for the test:
        consolidator.max_completion_tokens = 100
        consolidator._SAFETY_BUFFER = 0

        session = MagicMock()
        session.last_consolidated = 0
        session.key = "test:key"
        session.messages = [
            {"role": "user", "content": "x" * 200},
            {"role": "assistant", "content": "y" * 200},
        ]
        consolidator.estimate_session_prompt_tokens = MagicMock(return_value=(300, "test"))
        consolidator.archive = AsyncMock(return_value=True)

        await consolidator.maybe_consolidate_by_tokens(session)
        # 300 >= budget(400)? No -> should NOT consolidate
        consolidator.archive.assert_not_called()

    async def test_ratio_fires_when_estimated_exceeds_budget(self, consolidator):
        """With ratio=0.5, consolidation fires when estimated >= budget."""
        consolidator.trigger_ratio = 0.5
        consolidator.context_window_tokens = 1000
        consolidator.max_completion_tokens = 100
        consolidator._SAFETY_BUFFER = 0

        session = MagicMock()
        session.last_consolidated = 0
        session.key = "test:key"
        session.messages = [
            {"role": "user", "content": "x" * 400},
            {"role": "assistant", "content": "y" * 400},
        ]
        # budget = 500 - 100 = 400; target = 200
        consolidator.estimate_session_prompt_tokens = MagicMock(return_value=(450, "test"))
        consolidator.archive = AsyncMock(return_value=True)

        await consolidator.maybe_consolidate_by_tokens(session)
        # 450 >= 400 -> fire; 450 > 200 -> need consolidation
        consolidator.archive.assert_called_once()
```

**Verify:**
```bash
cd ~/git_code/nanobot
python -m pytest tests/agent/test_consolidator.py -v
```

---

## Task 6 — Integration tests for end-to-end trigger

**File:** `tests/agent/test_loop_consolidation_tokens.py`

Add two tests after the existing ones:

```python
@pytest.mark.asyncio
async def test_trigger_ratio_0_5_fires_earlier(tmp_path) -> None:
    """With ratio=0.5, a 250-token estimate against a 500-token window should fire."""
    loop = _make_loop(tmp_path, estimated_tokens=250, context_window_tokens=500)
    loop.consolidation_trigger_ratio = 0.5
    loop.memory_consolidator.trigger_ratio = 0.5
    loop.memory_consolidator._SAFETY_BUFFER = 0

    loop.consolidator.archive = AsyncMock(return_value=True)

    session = loop.sessions.get_or_create("cli:test")
    session.messages = [
        {"role": "user", "content": "u1", "timestamp": "2026-01-01T00:00:00"},
        {"role": "assistant", "content": "a1", "timestamp": "2026-01-01T00:00:01"},
        {"role": "user", "content": "u2", "timestamp": "2026-01-01T00:00:02"},
        {"role": "assistant", "content": "a2", "timestamp": "2026-01-01T00:00:03"},
    ]
    loop.sessions.save(session)

    await loop.process_direct("hello", session_key="cli:test")
    loop.consolidator.archive.assert_called()


@pytest.mark.asyncio
async def test_trigger_ratio_0_5_does_not_fire_when_below_budget(tmp_path) -> None:
    """With ratio=0.5, a 150-token estimate against a 500-token window should NOT fire."""
    loop = _make_loop(tmp_path, estimated_tokens=150, context_window_tokens=500)
    loop.consolidation_trigger_ratio = 0.5
    loop.memory_consolidator.trigger_ratio = 0.5
    loop.memory_consolidator._SAFETY_BUFFER = 0

    loop.consolidator.archive = AsyncMock(return_value=True)

    session = loop.sessions.get_or_create("cli:test")
    session.messages = [
        {"role": "user", "content": "u1", "timestamp": "2026-01-01T00:00:00"},
        {"role": "assistant", "content": "a1", "timestamp": "2026-01-01T00:00:01"},
        {"role": "user", "content": "u2", "timestamp": "2026-01-01T00:00:02"},
        {"role": "assistant", "content": "a2", "timestamp": "2026-01-01T00:00:03"},
    ]
    loop.sessions.save(session)

    await loop.process_direct("hello", session_key="cli:test")
    loop.consolidator.archive.assert_not_called()
```

**Verify:**
```bash
cd ~/git_code/nanobot
python -m pytest tests/agent/test_loop_consolidation_tokens.py -v
```

---

## Task 7 — Regression: existing tests still pass

**Verify:**
```bash
cd ~/git_code/nanobot
python -m pytest tests/agent/test_consolidator.py tests/agent/test_loop_consolidation_tokens.py tests/agent/test_context_pruner.py -v
```

All green before proceeding.

---

## Config Usage (for the user)

After deploy, set in `~/.nanobot/config.json`:

```json
{
  "agents": {
    "defaults": {
      "contextWindowTokens": 1000000,
      "consolidationTriggerRatio": 0.4
    }
  }
}
```

- `1.0` = fire at full window (legacy behavior, default).
- `0.4` = fire at 40 % of window.
- `0.0` or negative = treated as `1.0` (disabled).

---

## Rollback

Single revert of all six files. No data migration (Pydantic default covers missing keys).
