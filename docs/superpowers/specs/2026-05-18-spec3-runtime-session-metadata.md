# Spec3 — Runtime Identity and Session Metadata Replay

## 1. 概述

本 spec 将 fork 生产环境的 runtime/session metadata 行为回放到 upstream `main` 架构上，覆盖以下目标：

1. Discord runtime context 包含 `channel_name`。
2. Runtime session metadata 和每轮 assistant message 的 `model` 字段写入 JSONL session log。
3. 每个 assistant message 记录 `token_usage`、`elapsed_ms`、`llm_elapsed_ms`。
4. 每个 user message 记录 `sender_id` 和可选的 `sender_name`。
5. `/new` 清除对话历史但保留 runtime identity metadata。
6. Session identity 与日期完全解耦。

**范围边界**：本 spec 不涉及 provider routing、Discord transcription/TTS、command rewrite、subagent trace logs、memory pruning、workspace layout、bootstrap/SOUL 修改。只处理 runtime identity 和 session metadata 的数据流与持久化。

## 2. 行为需求

| # | 需求 | 优先级 |
|---|---|---|
| 2.1 | Discord 消息触发时，runtime context 的 `[Runtime Context]` 块包含 `Channel Name: <name>`（DM 时省略）。 | P0 |
| 2.2 | Discord 消息触发时，runtime context 包含 `Sender Name: <name>`（无法获取时省略）。 | P0 |
| 2.3 | 每个持久化的 user message 包含 `sender_id`；能获取到 sender name 时同时包含 `sender_name`。 | P0 |
| 2.4 | 每个持久化的 assistant message 包含 `model`（字符串，如 `gpt-4o`）。 | P0 |
| 2.5 | 每个持久化的 assistant message 包含 `usage`（JSON object，如 `{"prompt_tokens": 10, "completion_tokens": 2}`）。 | P0 |
| 2.6 | 每个持久化的 assistant message 包含 `latency_ms`（单轮 wall-clock 延迟，已存在于 upstream）。 | P0（复用） |
| 2.7 | 每个持久化的 assistant message 包含 `elapsed_ms`（AgentRunner 整个 run 的 wall-clock 时间）和 `llm_elapsed_ms`（仅 LLM provider 调用耗时之和）。 | P0 |
| 2.8 | `/new` 命令后：messages 被清空、`last_consolidated` 重置、`_last_summary` 被 pop，但 `session.metadata` 保留。 | P0 |
| 2.9 | `provider_name` 不出现在 runtime context、session metadata 或任何持久化 message 中。 | P0 |
| 2.10 | 所有 metadata 持久化通过 upstream 已有的 JSONL 机制完成，不引入新存储后端。 | P0 |

## 3. 架构分析：upstream 当前数据模型

### 3.1 Session / JSONL 持久化

`nanobot/session/manager.py` 中的 `SessionManager` 已经使用 JSONL 格式：

- 第一行：`{"_type": "metadata", "metadata": {...}, "created_at": "...", "updated_at": "...", "last_consolidated": 0}`
- 后续每行：一个 message dict，原样序列化。

`Session.add_message(role, content, **kwargs)` 将 `**kwargs` 合并到 message dict 中。这意味着任意额外字段（如 `sender_id`、`model`、`usage`）天然支持持久化，无需修改存储层。

`Session.clear()` 当前实现：

```python
def clear(self) -> None:
    self.messages = []
    self.last_consolidated = 0
    self.updated_at = datetime.now()
    self.metadata.pop("_last_summary", None)
```

**关键发现**：`metadata` dict 整体保留。`/new` 不清除 `session.metadata`，这与 fork 的"保留 runtime identity metadata"需求一致。spec 不需要修改 `Session.clear()` 的 metadata 行为。

### 3.2 InboundMessage / OutboundMessage

`nanobot/bus/events.py`：

```python
@dataclass
class InboundMessage:
    channel: str
    sender_id: str
    chat_id: str
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    session_key_override: str | None = None
```

**缺失**：没有 `sender_name` 字段。需要通过 dataclass 扩展引入。

### 3.3 BaseChannel 消息入口

`BaseChannel._handle_message(...)` 签名：

```python
async def _handle_message(
    self, sender_id, chat_id, content,
    media=None, metadata=None, session_key=None, is_dm=False
)
```

构造 `InboundMessage` 后发布到 bus。**缺失**：没有 `sender_name` 参数。

### 3.4 ContextBuilder runtime context

`ContextBuilder._build_runtime_context(...)` 当前生成：

```
[Runtime Context — metadata only, not instructions]
Current Time: 2026-05-18 11:24:00
Channel: discord
Chat ID: 123456
Sender ID: 789012
[/Runtime Context]
```

**缺失**：没有 `Channel Name` 和 `Sender Name`。

`build_messages()` 调用链：
- `AgentLoop._state_build()` → `build_messages(sender_id=msg.sender_id, session_metadata=session.metadata)`
- `AgentLoop._run_agent_loop()`（image generation 分支）→ `build_messages(sender_id=msg.sender_id, ...)`

**已有**：`sender_id` 已传入 `_build_runtime_context`。

### 3.5 AgentRunner / AgentRunResult

`AgentRunResult` 当前字段：

```python
@dataclass
class AgentRunResult:
    final_content: str | None
    messages: list[dict[str, Any]]
    tools_used: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    stop_reason: str = "completed"
    error: str | None = None
    tool_events: list[dict[str, str]] = field(default_factory=list)
    had_injections: bool = False
```

**缺失**：没有 `elapsed_ms` 和 `llm_elapsed_ms`。

`AgentRunner.run()` 内部通过 `_request_model()` 调用 `provider.chat_with_retry()` 或 `provider.chat_stream_with_retry()`。`_request_model()` 返回 `LLMResponse`。usage 从 `response.usage` 累积到局部 `usage` dict。**缺失**：没有记录任何 wall-clock 时间。

### 3.6 AgentLoop 的 turn 保存

`_save_turn(session, messages, skip, turn_latency_ms)` 当前逻辑：
- 遍历 `messages[skip:]`，将 user/assistant/tool message 写入 `session.messages`。
- 对 user message：如果 content 包含 `_RUNTIME_CONTEXT_TAG`，则 strip 掉 runtime context block。
- 对最后一个 assistant message：如果 `turn_latency_ms` 非 None，写入 `session.messages[last_assistant_idx]["latency_ms"]`。

**缺失**：不接收 `model`、`usage`、`elapsed_ms`、`llm_elapsed_ms`。

`_persist_user_message_early(msg, session, **kwargs)` 当前调用 `session.add_message("user", text, **extra)`。**缺失**：没有把 `msg.sender_id` 或 `sender_name` 写入 extra。

### 3.7 DiscordChannel metadata

`DiscordChannel._build_inbound_metadata()` 当前返回：

```python
{
    "message_id": str(message.id),
    "guild_id": str(message.guild.id) if message.guild else None,
    "reply_to": reply_to,
}
```

**缺失**：没有 `channel_name` 和 `sender_name`。

### 3.8 LLMResponse

`LLMResponse` 已有 `usage: dict[str, int]` 和 `latency_ms: float`（从 HTTP 响应头计算）。upstream provider 层已经能返回 usage。

## 4. 技术方案

### 4.1 数据模型设计：四层隔离

| 层级 | 内容 | 持久化位置 | 生命周期 |
|---|---|---|---|
| **Session-level metadata** | 跨 turn 的 identity（如 `channel_name`、`sender_id`、`sender_name` 的基准值） | JSONL header (`session.metadata`) | 与 session 同寿，`/new` 保留 |
| **User message metadata** | `sender_id`、`sender_name` | 每个 user message dict | 随 message 存入 JSONL |
| **Assistant message metadata** | `model`、`usage`、`latency_ms`、`elapsed_ms`、`llm_elapsed_ms` | 每个 assistant message dict | 随 message 存入 JSONL |
| **Runtime-only context** | `[Runtime Context]` prompt 块中的 `Channel Name`、`Sender Name`、`Current Time` | **不持久化**，仅拼接在 user content 末尾发给 LLM | 单 turn |

> **明确标注**：Session-level metadata 当前不做额外持久化写入，runtime 信息（channel_name 等）仅注入 prompt context，不写入 session.metadata。

**关键设计**：`channel_name` 和 `sender_name` 属于 runtime-only context（不持久化），但 `sender_id` 和可选的 `sender_name` 作为 user message metadata 持久化。这避免了在 prompt 中重复注入已持久化的信息，同时保留审计能力。

### 4.2 channel_name / sender_id / sender_name 的入口边界

#### 4.2.1 数据入口：DiscordChannel

在 `DiscordChannel._build_inbound_metadata()` 中增加：

- `channel_name`：从 `message.channel.name` 读取（DM 时省略）。
- `sender_name`：按以下优先级：
  1. `message.author.display_name`（truthy）
  2. `message.author.global_name`（truthy）
  3. `message.author.name`
  4. fallback 到 `sender_id`（仅当字符串必须时）

这些字段进入 `InboundMessage.metadata`，因为 `InboundMessage` dataclass 没有对应字段。

**替代方案**：给 `InboundMessage` 增加 `sender_name: str | None = None` 字段。此方案更干净，但会触发所有 channel 实现和测试的构造函数签名变更。由于 plan 中说明"如果造成大规模 positional-constructor churn，可接受仅使用 metadata"，本 spec 采用 **渐进方案**：

- **Phase 1**：先只通过 `metadata` 传递，不修改 `InboundMessage` dataclass。
- **Phase 2**（可选）：如果 executor 发现变更范围可控，再升级为显式字段。

本 spec 按 Phase 1 编写。

#### 4.2.2 进入 runtime context 的边界：ContextBuilder

修改 `ContextBuilder._build_runtime_context()` 签名：

```python
@staticmethod
def _build_runtime_context(
    channel: str | None,
    chat_id: str | None,
    timezone: str | None = None,
    sender_id: str | None = None,
    channel_name: str | None = None,
    sender_name: str | None = None,
    supplemental_lines: Sequence[str] | None = None,
) -> str:
```

内部追加：

```python
if channel_name:
    lines += [f"Channel Name: {channel_name}"]
if sender_name:
    lines += [f"Sender Name: {sender_name}"]
```

修改 `build_messages()` 签名，接收 `channel_name` 和 `sender_name`，向下传递。

修改 `AgentLoop._state_build()` 调用 `build_messages()` 时，从 `msg.metadata` 提取 `channel_name` 和 `sender_name` 传入。

#### 4.2.3 进入 user message 持久化的边界：AgentLoop

修改 `_persist_user_message_early()`：

```python
def _persist_user_message_early(self, msg: InboundMessage, session: Session, **kwargs) -> bool:
    ...
    extra = {"media": list(media_paths)} if media_paths else {}
    extra["sender_id"] = msg.sender_id
    if msg.metadata.get("sender_name"):
        extra["sender_name"] = msg.metadata["sender_name"]
    extra.update(kwargs)
    ...
```

### 4.3 token usage / elapsed_ms 从 LLMResponse 到 assistant message 的数据流

#### 4.3.1 AgentRunner 层

1. **扩展 `AgentRunResult`**：
   ```python
   elapsed_ms: int = 0
   llm_elapsed_ms: int = 0
   ```

2. **在 `_request_model()` 内部完成计时**（与 Spec5 LLM 日志统一设计）：
   `_request_model()` 是 provider I/O 的单一边界。不新增 `_timed_request_model()` 包装器，而是直接在 `_request_model()` 内部计时并返回 `(response, elapsed_ms)`：
   ```python
   async def _request_model(self, spec, messages, hook, context) -> tuple[LLMResponse, int]:
       start = time.monotonic()
       # ... existing provider call logic ...
       elapsed_ms = max(0, int((time.monotonic() - start) * 1000))
       # Spec5 LLM 日志也在此处插入（logger.info request/response preview）
       return response, elapsed_ms
   ```
   这样 Spec3 的计时和 Spec5 的日志共享同一个插桩点，不会重复计时或绕过日志。

3. **`_request_finalization_retry()` 计时策略**（与 Spec5 日志策略协调）：
   `_request_finalization_retry()` 直接调用 `provider.chat_with_retry()`，不走 `_request_model()`。
   - **计时**：在 `_request_finalization_retry()` 内部独立计时，其耗时纳入 `llm_elapsed_ms` 累加（保证计时完整性）。
   - **日志**：不产生完整 request/response preview 日志（避免与 `_request_model()` 日志混淆，这是 Spec5 的决策）。
   - 计时和日志是两件事，不因"不想多一行日志"而漏掉计时。

4. **修改 `run()`**：
   - 顶部记录 `run_started = time.monotonic()`。
   - 每次调用 `_request_model()` 取返回的 `elapsed_ms`，累加 `llm_elapsed_ms`。
   - `_request_finalization_retry()` 内部独立计时，其耗时也累加 `llm_elapsed_ms`。
   - 返回时：
     ```python
     elapsed_ms = max(0, int((time.monotonic() - run_started) * 1000))
     return AgentRunResult(
         ...,
         elapsed_ms=elapsed_ms,
         llm_elapsed_ms=llm_elapsed_ms,
     )
     ```

#### 4.3.2 AgentLoop 层

1. **扩展 `TurnContext`**：
   ```python
   turn_elapsed_ms: int = 0
   turn_llm_elapsed_ms: int = 0
   ```

2. **修改 `_run_agent_loop()`**：
   当前返回 `tuple[str | None, list[str], list[dict], str, bool]`。由于此 tuple 在 `_state_run()` 中解包，且 `_state_run()` 需要 usage/elapsed 信息传递给 `_save_turn()`，有两种方案：

   - **方案 A**：扩展 tuple 为 `(final_content, tools_used, messages, stop_reason, had_injections, usage, elapsed_ms, llm_elapsed_ms)`。
   - **方案 B**：把 `AgentRunResult` 整体存入 `TurnContext`。

   **推荐方案 B**（面向复用）：把 `AgentRunResult` 整体存入 `TurnContext.run_result`，`_state_run()` 从 `ctx.run_result.elapsed_ms` 等属性读取。理由：
   - Spec5 也会扩展 `AgentRunResult`（加 `model` 字段），继续扩展 tuple 会越来越脆弱，位置参数错一个就全歪。
   - 方案 B 的侵入度并不比 A 高——`_state_run()` 是唯一调用点，改一处解包逻辑即可。
   - 未来 upstream 若本身重构为返回 `AgentRunResult` 对象（向前兼容性表中已标注此可能），方案 B 天然对齐。

    3. **定义 `AssistantTurnMetrics` dataclass 并修改 `_save_turn()` 签名**：
       ```python
       @dataclass
       class AssistantTurnMetrics:
           model: str | None = None
           usage: dict[str, int] | None = None
           latency_ms: int | None = None
           elapsed_ms: int | None = None
           llm_elapsed_ms: int | None = None
       ```

       ```python
       def _save_turn(
           self,
           session: Session,
           messages: list[dict],
           skip: int,
           *,
           metrics: AssistantTurnMetrics | None = None,
       ) -> None:
       ```

       在写入最后一个 assistant message 后，追加：
       ```python
       if last_assistant_idx is not None:
           am = session.messages[last_assistant_idx]
           if metrics is not None:
               if metrics.model is not None:
                   am["model"] = metrics.model
               if metrics.usage:
                   am["usage"] = dict(metrics.usage)
               if metrics.latency_ms is not None:
                   am["latency_ms"] = int(metrics.latency_ms)
               if metrics.elapsed_ms is not None:
                   am["elapsed_ms"] = int(metrics.elapsed_ms)
               if metrics.llm_elapsed_ms is not None:
                   am["llm_elapsed_ms"] = int(metrics.llm_elapsed_ms)
       ```

    4. **`_state_save()` 调用 `_save_turn()` 时传入 `metrics`**：
       - 构造 `AssistantTurnMetrics`：
         ```python
         metrics = AssistantTurnMetrics(
             model=self.model,
             usage=self._last_usage,
             latency_ms=turn_latency_ms,
             elapsed_ms=ctx.turn_elapsed_ms,
             llm_elapsed_ms=ctx.turn_llm_elapsed_ms,
         )
         ```
       - 调用 `_save_turn(session, messages, skip, metrics=metrics)`。

### 4.4 provider_name 为什么不应该进入 runtime context

**设计决策记录**：

1. **语义分层**：provider 是路由与凭证的实现细节，不是对话上下文。告诉模型 provider 名称（如 `openai_compat`）对理解用户请求没有任何帮助。
2. **信息泄漏风险**：provider 名称可能泄漏部署拓扑（如内部网关名、私有 endpoint 标识），增加攻击面。
3. **稳定性假象**：Pack1 的 provider routing 允许同一 backend 对应多个 provider 名称。持久化 provider name 会在路由变更后产生 stale identity，误导运维审计。
4. **已有足够信息**：`model` 字段已经能回答"这轮用了什么模型"的问题。provider 级别的调试追踪应写入操作日志，而非 runtime context 或 session JSONL。

**验收标准**：runtime context 字符串、session metadata、user/assistant message metadata 中均不得出现 `provider` 或 `provider_name` 键。

### 4.5 /new 清除行为的契约

Upstream `Session.clear()` 当前行为：

```python
def clear(self) -> None:
    self.messages = []
    self.last_consolidated = 0
    self.updated_at = datetime.now()
    self.metadata.pop("_last_summary", None)
```

**本 spec 不修改 `Session.clear()`**。原因：
- `messages` 已清空（符合需求）。
- `last_consolidated` 已重置（符合需求）。
- `_last_summary` 已 pop（符合需求，避免旧对话 summary 污染新 session）。
- `metadata` dict **整体保留**（符合"保留 runtime identity metadata"需求）。

如果 fork 的旧代码在 `cmd_new` 中有额外清理逻辑（如清除 checkpoint keys），需在 `cmd_new` 中显式处理。Upstream 的 `cmd_new` 当前逻辑：

```python
session.clear()
loop.sessions.save(session)
loop.sessions.invalidate(session.key)
```

这已经足够。如果未来发现 `metadata` 中残留了不应保留的 turn-level 状态（如 `_runtime_checkpoint`），在 `cmd_new` 中显式 `pop` 即可，但当前上游的 checkpoint 机制由 `_restore_runtime_checkpoint()` 自动清理，不会累积。

### 4.6 Session identity 与日期解耦的策略

Upstream 已经解耦：
- `Session.key` 格式为 `{channel}:{chat_id}`（如 `discord:123456`）。
- `SessionManager` 使用 `safe_filename(key)` 将 key 映射为文件名，文件名不包含日期。
- `created_at` 和 `updated_at` 存储在 JSONL header 中，仅用于列表排序和显示，不参与 identity 计算。

**本 spec 不需要任何修改**。验收标准：确认 `SessionManager.save()` 和 `load()` 不基于日期做任何 session 路由或命名决策。

## 5. 最小侵入评估

| 修改点 | 侵入度 | 说明 |
|---|---|---|
| `ContextBuilder._build_runtime_context()` | 低 | 增加两个可选参数，内部追加两行文本。|
| `ContextBuilder.build_messages()` | 低 | 增加两个可选参数，透传。|
| `AgentRunResult` | 低 | 增加两个 int 字段，默认 0。不影响现有调用方。|
| `AgentRunner._request_model()` | 中 | 在内部增加计时（与 Spec5 日志共享同一插桩点），返回值从 `LLMResponse` 变为 `tuple[LLMResponse, int]`。所有调用点在 `run()` 内部，可控。|
| `AgentLoop._run_agent_loop()` | 中 | 改为返回 `AgentRunResult` 对象（方案 B）。所有调用点仅 `_state_run()` 一处，可控。|
| `TurnContext` | 低 | 增加两个 int 字段。|
| `AgentLoop._state_run()` | 低 | 将 `AgentRunResult` 存入 `TurnContext.run_result`，后续从 `ctx.run_result` 读取 timing 与 usage。|
| `AgentLoop._save_turn()` | 低 | 改为接收 `AssistantTurnMetrics | None`，在最后一个 assistant message 上写键。|
| `AgentLoop._state_save()` | 低 | 调用 `_save_turn()` 时传入新参数。|
| `AgentLoop._persist_user_message_early()` | 低 | 增加 `sender_id`/`sender_name` 写入。|
| `DiscordChannel._build_inbound_metadata()` | 低 | 增加两个字段。|
| `cmd_new` | 无 | 不修改。upstream 已满足需求。|
| `Session.clear()` | 无 | 不修改。upstream 已满足需求。|

**不需要修改的模块**：
- `SessionManager` 存储层：已通过 `add_message(**kwargs)` 支持任意字段。
- `LLMProvider` / provider 实现：`LLMResponse` 已有 `usage`。
- `InboundMessage` dataclass：Phase 1 通过 `metadata` 传递，避免构造函数签名变更。

## 6. 测试方案

### 6.1 原则

- **不依赖真实文件系统**：Session JSONL 持久化测试使用 `tmp_path` pytest fixture（已经是 upstream 的标准做法）。
- **不依赖真实 Discord 连接**：Discord 测试使用 `SimpleNamespace` fake object（与 upstream `tests/channels/test_discord_channel.py` 风格一致）。
- **不依赖真实 LLM**：Runner 测试使用 `MagicMock` provider。

### 6.2 测试矩阵

| 测试模块 | 测试名 | 覆盖点 |
|---|---|---|
| `tests/agent/test_loop_save_turn.py` | `test_save_turn_persists_model_usage_and_timing` | `_save_turn()` 写入 `model`、`usage`、`latency_ms`、`elapsed_ms`、`llm_elapsed_ms` 到最后一个 assistant message。 |
| `tests/agent/test_loop_save_turn.py` | `test_persist_user_message_includes_sender_id_and_name` | `_persist_user_message_early()` 把 `sender_id` 和可选 `sender_name` 写入 user message。 |
| `tests/agent/test_loop_save_turn.py` | `test_runtime_context_stripped_from_persisted_user_message` | 已有测试覆盖，保持不变。 |
| `tests/agent/test_runner_timing.py`（新建） | `test_run_returns_elapsed_ms_and_llm_elapsed_ms` | `AgentRunner.run()` 返回非负 `elapsed_ms` 和 `llm_elapsed_ms`。 |
| `tests/agent/test_runner_timing.py`（新建） | `test_request_model_returns_elapsed_ms` | Monkeypatch `time.monotonic` 确认 `_request_model()` 返回正确 elapsed_ms。 |
| `tests/agent/test_runner_timing.py`（新建） | `test_run_accumulates_llm_elapsed_ms` | Monkeypatch `time.monotonic` 确认多次 provider call 的累加正确（含 finalization retry 的独立计时）。 |
| `tests/channels/test_discord_channel.py` | `test_build_inbound_metadata_includes_channel_name_and_sender_name` | `_build_inbound_metadata()` 对 guild message 返回 `channel_name` 和 `sender_name`。 |
| `tests/channels/test_discord_channel.py` | `test_build_inbound_metadata_omits_channel_name_for_dm` | DM 消息 metadata 中无 `channel_name`。 |
| `tests/agent/test_context_builder.py`（新建或已有） | `test_runtime_context_includes_channel_name_and_sender_name` | `_build_runtime_context()` 输出包含 `Channel Name` 和 `Sender Name`。 |
| `tests/agent/test_context_builder.py` | `test_runtime_context_omits_sender_name_when_none` | `sender_name=None` 时输出中无 `Sender Name`。 |
| `tests/session/test_manager.py`（新建） | `test_session_clear_preserves_metadata` | `session.clear()` 后 `metadata` 保留。 |

### 6.3 JSONL 持久化测试策略

使用 `SessionManager` + `tmp_path`：

```python
def test_assistant_message_metadata_persisted_to_jsonl(tmp_path):
    mgr = SessionManager(workspace=tmp_path)
    session = mgr.get_or_create("test:1")
    session.add_message("assistant", "hello", model="gpt-4o", usage={"prompt_tokens": 10})
    mgr.save(session)

    loaded = mgr.load("test:1")
    assert loaded.messages[0]["model"] == "gpt-4o"
    assert loaded.messages[0]["usage"]["prompt_tokens"] == 10
```

此测试不依赖真实文件系统布局，只验证 `SessionManager` 的 round-trip 行为。upstream 的 `SessionManager` 已有此能力，测试用于回归保护。

### 6.4 参考文件存在性确认

| 文件 | 状态 |
|---|---|
| `tests/agent/test_loop_save_turn.py` | 存在 |
| `tests/channels/test_discord_channel.py` | 存在 |
| `tests/session/test_manager.py` | **不存在** |
| `tests/session/test_session_manager.py` | **不存在** |
| `tests/agent/test_runner_timing.py` | **不存在**（新建） |
| `tests/agent/test_context_builder.py` | 需检查 |

对于不存在的 `tests/session/test_manager.py`，新建测试可放在 `tests/session/test_manager_metadata.py` 或复用 `tests/agent/test_session_manager_history.py`。

## 7. 向前兼容性

| 设计决策 | 依赖 upstream 版本细节 | 未来升级 review point |
|---|---|---|
| `AgentRunResult` 增加 `elapsed_ms` / `llm_elapsed_ms` | `AgentRunResult` dataclass 定义 | upstream 若改为 pydantic model 或 NamedTuple，字段添加方式需调整。 |
| `AgentLoop._run_agent_loop()` 返回 `AgentRunResult` | 本 spec 已采用方案 B | 与 Spec5 对齐。upstream 若改变 `AgentRunResult` 字段，需同步更新 `_state_run()` 读取逻辑。 |
| `ContextBuilder._build_runtime_context()` 签名扩展 | 当前是 `@staticmethod` | upstream 若改为实例方法或引入 dataclass/pydantic 参数对象，签名变更成本降低，可同时引入更多字段。 |
| `sender_name` 通过 `metadata` 传递 | `InboundMessage.metadata` 是 `dict[str, Any]` | 若未来 `InboundMessage` 引入显式 `sender_name` 字段，metadata 路径可作为 fallback 保留一个版本。 |
| `Session.add_message(**kwargs)` 透传 | 当前是 `**kwargs` 合并 | upstream 若改为显式字段验证（如 pydantic model），需同步修改所有 `add_message` 调用点。 |
| `Session.clear()` 保留 `metadata` | 当前实现保留整个 dict | 若 upstream 未来改为更激进的清除策略（如 `metadata = {}`），需要重新评估 `/new` 的保留逻辑。 |

## 8. 实现顺序

推荐按以下顺序实现，每一步都有独立可运行的测试：

1. **Runner 计时**（`AgentRunner`）
   - 添加 `elapsed_ms` / `llm_elapsed_ms` 到 `AgentRunResult`。
   - 在 `_request_model()` 内部增加计时，返回 `(response, elapsed_ms)`。
   - 修改 `run()` 计时逻辑。
   - 测试：`tests/agent/test_runner_timing.py`

2. **AgentLoop turn 保存增强**（`AgentLoop._save_turn`, `_state_save`, `_state_run`）
   - 扩展 `TurnContext`。
   - 改 `_run_agent_loop()` 返回 `AgentRunResult` 对象（方案 B）。
   - 扩展 `_save_turn()` 签名和 assistant message 写入逻辑。
   - 测试：`tests/agent/test_loop_save_turn.py`

3. **User message sender metadata**（`AgentLoop._persist_user_message_early`）
   - 在 `_persist_user_message_early()` 中写入 `sender_id` 和 `sender_name`。
   - 测试：`tests/agent/test_loop_save_turn.py`

4. **Discord metadata 丰富**（`DiscordChannel._build_inbound_metadata`）
   - 增加 `channel_name` 和 `sender_name`。
   - 测试：`tests/channels/test_discord_channel.py`

5. **Runtime context 扩展**（`ContextBuilder`）
   - 扩展 `_build_runtime_context()` 和 `build_messages()`。
   - 修改 `AgentLoop._state_build()` 从 `msg.metadata` 提取并传入。
   - 测试：新建 `tests/agent/test_context_builder.py` 或放在最近的 context 测试模块。

6. **回归验证**
   - 运行 `tests/agent/test_loop_save_turn.py` 全部用例。
   - 运行 `tests/channels/test_discord_channel.py` 全部用例。
   - 运行 `tests/session/` 相关用例。
   - 确认 `/new` 后 `session.metadata` 保留（手动或集成测试）。

---

## 附录 A：跨 Spec 协调声明

本 spec 与其他 spec 有以下交叉点，已在正文中对齐：

| 交叉点 | 涉及 Spec | 对齐结论 |
|--------|-----------|---------|
| `_request_model()` 计时 | Spec3 + Spec5 | 统一在 `_request_model()` 内部完成计时+日志，返回 `(response, elapsed_ms)`。不新增 `_timed_request_model()` 包装器。 |
| `_request_finalization_retry()` | Spec3 + Spec5 | 计时纳入 `llm_elapsed_ms`（Spec3），日志静默（Spec5）。 |
| `AgentRunResult` 扩展方式 | Spec3 + Spec5 | 统一用方案 B（`AgentRunResult` 整体传递），不扩展 tuple。 |
| `sender_name` 数据流 | Spec3 → Spec2 | Spec3 建立入口（`InboundMessage.metadata["sender_name"]`），Spec2 在 outbound 阶段消费。metadata key 名必须一致：`"sender_name"`。实现顺序：先 Spec3 后 Spec2。 |
| `AgentLoop` 构造函数参数 | Spec3 | **Spec3 不涉及 AgentLoop 构造函数参数新增**。 |

## 附录 B：关键设计决策速查

| 决策 | 选择 | 理由 |
|---|---|---|
| `sender_name` 显式字段 vs metadata | **metadata（Phase 1）** | 避免 `InboundMessage` 构造函数签名变更带来的大规模调用点修改。 |
| `channel_name` / `sender_name` 是否持久化 | **不持久化在 message 中** | 它们属于 prompt 装饰信息，持久化会污染 history 并增加 token。`sender_id` 已足够用于审计。 |
| `provider_name` 是否保留 | **完全不保留** | 实现细节，无对话价值，有泄漏风险。 |
| `Session.clear()` 是否修改 | **不修改** | upstream 已保留 `metadata`，行为符合需求。 |
| `AgentRunResult` 新增字段默认值 | **0** | 保持向后兼容，现有调用方无需修改。 |
| `_run_agent_loop()` 返回方式 | **方案 B：返回 `AgentRunResult`** | 与 Spec5 对齐，避免 tuple 位置参数膨胀。调用点唯一（`_state_run`），侵入可控。 |

## 附录 C：不确定点

1. **Upstream `openai_compat_provider.py` 的 usage 字段粒度**：plan 提到该 provider 可能已保留更详细的 usage 字段（如 `cached_tokens`）。实现前需 inspect `nanobot/providers/openai_compat_provider.py` 中 `LLMResponse` 的构造逻辑。如果已完整保留，则无需修改 provider 层；否则在 provider 层补充。
2. **`tests/agent/test_context_builder.py` 是否存在**：未在 plan 中明确列出。若不存在，测试应新建在 `tests/agent/` 下。若上游已有但未发现，应复用现有模块。
3. **`_request_finalization_retry()` 的计时**（已与 Spec5 对齐）：该方法直接调用 `provider.chat_with_retry()` 不走 `_request_model()`。本 spec 要求在其内部独立计时并纳入 `llm_elapsed_ms`，但不产生完整 request/response preview 日志（Spec5 的日志策略）。计时和日志是独立关注点。
4. **Slash command 的 metadata**：plan 提到 slash-command 路径也应获得同样的 metadata shape。当前 `DiscordBotClient._on_interaction` 通过 `_handle_message()` 发送命令，`_build_inbound_metadata()` 仅用于普通消息。需确认 slash command 的 `metadata` 是否也经过 `_build_inbound_metadata()` 或需要单独补充 `channel_name`/`sender_name`。
