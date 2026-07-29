# 只读审查：cron / session / 模型预设 / 工具层 / commit 卫生

- 审查范围：`git diff 3f808d0a...HEAD`（worktree `/root/git_code/nanobot/.worktrees/sync-2026-07`，分支 `sync-upstream-2026-07`，36 个 commit）
- 纪律来源：`docs/202607/upstream-sync-2026-07/plan.md` L615 / L632 / L653 / L811
- 本次只读，未改任何源码，未做任何 git 写操作，未删任何 session 文件

## 结论

**一条阻断：cron 的 `model` 预设根本不落盘，切到这个分支会当场抹掉线上 3 个 job 已有的 `model: "fast"`。** 另有两条重要：`run_cron_job` 的 bound/unbound 分派条件和 `is_bound_cron_job()` 定义不一致；生产代码里塞了一个只为测试替身存在的 `getattr` 兜底。回收逻辑本身正确且不误删，但对生产现存的 191 个旧命名 `cron_*.jsonl` 完全无效。`/new` 清预设、loop.py 降级路径、read_file 三处一致、git blame 不吞失败、commit 卫生，均核对通过。

---

## 阻断

### B1. `CronPayload.model` 只读不写，保存即丢失

证据：

- `nanobot/cron/types.py:55` 新增 `model: str | None = None`；`types.py:76` 的 `from_store_dict` 读 `data.get("model")`
- `nanobot/cron/service.py` 的 `_save_store()` 序列化 payload 时**没有** `"model"` 字段（`git show 745c9584 -- nanobot/cron/service.py` 只增了 `add_job(model=...)` 与 dataclass 字段，序列化侧一行未加）
- 当前生产主干 `/root/git_code/nanobot`（5110edf3）`nanobot/cron/service.py:270` 写的是 `"model": j.payload.model` —— 说明这是同步过程中丢的实现，不是新特性
- 实测（临时目录，未碰生产数据）：

```
in-memory model: 'fast'
after reload model: None
model in file: False
```

- 生产 `/root/workspace/cron/jobs.json` 中有 3 个 job 带 `model: "fast"`。本分支上线后，任何一次 `_save_store()`（增删改任务、任务状态更新）都会把这几个字段永久抹掉

为什么测试没抓到：`tests/cron/test_cron_model_field.py` 的持久化用例是在 `CronService` **未 start** 的状态下 `add_job`，走的是 `_append_action("add", asdict(job))` 动作日志分支，`asdict` 天然带 `model`，所以能读回来。gateway 里 service 是 running 的，走 `_save_store()`，才是丢字段的那条路。154 个 cron/session 测试全绿，掩盖了这个洞。

严重级别：**阻断**（静默数据丢失，且违反 plan L632「`None` 的存储语义保持『未显式指定』」——现在是「永远未指定」）

最小修复：`_save_store()` 的 payload dict 补 `"model": j.payload.model`；测试补一条 `await service.start()` 之后再 reload 的 round-trip 断言。

---

## 重要

### I1. bound/unbound 分派条件与 `is_bound_cron_job()` 不一致

证据：

- `nanobot/cron/bound_runner.py`：`run_cron_job()` 判定条件是 `if job.payload.session_key:` → `run_bound_cron_job`，否则 `run_unbound_cron_job`
- `nanobot/cron/session_turns.py:71` 的 `is_bound_cron_job()` 要求三件事同时成立：`session_key` 且 `origin_channel` 且 `origin_chat_id`（并排除 deliver 类 payload）
- `nanobot/cron/session_delivery.py` 的 `origin_delivery_context()` 在 origin 缺失时 **raise ValueError**
- `745c9584` 删掉了 `_enforce_agent_binding` / `_is_unbound_agent_job`（plan L632 允许删除自动 disable 规则），但同时也删掉了「malformed delivery payload 仍拒绝」的唯一服务层守卫

后果：payload 带 `session_key` 却缺 origin（或 `deliver=True`）的 job，会被当成 bound 送进 `run_bound_cron_job`，每次触发抛 ValueError、写一条 error 运行记录，永远不会落到本次新增的 per-run 隔离会话上——而这类 job 恰恰是这个特性想救的对象。

生产现状（只读统计 `/root/workspace/cron/jobs.json`，14 个 job）：

```
(session_key, origin_channel, origin_chat_id, kind) -> count
(False, False, False, 'agentTurn')   : 6
(True,  False, False, 'agent_turn')  : 5
(False, False, False, 'agent_turn')  : 2
(False, False, False, 'system_event'): 1
```

那 5 个「有 session_key 无 origin」的 job 暂时不会炸，因为它们都带 `channel`/`to`，`_normalize_agent_turn_job()` 在加载时把 origin 补齐了。也就是说这条命只靠一个迁移函数吊着，一旦有 job 只有 session_key（历史 CLI 会话绑定就是这形态）就必崩。

严重级别：**重要**（当前生产侥幸不触发，语义已经错位；上游 bound 路径本身保留完好，这点没问题）

最小修复：`run_cron_job()` 改用 `is_bound_cron_job(job)` 分派，非完整绑定一律走 per-run 隔离会话；如果坚持要「拒绝 malformed」，就把拒绝写成显式分支和明确错误，别靠深处 raise。

### I2. 生产代码为测试替身开洞

证据：`nanobot/cli/commands.py` gateway 启动处（`prune_cron_run_sessions` 调用点，commit `19859b90`）：

```python
prune = getattr(session_manager, "prune_cron_run_sessions", None)
if callable(prune):
    with suppress(OSError):
        prune()
```

同一函数里 `session_manager` 就是当场 `SessionManager(...)` 构造出来的，真实对象必然有这个方法。这个 `getattr/callable` 存在的唯一理由是 `tests/cli/test_commands.py` 用桩替换了 `SessionManager` 符号。commit message 自己写明了：「gateway 测试替身无清理能力时跳过回收」——补在了被调用方，不是调用点。

严重级别：**重要**（违反「Patch the call site, not the callee」；更实际的风险是以后真出现替身/子类漏方法时，回收静默失效，没有任何日志）

最小修复：给测试替身补上 `prune_cron_run_sessions` 空实现（或直接用真 `SessionManager` + tmp workspace），生产侧恢复直呼。

---

## 建议

### S1. 回收只在 gateway 启动时跑一次

证据：`prune_cron_run_sessions` 全仓仅一个调用点，在 `nanobot/cli/commands.py` 的 gateway 启动路径；`nanobot/session/manager.py:426-427` `_CRON_RUN_RETAIN_DAYS = 30`、`_CRON_RUN_KEEP_PER_JOB = 3`。

长驻 gateway 不重启就永不回收。稳态文件量 = 30 天的 run 数：一个 5 分钟频率的 unbound job 就是 ~8600 个文件堆在一个目录里，而这些文件在进程活着期间一个都不会被删。建议要么挂到定时/每次 cron tick 后触发（`prune` 本身对 OSError 做了 per-file 兜底，`manager.py:951-957`，可以安全高频调用），要么把「每 job 保留数」做成硬上限而不只是 30 天窗口的免死名额。

### S2. 生产现存 191 个 `cron_*.jsonl` 这套回收一个都碰不到

证据（只读，没删任何东西）：

- `manager.py:506-511` `_session_key_from_path()` 只认规范 base64url 文件名，且要求 `_storage_key(key) == path.stem` 完全往返
- `manager.py:923-931` 回收 sweep 完全建立在这个解码之上；解不出来就 `continue`
- 实测 `/root/workspace/sessions`：259 个 `.jsonl`，**能解码 0 个**，命中 cron-run 正则 0 个；其中 191 个是旧的 lossy 命名 `cron_*.jsonl`
- 就算文件名迁移到 base64，旧 key 形态 `cron:{job}:{ms}` 也不匹配 `manager.py:423-425` 的新正则（要求 job id 重复两段 + 8 位 hex）

也就是说：新逻辑对新会话有效，对历史债务零作用。这未必是缺陷（严格解码是刻意设计，注释 `manager.py:433-435` 写明了），但「回收上线 = 目录会瘦」是错觉，得靠一次性迁移/清理脚本单独处理。**不误删**这一侧核对通过：绑定会话 key 形如 `discord:...`、普通会话 `cli_*`，均不匹配正则；`tests/session/test_cron_retention.py` 16 个用例覆盖了不误删非 cron、不误删 bound、keep_per_job、dry_run，实跑 154 passed。

### S3. `git ls-files` 的运行时失败被吞成空列表

证据：`nanobot/utils/gitstore.py:325`

```python
if tracked.returncode != 0 or not tracked.stdout.strip():
    return []
```

`returncode != 0`（不是 git 仓库、index 损坏、git 报错）和「文件未跟踪」（rc=0、stdout 空）被混成同一个 `[]`。blame 侧是对的：`gitstore.py:338-341` 非零直接 `raise GitStoreError` 带 stderr，`OSError` 也 raise（322-323、335-336），docstring `gitstore.py:302-303` 承诺一致。所以「不掩盖运行时失败」总体达标，只有 ls-files 这一处破口。

最小修复：拆成两个分支，`returncode != 0` 抛 `GitStoreError(... stderr)`，只有 rc=0 且空输出才 `return []`。

### S4. `read_file` 的 `force` 描述措辞反向

证据：`nanobot/agent/tools/filesystem.py:237-243`（schema `default=True`）、`:271-272`（description）、`:286`（`force: bool = True`）—— 三处一致，无自相矛盾，这条核对通过。

唯一挑刺：描述写的是「Read deduplication switch. Defaults to true」，参数名叫 `force`，而 true 的实际含义是**关掉** dedup。字面读起来像「dedup 开着」。建议改成「Set false to allow a short stub when the file is unchanged」这类以行为描述的措辞。

---

## 核对通过（附证据）

1. **上游 bound 路径未被删**：`nanobot/cron/bound_runner.py` 的 `run_bound_cron_job()` 完整保留，仍走 `origin_delivery_context()` + 上游会话语义；`nanobot/cron/service.py:559` 仍用 `is_bound_cron_job(job)` 做调度侧判断。（分派条件的偏差见 I1）
2. **per-run 会话键格式**：`cron:{job.id}:{run_id}`，`run_id = {job.id}:{started_ms}:{uuid4().hex[:8]}`，与 `manager.py:423-425` 正则严格对应；job id 由 `service.py:584` `str(uuid.uuid4())[:8]` 生成，字符集落在 `[0-9A-Za-z_-]` 内；同 job 并发不碰撞。
3. **`/new` 清预设落在 `Session.clear()` 且用常量**：`nanobot/session/manager.py` 的 `Session.clear()` 内 `self.metadata.pop(SESSION_MODEL_PRESET_METADATA_KEY, None)`，未出现字面量；`grep -rn "SESSION_MODEL_PRESET_METADATA_KEY"` 全仓无裸字符串写法。commit `8d829c25` 只动 3 行生产代码 + 两个测试文件。
4. **loop.py 预设降级恢复路径未被误改**：`git diff 3f808d0a...HEAD -- nanobot/agent/loop.py` 的 hunk 全部落在身份块/耗时统计区域，preset fallback 那段（约 L525-575）零改动。
5. **unbound 的 model 解析走 RuntimeResolver**：`bound_runner.py:102` `model = job.payload.model or DEFAULT_CRON_MODEL_PRESET`（`= "deep"`，符合 plan L653「`model is None → deep`」），经 `agent.set_session_model_preset()` 落到 `RuntimeResolver`；解析失败被 catch 后写 error 运行记录再 raise，不会留下 running 悬空。生产 `modelPresets` 含 `deep`，不会全线炸；顺带提示：unbound cron 的实际模型由默认 `deepest` 变成 `deep`，这是 plan 里定死的行为变化，不是缺陷。
6. **测试实跑**：`uv run --frozen pytest -q tests/cron tests/session/test_cron_retention.py` → `154 passed`；`uv run --frozen pytest -q tests/utils/test_gitstore.py tests/tools/test_read_enhancements.py tests/session tests/cli/test_commands.py` → `264 passed`。（未改任何测试）
7. **commit 卫生**：逐个 `git show --stat`，本任务相关 11 个 commit 全部只含本任务文件，无夹带：

```
745c9584  cron/tools/cron.py, cron/service.py, cron/types.py, tests/cron/*        (5 files)
da218ea7  cli/commands.py, cron/bound_runner.py, webui/ws_http.py, tests/*        (5 files)
3075f3a5  websocket/tests/test_websocket_http_routes.py                            (1 file)
20c9adc0  cli/commands.py, session/manager.py, tests/session/test_cron_retention.py(3 files)
19859b90  cli/commands.py                                                          (1 file)
c061c05b / 43689802 / cc05a491  tests/cli/test_commands.py                         (各 1 file)
8d829c25  session/manager.py + 2 个测试                                            (3 files)
e0b4f4d3  agent/tools/filesystem.py + 2 个测试                                     (3 files)
82666ce4  utils/gitstore.py + tests/utils/test_gitstore.py                         (2 files)
```

`d0c9dd2e`（.gitignore）、`3a9ea5ea`（docs 归档）与本任务无关但自身干净，无 session/workspace 产物误入库。唯一噪音：`43689802 / cc05a491 / 19859b90` 三个连续修测试的尾巴 commit，其中 `19859b90` 把测试问题修到了生产代码里（见 I2）。

---

## 修复优先级

1. B1 `_save_store` 补 `model` —— 上线前必须做，否则生产数据丢失
2. I1 分派改 `is_bound_cron_job()` —— 语义一致性，顺手补 malformed 的显式处理
3. I2 撤掉 `getattr` 兜底，改修测试替身
4. S1/S2/S3/S4 可排后续
