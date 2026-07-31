# 包 E：测试质量与 utils 审查

## 结论

**Important 级问题 4 项，Minor 级问题 2 项，无 Critical。** 四组关键变异均被测试杀死，说明 stall 恢复、fallback 冷却边界、OAuth 流式重试和 cron 回收主体逻辑有真实防线。但测试套件仍有两个明显空洞：`git blame --porcelain` 解析器已被实证为可被普通提交信息击穿；cron 启动接线测试只是搜源码字符串，删除真实调用后仍可通过。替身则大量散落，尤其 CLI 的 SessionManager/Cron/AgentLoop 替身重复且已靠手工补方法追赶生产接口。

## Critical

无。

## Important

### 1. `git blame --porcelain` 解析器会把普通 commit message 误认成记录头

**位置：** `nanobot/utils/gitstore.py:59-75`

**问题本质：** `_parse_blame_porcelain()` 用 `len(line) > 40 and line[40] == " "` 判定 blame 记录头，没有校验前 40 字符是十六进制 object id，也没有校验后续字段是三个整数。porcelain 元数据中的 `summary` 行由用户提交信息决定；当提交主题前 32 个字符无空格、第 33 个字符为空格时，`summary ` 前缀加 32 字符恰好让索引 40 为空格。解析器随后尝试把普通单词转成整数，直接抛 `ValueError`，而 `_compute_line_ages()` 只包装了 `OSError`，不会转成 `GitStoreError`。

实际探针在副本创建真实 Git 仓库并运行真实 `git blame --porcelain`：

```text
$ uv run --frozen pytest -q tests/utils/test_zz_blame_probe.py
.F
ValueError: invalid literal for int() with base 10: 'tail'
1 failed, 1 passed in 0.64s
```

短提交信息通过，`"a" * 32 + " tail of a normal commit msg"` 失败，证明这不是推理上的边角，而是稳定可复现的新失败模式。现有 `tests/utils/test_gitstore.py` 只覆盖短提交信息、路径空格、未跟踪文件和工作区行，未覆盖 porcelain 元数据与头部相撞。

**最小修复方向：** 记录头必须按完整语法解析：40/64 位十六进制 oid 加三个十进制字段，并只把成功解析的头用于更新 `current_count`；解析异常统一包装成 `GitStoreError`。增加含长 summary、非 ASCII summary、多行 commit message 的真实 git 集成测试。

### 2. cron 启动接线测试是重言式源码字符串检查

**位置：** `tests/session/test_cron_retention.py:227-232`

**问题本质：** `TestGatewayWiresItOnce.test_startup_prune_is_invoked` 不启动 gateway，也不调用入口，只断言 `nanobot/cli/commands.py` 文本包含 `prune_cron_run_sessions`。删除真正的启动调用，只保留 import、注释、替身方法甚至无关字符串，测试仍绿。它验证的是实现文本里出现某个词，不是“gateway startup 会调用一次 prune”的外部契约。

**最小修复方向：** 构造 gateway 启动路径，把 `SessionManager.prune_cron_run_sessions` patch 成 spy，执行实际 startup，断言调用一次及异常策略；不要读取生产源码。

### 3. `git blame` 子进程没有超时，可能无限挂住调用线程

**位置：** `nanobot/utils/gitstore.py:310-321`

**问题本质：** 新实现把 dulwich 内存遍历改成外部 `git blame`，却没有 `timeout`。损坏仓库、过滤器、网络文件系统或异常 git 行为都可能让同步调用无限等待。当前仅捕获 `OSError` 和非零退出码，没有 `subprocess.TimeoutExpired` 处理。`line_ages()` 因此引入了此前没有的无限阻塞失败模式。

**最小修复方向：** 给 `subprocess.run` 设置有依据的有限超时，捕获 `TimeoutExpired` 并抛含路径和超时值的 `GitStoreError`；补一个 patch `subprocess.run` 抛 `TimeoutExpired` 的测试。

### 4. CLI 的会话与 cron 替身已经发生接口追赶，重复实现放大漂移风险

**位置：** `tests/cli/test_commands.py:1849-1855, 1933-1936, 2604-2607, 2745-2748, 2959-2962, 3235-3238, 3434-3437, 3539-3542`

**问题本质：** 同一个 `_FakeSessionManager.prune_cron_run_sessions()` 在至少八处重复，`_FakeCron`、`_FakeAgentLoop.from_config` 也在同一文件反复定义。1849 行的注释已经直说“gateway 启动会清理 cron 会话，替身必须提供这些方法”，这正是替身漂移的证据：生产入口增加必调方法后，每个结构型 fake 都要手工追赶。多数 fake 未继承 `SessionManager` 或受 Protocol/spec 约束，生产签名、同步异步属性或副作用改变时不会自动暴露脱节。

**最小修复方向：** 抽成 `tests/cli/conftest.py` 的 `session_manager_stub`、`cron_service_stub`、`agent_loop_stub` fixture；用 `create_autospec` 或最小 Protocol 约束生产接口，并允许每个测试只覆盖关心的行为和调用记录。

## Minor

### 1. helpers 的异常吞没契约缺少 rename 失败测试

**位置：** `nanobot/utils/helpers.py:15-20`，`tests/utils/test_helpers.py:80-94`

**问题本质：** `_atomic_write_text()` 新增父目录 fsync，代码有意吞掉目录 `os.open/os.fsync` 的 `OSError`。测试只模拟目录 fsync 不支持，没有覆盖 `tmp.replace(path)` 失败时必须继续向上传播且不声称写入成功。由于 fsync 和 replace 位于同一个小函数，未来调整异常范围时容易误吞真正写失败。

**最小修复方向：** patch `Path.replace` 抛 `OSError`，断言异常传播且目标文件未被替换；保留当前“仅目录持久化失败可忽略”的边界。

### 2. 部分测试名仍只描述动作，没有说清条件与结果

**位置：** `tests/session/test_cron_retention.py:210-216`，`tests/agent/test_runner_stall_recovery.py:40-41`

**问题本质：** `test_a_broken_sweep_never_escapes` 没有断言，只靠“不抛异常”表达结果；`test_initial_stall_retries_same_model` 未在名字里说明“下一轮成功时清零总 stall 预算”的核心条件。测试体本身有价值，但失败报告无法直接告诉维护者被破坏的契约。

**最小修复方向：** 分别改成 `test_maybe_prune_swallows_prune_exception_and_throttles_next_call`（并断言后续调用语义）和 `test_success_after_initial_stall_resets_total_stall_budget_for_later_recovery`。

## 变异测试实证

所有破坏只发生在副本 `/root/workspace/tmp/mut-review-e/sync-2026-07`，原工作树未改动。

### Agent runner：成功后不清零总 stall 计数

变异：删除 `nanobot/agent/runner.py` 中成功响应后的 `total_stalls = 0`。

```text
$ uv run --frozen pytest -q tests/agent/test_runner_stall_recovery.py
F.                                                                       [100%]
FAILED tests/agent/test_runner_stall_recovery.py::test_initial_stall_retries_same_model
1 failed, 1 passed in 0.46s
```

**结论：有效测试。** 它能杀死“早先 stall 污染后续恢复预算”的回归。

### Fallback provider：删除冷却上下界夹取

变异：把 `min(max(retry_after or default, MIN), MAX)` 改成 `retry_after or default`。

```text
$ uv run --frozen pytest -q tests/providers/test_fallback_provider.py
FAILED ...::test_quota_retry_after_is_clamped_to_minimum
FAILED ...::test_quota_retry_after_is_clamped_to_maximum
2 failed, 23 passed in 1.51s
```

**结论：有效测试。** 冷却最小值与最大值边界都被独立拦截。

### Anthropic OAuth：流式输出后错误仍刷新并重试

变异：把 `if not emitted[0] and auth_error ...` 改为忽略 `emitted[0]`。

```text
$ uv run --frozen pytest -q tests/providers/test_anthropic_token_refresh.py
FAILED tests/providers/test_anthropic_token_refresh.py::test_stream_401_after_output_is_not_retried
E assert 2 == 1
1 failed, 15 passed in 1.52s
```

**结论：有效测试。** 它能拦住流式输出后重放请求导致重复内容的回归。

补充变异：将 401/403 判定改为仅 403，同一文件结果为 `5 failed, 11 passed`，401 同步与流式刷新路径均有覆盖。

### Session cron 回收：保留配额 off-by-one

变异：把 `runs[keep_per_job:]` 改成 `runs[keep_per_job + 1:]`。

```text
$ uv run --frozen pytest -q tests/session/test_cron_retention.py
FAILED ...::test_a_stale_cron_run_session_is_removed
FAILED ...::test_the_newest_runs_survive_however_old_they_are
FAILED ...::test_the_survivors_are_the_newest_ones
FAILED ...::test_each_job_keeps_its_own_quota
FAILED ...::test_dry_run_reports_without_deleting
FAILED ...::test_dry_run_reports_reclaimable_bytes
FAILED ...::test_one_bad_file_does_not_stop_the_rest
FAILED ...::test_cache_is_cleared_along_with_the_file
8 failed, 18 passed in 1.06s
```

**结论：有效测试。** 配额边界、分 job、dry-run、失败隔离和 cache 清理均依赖真实删除集合，不是假测试。

## 总评

这套测试真正能拦住的是关键状态机和数值边界回归，拦不住的是外部工具格式变化、启动接线被删除，以及无接口约束替身与生产代码慢慢分叉。