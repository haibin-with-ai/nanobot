# Pack F/G/H 审计：10 个 commit 对照 plan.md

基线：本地 `9ca8c42d`，新基座 worktree `/root/git_code/nanobot/.worktrees/sync-2026-07`（upstream/main = 3f808d0a）。
判定口径：COVERED = plan 有对应 Task 原句；DROPPED-OK = 上游实读代码已有等价能力或在已砍清单；GAP = 行为会丢且 plan 无处承接。

---

## 551fe46b feat: subagent 独立模型与 provider

COVERED。plan 第 3 节「subagent 独立模型与 provider（A2）」原句：「保留的产品能力是『spawn 可选择独立 preset/model』，实现必须落到上游 runtime 抽象，不能恢复旧 `AgentRunSpec(model=...)`」；Task 3.2 原句：「spawn schema 增加可选 `model`，保持 `task / label / temperature / wait`」。
上游 `nanobot/agent/tools/spawn.py:22-43` 的 schema 目前只有 `task/label/temperature/wait`，确实没有 `model`，需靠 Task 3.2 补，方向一致。旧实现里的 `timeout_seconds`、TraceHook 已在已砍清单，Task 3.1 也明写「不恢复」。

## 409a3929 fix(tests): spawn mock 签名补 `**kwargs`

DROPPED-OK。这条只是本地测试为兼容新增 kwargs 打的补丁。上游 `tests/test_tool_contextvars.py:68-80`、`:199-210`、`:237-` 三处 `_Manager.spawn` 已显式列出 `temperature=None, workspace_scope=None`，签名随上游 spawn 演进而非靠 `**kwargs` 兜底。plan Task 3 的 Files 也已列 `tests/test_tool_contextvars.py`，重放 Task 3.2 时该文件自然会被同步改。无独立行为。

## cb1dadfa fix: subagent spawn 捕获 model alias resolver 的 KeyError

**GAP（小，但必挂）。**
- 丢失行为：spawn 传了未知的 preset/alias 时，本地是 `except (ValueError, KeyError)` → 打 warning、退回裸字符串继续跑；不捕获就是整条 spawn 调用抛异常。
- 本地位置：`nanobot/agent/subagent.py:247`（本地 `SubagentManager` 内 `self._model_alias_resolver(model)` 的 try 块）。
- 上游查证：`nanobot/agent/model_presets.py:76-82` `normalize_preset_name()` 对未知名字 `raise KeyError(f"model_preset {name!r} not found. Available: ...")`，`resolve_preset()`（`nanobot/agent/model_runtime.py:116-131`）直接透传；上游 `spawn.py` 全文无任何 try/except，只有并发上限与 runtime 缺失两处 `return`。也就是说 Task 3.2 一旦把 `model` 接进 spawn，未知 preset 就会以异常形式冒到工具调用层。
- 建议：补在 plan Task 3.1 的测试清单（「传 preset 时由上游 RuntimeResolver 得到完整 LLMRuntime」之后加一条「未知 preset 返回 `ToolResult.error` 而非抛 KeyError/ValueError」），实现落在 Task 3.2 的 spawn 边界。注意口径要跟上游 `dfc3919b`「不掩盖 runtime failure」对齐——建议返回明确错误串，而不是照抄本地的静默降级。

## 58cd1135 fix(subagent): 跨 provider preset 同时换 provider+model

COVERED。plan Task 3.1 原句：「跨 provider preset 同时切换 provider、model、reasoning 参数」；Task 3.2 原句：「独立 runtime 从 `ProviderSnapshot` 构造，不能就地修改调用方 runtime 或共享 provider client」。
本地这条正是把 `preset_snapshot_loader` 穿进 `SubagentManager`；上游 `model_runtime.py:95-131` 的 `resolve_snapshot/resolve_preset` + `model_presets.py:64-73` 的 `build_runtime_preset_snapshot(loader=...)` 已是同一机制的上游形态。回归测试 `test_subagent_preset_swaps_provider_and_model` 的意图由 Task 3.1 承接。

## c41b8717 fix: subagent 并发上限

DROPPED-OK。上游已具备等价能力，三处实读：
- `nanobot/config/schema.py:131`：`max_concurrent_subagents: int = Field(default=1, ge=1)`
- `nanobot/agent/loop.py:407-408`：`self._concurrency_gate: asyncio.Semaphore | None = (asyncio.Semaphore(_max) if _max > 0 else None)`，且 `loop.py:466` 用 `max_concurrent_subagents=defaults.max_concurrent_subagents` 把配置值真正接进去
- `nanobot/agent/tools/spawn.py:80-87`：`limit = self._manager.max_concurrent_subagents` 后返回「concurrency limit reached (running/limit)」
本地当时修的「配置值没传到 manager」在上游已不存在。

## ced62a8c 本地 ContextPruner

DROPPED-OK（在已砍清单）。上游等价物是 `nanobot/agent/context_governance.py`：`ContextGovernor.prepare_for_model`（:75）、`apply_tool_result_budget`（:301）、`compact_inflight_overflow`（:322）、`snip_history`（:383）、`_compact_tool_result_at`（:510），并由 `nanobot/agent/runner.py:15` 直接引入使用。覆盖面比本地 pruner 宽，不重放。

## 057b23ad feat(tools): ripgrep-first grep + spawn timeout + Phase1 测试

分三段判：

1. **ripgrep-first grep**：DROPPED-OK。已被本地自己的 862cf645 回滚，且 `search.py` 本地改动在已砍清单。
2. **spawn timeout_seconds**（含 `tests/agent/tools/test_subagent_tools.py` 新增的 `test_spawn_tool_schema_includes_timeout_seconds`、`test_spawn_tool_timeout_seconds_applied`，以及 `_resolve_tool_config_refs()` 前置调用）：DROPPED-OK。`timeout_seconds` 与 Pydantic forward-ref 修复都在已砍清单，plan Task 3.1 也明写「不恢复 `timeout_seconds`」。
3. **三个测试文件的本地新增断言**，逐条比对上游：
   - `tests/config/test_config_paths.py`：`get_logs_dir` / `get_runtime_subdir` / 显式 `get_workspace_path` 三类断言在上游同名文件中已有等价用例（`get_runtime_subdir` 上游用 `cron` 而非 `tmp`，同一函数同一路径拼装逻辑）——DROPPED-OK。
   - `tests/tools/test_exec_security.py`：`/dev/null` 这类良性设备路径不被安全网拦截，上游同文件已有 `_guard_command(...) is None` 的重定向/设备路径用例覆盖——DROPPED-OK。
   - `tests/tools/test_message_tool_suppress.py`：媒体路径追踪、跨 target 不追踪，分别由上游 `tests/tools/test_message_tool.py` 的 `test_message_tool_tracks_turn_media_for_same_target`、`test_message_tool_cross_target_does_not_track_turn_media` 覆盖（跨 target 的守卫在 `nanobot/agent/tools/message.py:262` 同一个条件上）；省略 channel 走默认 context 由上游同文件的继承类用例覆盖。**唯一没等价物的是「send_callback 抛异常时返回 `Error sending message: ...` 且 `_sent_in_turn` 保持 False」** —— 见下条 GAP。

**GAP（测试覆盖级）：**
- 丢失行为：发送回调失败路径的断言。上游 `nanobot/agent/tools/message.py` 里 `_sent_in_turn = True` 写在 `await send_callback(...)` 之后的 try 内，行为本身正确，但 `tests/tools/test_message_tool*.py` 全文没有 `side_effect` 形式的失败注入用例，这条不变量目前无人看守。
- 本地位置：`tests/tools/test_message_tool_suppress.py`（057b23ad 新增段）。
- 建议：补在 plan 第 5 节之外的工具层收尾处，最省事是挂到 Task 4 的「两个低风险工具层补丁」后面新增一条 Task 4.3（只加测试、不动实现）。

## 862cf645 revert: 回滚 ripgrep-first grep

DROPPED-OK。回滚 commit，且 `search.py` 本地改动整条在已砍清单。上游 `nanobot/agent/tools/search.py` 为准，重放时该文件不动即天然等价。

## 45f75cb2 fix: read_file 默认返回全文

COVERED。plan Task 4.1「read_file 默认总返回全文」原句：「上游已有完整 dedup 与 `force` 机制……本任务只翻转默认值，不新建测试文件」，并逐行点名三处改动。
上游实读一致：`nanobot/agent/tools/filesystem.py:237` schema `force=BooleanSchema(... default=False)`、`:282` `force: bool = False`、`:268` description 里的 "Use force=true to re-read content even if unchanged."、`:336` `not force` 分支——三处都还在原状，Task 4.1 的行号描述准确。

## e0e86179 fix: gitstore 行龄用原生 git blame

COVERED。plan Task 4.2「gitstore 行龄改用原生 git blame」原句：「实现用 `git blame --porcelain -- <file>`，解析 `committer-time`；不要复制本地吞异常的兜底」。
上游实读：`nanobot/utils/gitstore.py:294` 仍是 `porcelain.annotate(str(self._workspace), file_path)`，`_compute_line_ages`（:49）从 annotate 结果取时间——正是本地这条要替换的旧实现。Task 4.2 的测试清单（多 commit 各自 committer time、未跟踪文件返回空、路径含空格、blame 失败不伪造成功）覆盖了本地行为。

---

## GAP 汇总

| # | commit | 丢失行为 | 本地位置 | 上游证据 | 建议补入 |
|---|--------|----------|----------|----------|----------|
| 1 | cb1dadfa | spawn 传未知 preset/alias 时不崩（本地降级为裸字符串） | `nanobot/agent/subagent.py:247` | `model_presets.py:76-82` 未知名 `raise KeyError`；`model_runtime.py:116` 透传；`spawn.py` 全文无 try/except | plan Task 3.1 加一条断言「未知 preset 返回 ToolResult.error 不抛异常」，实现落 Task 3.2 spawn 边界 |
| 2 | 057b23ad | send_callback 失败时返回 `Error sending message:` 且 `_sent_in_turn` 不置位 | `tests/tools/test_message_tool_suppress.py`（057b23ad 新增段） | 实现存在于 `message.py`（赋值在 await 之后），但 `tests/tools/test_message_tool*.py` 无 `side_effect` 注入用例 | plan 第 4 节新增 Task 4.3，仅补测试 |

其余 8 条：COVERED 4（551fe46b / 58cd1135 / 45f75cb2 / e0e86179），DROPPED-OK 4（409a3929 / c41b8717 / ced62a8c / 862cf645），057b23ad 主体亦为 DROPPED-OK，仅拆出上表第 2 条。
