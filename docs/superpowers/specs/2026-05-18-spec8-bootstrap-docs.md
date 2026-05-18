# Spec 8 — Local Docs and Assistant Bootstrap Replay

## 0. Overview

Pack8 是 upstream sync replay 系列的最后一个 pack。它将 fork 的本地文档策略、引导文件秩序和助手身份锚定注入到 upstream 代码库中，同时保持对 upstream 架构的最小侵入。

本 spec 覆盖四个核心变更：

1. **CLAUDE.md 不合并**：CLAUDE.md 保持 upstream 原样，fork 的哲学/架构指南独立存放，不污染 upstream 文档。
2. **Bootstrap 文件重排序与 soul anchor**：调整主 agent 的引导文件加载顺序，并在系统提示末尾追加 soul anchor（`# Remember` 块）。
3. **Subagent bootstrap 注入**：为子 agent 系统提示注入精简后的引导内容（仅 `SOUL.md` + `TOOLS.md`）。
4. **identity.md Discord table hint**：在 Discord 格式提示中增加 Markdown 表格不渲染的警告。

额外工作：将 fork 本地文档复制到 `docs/superpowers/` 留存参考。

---

## 1. Behavioral Requirements

### 1.1 CLAUDE.md

- **CLAUDE.md 保持 upstream 原样，不追加 fork 内容。**
- Fork 的哲学/架构指南已经在 `SOUL.md` 中体现（`SOUL.md` 是给模型读的运行时契约）。
- 如果有 fork-specific 的开发者参考（如 coding philosophy），放在独立的 `FORK_GUIDE.md` 或 `docs/` 下，不污染 upstream `CLAUDE.md`。
- 这样每次 upstream 更新 `CLAUDE.md` 时零冲突。

### 1.2 Bootstrap 加载顺序

主 agent 的 `ContextBuilder.BOOTSTRAP_FILES` 必须变为：

```python
BOOTSTRAP_FILES = ["SOUL.md", "USER.md", "AGENTS.md", "TOOLS.md"]
```

理由：利用 U 形注意力曲线的首因效应（primacy peak），让 SOUL.md（行为人格定义）最先进入上下文。

### 1.3 Soul anchor（主 agent）

- 当工作区根目录存在 `SOUL.md` 时，`ContextBuilder.build_system_prompt()` 返回的字符串末尾必须追加一个 `# Remember` 块，内容为 `SOUL.md` 的 stripped 内容。
- 若 `SOUL.md` 不存在，不追加任何内容。
- Soul anchor 必须位于 `session_summary` 块之后（即系统提示的最末尾，在 `return` 之前）。
- **子 agent 系统提示中不得出现 soul anchor。**
- **实验性特性**：soul anchor 基于 fork 生产经验，缺乏 A/B 测试验证。如果 upstream 未来提供 prompt importance/pinning 机制，soul anchor 应迁移到该机制。

### 1.4 Subagent bootstrap 注入

- `SubagentManager._build_subagent_prompt()` 必须从工作区根目录读取 `SOUL.md` 和 `TOOLS.md`。
- 格式化为 `## {filename}\n\n{content}` 块，缺失或空文件跳过，块之间以 `\n\n` 连接。
- 将连接后的字符串作为 `bootstrap` 变量传给 `render_template("agent/subagent_system.md", ...)`。
- 子 agent 不注入 `USER.md` 和 `AGENTS.md`——子 agent 是任务专注的，不需要用户特定上下文或 agent 路由规则。
- **显式验证**：`USER.md` 包含用户个人信息和偏好，subagent 是任务专注的短生命周期执行者，不需要也不应该接触用户个人数据。如果未来出现 subagent 需要用户偏好的场景（如用户是色盲），应通过 task prompt 显式传递相关上下文，而不是注入完整 `USER.md`。
- 子 agent 使用独立的本地文件列表，不重用 `ContextBuilder.BOOTSTRAP_FILES`。

### 1.5 Subagent 模板渲染位置

`nanobot/templates/agent/subagent_system.md` 中，`bootstrap` 块必须出现在：

1. 子 agent 身份说明句（"You are a subagent spawned..."）之后；
2. `{% include 'agent/_snippets/untrusted_content.md' %}` 之前。

### 1.6 Discord table hint

`nanobot/templates/agent/identity.md` 的 Discord format hint 中，在 `Use **bold** sparingly.` 之后追加：

```
**CRITICAL: Discord does NOT render Markdown tables.** Never use `|` column syntax. If you must show structured data, use a plain list or an ASCII table inside a fenced code block (```).
```

### 1.7 本地文档留存

将以下 fork 文档从 `origin/main` 复制到当前工作树：

- `docs/superpowers/plans/2026-03-31-code-review-fixes.md`
- `docs/superpowers/plans/2026-03-31-fallback-provider.md`
- `docs/superpowers/plans/2026-03-31-llm-trace-hook.md`
- `docs/superpowers/plans/2026-04-01-workspace-layout-refactor.md`
- `docs/superpowers/plans/2026-04-03-model-command.md`
- `docs/superpowers/plans/2026-04-08-integrate-context-pruner.md`
- `docs/superpowers/specs/2026-04-01-workspace-layout-refactor-design.md`

### 1.8 .gitignore

当前 `.gitignore` 已包含 `docs/superpowers/` 和 `docs/plans/`，无需变更。

---

## 2. Architecture Analysis

### 2.1 现有代码结构

#### `ContextBuilder`（`nanobot/agent/context.py`）

```python
def load_and_format_bootstrap(filenames: list[str], workspace: Path) -> str:
    """Load and format bootstrap files as `## {name}\n\n{content}` blocks."""
    parts = []
    for filename in filenames:
        file_path = workspace / filename
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8")
            parts.append(f"## {filename}\n\n{content}")
    return "\n\n".join(parts) if parts else ""


class ContextBuilder:
    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md"]

    def build_system_prompt(self, ...):
        parts = [self._get_identity(channel=channel)]
        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)
        memory = self.memory.get_memory_context()
        if memory and not self._is_template_content(...):
            parts.append(f"# Memory\n\n{memory}")
        always_skills = self.skills.get_always_skills()
        ...
        entries = self.memory.read_unprocessed_history(...)
        if entries:
            ...
            parts.append("# Recent History\n\n" + history_text)
        if session_summary:
            parts.append(f"[Archived Context Summary]\n\n{session_summary}")
        return "\n\n---\n\n".join(parts)

    def _load_bootstrap_files(self) -> str:
        return load_and_format_bootstrap(self.BOOTSTRAP_FILES, self.workspace)
```

当前 `build_system_prompt` 的结构（从上到下）是：

1. Identity（渲染 `agent/identity.md`）
2. Bootstrap files（`_load_bootstrap_files()`）
3. Memory（`# Memory` 块，条件注入）
4. Active Skills（`# Active Skills` 块，条件注入）
5. Skills section（`agent/skills_section.md`，条件注入）
6. Recent History（`# Recent History` 块，条件注入）
7. Session Summary（`[Archived Context Summary]` 块，条件注入）
8. 以 `\n\n---\n\n` 连接所有 parts

Soul anchor 要追加在第 7 步之后、第 8 步之前，成为系统提示的最后一个语义块。

#### `SubagentManager`（`nanobot/agent/subagent.py`）

```python
def _build_subagent_prompt(self) -> str:
    time_ctx = ...
    skills_summary = SkillsLoader(...).build_skills_summary()
    return render_template(
        "agent/subagent_system.md",
        time_ctx=time_ctx,
        workspace=str(self.workspace),
        skills_summary=skills_summary or "",
    )
```

当前 `_build_subagent_prompt` 不接收也不传递 `bootstrap` 变量。

#### `subagent_system.md`（`nanobot/templates/agent/subagent_system.md`）

```jinja2
# Subagent

{{ time_ctx }}

You are a subagent spawned by the main agent to complete a specific task.
Stay focused on the assigned task. Your final response will be reported back to the main agent.

{% include 'agent/_snippets/untrusted_content.md' %}

## Workspace
Your workspace is at:  {{ workspace }}
{% if skills_summary %}

## Skills

Read SKILL.md with read_file to use a skill.

{{ skills_summary }}
{% endif %}
```

#### `identity.md`（`nanobot/templates/agent/identity.md`）

包含按 `channel` 分发的 format hint。当前 Discord 分支：

```jinja2
{% if channel == 'telegram' or channel == 'qq' or channel == 'discord' %}
## Format Hint
This conversation is on a messaging app. Use short paragraphs. Avoid large headings (#, ##). Use **bold** sparingly. No tables — use plain lists.
{% elif ... %}
```

### 2.2 关键差异点

| 维度 | upstream | fork | Pack8 决策 |
|------|----------|------|-----------|
| Bootstrap 顺序 | `AGENTS, SOUL, USER, TOOLS` | `SOUL, USER, AGENTS, TOOLS` | 采用 fork 顺序 |
| Soul anchor | 不存在 | 主 agent 有，子 agent 无 | 采用 fork 最终状态 |
| Subagent bootstrap | 不存在 | `SOUL.md` + `TOOLS.md` | 采用 fork 最终状态 |
| CLAUDE.md | 纯技术参考 | 纯哲学指南 | **不合并**：保持 upstream 原样，fork 内容放 SOUL.md / FORK_GUIDE.md |
| Discord tables | 无特殊提示 | 明确禁止 `|` 语法 | 采用 fork 提示 |

---

## 3. Technical Design

### 3.1 CLAUDE.md 不合并策略

**决策**：CLAUDE.md **不合并**，保持 upstream 原样。

**理由**：

1. **零冲突**：upstream 会不断更新 `CLAUDE.md`（技术参考、工具说明等）。如果 fork 追加内容，每次 upstream 更新都会产生 merge conflict。
2. **语义分离**：upstream `CLAUDE.md` 是面向 Claude Code 的技术指南；fork 的哲学/架构指南是面向模型运行时的身份定义。两者消费方不同，不需要放在同一个文件。
3. **已有替代载体**：fork 的 persona 内容已经在 `SOUL.md` 中完整表达（通过 bootstrap 注入到主 agent 上下文）。`SOUL.md` 才是给模型读的运行时契约，`CLAUDE.md` 是给开发者读的静态参考。
4. **开发者参考独立存放**：如果有 fork-specific 的开发者参考（如 coding philosophy、架构决策记录），放在 `FORK_GUIDE.md` 或 `docs/` 下，不污染 upstream 文件。

**实现**：

- `CLAUDE.md` 不做任何修改，直接使用 upstream 版本。
- Fork 的 persona XML 内容迁移到 `SOUL.md`（如果尚未迁移）。
- 可选：在 `docs/superpowers/` 下保留 `FORK_GUIDE.md` 作为开发者参考。

### 3.2 Bootstrap 文件顺序调整

**修改点**：`ContextBuilder.BOOTSTRAP_FILES`

**从**：
```python
BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md"]
```

**到**：
```python
BOOTSTRAP_FILES = ["SOUL.md", "USER.md", "AGENTS.md", "TOOLS.md"]
```

**影响分析**：
- `_load_bootstrap_files()` 按 `BOOTSTRAP_FILES` 顺序遍历，顺序改变即加载顺序改变。
- 该修改对现有测试 `test_bootstrap_files_are_backed_by_templates` 没有影响（它只检查每个文件是否存在于模板目录，不检查顺序）。
- 对 `test_system_prompt_stays_stable_when_clock_changes` 无影响（bootstrap 顺序是稳定的，不依赖时间）。
- 对 `test_bootstrap_order_respected` 有影响——该测试当前断言 `AGENTS.md` 在 `SOUL.md` 之前。需要更新该测试以反映新顺序。

### 3.3 Soul Anchor：最终状态

**最终状态（写死，不重演振荡历史）**：

- **主 agent**：系统提示末尾有 `# Remember` 块，内容是 `SOUL.md`。
- **子 agent**：系统提示中**没有** soul anchor。子 agent 通过 bootstrap 注入获得 `SOUL.md` 内容一次，足够。
- **实验性声明**：soul anchor 是基于 fork 生产经验的启发式做法，缺乏 A/B 测试验证其有效性。如果 upstream 未来提供 prompt importance/pinning 机制（如原生支持标记某些 prompt 片段为 high-priority），soul anchor 应迁移到该机制，而不是继续用 `# Remember` hack。

**历史振荡（仅供理解，spec 不实现中间态）**：
1. `5369da7e` — 主 agent 添加 soul anchor。
2. `55de6e88` — 将 bootstrap（含 SOUL.md）注入子 agent。
3. `0b2565a8` — 从子 agent 移除 soul_anchor（bootstrap 已提供 SOUL.md）。
4. `ca66cb44` — 又向子 agent 添加 soul_anchor。
5. `74681de2` — **永久删除**子 agent 的 soul_anchor。

**实现**：

在 `ContextBuilder` 中新增方法 `_load_soul_anchor`：

```python
def _load_soul_anchor(self) -> str | None:
    """Read SOUL.md from workspace for tail injection."""
    soul_path = self.workspace / "SOUL.md"
    if soul_path.exists():
        content = soul_path.read_text(encoding="utf-8").strip()
        if content:
            return content
    return None
```

在 `build_system_prompt` 中，`session_summary` 块之后、`return` 之前插入：

```python
soul_anchor = self._load_soul_anchor()
if soul_anchor:
    parts.append(f"# Remember\n\n{soul_anchor}")
```

**位置说明**：放在 `session_summary` 之后，因为 session summary 是归档上下文，而 soul anchor 是人格提醒，应该作为系统提示的最后一个强信号。放在 `return "\n\n---\n\n".join(parts)` 之前，确保它成为连接后的最后一个部分。

### 3.4 Subagent Bootstrap 注入机制

**修改点**：`SubagentManager._build_subagent_prompt`

**实现**：

```python
def _build_subagent_prompt(self) -> str:
    time_ctx = ...  # existing
    skills_summary = ...  # existing

    # NEW: load bootstrap files for subagent via shared helper
    bootstrap = load_and_format_bootstrap(
        ["SOUL.md", "TOOLS.md"],
        self.workspace,
    )

    return render_template(
        "agent/subagent_system.md",
        time_ctx=time_ctx,
        workspace=str(self.workspace),
        skills_summary=skills_summary or "",
        bootstrap=bootstrap,  # NEW
    )
```

**模板修改**：`nanobot/templates/agent/subagent_system.md`

```jinja2
# Subagent

{{ time_ctx }}

You are a subagent spawned by the main agent to complete a specific task.
Stay focused on the assigned task. Your final response will be reported back to the main agent.

{% if bootstrap %}

{{ bootstrap }}
{% endif %}

{% include 'agent/_snippets/untrusted_content.md' %}

## Workspace
Your workspace is at:  {{ workspace }}
{% if skills_summary %}

## Skills

Read SKILL.md with read_file to use a skill.

{{ skills_summary }}
{% endif %}
```

**为什么 bootstrap 放在这里**：
- 在身份句之后：先确立 "you are a subagent"，再注入行为指南。
- 在 untrusted_content 之前：SOUL.md 包含的是可信指令（人格、行为规则），untrusted content 警告是关于运行时元数据的，行为规则应该先于警告出现，让模型先建立行为框架。

### 3.5 identity.md Discord Table Hint

**修改点**：`nanobot/templates/agent/identity.md` 的 Discord format hint。

**从**：
```
This conversation is on a messaging app. Use short paragraphs. Avoid large headings (#, ##). Use **bold** sparingly. No tables — use plain lists.
```

**到**：
```
This conversation is on a messaging app. Use short paragraphs. Avoid large headings (#, ##). Use **bold** sparingly.
**CRITICAL: Discord does NOT render Markdown tables.** Never use `|` column syntax. If you must show structured data, use a plain list or an ASCII table inside a fenced code block (```).
```

**注意**：只修改 Discord 分支（`channel == 'discord'` 的条件块），不触碰其他 channel 的 format hint。

---

## 4. Minimal Invasiveness Assessment

### 4.1 侵入性矩阵

| 文件 | 修改类型 | 侵入性 | 理由 |
|------|---------|--------|------|
| `CLAUDE.md` | 无修改 | 无 | 直接使用 upstream 版本，零侵入 |
| `ContextBuilder` | 常量重排 + 新方法 + 一行插入 | 低 | 不改动现有方法签名，只调整内部顺序 |
| `SubagentManager` | 方法内新增局部逻辑 + 模板变量 | 低 | 不改动类接口，_build_subagent_prompt 是内部方法 |
| `subagent_system.md` | 新增条件块 | 低 | Jinja2 条件渲染，不传变量时无输出差异 |
| `identity.md` | 单字符串替换 | 极低 | 仅 Discord 分支内一行变多行 |
| `test_context_builder.py` | 新增/更新测试 | 低 | 纯测试扩展 |
| `test_subagent.py` | 新增测试 | 低 | 纯测试扩展 |

### 4.2 不修改的代码

- `AgentLoop`：Pack8 不触碰 loop.py。Soul anchor 和 bootstrap 注入都由 `ContextBuilder` 和 `SubagentManager` 内部完成，loop 只需继续调用 `build_system_prompt()` 和 `_build_subagent_prompt()`，无感知。
- `AgentRunner`：不修改。
- `ToolRegistry` / `ToolLoader`：不修改。
- `MessageBus` / `Channel` 层：不修改。

---

## 5. Test Strategy

### 5.1 测试文件

- `tests/agent/test_context_builder.py`：已有文件，追加/修改测试。
- `tests/agent/test_subagent.py`：已有文件，追加测试。

### 5.2 新增/修改的测试用例

#### `tests/agent/test_context_builder.py`

**`test_bootstrap_order_respected`**（修改现有测试）：

```python
def test_bootstrap_order_respected(tmp_path):
    """Bootstrap files must appear in the declared order."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    for name in ContextBuilder.BOOTSTRAP_FILES:
        (workspace / name).write_text(name, encoding="utf-8")

    builder = ContextBuilder(workspace)
    prompt = builder.build_system_prompt()
    # SOUL.md should come before USER.md, which comes before AGENTS.md
    assert prompt.index("SOUL.md") < prompt.index("USER.md")
    assert prompt.index("USER.md") < prompt.index("AGENTS.md")
```

**`test_soul_anchor_injected_when_file_exists`**（新增）：

```python
def test_soul_anchor_injected_when_file_exists(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "SOUL.md").write_text("Be kind. Be sharp.", encoding="utf-8")

    builder = ContextBuilder(workspace)
    prompt = builder.build_system_prompt()
    assert "# Remember" in prompt
    assert "Be kind. Be sharp." in prompt
```

**`test_soul_anchor_omitted_when_file_missing`**（新增）：

```python
def test_soul_anchor_omitted_when_file_missing(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    builder = ContextBuilder(workspace)
    prompt = builder.build_system_prompt()
    assert "# Remember" not in prompt
```

**`TestIdentityTemplate.test_discord_table_hint_present`**（新增类）：

```python
class TestIdentityTemplate:
    def test_discord_table_hint_present(self):
        from nanobot.utils.prompt_templates import render_template
        result = render_template(
            "agent/identity.md",
            workspace_path="/tmp",
            runtime="test",
            platform_policy="",
            channel="discord",
        )
        assert "Discord does NOT render Markdown tables" in result
        assert "Never use `|` column syntax" in result
```

#### `tests/agent/test_subagent.py`

**`test_subagent_bootstrap_includes_soul_and_tools`**（新增）：

```python
@pytest.mark.asyncio
async def test_subagent_bootstrap_includes_soul_and_tools(tmp_path):
    """Subagent prompt should include SOUL.md and TOOLS.md as bootstrap."""
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
```

**`test_subagent_bootstrap_skips_missing_files`**（新增）：

```python
@pytest.mark.asyncio
async def test_subagent_bootstrap_skips_missing_files(tmp_path):
    """Subagent bootstrap should gracefully skip missing files."""
    (tmp_path / "SOUL.md").write_text("Soul only.", encoding="utf-8")
    # TOOLS.md is intentionally missing

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
    assert "## TOOLS.md" not in prompt
```

**`test_subagent_no_soul_anchor`**（新增）：

```python
@pytest.mark.asyncio
async def test_subagent_no_soul_anchor(tmp_path):
    """Subagent prompt must NOT contain a # Remember block."""
    (tmp_path / "SOUL.md").write_text("Soul content.", encoding="utf-8")

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
    assert "# Remember" not in prompt
```

### 5.3 回归测试策略

1. 运行 `tests/agent/test_context_builder.py` 全量——确认现有测试（尤其是缓存友好性测试）不因 soul anchor 的注入而破坏 prompt 稳定性。
2. 运行 `tests/agent/test_subagent.py` 全量——确认 bootstrap 注入不破坏子 agent 工具隔离测试。
3. 运行 `tests/agent/` 目录全量——确保没有跨文件回归。

**特别关注点**：`test_system_prompt_stays_stable_when_clock_changes` 检查的是 wall clock 变化不会导致 prompt 变化。soul anchor 读取的是静态文件内容，不受时间影响，因此不会破坏该测试。但如果 `SOUL.md` 不存在，该测试所在的 workspace 也没有 `SOUL.md`，所以 `# Remember` 不会出现——安全。

---

## 6. Forward Compatibility

### 6.1 依赖的 upstream 实现细节

| 决策点 | 依赖的 upstream 版本细节 | 兼容性风险 |
|--------|------------------------|-----------|
| `_load_bootstrap_files()` 的存在 | `context.py` 中已有该方法 | 低——该方法稳定存在多个版本 |
| `_build_subagent_prompt()` 的内部结构 | `subagent.py` 中已有该方法 | 低——子 agent prompt 构建逻辑稳定 |
| `render_template` 的 kwarg 传递 | `utils.prompt_templates.render_template` | 低——模板变量通过 kwargs 传递是标准做法 |
| `subagent_system.md` 的 Jinja2 语法 | 当前模板已使用 `{% if %}` 和 `{% include %}` | 极低——新增 `{% if bootstrap %}` 是相同语法 |

### 6.2 nanobot 3.0/4.0 replay 可行性

- **Bootstrap 顺序**：常量列表的修改是 trivial replay，没有版本锁定。
- **Soul anchor**：只要 `ContextBuilder` 的 `build_system_prompt` 结构保持 "parts list + join" 模式，`_load_soul_anchor` 的插入逻辑就可直接 replay。
- **Subagent bootstrap**：只要 `SubagentManager` 有内部 prompt 构建方法，且模板系统支持 kwarg 变量传递，就可 replay。
- **identity.md Discord hint**：纯模板内容修改，任何使用 Jinja2 模板系统的版本都可 replay。

### 6.3 版本标注

本 spec 假设以下 upstream 实现细节在目标版本（upstream/main `ba38f908`）中成立：

1. `ContextBuilder` 使用 `parts: list[str]` 收集系统提示各部分，最后以 `"\n\n---\n\n".join(parts)` 连接。
2. `ContextBuilder._load_bootstrap_files()` 按 `BOOTSTRAP_FILES` 顺序遍历工作区文件。
3. `SubagentManager._build_subagent_prompt()` 调用 `render_template("agent/subagent_system.md", ...)` 并返回字符串。
4. `render_template` 支持通过 kwargs 向 Jinja2 模板传递任意变量。
5. `identity.md` 使用 `{% if channel == 'discord' %}` 条件渲染 Discord format hint。

若未来 upstream 重构了 `build_system_prompt` 的结构（例如不再使用 parts list 模式），soul anchor 的注入点需要重新定位。

---

## 7. Implementation Order

### Phase 1 — 测试先行（TDD）

1. **修改现有测试**：更新 `test_bootstrap_order_respected` 以期望新的 bootstrap 顺序。
2. **新增 ContextBuilder 测试**：`test_soul_anchor_injected_when_file_exists`、`test_soul_anchor_omitted_when_file_missing`。
3. **新增 identity 测试**：`TestIdentityTemplate.test_discord_table_hint_present`。
4. **新增 SubagentManager 测试**：`test_subagent_bootstrap_includes_soul_and_tools`、`test_subagent_bootstrap_skips_missing_files`、`test_subagent_no_soul_anchor`。
5. **运行测试确认 RED**。

### Phase 2 — 核心实现

6. **修改 `ContextBuilder`**：
   - 重排 `BOOTSTRAP_FILES`。
   - 新增 `_load_soul_anchor()` 方法。
   - 在 `build_system_prompt()` 末尾注入 soul anchor。
7. **修改 `SubagentManager`**：在 `_build_subagent_prompt()` 中加载 `SOUL.md` + `TOOLS.md` 并传入 `bootstrap`。
8. **修改 `subagent_system.md`**：在身份句之后、untrusted_content 之前插入 `{% if bootstrap %}` 块。
9. **修改 `identity.md`**：在 Discord format hint 中追加 table 警告。
10. **运行测试确认 GREEN**。

### Phase 3 — 文档留存与确认

11. **确认 CLAUDE.md 不合并**：直接使用 upstream 版本，不追加 fork 内容。fork 的 persona 内容确保已在 `SOUL.md` 中完整表达。
12. **复制 fork 本地文档**：将 6 个 plan 文件和 1 个 spec 文件从 `origin/main` 复制到当前工作树。
13. **验证 `.gitignore`**：确认 `docs/superpowers/` 已在 ignore 列表中，无需变更。

### Phase 4 — 回归验证

14. 运行 `tests/agent/test_context_builder.py` 全量。
15. 运行 `tests/agent/test_subagent.py` 全量。
16. 运行 `tests/agent/` 目录全量（`--timeout=60`）。
17. 执行 §9 Manual Smoke Checks（5 项）。

---

## 8. Manual Smoke Checks

实施完成后执行以下手工验证：

### 8.1 Bootstrap order smoke
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
    assert result.index("SOUL.md") < result.index("USER.md")
    assert result.index("USER.md") < result.index("AGENTS.md")
```

### 8.2 Soul anchor smoke
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

### 8.3 Subagent bootstrap smoke
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
    assert "# Remember" not in prompt
```

### 8.4 Identity template smoke
```python
from nanobot.utils.prompt_templates import render_template
out = render_template("agent/identity.md", workspace_path="/tmp", runtime="test", platform_policy="", channel="discord")
assert "Discord does NOT render Markdown tables" in out
```

### 8.5 CLAUDE.md smoke
```bash
# CLAUDE.md should be exactly the upstream version — no fork additions
git diff upstream/main -- CLAUDE.md | wc -l | grep -q "^0$"
# Verify SOUL.md contains the fork persona content (migrated from old CLAUDE.md)
grep -q "SOUL.md" SOUL.md 2>/dev/null || echo "WARN: SOUL.md may need persona migration"
```

---

## 9. Rollback Plan

若 Pack8 引入 prompt 结构或 agent 行为回归：

1. `git checkout upstream/main -- CLAUDE.md`
2. `git checkout upstream/main -- nanobot/agent/context.py`
3. `git checkout upstream/main -- nanobot/agent/subagent.py`
4. `git checkout upstream/main -- nanobot/templates/agent/identity.md`
5. `git checkout upstream/main -- nanobot/templates/agent/subagent_system.md`
6. `git checkout upstream/main -- tests/agent/test_context_builder.py`
7. `git checkout upstream/main -- tests/agent/test_subagent.py`
8. `rm -f docs/superpowers/plans/2026-03-31-*.md docs/superpowers/plans/2026-04-0*.md docs/superpowers/plans/2026-04-03-*.md docs/superpowers/specs/2026-04-01-workspace-layout-refactor-design.md`
9. 重新运行 `tests/agent/` 确认基线恢复。

---

## 10. Completion Criteria

Pack8 完成当且仅当以下全部成立：

1. `CLAUDE.md` 保持 upstream 原样，未追加 fork 内容；fork 哲学/架构指南在 `SOUL.md` 中表达，开发者参考在独立文件中。
2. `ContextBuilder.BOOTSTRAP_FILES == ["SOUL.md", "USER.md", "AGENTS.md", "TOOLS.md"]`。
3. `ContextBuilder.build_system_prompt` 在文件存在时追加 `# Remember` 块。
4. `SubagentManager._build_subagent_prompt` 传递 `SOUL.md` + `TOOLS.md` 作为 `bootstrap`。
5. `subagent_system.md` 在正确位置渲染 `bootstrap` 变量。
6. `identity.md` 的 Discord 分支包含 table hint。
7. 所有新增/修改测试通过。
8. `tests/agent/` 全量回归通过。
9. Fork 本地文档已复制到 `docs/superpowers/`。
10. `.gitignore` 未做不必要的变更。
11. **未向 `sync-upstream-2026-05-replay` 分支提交任何 commit。**
