---
tapd_url: ""
req_type: tech
status: draft
spec_version: 1
---

# Spec: Dream 单阶段机制移植

> TL;DR：把 upstream PR #3990 / commit `d1a94dae8aa29cabf6808f4abc84c5b269e29883` 的 Dream 重构移植到当前 fork：Dream 不再自己跑两阶段专用 AgentRunner，而是作为 cron/system job 构造一条普通用户消息，通过 `AgentLoop.process_direct()` 走主 agent 工具调用链，让模型直接维护 `SOUL.md`、`USER.md`、`memory/MEMORY.md` 和 `.dream_cursor`。

## 1. Intent / Why

当前 fork 的 Dream 机制是两阶段：phase1 先分析 history，phase2 再通过隔离 Runner 和文件工具写 memory。这个机制带来两个实际问题：

- 记忆生成规则分散在 `dream_phase1.md` / `dream_phase2.md` 和 `Dream` 类内部，难以调粒度和行为。
- Dream 有一套独立的工具执行/runner 路径，和普通 agent 行为不一致，维护面更大。

upstream 已经在 PR #3990 将 Dream 简化为 cron/system prompt 驱动：Dream 构造一条完整任务消息，把 history batch、当前 memory 文件、操作规则交给普通 `process_direct()` 执行。这个改动更适合后续干预记忆粒度：只需要改一个 Dream prompt，而不是两阶段流水线。

## 2. Scope + Non-goals

Scope：

- 移植 upstream Dream 单阶段 prompt：新增/替换 `nanobot/templates/agent/dream.md`。
- 改 `/dream` 命令：从 `loop.dream.run()` 切换为 `loop.process_direct(build_dream_prompt(...))`。
- 改 cron Dream job：从内部 `agent.dream.run()` 切换为普通 direct process。
- 保留本地已存在的 memory 版本管理、`/dream-log`、`/dream-restore`、`.dream_cursor`、GitStore 追踪能力。
- 删除或停用已经不用的两阶段 `Dream` 专用 Runner 路径，避免死代码继续误导。
- 增加单元测试覆盖 prompt 构造、`/dream` 命令、cron job direct invocation、cursor 推进指令。

Non-goals：

- 不重新设计 haibin 的记忆粒度规则；本次只迁移机制，粒度策略后续在 `dream.md` prompt 上单独调。
- 不改 `history.jsonl` 归档策略；`/new` 导致碎片 history 的问题另开任务。
- 不改普通 system prompt 中 SOUL/USER/MEMORY 注入方式。
- 不迁移 upstream 与本任务无关的其它文件差异。

## 3. Testable Acceptance

- [x] 存在 `nanobot/templates/agent/dream.md`，并包含对三类文件的维护规则：`SOUL.md`、`USER.md`、`memory/MEMORY.md`。
- [x] Dream 任务消息包含：待处理 history entries、当前文件内容（带截断 caps）、target cursor 信息、必须用文件工具编辑的要求。
- [x] `/dream` 不再直接调用 `loop.dream.run()`，而是调用 `loop.run_dream_once()`（内部走 `process_direct`）。
- [x] cron Dream job 不再直接调用 `agent.dream.run()`，而是调用 `agent.run_dream_once()`。
- [x] 若没有待处理 history，`/dream` 返回 nothing-to-process，不发起 LLM 调用。
- [x] cursor 推进采用 upstream 语义：模型只维护记忆文件，`run_dream_once()` 在 `dream_run_completed` 验证通过后由 Python 调用 `set_last_dream_cursor(target_cursor)` 并 auto-commit；模型被明确禁止触碰 `.dream_cursor`。测试覆盖完成/未完成两条路径。
- [x] 相关 pytest 通过（3388 passed / 21 failed，失败清单与干净树逐字节一致，全部 pre-existing）。

## 4. Key Decisions

### Decision 1: 以 upstream commit 为行为基线，但不盲目覆盖本地 fork

当前 fork 已有本地修复：Dream provider override、GitStore blame 性能修复、memory version log、`.dream_cursor`、`/dream-log`/restore 等。移植时只拿 upstream 机制，不用整文件覆盖。

### Decision 2: Dream prompt 是后续粒度治理的唯一入口

迁移后，记忆粒度、排除类别、合并策略都应集中在 `dream.md`。这能解决当前两阶段 prompt + Python orchestration 分散的问题。

### Decision 3: 本次不顺手解决 `/new` 归档过细

`/new` 会触发 session archive，并写入 `history.jsonl`。这是 history 原料粒度问题，和 Dream 消费机制相关但不属于同一层。本次只换 Dream 消费/写入机制。

## 5. Interface / Contract

### `build_dream_prompt(store, max_batch_size) -> tuple[str | None, int | None]`

建议新增一个纯函数或轻量方法，用于构造 Dream direct message。

输入：

- `MemoryStore`
- `max_batch_size`

输出：

- `None, None`：无待处理 history
- `prompt, last_cursor`：有待处理 history，prompt 内含处理说明和目标 cursor

### `/dream`

用户可见行为保持：先返回 `Dreaming...`，后台完成后发 `Dream completed...` 或 `Dream: nothing to process.`。

内部行为变化：不再调用 `Dream.run()`；改为构造 prompt 后调用 `process_direct()`。

### cron Dream job

保留现有 cron 注册方式和 schedule 配置。触发时执行同一套 direct Dream prompt。

### 文件写入契约

Dream agent 必须通过文件工具完成：

- 读取/更新 `SOUL.md`
- 读取/更新 `USER.md`
- 读取/更新 `memory/MEMORY.md`

cursor 契约（最终采用 upstream 语义）：模型不触碰 `memory/.dream_cursor`；`run_dream_once()` 在完成标记（`_stop_reason == "completed"`）验证通过后，由 Python 推进 cursor 并连同记忆文件一起 auto-commit。未完成则 cursor 不动，本 batch 下次重试。
