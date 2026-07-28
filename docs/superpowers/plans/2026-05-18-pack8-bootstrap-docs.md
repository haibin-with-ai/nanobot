# Pack8 — Local Docs and Assistant Bootstrap Replay Plan

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

Pack8 is the final pack of the upstream sync replay series. It covers:
- CLAUDE.md merge (fork philosophy/architecture guide + upstream technical guide)
- Bootstrap file reorder and soul anchor for main-agent identity reinforcement
- Bootstrap file injection into subagent system prompt
- identity.md Discord table hint
- Local superpowers plans/specs preservation
- `.gitignore` evaluation (likely no change needed)

Pack8 must not smuggle provider routing, Discord/TTS/transcription UX, runtime/session metadata, command rewrite, subagent trace logging, memory consolidation/pruning, or tools/workspace behavior into it. A sync pack is not a junk drawer.

Checked facts for this planning pass:

- Current worktree branch is `sync-upstream-2026-05-replay`.
- `HEAD` during inspection was `dd36ca38 docs: add pack7 tools workspace behavior replay plan`.
- Merge base with `upstream/main` is `ba38f9083291a899d62c9b4b2a7b46429c39b062`.
- `CLAUDE.md` exists in both worktree (upstream version, 84 lines, Markdown, technical) and `origin/main` (fork version, XML-tag style, philosophy/architecture guide in Chinese).
- `nanobot/agent/context.py` exists in both; upstream has no soul_anchor, origin/main has soul_anchor in `build_system_prompt`.
- `nanobot/agent/subagent.py` exists in both; upstream has no bootstrap injection, origin/main injects `SOUL.md` and `TOOLS.md` into subagent prompt.
- `nanobot/templates/agent/identity.md` exists in both; upstream lacks Discord table hint, origin/main has it.
- `nanobot/templates/agent/subagent_system.md` exists in both; upstream has no `bootstrap` block, origin/main has it.
- `tests/agent/test_context_builder.py` exists in worktree (349 lines); it tests bootstrap loading but does not test order or soul_anchor.
- `tests/agent/test_subagent.py` exists in worktree (53 lines); it does not test system prompt content.
- `.gitignore` in worktree already ignores `docs/superpowers/` and `docs/plans/`; tracked files in those directories are unaffected.
- `docs/superpowers/plans/` in worktree already contains Pack1–Pack7 plan files + `2026-05-18-upstream-sync.md`.
- `origin/main:docs/superpowers/plans/` contains additional fork plan files not yet in worktree.
- `origin/main:docs/superpowers/specs/` contains one additional spec file not yet in worktree.

---

## 1. Goal

Replay the fork's production-critical bootstrap, identity, and local-docs behavior onto upstream `main`, producing a single clean commit that closes the upstream sync replay series.

Specific behaviors to preserve:

1. **CLAUDE.md merge**: The fork has a rich coding-philosophy/architecture guide. Upstream has a concise technical reference. The merged file must keep both without information loss.
2. **Bootstrap file order**: `SOUL.md` first in `ContextBuilder.BOOTSTRAP_FILES` to exploit primacy effect (U-shaped attention).
3. **Soul anchor in main agent**: Repeat `SOUL.md` as a `# Remember` block at the tail of the main-agent system prompt, after skills/memory. Subagent does **not** get a soul anchor (fork deleted it after oscillation; bootstrap already provides SOUL.md there).
4. **Subagent bootstrap injection**: Subagent system prompt loads `SOUL.md` and `TOOLS.md` from workspace and renders them via the `subagent_system.md` template.
5. **identity.md Discord table hint**: Add the critical Discord table rendering warning.
6. **Local docs preservation**: Copy fork's additional plan/spec files into the worktree for reference.

---

## 2. Non-goals

- Do not touch Anthropic OAuth/provider routing — Pack1.
- Do not touch Discord UX/TTS/transcription — Pack2.
- Do not touch session/runtime metadata — Pack3.
- Do not touch command rewrite / rtk — Pack4.
- Do not touch subagent model override/TraceHook/LLM logs — Pack5.
- Do not touch memory/consolidation/context pruning — Pack6.
- Do not touch tools/workspace behavior — Pack7.
- Do not reimplement `WorkspaceLayout`-aware bootstrap path resolution. origin/main uses `WorkspaceLayout` for per-channel/per-sender bootstrap overrides. Upstream lacks `WorkspaceLayout` in `ContextBuilder`. Pack8 keeps bootstrap loading simple (root workspace files only) and leaves layout-aware resolution for a future pack if needed.
- Do not change the main agent `build_system_prompt` signature or channel-sender filtering logic. Pack8 only adds the soul_anchor call at the end.
- Do not modify `.gitignore` unless an untracked local doc must be tracked. The existing ignore rules (`docs/superpowers/`, `docs/plans/`) are intentional upstream hygiene. Already-tracked Pack plan files are unaffected.

---

## 3. Source commits

| Commit | Message | Files touched | Relevance |
|--------|---------|---------------|-----------|
| `75266d8e` | Add CLAUDE.md coding philosophy and architecture guide | `A CLAUDE.md` | Full CLAUDE.md content from fork. |
| `5369da7e` | feat(context): reorder bootstrap files and add soul anchor | `M nanobot/agent/context.py` | BOOTSTRAP_FILES reorder + `_load_soul_anchor` + tail injection in `build_system_prompt`. |
| `55de6e88` | feat(subagent): inject bootstrap files into subagent system prompt | `M nanobot/agent/subagent.py`, `M nanobot/templates/agent/subagent_system.md` | Bootstrap loading in `_build_subagent_prompt`, `bootstrap` var in template. |
| `0b2565a8` | fix: remove soul_anchor from subagent prompt | `M nanobot/agent/subagent.py`, `M nanobot/templates/agent/subagent_system.md` | Intermediate oscillation; subagent soul_anchor removed because bootstrap already provides SOUL.md. |
| `ca66cb44` | feat(subagent): add soul anchor to subagent prompt | `M nanobot/agent/subagent.py`, `M nanobot/templates/agent/subagent_system.md` | Intermediate oscillation; soul_anchor re-added. |
| `74681de2` | delete soul_anchor | `M nanobot/agent/subagent.py`, `M nanobot/templates/agent/subagent_system.md` | **Final state**: subagent soul_anchor deleted forever. Bootstrap files reduced to `["SOUL.md", "TOOLS.md"]` for subagent. |
| `8cbd5e2b` | fix: minor uncommitted cleanup (runner import, discord table hint in identity.md) | `M nanobot/agent/runner.py`, `M nanobot/templates/agent/identity.md` | Only the `identity.md` Discord table hint belongs to Pack8. The `runner.py` 120→1024 truncation changes are logging behavior and belong to Pack5; they are **not** replayed here. |

---

## 4. Files expected to change

### Implementation files

```text
CLAUDE.md                                    # merge upstream + fork content
nanobot/agent/context.py                     # BOOTSTRAP_FILES reorder + soul_anchor
nanobot/agent/subagent.py                    # inject bootstrap into subagent prompt
nanobot/templates/agent/identity.md          # Discord table hint
nanobot/templates/agent/subagent_system.md   # bootstrap conditional block
```

### Test files

```text
tests/agent/test_context_builder.py          # add bootstrap-order + soul_anchor tests
tests/agent/test_subagent.py                 # add subagent bootstrap injection test
```

### Docs (reference only, no code impact)

```text
docs/superpowers/plans/2026-03-31-code-review-fixes.md           # copy from origin/main
docs/superpowers/plans/2026-03-31-fallback-provider.md           # copy from origin/main
docs/superpowers/plans/2026-03-31-llm-trace-hook.md              # copy from origin/main
docs/superpowers/plans/2026-04-01-workspace-layout-refactor.md   # copy from origin/main
docs/superpowers/plans/2026-04-03-model-command.md               # copy from origin/main
docs/superpowers/plans/2026-04-08-integrate-context-pruner.md    # copy from origin/main
docs/superpowers/specs/2026-04-01-workspace-layout-refactor-design.md  # copy from origin/main
```

---

## 5. Upstream baseline observations

### 5.1 CLAUDE.md

Upstream (worktree) `CLAUDE.md` (84 lines, Markdown):

- Sections: Project Overview, Development Commands, High-Level Architecture (Core Data Flow, Key Modules), Entry Points, Project-Specific Notes, Branching Strategy, Code Style, Common File Locations.
- Tone: concise technical reference for Claude Code (claude.ai/code).
- No philosophy, identity, or cognitive-architecture content.

Fork `origin/main:CLAUDE.md` (XML-tag style, Chinese, philosophy/architecture guide):

- Sections: `<identity>`, `<cognitive_architecture>` (phenomenal/essential/philosophical layers), `<cognitive_mission>`, `<role_trinity>`, `<philosophy_good_taste>`, `<philosophy_pragmatism>`, `<philosophy_simplicity>`, `<design_freedom>`, `<code_output_structure>`, `<quality_metrics>`, `<code_smells>`, `<architecture_documentation>`, `<documentation_protocol>`, `<interaction_protocol>`, `<ultimate_truth>`.
- Tone: identity reinforcement and architecture philosophy.
- No project overview, no development commands, no file locations.

**Observation**: The two files are almost entirely non-overlapping in content and format. They serve different purposes. Merge strategy is therefore straightforward concatenation.

### 5.2 context.py

Upstream `ContextBuilder`:

- `BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md"]`
- `build_system_prompt` assembles: identity → bootstrap → memory → active skills → skills summary → recent history → session summary. No soul_anchor.
- `_load_bootstrap_files()` reads root workspace files only. No per-channel/per-sender resolution.

Fork `origin/main:nanobot/agent/context.py`:

- `BOOTSTRAP_FILES = ["SOUL.md", "USER.md", "AGENTS.md", "TOOLS.md"]`
- `build_system_prompt` ends with `# Remember\n\n{soul_anchor}` after skills.
- `_load_soul_anchor()` loads SOUL.md from root workspace.

### 5.3 subagent.py

Upstream `SubagentManager._build_subagent_prompt`:

- Builds `time_ctx` + `skills_summary`.
- Calls `render_template("agent/subagent_system.md", time_ctx=..., workspace=..., skills_summary=...)`.
- No bootstrap variable passed.

Fork `origin/main:nanobot/agent/subagent.py` (final state after `74681de2`):

- Builds `time_ctx` + `skills_summary`.
- Loads bootstrap files `SOUL.md` and `TOOLS.md` from workspace root.
- Calls `render_template(..., bootstrap=bootstrap)`.
- No `soul_anchor` variable.

### 5.4 subagent_system.md

Upstream template:

```markdown
# Subagent

{{ time_ctx }}

You are a subagent ...

{% include 'agent/_snippets/untrusted_content.md' %}

## Workspace
{{ workspace }}
{% if skills_summary %}
...
{% endif %}
```

Fork template (after `74681de2`):

```markdown
# Subagent

{{ time_ctx }}

You are a subagent ...

{% if bootstrap %}

{{ bootstrap }}
{% endif %}

{% include 'agent/_snippets/untrusted_content.md' %}

## Workspace
Your workspace is at:  {{ workspace }}
...
```

Note: The `{% include %}` line moved below the `bootstrap` block in the fork. Preserve that relative ordering.

### 5.5 identity.md

Upstream:

```markdown
{% if channel == 'telegram' or channel == 'qq' or channel == 'discord' %}
## Format Hint
This conversation is on a messaging app. Use short paragraphs. Avoid large headings (#, ##). Use **bold** sparingly. No tables — use plain lists.
```

Fork (after `8cbd5e2b`):

```markdown
{% if channel == 'telegram' or channel == 'qq' or channel == 'discord' %}
## Format Hint
This conversation is on a messaging app. Use short paragraphs. Avoid large headings (#, ##). Use **bold** sparingly.
**CRITICAL: Discord does NOT render Markdown tables.** Never use `|` column syntax. If you must show structured data, use a plain list or an ASCII table inside a fenced code block (```).
```

The change is a line split + addition. The first sentence is preserved; the second sentence is replaced by two lines.

### 5.6 .gitignore

Upstream worktree `.gitignore` already contains:

```text
docs/superpowers/
docs/plans/
```

This means any **new untracked** files placed under those directories will be ignored by git status. However, already-tracked files (the Pack1–Pack7 plan files) are unaffected. Since the additional fork docs are copied for local reference and do not need to be committed as part of the sync replay, no `.gitignore` change is required.

If a future agent decides to commit them, it can force-add individual files or adjust `.gitignore` at that time. Pack8 does not modify `.gitignore`.

---

## 6. Design decisions

### 6.1 CLAUDE.md merge strategy

**Decision**: Concatenate upstream content first, then append fork philosophy content, separated by a clear horizontal rule.

**Rationale**:
- The two files serve different audiences: upstream CLAUDE.md is a quick technical reference for Claude Code; fork CLAUDE.md is a deep identity/philosophy anchor.
- They share almost zero content overlap.
- Putting the technical reference first preserves the original upstream behavior for generic queries ("how do I run tests?").
- Putting the philosophy guide after ensures it is still loaded into context when the file is read in full.

**Implementation rule**:
1. Keep the upstream `CLAUDE.md` exactly as-is (lines 1–84).
2. Append `\n\n---\n\n`.
3. Append the full `origin/main:CLAUDE.md` content after the separator.
4. Do not reformat the fork content into Markdown; keep its XML-tag style because that is how the fork's persona system consumes it.

### 6.2 Bootstrap file order

**Decision**: Change `BOOTSTRAP_FILES` to `["SOUL.md", "USER.md", "AGENTS.md", "TOOLS.md"]`.

**Rationale**: Fork commit `5369da7e` explicitly reordered SOUL.md to the first position to exploit the primacy peak of U-shaped attention. This is the desired production behavior.

### 6.3 Soul anchor final state

**Decision**: Add `_load_soul_anchor` and tail injection to `ContextBuilder.build_system_prompt`. Do **not** add any soul anchor logic to `SubagentManager` or `subagent_system.md`.

**Rationale**: The fork oscillated four times:
1. `5369da7e` — added soul anchor to main agent context.
2. `55de6e88` — injected bootstrap into subagent (including SOUL.md).
3. `0b2565a8` — removed soul_anchor from subagent (bootstrap already provides SOUL.md).
4. `ca66cb44` — re-added soul_anchor to subagent.
5. `74681de2` — **deleted** soul_anchor from subagent forever.

Final fork state: main agent has soul_anchor; subagent does not. Bootstrap injection in subagent provides SOUL.md once, which is sufficient. Replaying the oscillation is wasteful; jump directly to the final state.

**Implementation rule**:
- Add `def _load_soul_anchor(self) -> str | None:` to `ContextBuilder`.
- It reads `self.workspace / "SOUL.md"`; returns content stripped, or `None` if missing.
- In `build_system_prompt`, after the session_summary block (i.e., at the very end before `return`), add:
  ```python
  soul_anchor = self._load_soul_anchor()
  if soul_anchor:
      parts.append(f"# Remember\n\n{soul_anchor}")
  ```
- Do not touch `SubagentManager` soul_anchor.

### 6.4 Subagent bootstrap injection

**Decision**: In `SubagentManager._build_subagent_prompt`, load `SOUL.md` and `TOOLS.md` from the workspace root, format them as `## {filename}\n\n{content}` blocks, join with `\n\n`, and pass to the template as `bootstrap`.

**Rationale**: Fork `74681de2` settled on `BOOTSTRAP_FILES = ["SOUL.md", "TOOLS.md"]` for subagents. USER.md and AGENTS.md are omitted because subagents are task-focused and do not need user-specific context or agent routing rules.

**Implementation rule**:
- Use a local `BOOTSTRAP_FILES = ["SOUL.md", "TOOLS.md"]` inside `_build_subagent_prompt` (do not reuse `ContextBuilder.BOOTSTRAP_FILES` because the subagent list is intentionally shorter).
- Read each file from `self.workspace / filename`.
- Skip missing or empty files.
- Format exactly as `f"## {filename}\n\n{content}"`.
- Join with `\n\n`.
- Pass `bootstrap=bootstrap` to `render_template`.

### 6.5 subagent_system.md template ordering

**Decision**: Place the `{% if bootstrap %}` block **after** the subagent identity sentence and **before** the `{% include 'agent/_snippets/untrusted_content.md' %}` line. This matches the fork final state.

**Rationale**: In the fork, the bootstrap content (SOUL.md behavioral guidelines) should appear early enough to shape the subagent's reasoning, before the untrusted-content warning.

### 6.6 identity.md change scope

**Decision**: Modify only the Discord `elif` branch. Keep all other branches untouched.

**Exact change**:
Replace:
```markdown
This conversation is on a messaging app. Use short paragraphs. Avoid large headings (#, ##). Use **bold** sparingly. No tables — use plain lists.
```
With:
```markdown
This conversation is on a messaging app. Use short paragraphs. Avoid large headings (#, ##). Use **bold** sparingly.
**CRITICAL: Discord does NOT render Markdown tables.** Never use `|` column syntax. If you must show structured data, use a plain list or an ASCII table inside a fenced code block (```).
```

### 6.7 Local docs preservation

**Decision**: Copy the listed fork plan/spec files into the worktree under the same paths. Do not edit their contents. They may remain untracked (ignored by `.gitignore`).

**Rationale**: These are historical reference documents. They are not runtime code. Preserving them aids future archaeology. Because `.gitignore` ignores the directories, they will not show up in `git status` as untracked noise.

---

## 7. TDD task sequence

### Task 1 — Bootstrap order test

Add a test to `tests/agent/test_context_builder.py` in `TestLoadBootstrapFiles`:

```python
def test_bootstrap_file_order(self, tmp_path):
    """SOUL.md must appear before AGENTS.md in the loaded bootstrap."""
    for name in ContextBuilder.BOOTSTRAP_FILES:
        (tmp_path / name).write_text(f"Content of {name}", encoding="utf-8")
    builder = _builder(tmp_path)
    result = builder._load_bootstrap_files()
    soul_pos = result.find("## SOUL.md")
    agents_pos = result.find("## AGENTS.md")
    assert soul_pos != -1
    assert agents_pos != -1
    assert soul_pos < agents_pos
```

Run:

```bash
python3 -m pytest tests/agent/test_context_builder.py::TestLoadBootstrapFiles::test_bootstrap_file_order -q
```

Expected RED (upstream order is AGENTS.md first).

Implement: change `BOOTSTRAP_FILES` order to `["SOUL.md", "USER.md", "AGENTS.md", "TOOLS.md"]`.

Re-run until green.

### Task 2 — Soul anchor presence test

Add to `tests/agent/test_context_builder.py` under a new `TestSoulAnchor` class:

```python
class TestSoulAnchor:
    def test_soul_anchor_appended_when_soul_md_exists(self, tmp_path):
        (tmp_path / "SOUL.md").write_text("Be kind.", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder.build_system_prompt()
        assert "# Remember" in result
        assert "Be kind." in result
        # Must appear after skills/memory sections (after the last "---" separator)
        remember_pos = result.find("# Remember")
        assert remember_pos > result.find("---")

    def test_no_soul_anchor_when_missing(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder.build_system_prompt()
        assert "# Remember" not in result
```

Run:

```bash
python3 -m pytest tests/agent/test_context_builder.py::TestSoulAnchor -q
```

Expected RED.

Implement:
1. Add `_load_soul_anchor` method to `ContextBuilder`.
2. Append soul anchor in `build_system_prompt` before the final `return`.

Re-run until green.

### Task 3 — Subagent bootstrap injection test

Add to `tests/agent/test_subagent.py`:

```python
@pytest.mark.asyncio
async def test_subagent_system_prompt_includes_bootstrap(tmp_path):
    """Subagent prompt must include SOUL.md and TOOLS.md when they exist."""
    (tmp_path / "SOUL.md").write_text("Soul content.", encoding="utf-8")
    (tmp_path / "TOOLS.md").write_text("Tools content.", encoding="utf-8")
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test"
    sm = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        model="test",
        max_tool_result_chars=16_000,
    )
    prompt = sm._build_subagent_prompt()
    assert "## SOUL.md" in prompt
    assert "Soul content." in prompt
    assert "## TOOLS.md" in prompt
    assert "Tools content." in prompt

@pytest.mark.asyncio
async def test_subagent_system_prompt_omits_missing_bootstrap(tmp_path):
    """Subagent prompt must not reference missing bootstrap files."""
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test"
    sm = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        model="test",
        max_tool_result_chars=16_000,
    )
    prompt = sm._build_subagent_prompt()
    assert "## SOUL.md" not in prompt
    assert "## TOOLS.md" not in prompt
```

Run:

```bash
python3 -m pytest tests/agent/test_subagent.py -q
```

Expected RED (upstream has no bootstrap injection).

Implement:
1. In `SubagentManager._build_subagent_prompt`, load `SOUL.md` and `TOOLS.md`.
2. Pass `bootstrap` to `render_template`.
3. Update `nanobot/templates/agent/subagent_system.md` to render `bootstrap`.

Re-run until green.

### Task 4 — identity.md Discord table hint test

Add a template render test. Because there is no dedicated identity.md test file, add a small inline test in `tests/agent/test_context_builder.py`:

```python
class TestIdentityTemplate:
    def test_discord_table_hint_present(self):
        from nanobot.utils.prompt_templates import render_template
        result = render_template("agent/identity.md", workspace_path="/tmp", runtime="test", platform_policy="", channel="discord")
        assert "Discord does NOT render Markdown tables" in result
        assert "Never use `|` column syntax" in result
```

Run:

```bash
python3 -m pytest tests/agent/test_context_builder.py::TestIdentityTemplate -q
```

Expected RED.

Implement: apply the identity.md change described in §6.6.

Re-run until green.

### Task 5 — CLAUDE.md merge

This is a content file with no runtime behavior, so there is no automated test beyond "file exists and contains both upstream and fork markers".

Manually verify after writing:

```bash
# Check upstream technical section is preserved
grep -q "Project Overview" CLAUDE.md
# Check fork philosophy section is preserved
grep -q "<identity>" CLAUDE.md
```

Implement: write the merged CLAUDE.md per §6.1.

### Task 6 — Local docs copy

No tests. Execute copy commands:

```bash
cd /root/git_code/nanobot/.worktrees/sync-upstream-2026-05-merge

# Create directories if needed
mkdir -p docs/superpowers/specs

# Copy fork plan files that are not already present
for f in 2026-03-31-code-review-fixes.md 2026-03-31-fallback-provider.md 2026-03-31-llm-trace-hook.md 2026-04-01-workspace-layout-refactor.md 2026-04-03-model-command.md 2026-04-08-integrate-context-pruner.md; do
  if [ ! -f "docs/superpowers/plans/$f" ]; then
    git show origin/main:docs/superpowers/plans/"$f" > "docs/superpowers/plans/$f"
  fi
done

# Copy spec file
if [ ! -f "docs/superpowers/specs/2026-04-01-workspace-layout-refactor-design.md" ]; then
  git show origin/main:docs/superpowers/specs/2026-04-01-workspace-layout-refactor-design.md > docs/superpowers/specs/2026-04-01-workspace-layout-refactor-design.md
fi
```

Verify files exist on disk:

```bash
ls -la docs/superpowers/plans/2026-03-31-* docs/superpowers/plans/2026-04-* docs/superpowers/specs/
```

---

## 8. Pack-level verification

After all tasks pass, run the agent-level test suite to ensure no regressions:

```bash
python3 -m pytest tests/agent/test_context_builder.py tests/agent/test_subagent.py -q
```

Expected: all green.

Also run a broader agent test sweep to catch any prompt-structure regressions:

```bash
python3 -m pytest tests/agent/ -q --timeout=60
```

If any test fails because prompt length or ordering changed unexpectedly, investigate before declaring Pack8 complete.

---

## 9. Manual smoke check

1. **Bootstrap order smoke**:
   ```python
   from nanobot.agent.context import ContextBuilder
   from pathlib import Path
   import tempfile
   with tempfile.TemporaryDirectory() as d:
       p = Path(d)
       for name in ContextBuilder.BOOTSTRAP_FILES:
           (p / name).write_text(name)
       cb = ContextBuilder(p)
       result = cb._load_bootstrap_files()
       assert result.index("SOUL.md") < result.index("AGENTS.md")
   ```

2. **Soul anchor smoke**:
   ```python
   from nanobot.agent.context import ContextBuilder
   from pathlib import Path
   import tempfile
   with tempfile.TemporaryDirectory() as d:
       p = Path(d)
       (p / "SOUL.md").write_text("Kindness first.")
       cb = ContextBuilder(p)
       prompt = cb.build_system_prompt()
       assert "# Remember" in prompt
       assert "Kindness first." in prompt
   ```

3. **Subagent bootstrap smoke**:
   ```python
   from nanobot.agent.subagent import SubagentManager
   from nanobot.bus.queue import MessageBus
   from pathlib import Path
   from unittest.mock import MagicMock
   import tempfile
   with tempfile.TemporaryDirectory() as d:
       p = Path(d)
       (p / "SOUL.md").write_text("Soul.")
       (p / "TOOLS.md").write_text("Tools.")
       provider = MagicMock()
       provider.get_default_model.return_value = "test"
       sm = SubagentManager(provider=provider, workspace=p, bus=MessageBus(), model="test", max_tool_result_chars=16_000)
       prompt = sm._build_subagent_prompt()
       assert "## SOUL.md" in prompt
       assert "## TOOLS.md" in prompt
   ```

4. **Identity template smoke**:
   ```python
   from nanobot.utils.prompt_templates import render_template
   out = render_template("agent/identity.md", workspace_path="/tmp", runtime="test", platform_policy="", channel="discord")
   assert "Discord does NOT render Markdown tables" in out
   ```

5. **CLAUDE.md smoke**:
   ```bash
   head -n 5 /root/git_code/nanobot/.worktrees/sync-upstream-2026-05-merge/CLAUDE.md | grep -q "Project Overview"
   tail -n 20 /root/git_code/nanobot/.worktrees/sync-upstream-2026-05-merge/CLAUDE.md | grep -q "<ultimate_truth>"
   ```

---

## 10. Rollback plan

If Pack8 introduces regressions in prompt structure or agent behavior:

1. Revert `CLAUDE.md` to upstream version:
   ```bash
   git checkout upstream/main -- CLAUDE.md
   ```

2. Revert `nanobot/agent/context.py` bootstrap order and soul_anchor:
   ```bash
   git checkout upstream/main -- nanobot/agent/context.py
   ```

3. Revert `nanobot/agent/subagent.py` bootstrap injection:
   ```bash
   git checkout upstream/main -- nanobot/agent/subagent.py
   ```

4. Revert template changes:
   ```bash
   git checkout upstream/main -- nanobot/templates/agent/identity.md
   git checkout upstream/main -- nanobot/templates/agent/subagent_system.md
   ```

5. Remove any added tests and revert test file changes:
   ```bash
   git checkout upstream/main -- tests/agent/test_context_builder.py
   git checkout upstream/main -- tests/agent/test_subagent.py
   ```

6. Remove copied local docs (they are untracked/ignored, so just delete):
   ```bash
   rm -f docs/superpowers/plans/2026-03-31-*.md docs/superpowers/plans/2026-04-0*.md docs/superpowers/plans/2026-04-03-*.md docs/superpowers/specs/2026-04-01-workspace-layout-refactor-design.md
   ```

Then re-run `tests/agent/` to confirm baseline is restored.

---

## 11. Completion criteria

Pack8 is complete when **all** of the following are true:

1. `CLAUDE.md` contains both the upstream technical reference (lines 1–84 preserved) and the fork philosophy/architecture guide (concatenated after `---`).
2. `ContextBuilder.BOOTSTRAP_FILES` is `["SOUL.md", "USER.md", "AGENTS.md", "TOOLS.md"]`.
3. `ContextBuilder.build_system_prompt` appends a `# Remember` block with `SOUL.md` content when the file exists.
4. `SubagentManager._build_subagent_prompt` loads `SOUL.md` and `TOOLS.md` from workspace and passes them to the template as `bootstrap`.
5. `nanobot/templates/agent/subagent_system.md` renders the `bootstrap` variable in the correct position (after identity sentence, before untrusted-content include).
6. `nanobot/templates/agent/identity.md` contains the Discord table hint exactly as specified in §6.6.
7. All new tests in `tests/agent/test_context_builder.py` and `tests/agent/test_subagent.py` pass.
8. The full `tests/agent/` suite passes with no regressions.
9. Fork local docs are copied into `docs/superpowers/plans/` and `docs/superpowers/specs/` for reference.
10. No `.gitignore` changes were made (or if made, they are documented and justified).
11. **No commits have been made to `sync-upstream-2026-05-replay` yet.** The execution agent will commit when all packs are ready.

This is the last pack. After Pack8 passes, the upstream sync replay plan series is closed. The execution agent should then:
- Run the full test suite across all touched modules.
- Produce a final summary commit (or a series of commits, one per pack) on `sync-upstream-2026-05-replay`.
- Prepare for merge review into `upstream/main`.
