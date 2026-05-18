# Spec5 — Subagent Model Override + Trace / LLM Logging Replay

> 对应 Plan: `docs/superpowers/plans/2026-05-18-pack5-subagent-trace-logging.md`  
> 目标分支: `sync-upstream-2026-05-replay`（replay base: `upstream/main` `ba38f908`）  
> 工作目录: `/root/git_code/nanobot/.worktrees/sync-upstream-2026-05-merge`

---

## 1. 概述

本 spec 要求在 upstream 代码上 replay 以下 fork 行为，同时保持最小侵入、面向复用、向前兼容：

1. **AgentHookContext.model** — 让 hook（含后续 Pack4 的 hook composition）能感知当前运行使用的模型名。
2. **Subagent 独立 provider 生命周期** — 当配置 `subagent_model` 时，subagent 使用独立创建的 provider/model，主 provider 切换（`/model` 命令、snapshot apply）不再影响 subagent。
3. **Per-spawn model override + alias resolution** — `spawn` 工具支持传入 `model` 参数，只影响该次 spawn，支持 exact/unique alias 解析，拒绝 ambiguous alias。
4. **Subagent generation overrides** — `reasoning_effort`、`max_tokens` 可从配置流向 `AgentRunSpec`。
5. **TraceHook** — 轻量级 JSONL trace，记录每次迭代的模型、迭代号、finish reason、usage。
6. **LLM req/resp 日志** — 在 runner I/O 边界输出单条 loguru 日志，包含 256 字符 preview，不 dump 敏感内容。
7. **llm_logs 路径策略** — 在没有 `workspace/layout.py` 的环境下，使用 `workspace/llm_logs/` 作为 trace 和 LLM 日志的根目录。

---

## 2. 行为需求

### 2.1 AgentHookContext.model
- `AgentHookContext` 新增字段 `model: str | None = None`。
- 主 agent 和 subagent 的每次迭代开始前，`context.model` 被赋值为当前 `AgentRunSpec.model`。
- `TraceHook` 和 `CompositeHook` 均无需修改即可读取该字段。

### 2.2 Subagent 独立 provider 生命周期
- `AgentDefaults` 新增三个可选字段，均默认 `None`，且支持 camelCase（通过 `Base` 的 `alias_generator=to_camel` 自动支持，无需额外 `AliasChoices`）：
  - `subagent_model: str | None`
  - `subagent_reasoning_effort: str | None`
  - `subagent_max_tokens: int | None`
- 当 `subagent_model` 为 `None` 时，保持现有行为：
  - `AgentLoop` 构造时将主 provider/model 传入 `SubagentManager`。
  - `_apply_provider_snapshot()` 调用 `self.subagents.set_provider(provider, model)` 以同步主 provider 变更。
- 当 `subagent_model` 不为 `None` 时：
  - `AgentLoop.from_config()` 或同级构造逻辑使用 `nanobot.providers.factory` 的现有工具（如 `build_provider_snapshot` / `make_provider`）为 subagent 独立创建 provider 和 model。
  - `SubagentManager` 持有该独立 provider；`_apply_provider_snapshot()` **不再**调用 `self.subagents.set_provider(...)`，避免主 provider 切换覆盖 subagent 配置。
  - 独立 subagent provider 必须和 main provider 完全隔离，不共享 client 状态。

### 2.3 Per-spawn model override 和 alias resolution
- `SpawnTool` schema 新增可选参数 `model: StringSchema("Exact model name or alias")`。
- `SpawnTool.execute()` 将 `model` 透传至 `SubagentManager.spawn(..., model=...)`。
- `SubagentManager.spawn()` 签名新增 `model: str | None = None`。
- 解析规则：
  - `model=None` → 使用 manager 默认 model（即构造时的 `self.model`）。
  - `model` 为字符串时，先走 `model_alias_resolver`（见 §4.3）解析为实际 model 字符串。
  - 解析失败（ambiguous alias / unknown preset）时，`spawn()` 在创建 task 前抛出 `ValueError`，不进入 runner。
- 该 override **仅**影响本次 `AgentRunSpec.model`，不修改 `SubagentManager.model`、不修改 `self.runner.provider`、不修改主 provider。

### 2.4 Subagent generation overrides
- `SubagentManager` 构造时接收 `reasoning_effort` 和 `max_tokens`。
- 在 `_run_subagent()` 构建 `AgentRunSpec` 时，将这两个字段传入对应参数。
- Per-spawn `model` override 不改变 `reasoning_effort` / `max_tokens`；它们继续使用 manager 级配置。

### 2.5 TraceHook
- 新增 `TraceHook(AgentHook)`，实现 `after_iteration()`。
- 每次迭代在 `after_iteration` 中追加一行 JSONL，字段包括：
  - `timestamp`（ISO 8601 或 Unix float）
  - `model`（来自 `AgentHookContext.model`）
  - `iteration`（来自 `AgentHookContext.iteration`）
  - `finish_reason`（来自 `AgentHookContext.stop_reason`）
  - `usage`（来自 `AgentHookContext.usage` 的 dict）
- 文件路径由调用方（`AgentLoop` 或 `SubagentManager`）注入，而非 `TraceHook` 自行推导。
- 写文件使用 append 模式，目录 lazy 创建。
- 主 agent 和每个 subagent 使用**独立**的 trace 文件。

### 2.6 LLM req/resp 日志
- 在 `AgentRunner._request_model()` 的 provider 调用前后插入 loguru 日志：
  - **Request log**: 单行，包含 `model`、消息数量、`last_message_preview`（≤256 字符，单行，去除换行）。不包含 `tools=` 计数。
  - **Response log**: 单行，包含 `finish_reason`、`usage` 摘要（prompt/completion/total tokens）、elapsed 毫秒。
  - **Tool-call log**（若 response 含 tool calls）：单行，仅列出工具名列表，不 dump 完整参数 JSON。
- `_request_finalization_retry()` 不重复产生上述日志。实现上保持 finalization retry 静默，或仅输出一条区别于正常 request 的简短日志（如 "finalization retry"），不重复完整 request/response preview。
- Preview 截断策略：取最后一条 user/assistant message 的 `content`（或 `tool_calls` 摘要），用 `truncate_text` 或等价逻辑限制到 256 字符，替换内部换行为空格。

### 2.7 llm_logs 路径策略
- 根目录：`Path(workspace) / "llm_logs"`。
- 目录 lazy 创建：首次写入前调用 `path.mkdir(parents=True, exist_ok=True)`。
- TraceHook 文件命名：
  - 主 agent: `{session_key or "main"}.trace.jsonl`
  - Subagent: `{task_id}.trace.jsonl`（`task_id` 由 `SubagentManager.spawn()` 生成的 8 字符短 UUID）
- 文件名中非法字符用简单替换逻辑处理（如替换 `/` 为 `-`）。
- 不创建 `workspace/layout.py`；路径计算内联在 `AgentLoop` / `SubagentManager` 中。

---

## 3. 架构分析

### 3.1 当前 upstream 关键状态（ba38f908 基线 + replay branch 已有变更）

| 组件 | 当前状态 |
|------|----------|
| `AgentHookContext` | 无 `model` 字段；已有 `iteration`, `messages`, `response`, `usage`, `tool_calls`, `stop_reason` 等 |
| `AgentRunSpec` | 已有 `model`, `temperature`, `max_tokens`, `reasoning_effort`, `session_key`, `provider_retry_mode`, `llm_timeout_s` |
| `AgentRunner._request_model()` | provider I/O 边界；构建 kwargs → 选择 streaming / progress / plain → 调用 provider → 返回 `LLMResponse`。无日志 |
| `AgentRunner._request_finalization_retry()` | 直接调用 `self.provider.chat_with_retry(**kwargs)`，不走 `_request_model()` |
| `SubagentManager` | 构造接收 `provider`, `model`, `max_iterations`, `max_tool_result_chars`, `bus`, `workspace`, `tools_config`, `restrict_to_workspace`, `disabled_skills`, `llm_wall_timeout_for_session`。`_run_subagent()` 构建 `AgentRunSpec` 时只传了 `model=self.model`，未传 `reasoning_effort`/`max_tokens` |
| `SubagentManager.set_provider()` | 修改 `self.provider`, `self.model`, `self.runner.provider` |
| `SpawnTool` | schema 仅 `task` + `label`；`execute()` 调用 `self._manager.spawn(...)` 无 `model` |
| `AgentLoop.__init__` | 构造 `SubagentManager(...)` 时传入主 provider/model；`_apply_provider_snapshot()` 调用 `self.subagents.set_provider(provider, model)` |
| `AgentLoop.from_config()` | 通过 `make_provider(config)` 创建主 provider；`config.resolve_preset()` 解析主 model |
| `nanobot/providers/factory.py` | replay branch 已有文件，暴露 `ProviderSnapshot`, `make_provider`, `build_provider_snapshot`, `load_provider_snapshot`, `provider_signature` 等 |
| `nanobot/utils/llm_runtime.py` | 存在；仅 `LLMRuntime`, `LLMRuntimeResolver`, `static_llm_runtime`；本 spec 不依赖它 |
| `nanobot/workspace/layout.py` | **不存在** |

### 3.2 扩展点识别

1. **AgentHookContext** 是 dataclass，直接加字段不会影响现有 hook（因为它们通过关键字访问或忽略未用字段）。
2. **AgentRunSpec** 是 dataclass，已有 `model`/`max_tokens`/`reasoning_effort`，subagent 只需在构造时传入。
3. **AgentRunner._request_model()** 是单一 I/O 边界，插入日志最自然；`_request_finalization_retry()` 是旁路，需要特殊处理避免重复。
4. **SubagentManager** 的 `_run_subagent()` 是构建 `AgentRunSpec` 的唯一位置，是传入 `reasoning_effort`/`max_tokens`/per-spawn `model` 的唯一切入点。
5. **AgentLoop** 的 `from_config()` / `__init__` 是注入独立 subagent provider 和 `model_alias_resolver` 的位置。
6. **`_apply_provider_snapshot()`** 需要识别 subagent 是否独立，以决定是否调用 `set_provider()`。

---

## 4. 技术方案

### 4.1 AgentHookContext.model

**变更文件**: `nanobot/agent/hook.py`

- 在 `AgentHookContext` 新增字段：
  ```python
  model: str | None = None
  ```

**变更文件**: `nanobot/agent/runner.py`

- 在 `AgentRunner.run()` 的迭代循环中，每次调用 `hook.before_iteration(context)` **之前**设置：
  ```python
  context.model = spec.model
  ```
- 位置在 `_request_model()` 之前即可，因为 `before_iteration` 就需要 model 信息（TraceHook 虽然用 `after_iteration`，但其他 hook 可能想在 `before_iteration` 读取）。
- 由于 `AgentHookContext` 是可变 dataclass（无 `frozen=True`），直接赋值即可。

### 4.2 Subagent 独立 provider 生命周期

#### 4.2.1 Config 层扩展

**变更文件**: `nanobot/config/schema.py`

- 在 `AgentDefaults` 追加：
  ```python
  subagent_model: str | None = None
  subagent_reasoning_effort: str | None = None
  subagent_max_tokens: int | None = None
  ```
- `Base` 已配置 `alias_generator=to_camel, populate_by_name=True`，因此 `subagentModel` / `subagentReasoningEffort` / `subagentMaxTokens` 自动可用，无需额外 `AliasChoices`。

#### 4.2.2 SubagentManager 扩展

**变更文件**: `nanobot/agent/subagent.py`

- `SubagentManager.__init__` 新增参数：
  ```python
  reasoning_effort: str | None = None,
  max_tokens: int | None = None,
  model_alias_resolver: Callable[[str], str] | None = None,
  trace_file: Path | None = None,
  # 保留现有参数
  ```
- 新增内部状态：
  ```python
  self.reasoning_effort = reasoning_effort
  self.max_tokens = max_tokens
  self.model_alias_resolver = model_alias_resolver
  self.trace_file = trace_file
  ```
- 若 `trace_file` 不为 `None`，在 `_run_subagent()` 构建 `AgentRunSpec` 时注入 `hook=CompositeHook([...])` 或 `TraceHook(...)`。具体见 §4.4。

#### 4.2.3 AgentLoop 构造逻辑

**变更文件**: `nanobot/agent/loop.py`

- `AgentLoop.__init__` 通过 `ForkConfig` 接收参数（Spec4 定义的跨 spec 参数打包机制）：
  ```python
  subagent_model: str | None = None,
  subagent_reasoning_effort: str | None = None,
  subagent_max_tokens: int | None = None,
  ```
- 在 `__init__` 中，区分两种情况：

**情况 A: `subagent_model` 未配置（`None`）**
```python
self.subagents = SubagentManager(
    provider=provider,
    workspace=workspace,
    bus=bus,
    model=self.model,
    tools_config=_tc,
    ...
    # 不传入独立 provider
)
self._subagent_has_independent_provider = False
```
`_apply_provider_snapshot()` 保持现有逻辑：调用 `self.subagents.set_provider(provider, model)`。

**情况 B: `subagent_model` 已配置**
- 使用 `build_provider_snapshot(config)` 或 `make_provider(config, ...)` 的现有能力，为 subagent 创建独立 provider。注意：由于 `AgentLoop.__init__` 不直接持有 `config` 对象，可以在 `from_config()` 层构建 subagent provider snapshot 后传入。
- `from_config()` 层示例逻辑：
  ```python
  subagent_provider = None
  subagent_model_resolved = None
  if config.agents.defaults.subagent_model:
      # 复用 factory 已有工具
      subagent_snapshot = build_provider_snapshot(config, preset_name=config.agents.defaults.subagent_model)
      subagent_provider = subagent_snapshot.provider
      subagent_model_resolved = subagent_snapshot.model
  ```
- 在 `__init__` 中：
  ```python
  self.subagents = SubagentManager(
      provider=subagent_provider or provider,
      workspace=workspace,
      bus=bus,
      model=subagent_model_resolved or model,
      tools_config=_tc,
      reasoning_effort=subagent_reasoning_effort,
      max_tokens=subagent_max_tokens,
    model_alias_resolver=...,  # 见 §4.3
    trace_file=...,            # 见 §4.5
    ...
  )
  self._subagent_has_independent_provider = subagent_provider is not None
  ```

- `_apply_provider_snapshot()` 修改：
  ```python
  if not self._subagent_has_independent_provider:
      self.subagents.set_provider(provider, model)
  ```

### 4.3 Per-spawn model override 和 alias resolution

#### 4.3.1 SpawnTool schema 扩展

**变更文件**: `nanobot/agent/tools/spawn.py`

- schema 新增 `model`：
  ```python
  model=StringSchema("Exact model name or alias for this spawn only (optional)"),
  ```
- `execute()` 透传：
  ```python
  return await self._manager.spawn(
      task=task,
      label=label,
      model=kwargs.get("model"),  # 或显式参数
      ...
  )
  ```

#### 4.3.2 SubagentManager.spawn() 和 alias resolution

**变更文件**: `nanobot/agent/subagent.py`

- `spawn()` 签名：
  ```python
  async def spawn(
      self,
      task: str,
      label: str | None = None,
      model: str | None = None,
      origin_channel: str = "cli",
      origin_chat_id: str = "direct",
      session_key: str | None = None,
      origin_message_id: str | None = None,
  ) -> str:
  ```
- 在生成 `task_id` 后、创建 `asyncio.create_task` 之前，解析 `model`：
  ```python
  spawn_model = self.model  # 默认值
  if model is not None:
      if self.model_alias_resolver is not None:
          try:
              resolved = self.model_alias_resolver(model)
          except ValueError as e:
              raise ValueError(f"Invalid spawn model alias {model!r}: {e}") from e
          spawn_model = resolved
      else:
          spawn_model = model  # 无 resolver 时直接透传（向后兼容）
  ```
- 将 `spawn_model` 作为参数传入 `_run_subagent(task_id, ..., spawn_model)`。

#### 4.3.3 model_alias_resolver 的构造与注入

- resolver 的职责是：把用户传入的字符串（可能是 exact model name、preset name、或 model 的 unique prefix）解析为最终 model 字符串。
- **复用已有逻辑**：`Config.resolve_preset()` 已支持 exact match 和 unique prefix match（plan 确认 upstream 有此语义）。
- 在 `AgentLoop.from_config()` 中构造 resolver：
  ```python
  def _resolve_alias(alias: str) -> str:
      # 优先尝试作为 preset name 解析
      try:
          preset = config.resolve_preset(alias)
          return preset.model
      except ValueError:
          pass
      # 若不是 preset，则假定是 exact model name，直接返回
      # 若后续需要验证 exact model name 是否合法，可扩展
      return alias
  ```
- 将 `_resolve_alias` 传入 `AgentLoop(...)`，再传入 `SubagentManager(...)`。
- **Ambiguous alias 处理**：`config.resolve_preset()` 在 ambiguous 时已经抛出 `ValueError`，`SubagentManager.spawn()` 只需捕获并重新抛出（或直接透传）。

#### 4.3.4 _run_subagent 中使用 per-spawn model

- `_run_subagent()` 签名新增 `model: str` 参数。
- 构建 `AgentRunSpec` 时：
  ```python
  AgentRunSpec(
      model=model,
      reasoning_effort=self.reasoning_effort,
      max_tokens=self.max_tokens,
      ...
  )
  ```

### 4.4 TraceHook 设计

**变更文件**: `nanobot/agent/hook.py`

- 新增类：
  ```python
  class TraceHook(AgentHook):
      def __init__(self, trace_file: Path) -> None:
          super().__init__()
          self._trace_file = trace_file

      async def after_iteration(self, context: AgentHookContext) -> None:
          entry = {
              "timestamp": time.time(),
              "model": context.model,
              "iteration": context.iteration,
              "finish_reason": context.stop_reason,
              "usage": dict(context.usage),
          }
          line = json.dumps(entry, ensure_ascii=False, default=str)
          self._trace_file.parent.mkdir(parents=True, exist_ok=True)
          with self._trace_file.open("a", encoding="utf-8") as f:
              f.write(line + "\n")
  ```
- 使用标准库 `json` 和 `time`，不引入新依赖。

**说明**：
- `TraceHook` 自身不感知主 agent / subagent 的区别；它只负责往给定的 `trace_file` append。
- `AgentHookContext.model` 已在 §4.1 中设置，因此 trace entry 天然带模型名。

### 4.5 llm_logs 路径策略

**变更文件**: `nanobot/agent/loop.py`, `nanobot/agent/subagent.py`

- 统一 helper（可放在 `nanobot/agent/loop.py` 内联，或 `subagent.py` 中各算各的）：
  ```python
  def _llm_logs_dir(workspace: Path) -> Path:
      return workspace / "llm_logs"
  ```
- **主 agent trace 文件**：在 `AgentLoop.run()` 中（或 `__init__` 中惰性计算）：
  ```python
  trace_path = _llm_logs_dir(self.workspace) / f"{session_key or 'main'}.trace.jsonl"
  ```
  - `session_key` 中可能含 `/` 或 `:`，替换为 `-`。
- **Subagent trace 文件**：在 `SubagentManager.spawn()` 中：
  ```python
  trace_path = _llm_logs_dir(self.workspace) / f"{task_id}.trace.jsonl"
  ```
- 目录 lazy 创建由 `TraceHook` 在首次 `after_iteration` 时负责（`mkdir(parents=True, exist_ok=True)`），因此构造时无需提前创建目录。
- **主 agent 的 TraceHook 注入**：
  - `AgentLoop.__init__` 若检测到需要 trace（由配置或默认行为决定），将 `TraceHook(trace_path)` 加入 `self._extra_hooks`。
  - 或更简单地，在 `AgentLoop.run()` 中动态构建 `CompositeHook([loop_hook, trace_hook, *self._extra_hooks])`。
  - 为保持和现有 `CompositeHook` 逻辑兼容，推荐在 `run()` 中把 `TraceHook` 塞进 hook list：
    ```python
    hooks = [loop_hook]
    if self._trace_hook is not None:
        hooks.append(self._trace_hook)
    if self._extra_hooks:
        hooks.extend(self._extra_hooks)
    hook = CompositeHook(hooks) if len(hooks) > 1 else hooks[0]
    ```
- **Subagent 的 TraceHook 注入**：
  - `SubagentManager` 在 `_run_subagent()` 构建 `AgentRunSpec.hook` 时，将 `TraceHook(self.trace_file)` 包装进 `CompositeHook`（若 subagent 需要 hook）。当前 subagent runner 调用时不传 hook，因此需要显式传入：
    ```python
    subagent_hook = TraceHook(self.trace_file)
    spec = AgentRunSpec(..., hook=subagent_hook, ...)
    ```
  - 若未来 subagent 也需要其他 hook，可改为 `CompositeHook([subagent_hook, ...])`。

### 4.6 LLM req/resp 日志边界和截断策略

**变更文件**: `nanobot/agent/runner.py`

#### 4.6.1 新增 helper

```python
@staticmethod
def _last_message_preview(messages: list[dict[str, Any]], limit: int = 256) -> str:
    if not messages:
        return ""
    last = messages[-1]
    content = last.get("content") or ""
    if isinstance(content, str):
        preview = content.replace("\n", " ").strip()
    else:
        preview = str(content)
    return preview[:limit]
```

#### 4.6.2 _request_model() 日志（与 Spec3 计时共享同一插桩点）

**重要协调**：Spec3 要求 `_request_model()` 返回值从 `LLMResponse` 变为 `tuple[LLMResponse, int]`（增加 `elapsed_ms`）。本 spec 的日志和 Spec3 的计时在同一个方法内完成，共享同一个 `time.monotonic()` 插桩点。实现时不能各自独立插桩导致重复计时。

在 `kwargs = self._build_request_kwargs(...)` 之后、`coro = self.provider.chat_...` 之前，插入 request log：

```python
logger.info(
    "LLM request model={} messages={} preview={!r}",
    spec.model,
    len(messages),
    self._last_message_preview(messages),
)
```

在 `response = await ...` 之后，插入 response log：

```python
elapsed_ms = int((time.monotonic() - request_start) * 1000)
usage = self._usage_dict(getattr(response, "usage", None))
logger.info(
    "LLM response model={} finish_reason={} usage={} elapsed_ms={}",
    spec.model,
    response.finish_reason,
    usage,
    elapsed_ms,
)
```

若 response 含 tool calls，再插入 tool-call summary：

```python
if response.tool_calls:
    tool_names = [tc.name for tc in response.tool_calls]
    logger.info("LLM tool_calls model={} tools={}", spec.model, tool_names)
```

#### 4.6.3 _request_finalization_retry() 防重复

`_request_finalization_retry()` 直接调用 `self.provider.chat_with_retry()`，不走 `_request_model()`。

**方案**（已与 Spec3 计时策略对齐）：
- **日志**：不在 `_request_finalization_retry()` 中添加 request/response preview 日志。仅在 `_request_model()` 中记录。finalization retry 属于 runner 内部容错，不应在正常的 LLM I/O 边界产生混淆日志。
- **计时**：但 `_request_finalization_retry()` 内部需独立计时（`time.monotonic()` 包裹），其耗时纳入 Spec3 的 `llm_elapsed_ms` 累加。计时和日志是独立关注点——不因"不写日志"而漏掉计时。

若需要标识 finalization retry 的发生，可单独在调用处（`runner.py` 中发起 retry 的位置）输出一条 debug 或 info：
```python
logger.debug("Attempting finalization retry for model={}", spec.model)
```
但这不属于 Task 4 的测试断言范围，可选。

#### 4.6.4 截断与隐私

- Preview 取最后一条 message 的 `content` 字符串。
- 替换换行为空格，strip 后截断到 256 字符。
- 不记录 tools 数组长度（避免暴露内部工具数量）。
- 不记录完整 message 内容、不记录 tool arguments。
- Usage 只记录数值摘要（prompt/completion/total）。

---

## 5. 最小侵入评估

| 目标 | 侵入方式 | 评估 |
|------|----------|------|
| `AgentHookContext.model` | 在 dataclass 加字段 | 无侵入；现有 hook 忽略新字段 |
| `AgentDefaults` 新增字段 | 在 schema 加三个可选字段 | 无侵入；不破坏现有配置解析 |
| `SubagentManager` 扩展 | 新增构造参数 + `spawn` 参数 | 轻微侵入；现有测试需更新 mock 构造（仅新增可选参数，默认值保持兼容） |
| `SpawnTool` schema 扩展 | schema 加可选字段 | 无侵入；旧调用不传 `model` 仍兼容 |
| 独立 subagent provider | `AgentLoop` 中增加分支判断 | 中等侵入；但完全复用 `factory.py` 已有 API，不新增 provider 创建逻辑 |
| `_apply_provider_snapshot()` 条件化 | 加一个 `if not self._subagent_has_independent_provider` | 轻微侵入；逻辑清晰 |
| `TraceHook` | 新增类，不影响现有 hook | 无侵入 |
| Runner LLM 日志 | 在 `_request_model()` 插入若干 `logger.info` | 轻微侵入；纯追加行为 |
| `llm_logs` 目录 | 惰性创建目录 | 无侵入；首次运行时创建 |

**总体结论**：本 spec 完全复用 upstream 已有扩展点（dataclass 字段追加、`AgentRunSpec` 参数、`factory.py` snapshot API、`CompositeHook` 组合），不魔改 provider 内部、不新增全局状态、不改动 `agent_browser` 等无关模块。

---

## 6. 测试方案

### 6.1 AgentHookContext.model
- **文件**: `tests/agent/test_runner_hooks.py`（或新建 `test_hook_context.py`）
- 用 fake provider 跑一轮 iteration，在自定义 hook 的 `before_iteration` 中断言 `context.model == "test-model"`。
- 断言 subagent 运行中 `context.model == "subagent-model"`。

### 6.2 Config 扩展与默认值
- **文件**: `tests/config/test_config_migration.py`
- 断言 `AgentDefaults()` 的 `subagent_model`, `subagent_reasoning_effort`, `subagent_max_tokens` 均为 `None`。
- 断言 snake_case 和 camelCase 解析均正确。

### 6.3 Subagent 独立 provider 生命周期
- **文件**: `tests/agent/test_subagent_model_override.py`（新建）
- 用 `AgentLoop.from_config()` + monkeypatched factory 断言：
  - `subagent_model` 配置时，`SubagentManager` 持有独立 fake provider（`is` 不相等）。
  - 未配置时，`SubagentManager` 持有主 provider。
  - `_apply_provider_snapshot()` 后，未配置时 subagent provider 被同步更新；已配置时 subagent provider 保持不变。

### 6.4 Per-spawn model override
- **文件**: `tests/agent/test_subagent_model_override.py`
- 构造带 `model_alias_resolver` 的 `SubagentManager`：
  - `spawn(model=None)` → 使用默认 model。
  - `spawn(model="exact-model")` → `AgentRunSpec.model == "exact-model"`。
  - `spawn(model="alias")` → resolver 返回 `"resolved-model"`，`AgentRunSpec.model == "resolved-model"`。
  - `spawn(model="ambiguous")` → resolver 抛 `ValueError`，`spawn()` 在创建 task 前抛异常。
- 断言 override 不修改 `SubagentManager.model`。

### 6.5 Subagent generation overrides
- **文件**: `tests/agent/test_subagent_model_override.py`
- 构造 `SubagentManager(reasoning_effort="low", max_tokens=1234)`。
- fake runner 捕获 `AgentRunSpec`，断言 `reasoning_effort == "low"` 且 `max_tokens == 1234`。

### 6.6 TraceHook
- **文件**: `tests/agent/test_trace_hook.py`（新建）
- 实例化 `TraceHook(temp_path)`，手动构造 `AgentHookContext` 并调用 `after_iteration()`。
- 断言文件存在、内容为一行合法 JSON、包含 `model`, `iteration`, `finish_reason`, `usage`。
- 多次调用断言文件为 JSONL（多行）。
- 传入超长 `final_content` 或 usage，验证 trace 不做截断（trace 是结构化数据，不应截断；与 LLM log 的 text preview 不同）。

### 6.7 llm_logs 路径策略
- **文件**: `tests/agent/test_trace_hook.py` 或 `test_subagent_model_override.py`
- 用临时 workspace 实例化 `AgentLoop` / `SubagentManager`，跑一轮 dummy turn 和一次 spawn。
- 断言 `workspace/llm_logs/main.trace.jsonl` 和 `workspace/llm_logs/{task_id}.trace.jsonl` 存在。

### 6.8 Runner LLM 日志
- **文件**: `tests/agent/test_runner_core.py` 或新建 `test_runner_llm_logging.py`
- 使用 pytest `caplog`（或 loguru 的 `logger.add` 到 `io.StringIO`）捕获 `AgentRunner._request_model()` 的日志。
- 断言：
  - request log 包含 `model=`, `messages=`, `preview=`。
  - preview 单行且 `len(preview) <= 256`。
  - request log 不包含 `tools=`。
  - response log 包含 `finish_reason=`, `usage=`, `elapsed_ms=`。
  - 含 tool calls 时存在 tool_calls log，且只列出工具名，不含完整参数。
- finalization retry 场景：断言不产生重复的 request/response preview 日志。

---

## 7. 向前兼容性

### 7.1 依赖 upstream 现有实现细节的设计决策

| 决策 | 依赖的 upstream 实现 | 兼容性说明 |
|------|----------------------|------------|
| `AgentHookContext` 新增 `model` | dataclass 无 `frozen=True`，字段可安全追加 | 兼容 |
| `AgentRunSpec` 传 `reasoning_effort`/`max_tokens` | upstream 已存在这两个字段 | 兼容。若未来 upstream 重命名，需同步调整 `SubagentManager._run_subagent()` 的赋值 |
| 使用 `ProviderSnapshot` | `nanobot/providers/factory.py` 已在 replay branch 中存在 | 该文件在 `origin/main` 不存在，是 replay branch 引入。若 upstream main 后续以不同 API 引入 provider snapshot，需要 adapter 层 |
| `build_provider_snapshot` / `make_provider` | 同上 | 同上。本 spec 假设这些 factory 函数在 replay branch 中稳定可用 |
| `config.resolve_preset()` 语义 | upstream `Config` 已有该方法，支持 exact + unique prefix | 兼容 |
| `_apply_provider_snapshot()` 调用 `set_provider()` | upstream 已有该方法 | 兼容。条件化分支是新增逻辑，不影响未配置 `subagent_model` 的路径 |

### 7.2 nanobot 3.0/4.0 replay 建议

- **不依赖具体行号**：所有修改都通过类/方法签名和明确的扩展点描述。
- **不依赖私有方法名**：只使用 `AgentRunner.run()`, `_request_model()`, `SubagentManager.spawn()`, `_run_subagent()`, `AgentLoop.from_config()`, `_apply_provider_snapshot()` 等在 spec 中明确提及的方法。
- **Hook 设计通用**：`TraceHook` 不依赖任何 nanobot 特有的内部状态，只要 `AgentHookContext` 提供 `model`/`iteration`/`stop_reason`/`usage` 即可在其他版本复用。
- **Config 字段向后兼容**：新增字段均为 `None` 默认值，旧配置文件自动兼容。

---

## 8. 实现顺序

推荐按以下顺序实现，每个步骤先写测试再写实现：

1. **AgentHookContext.model** — 基础数据结构变更，后续所有 hook 和 trace 都依赖它。
2. **Runner LLM req/resp 日志** — 独立功能，不依赖其他变更；可先验证 I/O 边界日志。
3. **AgentDefaults 扩展 + Config 测试** — schema 变更简单，但为后续 provider 创建提供输入。
4. **Subagent generation overrides (`reasoning_effort`, `max_tokens`)** — 在 `SubagentManager` 中传参到 `AgentRunSpec`，逻辑简单。
5. **Per-spawn model override + alias resolution** — 需要修改 `SpawnTool` schema 和 `SubagentManager.spawn()`。
6. **独立 subagent provider 生命周期** — 涉及 `AgentLoop` 构造逻辑和 `_apply_provider_snapshot()`，依赖步骤 3 的 config 字段。
7. **TraceHook + llm_logs 路径策略** — 依赖步骤 1 的 `context.model`；将 TraceHook 注入主 agent 和 subagent。
8. **端到端验证** — 运行 `test_subagent_model_override.py` + `test_trace_hook.py` + `test_runner_llm_logging.py` 全套。

---

## 9. 报告

### 9.1 输出文件路径
```
/root/git_code/nanobot/.worktrees/sync-upstream-2026-05-merge/docs/superpowers/specs/2026-05-18-spec5-subagent-trace-logging.md
```

### 9.2 关键设计决策

1. **AgentHookContext.model 而非 hook 传参**：在 dataclass 上加字段，所有 hook 自动可见，避免修改每个 hook 方法的签名。
2. **独立 provider 的判定放在 AgentLoop 层**：`AgentLoop` 通过 `_subagent_has_independent_provider` 标志控制 `_apply_provider_snapshot()` 的行为，避免 `SubagentManager` 引入循环依赖或复杂状态机。
3. **model_alias_resolver 以 callable 注入**：`SubagentManager` 不直接依赖 `Config`，而是接收 `Callable[[str], str]`。这样 resolver 的实现可以在 `AgentLoop.from_config()` 中自由调整（如复用 `config.resolve_preset()`），而 `SubagentManager` 保持可测试和可复用。
4. **TraceHook 写文件由调用方决定路径**：TraceHook 只负责序列化和 append，不感知 workspace 结构。主/subagent 的 trace 文件路径差异由 `AgentLoop` / `SubagentManager` 各自计算后注入。
5. **Runner LLM 日志仅加在 `_request_model()`**：`_request_finalization_retry()` 保持静默，避免 duplicate logs 和测试复杂度。这是最小侵入方案。
6. **llm_logs 用最小路径 `workspace/llm_logs/`**：不臆造 `workspace/layout.py`，目录 lazy 创建，文件名用 `{session_key}.trace.jsonl` / `{task_id}.trace.jsonl`。

### 9.3 不确定点

1. **ProviderSnapshot / factory.py 的 upstream 归属**：当前 replay branch 已有 `nanobot/providers/factory.py`，但 `origin/main` 不存在。若未来 upstream main 以不同签名引入类似能力，独立 subagent provider 的构造逻辑需要 adapter。
2. **TraceHook 的启用条件**：plan 未明确 trace 是默认开启还是由配置开关控制。本 spec 假设默认写入（只要 workspace 可写），因为 plan 的 completion criteria 要求 "Main agent and each subagent write to independent LLM log files"。若后续发现需要配置开关，可在 `AgentDefaults` 追加 `enable_trace_logging: bool = True`。
3. **Session key 含特殊字符的文件名安全**：当前 spec 仅做简单替换（`/` / `:` → `-`）。若 session key 可能包含其他文件系统非法字符（如 `\`, `\0`），需要更 robust 的 sanitize helper。建议在实现时封装一个 `_sanitize_filename()`。
4. **Per-spawn model override 的 provider 边界**：当前 spec 假设 per-spawn model 仍在 subagent 的同一 provider 下工作（即 model 字符串可直接用于 `provider.chat_with_retry()`）。若用户传入的 model 属于不同 provider（如 subagent 默认 anthropic，spawn 想 override 为 openai），当前设计不会自动切换 provider。plan 的范围未要求此能力，如需支持需额外设计 "per-spawn provider 重建"。
