# Spec6 — Memory Consolidation / Context Pruning Upstream Replay

> 历史归档，非当前实现。基座为 ba38f908（2026-05-18），与 upstream/main=3f808d0a 之后的结构不再对应。

> 对应 Plan: `docs/superpowers/plans/2026-05-18-pack6-memory-consolidation-pruning.md`
> 目标代码基: `sync-upstream-2026-05-replay` (HEAD `6845ba801073db5dfa9a0c9530d8e583d5d69cd0`)
> 禁止操作生产目录 `/root/git_code/nanobot`（除 `git show origin/main:<path>` 查看 fork 版本外）。

---

## 1. 概述

Pack6 的目标是在 nanobot upstream 代码基上，无侵入地 replay 以下能力：

1. **Context Pruning（瞬态上下文裁剪）**：在每次 LLM 请求前，对过长的 tool result 内容进行软裁剪（soft trim）或硬清空（hard clear），以降低 prompt token 占用。
2. **Consolidation Ratio（可配置化记忆压缩比例）**：确认 upstream 已实现的 `consolidation_ratio` 参数链路完整，并补全回归测试。

**非目标**：
- 不改动 provider 层、ContextBuilder、session JSONL 模型、llm_logs、Dream 长期记忆文件格式。
- 不引入 fork 的 `MemoryConsolidator` 旧 API 兼容层。
- 不修改子 agent trace logging、命令重写、搜索/workspace tool 行为。

---

## 2. 行为需求

### 2.1 Context Pruning

| 编号 | 需求 |
|------|------|
| CP-01 | 默认关闭（`enabled=False`），未配置时不改变任何 upstream 行为。 |
| CP-02 | 仅处理 `role == "tool"` 的消息，且 `content` 为 `str` 类型。 |
| CP-03 | 不删除消息，不改动 role，不删除 `tool_call_id`，不破坏 tool call / result 配对。 |
| CP-04 | 保护最近 `keep_last_assistants` 条 assistant 消息及其之后的所有 tool result。若消息总数中 assistant 数量不足，则全部保护（不裁剪）。 |
| CP-05 | 跳过包含图片块（`type in ("image_url", "image")`）的多模态 content。 |
| CP-06 | `hard_clear` 优先级高于 `soft_trim`。当 `enabled=True` 且 `len(content) / context_window_chars > ratio` 时，将 content 替换为 `[{len(content)} characters cleared]`。 |
| CP-07 | `soft_trim` 在 `hard_clear` 未触发时生效：当 `len(content) > chunk_size` 时，将 content 缩减至 `target_size`，策略为优先删除中间行，保留首尾。 |
| CP-08 | 输入消息列表不可变：返回全新的消息列表，原始列表及其中字典对象不被修改。 |
| CP-09 | 若 tool content 不是 `str`/`list`/`dict`，抛出 `ValueError`，避免静默吞掉异常结构。 |

### 2.2 Consolidation Ratio

| 编号 | 需求 |
|------|------|
| CR-01 | `AgentDefaults.consolidation_ratio` 默认 `0.5`，范围 `0.1..0.95`，别名 `consolidationRatio`。 |
| CR-02 | `AgentLoop` → `Consolidator` 链路正确透传该值。 |
| CR-03 | `Consolidator.maybe_consolidate_by_tokens()` 使用 `target = int(budget * consolidation_ratio)` 作为停止条件。 |

---

## 3. 架构分析

### 3.1 当前数据流（upstream 现状）

```
Config (AgentDefaults)
  ├─ consolidation_ratio ──────┐
  │                            ▼
  │                    AgentLoop.__init__()
  │                            │
  │                            ▼
  │                    Consolidator.__init__(consolidation_ratio=...)
  │                            │
  │                            ▼
  │                    maybe_consolidate_by_tokens()
  │                        target = budget * ratio
  │
  ├─ (无 context_pruning 相关字段)

AgentRunner.run() pre-provider path（每轮迭代）:
  messages_for_model = list(spec.initial_messages)
  → _drop_orphan_tool_results()
  → _backfill_missing_tool_results()
  → _microcompact()          # 替换旧 tool result 为单行摘要
  → _apply_tool_result_budget()  # 按 max_tool_result_chars 截断单条 tool result
  → _snip_history()          # token 预算超限后截断历史尾部
  → _drop_orphan_tool_results()
  → _backfill_missing_tool_results()
  → _request_model()
```

### 3.2 关键类现状

| 文件 | 类/函数 | 现状 |
|------|---------|------|
| `nanobot/config/schema.py` | `AgentDefaults` | 已有 `consolidation_ratio`（第151行）。**无** `context_pruning`。 |
| `nanobot/agent/memory.py` | `Consolidator` | 已完整实现 `consolidation_ratio` 使用（第469、694行）。 |
| `nanobot/agent/loop.py` | `AgentLoop` | 构造函数接收 `consolidation_ratio`（第182行），`from_config()` 从 `defaults.consolidation_ratio` 读取（第365行）。**未接收/传递 `context_pruning`**。 |
| `nanobot/agent/runner.py` | `AgentRunSpec` | **无** `context_pruning` 字段。pre-provider path 已包含 `_microcompact`、`_apply_tool_result_budget`、`_snip_history` 等治理步骤。 |
| `nanobot/agent/runner.py` | `AgentRunner.run()` | 第262–275行构建 `messages_for_model` 副本并依次执行治理。 |
| `nanobot/agent/pruner.py` | `ContextPruner` | **文件不存在**。 |
| `tests/agent/test_context_pruner.py` | — | **不存在**。 |

### 3.3 测试基线

| 测试文件 | 状态 | 作用 |
|----------|------|------|
| `tests/agent/test_consolidator.py` | 存在 | Consolidator 归档、边界、错误处理测试。 |
| `tests/agent/test_loop_consolidation_tokens.py` | 存在 | `maybe_consolidate_by_tokens` 端到端行为。 |
| `tests/agent/test_consolidation_ratio.py` | 存在 | `consolidation_ratio` 参数化回归测试。 |
| `tests/agent/test_auto_compact.py` | 存在 | AutoCompact TTL 与 idle session 归档集成。 |
| `tests/agent/test_autocompact_unit.py` | 存在 | AutoCompact 类方法单元测试。 |
| `tests/agent/test_runner_governance.py` | 存在 | AgentRunner orphan cleanup / backfill / snip 测试。 |
| `tests/agent/test_context_builder.py` | 存在 | ContextBuilder 行为测试。 |
| `tests/agent/test_context_prompt_cache.py` | 存在 | prompt cache 命中策略测试。 |
| `tests/agent/test_context_pruner.py` | **不存在** | 需新建。 |

---

## 4. 技术方案

### 4.1 ContextPruningConfig Schema 设计

在 `nanobot/config/schema.py` 中新增三个配置类，并挂入 `AgentDefaults`。

```python
class SoftTrimConfig(Base):
    enabled: bool = False
    chunk_size: int = Field(default=8_000, ge=0)
    target_size: int = Field(default=4_000, ge=0)

class HardClearConfig(Base):
    enabled: bool = False
    ratio: float = Field(default=0.8, ge=0.0, le=1.0)

class ContextPruningConfig(Base):
    enabled: bool = False
    keep_last_assistants: int = Field(default=3, ge=0)
    min_prunable_tool_chars: int = Field(default=50_000, ge=0)
    context_budget_multiplier: float = Field(default=4.0, ge=0.0)
    soft_trim: SoftTrimConfig = Field(default_factory=SoftTrimConfig)
    hard_clear: HardClearConfig = Field(default_factory=HardClearConfig)
```

在 `AgentDefaults` 中追加：

```python
context_pruning: ContextPruningConfig = Field(default_factory=ContextPruningConfig)
```

**设计理由**：
- 复用已有的 `Base`（`alias_generator=to_camel`, `populate_by_name=True`），自动获得 camelCase 序列化/反序列化能力：`contextPruning`, `keepLastAssistants`, `minPrunableToolChars`, `softTrim`, `hardClear`。
- 默认值 `enabled=False` 保证向前兼容：未配置的生产环境零行为差异。
- 校验规则：`chunk_size/target_size` 非负、`hard_clear.ratio` 在 `[0,1]`。不添加跨字段 model validator 除非测试证明需要（如 `target_size > chunk_size` 虽不合理，但 upstream 宽容策略是不阻塞启动）。

### 4.2 ContextPruner 裁剪策略和不变量

新建 `nanobot/agent/pruner.py`，实现纯函数式裁剪器。

```python
from typing import Any

from nanobot.config.schema import ContextPruningConfig

class ContextPruner:
    def __init__(self, config: ContextPruningConfig) -> None:
        self.config = config

    def prune(
        self,
        messages: list[dict[str, Any]],
        *,
        context_window_chars: int,
    ) -> list[dict[str, Any]]:
        ...
```

**核心算法步骤**（伪代码）：

```
1. if not config.enabled: return messages
2. 计算总可裁剪 tool chars（仅 role==tool 且 content 为 str 的消息）。
   if total < min_prunable_tool_chars: return messages
3. 确定保护边界：
   - 从末尾向前扫描，找到第 keep_last_assistants 条 assistant 消息。
   - 若 assistant 总数 < keep_last_assistants：return messages（全保护）。
   - 边界索引 = 该 assistant 消息的索引（含）之后所有消息受保护。
4. 策略查找表（policy 与执行分离）：
   ```python
   _PRUNE_POLICY = {
       ("tool", "str"): "ELIGIBLE",   # 进入 trim/clear 判断
       ("tool", "list"): "KEEP",       # image blocks etc
       ("tool", "other"): "KEEP",
       ("*", "*"): "KEEP",             # non-tool messages
   }
   ```
5. 遍历消息，构建新列表：
   - 若消息索引 >= 边界索引：原样拷贝。
   - 查表决定策略：根据 `(role, content_type)` 匹配 `_PRUNE_POLICY`。
   - `"KEEP"`：原样拷贝（但非 str/list/dict 的异常类型，抛 ValueError）。
   - `"ELIGIBLE"`：进入裁剪逻辑：
       a. hard_clear 优先：若 enabled 且 len(content)/context_window_chars > ratio：
          content = f"[{len(content)} characters cleared]"
       b. 否则 soft_trim：若 enabled 且 len(content) > chunk_size：
          content = _soft_trim(content, target_size)
   - 用 shallow copy 替换 content，其余字段不变。
6. return 新列表
```

**不变量（Invariants）**：

| 不变量 | 保证方式 |
|--------|----------|
| 不删除消息 | 输出列表长度 = 输入列表长度 |
| 不改 role | 仅替换 `content` 字段 |
| 不删 tool_call_id | 不触碰 `tool_call_id` 键 |
| 不破坏 tool call/result 配对 | ContextPruner.prune() 独立契约：输出消息列表长度 == 输入长度，每条消息的 role 和 tool_call_id 不变，只修改 string content 值 |
| 不删用户意图 | 非 tool 消息原样保留；短对话（assistant 不足）全部保护 |
| 不破坏 role alternation | ContextPruner.prune() 独立契约：输出消息列表长度 == 输入长度，每条消息的 role 不变，不插入/删除消息 |
| 输入不可变 | 使用 `dict(msg)` shallow copy，不修改原始字典 |

**soft trim 实现细节**：
- 优先按行分割。若行数过多，删除中间行，保留首尾。
- 若单行超长或无自然换行，按字符删除中间段。
- 最终长度不超过 `target_size`。

### 4.3 集成点：Runner Pre-Provider Path

**修改 1：AgentRunSpec 新增字段**

在 `nanobot/agent/runner.py` 的 `AgentRunSpec` dataclass 中追加：

```python
context_pruning: ContextPruningConfig | None = None
```

**修改 2：AgentLoop 透传配置**

- `context_pruning` 作为直接参数传给 `AgentLoop.__init__`，与 `consolidation_ratio` 同一模式：
  ```python
  def __init__(self, ..., context_pruning: ContextPruningConfig | None = None):
  ```
- `AgentLoop.from_config()` 从 `config.agents.defaults.context_pruning` 读取并传入。
- `AgentLoop` 在每次构建 `AgentRunSpec` 时将 `context_pruning` 传入（同 `max_tool_result_chars` 等字段的传递方式）。

**修改 3：AgentRunner.run() 插入裁剪步骤**

在 `AgentRunner.run()` 第262–275行的 pre-provider path 中，**在 `_drop_orphan_tool_results` 之前**插入：

```python
# Prune transient tool result bloat before token-based governance.
if spec.context_pruning is not None and spec.context_pruning.enabled:
    pruner = ContextPruner(spec.context_pruning)
    messages_for_model = pruner.prune(
        messages_for_model,
        context_window_chars=spec.max_tool_result_chars * spec.context_pruning.context_budget_multiplier,
    )
```

**为什么是 `max_tool_result_chars * 4`？**
- 这是一个经验性的 rough char budget。upstream 没有全局 `context_window_chars` 概念，而 `_apply_tool_result_budget` 使用 `max_tool_result_chars` 作为单条上限。乘以 4 近似于“当单条 tool result 已接近单条上限的 4 倍时视为相对于窗口过大”。该常数在测试中用 monkeypatch 覆盖，不引入 flakiness。

**插入位置说明**：
- 必须在 `_drop_orphan_tool_results` 之前：pruner 不删除消息，不会引入 orphan；放在前面可让后续 snip 在已裁剪的内容上进一步 token 治理。
- 必须在 `_microcompact` 之前：microcompact 会将旧 tool result 替换为单行摘要；若先 prune，可减少需要被 microcompact 处理的数据量。
- fallback 路径（第282行 `except Exception` 后的 minimal repair）**不重复执行 prune**，因为 fallback 的目标是“最小修复”，若 prune 本身抛异常，说明配置或数据严重异常，应直接跳过。

### 4.4 consolidation_ratio 数据流

> **Upstream 已有**：`consolidation_ratio` 已在 upstream 全链路实现（schema → AgentLoop → Consolidator → `maybe_consolidate_by_tokens()`），无需任何代码修改。仅需运行已有回归测试确认行为正确。从实现计划中删除 consolidation_ratio 相关实现步骤，仅保留验证步骤。

upstream 已实现完整链路，Pack6 只需**确认**并**补测试**。

```
AgentDefaults.consolidation_ratio (default=0.5, alias=consolidationRatio)
  ↓
AgentLoop.__init__(consolidation_ratio=...)
  ↓
AgentLoop.from_config(defaults.consolidation_ratio)
  ↓
Consolidator.__init__(consolidation_ratio=...)
  ↓
Consolidator.maybe_consolidate_by_tokens()
    budget = context_window_tokens - max_completion_tokens - SAFETY_BUFFER
    target = int(budget * self.consolidation_ratio)
```

**无需代码修改**，除非测试发现回归。

### 4.5 Upstream 已有能力 vs 需补缺的 Gap

| 能力 | 已有 / 缺失 | 说明 |
|------|-------------|------|
| `AgentDefaults.consolidation_ratio` 配置 | **已有** | schema.py 第151行，含别名。 |
| `Consolidator` 使用 ratio 计算 target | **已有** | memory.py 第694行。 |
| `AgentLoop` / `from_config` 透传 ratio | **已有** | loop.py 第182、365行。 |
| `ContextPruningConfig` schema | **缺失** | 需新增。 |
| `ContextPruner` 裁剪实现 | **缺失** | 需新建 `pruner.py`。 |
| Runner pre-provider path 集成 pruning | **缺失** | 需在 `AgentRunSpec` + `AgentRunner.run()` 中插入。 |
| `AgentLoop` 透传 `context_pruning` | **缺失** | 需修改 `__init__` 和 `from_config`。 |
| `test_context_pruner.py` | **缺失** | 需新建。 |
| `test_consolidation_ratio.py` | **已有** | 已覆盖 ratio 参数化回归，但需随 Pack6 一起运行确认通过。 |

---

## 5. 最小侵入评估

| 侵入点 | 侵入程度 | 理由 |
|--------|----------|------|
| `schema.py` | 低 | 仅追加新类和新字段，不改动已有字段。 |
| `runner.py` | 低 | 仅在 `AgentRunSpec` 追加一个可选字段；在 `run()` 中插入一段 `if enabled` 的独立调用块。不改动 `_snip_history`、`_microcompact` 等现有方法。 |
| `loop.py` | 低 | 仅新增参数透传，类似 `consolidation_ratio` 的传递模式。 |
| `pruner.py` | 零侵入 | 纯新增文件，无外部依赖。 |
| provider / ContextBuilder / session | 零 | 明确不改动。 |

**设计原则体现**：
- **不魔改 upstream 架构**：pruner 是独立类，runner 通过已有的 dataclass 扩展点传入配置。
- **不照抄 fork**：fork 的 `MemoryConsolidator` 旧 API 不引入；upstream 的 `Consolidator` 已足够。
- **优雅实现**：利用 `Base` 的 camelCase alias generator，避免手写别名代码。

---

## 6. 测试方案

### 6.1 测试原则

- **确定性**：token counting 测试必须 monkeypatch `estimate_session_prompt_tokens()` 和 `estimate_message_tokens()`，禁止依赖真实 `tiktoken`。
- **隔离性**：每个测试只测一个决策点（enabled gate、boundary、hard clear、soft trim、multimodal skip、immutability）。
- **不 flaky**：不使用时间、随机数、外部网络。

### 6.2 测试文件清单

| 文件 | 内容 | 状态 |
|------|------|------|
| `tests/config/test_context_pruning_config.py` | Schema 测试：默认值、camelCase 序列化、边界校验（负值、超范围 ratio）、snake_case 输入兼容。 | 新建 |
| `tests/agent/test_context_pruner.py` | Pruner 单元测试：disabled 直通、assistant 不足保护、hard clear 触发/未触发、soft trim 行/字符策略、multimodal skip、异常 content 抛错、输入不可变性。 | 新建 |
| `tests/agent/test_runner_governance.py` | Runner 集成测试：在已有 governance 测试中追加 `context_pruning` 传入后，pruner 生效且 orphan/backfill 后续仍正确的断言。 | 追加 |
| `tests/agent/test_consolidation_ratio.py` | 已有回归测试：运行并确认通过，验证 ratio 对 archive 轮次的影响。 | 已有，需回归 |
| `tests/agent/test_consolidator.py` | 已有回归测试：确认 `consolidation_ratio` 未破坏归档行为。 | 已有，需回归 |
| `tests/agent/test_loop_consolidation_tokens.py` | 已有回归测试：确认 token 触发的 consolidation 行为正常。 | 已有，需回归 |
| `tests/agent/test_auto_compact.py` | 已有回归测试：确认 AutoCompact 未受 pruning 默认关闭影响。 | 已有，需回归 |
| `tests/agent/test_autocompact_unit.py` | 已有回归测试：同上。 | 已有，需回归 |

### 6.3 关键测试用例示例

**test_context_pruner.py — 保护边界**

```python
def test_pruner_protects_last_n_assistants():
    config = ContextPruningConfig(enabled=True, keep_last_assistants=2)
    pruner = ContextPruner(config)
    messages = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "tool", "content": "x" * 100_000, "tool_call_id": "1"},
        {"role": "assistant", "content": "a2"},
        {"role": "tool", "content": "y" * 100_000, "tool_call_id": "2"},
        {"role": "assistant", "content": "a3"},
        {"role": "tool", "content": "z" * 100_000, "tool_call_id": "3"},
    ]
    result = pruner.prune(messages, context_window_chars=10_000)
    # a2 是倒数第 2 条 assistant，因此 a2 及之后（含 tool 2, a3, tool 3）受保护。
    assert result[2]["content"] != messages[2]["content"]   # tool 1 被裁剪
    assert result[4]["content"] == messages[4]["content"]   # tool 2 受保护
    assert result[6]["content"] == messages[6]["content"]   # tool 3 受保护
```

**test_context_pruner.py — 输入不可变**

```python
def test_pruner_does_not_mutate_original_messages():
    config = ContextPruningConfig(enabled=True, keep_last_assistants=0)
    pruner = ContextPruner(config)
    original = [{"role": "tool", "content": "long text", "tool_call_id": "t1"}]
    result = pruner.prune(original, context_window_chars=1)
    assert original[0]["content"] == "long text"
    assert result is not original
    assert result[0] is not original[0]
```

**test_runner_governance.py — 集成断言**

```python
async def test_runner_applies_pruning_before_governance(monkeypatch):
    # 构造 AgentRunSpec 时传入 enabled 的 ContextPruningConfig
    # 调用 runner.run()，捕获最终传给 provider 的 messages
    # 断言 tool result 已被 prune，且后续 _drop_orphan_tool_results 未破坏配对
```

---

## 7. 向前兼容性

| 决策 | 兼容性影响 |
|------|------------|
| `ContextPruningConfig.enabled` 默认 `False` | 未配置用户零行为差异。 |
| `AgentRunSpec.context_pruning` 为 `Optional` | 旧代码直接构造 `AgentRunSpec` 时不传该字段仍可编译/运行。 |
| Schema 使用 `Base` 的 camelCase alias | 与上游其他配置字段序列化风格一致；YAML 中写 `contextPruning` 或 `context_pruning` 均可。 |
| `hard_clear.ratio` 范围 `0..1` | 使用 Pydantic `Field(ge=0.0, le=1.0)`，与 `consolidation_ratio` 的校验风格一致。 |

**版本依赖标注**：
- AgentRunSpec 是普通 class，不是 dataclass(slots=True)。追加字段直接写即可，没有 `__slots__` 限制。向前兼容风险降低：普通 class 的字段追加比 slots dataclass 更自由。
- 依赖 `AgentLoop.from_config()` 的签名模式（从 `defaults` 逐项读取）。若 upstream 未来重构为 `**defaults.model_dump()` 批量转发，本 spec 的透传逻辑可简化。

---

## 8. 实现顺序

**Phase 1 — Schema & 纯类（无上游集成）**
1. 在 `schema.py` 追加 `SoftTrimConfig`, `HardClearConfig`, `ContextPruningConfig`。
2. 在 `AgentDefaults` 追加 `context_pruning`。
3. 新建 `nanobot/agent/pruner.py` 实现 `ContextPruner`。
4. 新建 `tests/config/test_context_pruning_config.py` 跑通 schema 测试。
5. 新建 `tests/agent/test_context_pruner.py` 跑通 pruner 单元测试。

**Phase 2 — Runner 集成**
6. 修改 `AgentRunSpec` 追加 `context_pruning` 字段。
7. 修改 `AgentRunner.run()` 在 pre-provider path 中插入 prune 调用。
8. 在 `tests/agent/test_runner_governance.py` 追加集成用例，确认 prune → orphan → backfill 链路正确。

**Phase 3 — Loop 透传**
9. 修改 `AgentLoop.__init__` 接收并保存 `context_pruning`。
10. 修改 `AgentLoop.from_config()` 从 `defaults.context_pruning` 读取并传入。
11. 修改 AgentLoop 中构造 `AgentRunSpec` 的位置，将 `context_pruning` 传入。

**Phase 4 — Consolidation Ratio 已有测试验证**
12. 运行已有 `tests/agent/test_consolidation_ratio.py`、`test_consolidator.py`、`test_loop_consolidation_tokens.py`、`test_auto_compact.py`、`test_autocompact_unit.py`，验证 upstream 已有 consolidation_ratio 功能行为正确。
13. 若有失败，定位是否为 Pack6 集成导致（如 `AgentLoop` 新增参数影响了 mock 构造），修复调用方而非被调用方。

**Phase 5 — 编译检查与收尾**
14. 运行 `python3 -m compileall nanobot/agent nanobot/config nanobot/session`。
15. 对照 Plan 第11节 Completion Criteria 逐项确认。

---

## 9. 关键设计决策

1. **集成点选在 `AgentRunner.run()` 而非 provider / ContextBuilder**：
   - Runner 的 pre-provider path 已存在 `_microcompact`、`_apply_tool_result_budget`、`_snip_history` 等上下文治理步骤，pruning 属于同一语义层级（瞬态 prompt 瘦身）。
   - Provider 层不应关心消息内容长度策略；ContextBuilder 负责组装 system prompt 和历史，不负责对已有历史进行有损压缩。

2. **`hard_clear` 优先于 `soft_trim`**：
   - 当单条 tool result 占窗口比例极高时，软裁剪仍可能留下大量无意义中间内容（如巨大 stack trace）。硬清空直接替换为占位符，更符合“极端情况保窗口”的目标。

3. **`context_window_chars` 使用 `max_tool_result_chars * 4` 作为代理值**：
   - upstream 当前没有统一的“当前 prompt 字符数”统计。该 proxy 是一个经验常数，足以触发 hard_clear/soft_trim 的阈值判断。未来若 upstream 引入精确字符预算，可在调用点替换而无需改动 `ContextPruner` 内部。

4. **Assistant 不足时全部保护**：
   - 避免在极短对话（如首轮 user + assistant + tool）中误删用户正在等待的 tool result。这是一种保守策略，宁可多 token 也不破坏用户体验。

5. **不引入 fork 的 `MemoryConsolidator` 兼容层**：
   - upstream `Consolidator` 已覆盖 fork 中 `MemoryConsolidator` 的核心职责（归档到 history.jsonl）。若发现具体调用方仍引用旧 API，应修改调用方而非在 Consolidator 上加 shim。

---

## 10. 不确定点

| 编号 | 不确定点 | 建议处理 |
|------|----------|----------|
| U-01 | `context_window_chars` 的 `* 4` 常数是否在所有生产 config 下都合理？ | 在实现阶段通过 `test_runner_governance.py` 的集成测试用不同 `max_tool_result_chars` 验证；若发现误触发，可将该常数提取到 `ContextPruningConfig` 的 `context_window_chars_multiplier` 配置项（默认 4）。 |
| U-02 | 某些 provider 对 `tool` role 的 content 为 list/dict 时的要求是否与 upstream 一致？ | Plan 已要求跳过 list 中的 image block；若测试中发现特定 provider（如 Gemini）要求 tool content 必须为 str，则应在 `ContextPruner` 中收紧“非 str 即报错”的条件。当前按 Plan 保留 list/dict 透传。 |
| U-03 | `AgentLoop` 构造 `AgentRunSpec` 的代码位置可能因上游迭代发生漂移（如新增字段）。 | 实现时先 grep `AgentRunSpec(` 在 `loop.py` 中的出现位置，确保 `context_pruning=...` 传入到正确的构造点。 |

---

*本文档为纯技术 spec，不执行实现、不运行测试、不提交代码。*
