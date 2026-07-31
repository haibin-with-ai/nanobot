# cron/session 三条收尾修复

范围：`nanobot/session/manager.py`、`nanobot/cron/{types,service,bound_runner}.py`、`tests/{cron,session}`。
两条 Critical 与存储迁移已在前序提交完成，本轮未动；`cli/commands.py`、`config/schema.py` 未改。

## 1. 重言式测试 → 真实调用点 + spy

原测试读 `cli/commands.py` 源码字符串断言「prune 已接线」，接线坏了它照样绿。删掉，改成 `tests/session/test_cron_retention.py::TestRealCronCallPointWiresRetention`：真跑
`nanobot.cron.bound_runner.run_cron_job`，agent 用最小 stub，`sessions.prune_cron_run_sessions`
换成 `Mock`，断言未绑定任务触发时恰好调用一次、绑定任务一次都不调。

阻断说明：本轮不许改 `cli/commands.py`，所以退一步测最近一个真实调用点（cron 触发路径
`run_cron_job`）。启动侧仍靠 `tests/cli/test_commands.py` 的既有 fake 覆盖（本轮跑过 143 passed），
端到端启动断言 deferred。

## 2. prune / maybe_prune 合并 + 节流门

两份近似算法合成一份 `SessionManager.prune_cron_run_sessions`，`maybe_prune_cron_run_sessions` 删除。

节流契约（写进 docstring）：

- **实例级**，不是进程级。节流状态是 `SessionManager._last_cron_prune_at`，每个 manager 实例各算各的。
- 运行期调用（不传 `now_ms`）走节流：`_retention_clock()` 距上次扫描不足 24h 就直接返回 `None`，不扫盘。
- 传 `now_ms` 的调用视为管理/测试扫描，绕过节流，永远真扫。
- 时钟由构造参数 `retention_clock` 注入，默认 `time.monotonic`（单调，不受系统改时间影响）。
- 保留策略仍是：默认 30 天窗口，且每个 job 至少保留最近 3 次。

## 3. 双份维护收敛

- **codec**：`service._save_store` 里手写的 46 行 camelCase 字面量删掉，编码搬进 `types.py`，
  与 `from_store_dict` 贴在一起：`CronSchedule/CronPayload/CronRunRecord/CronJobState/CronJob` 各有
  `to_store_dict`，`_save_store` 只写 `[j.to_store_dict() for j in ...]`。字段名现在只有一处。
- **cron session key**：`make_cron_run_session_key` / `parse_cron_run_session_key` 一对进 `manager.py`，
  job id 只编码一次。`run_bound_cron_job` 原先 `make_cron_run_session_key(...).removeprefix("cron:")`
  拼完再剥前缀，改成直接 `f"{job.id}:{_new_run_id()}"`——绑定任务本来就没有 run session。

## 测试

新增 `tests/cron/test_cron_codec.py`（编码字段名、`to_store_dict`↔`from_store_dict` 全字段往返、
`_save_store` 确实走 job codec）。红→绿：实现前 3 failed（`AttributeError: type object 'CronJob' has
no attribute 'to_store_dict'`），实现后全绿。`tests/cron/test_bound_runner.py` 里过期的
`maybe_prune_cron_run_sessions` fake 换成 `prune_cron_run_sessions` 计数 spy，并补了 key 里 job id
只出现一次、绑定 run_id 不带 `cron:` 前缀两条。

最终验证（真实输出）：`uv run --frozen pytest -q tests/cron tests/session` → `223 passed in 3.32s`；
`uv run --frozen ruff check`（六个改动文件 + 新测试）→ `All checks passed!`。

## 变异验证（副本 `/root/workspace/tmp/mut`，真实 checkout 未动）

基线：`47 passed`。

变异 1（第 1 条）：`bound_runner` 里 `agent.sessions.prune_cron_run_sessions()` 换成 `pass`。

```
2 failed, 45 passed
FAILED tests/cron/test_bound_runner.py::...::test_each_unbound_run_asks_for_a_sweep
  assert 0 == 1
FAILED tests/session/test_cron_retention.py::TestRealCronCallPointWiresRetention::
  test_unbound_run_invokes_the_throttled_prune_once
  AssertionError: Expected 'mock' to be called once. Called 0 times.
```

变异 2（第 2 条）：删掉节流门 `if last is not None and tick - last < _CRON_RUN_PRUNE_INTERVAL_S`。

```
2 failed, 45 passed
FAILED ...::TestThrottledSweep::test_instance_sweeps_at_most_once_per_day
  assert {'keys': [], 'count': 0, 'bytes': 0} is None
FAILED ...::TestThrottledSweep::test_the_daily_window_uses_the_injected_monotonic_clock
  assert {'keys': [], 'count': 0, 'bytes': 0} is None
```

两个变异都被杀死，变异体已在副本内还原。

## Deferred

- gateway 启动路径的端到端 prune 断言：需要改 `cli/commands.py` 把清理调用抽成可注入的接线点，本轮禁改。
- 节流是实例级：多个 `SessionManager` 会各扫一次。gateway 目前单实例，真要收紧得落盘时间戳，未做。
- `/root/workspace/tmp/mut` 副本未删（本轮禁用 rm/mv），需主线程清理。
- 未提交，git 写操作留给主线程；工作树里 `nanobot/providers/*` 的改动来自并行任务，不属本轮。
