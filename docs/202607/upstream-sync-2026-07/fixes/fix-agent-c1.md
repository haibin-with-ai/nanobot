# AgentRunner run 级总死线

## 修法

`AgentRunner.run()` 在进入循环前按一次模型请求的既有外层墙钟计算 run 级总预算，并用可注入的单调时钟生成绝对死线。

第一次模型请求保留原有单次超时语义。只有 stall 触发重试后，后续请求才取「单次外层墙钟」与「run 剩余时间」中的较小值，避免墙钟按 stall 次数叠加。

stall 状态机仍只识别 provider 的 `error_kind=timeout`。模型选择和 attempts 仍由 provider 负责。非 stall 响应仍重置连续 stall 计数。死线到达后复用通用错误收尾并写入 assistant 占位上下文。

测试改用注入时钟推进 0.11 秒，在 0.1 秒预算下断言只调用 provider 一次、返回 error、给出放弃文案并保存 assistant 上下文。

## 验证

种子测试初始状态：

```text
uv run --frozen pytest -q tests/agent/test_runner_stall_recovery.py
12 passed, 1 failed in 6.32s
失败项等待约 5 秒后得到 stop_reason=max_iterations，而非预期 error。
```

定向测试：

```text
uv run --frozen pytest -q tests/agent/test_runner_core.py tests/agent/test_runner_stall_recovery.py
32 passed in 1.11s
```

Ruff：

```text
uv run --frozen ruff check nanobot/agent/runner.py tests/agent/test_runner_stall_recovery.py
All checks passed!
```

agent 测试集：

```text
uv run --frozen pytest -q tests/agent
1505 passed, 3 failed in 39.76s
```

三个失败均在未改动的 `test_context_builder.py::TestLoadBootstrapFiles`。单独复跑仍为 `9 passed, 3 failed in 0.31s`，属于当前工作树既有的 ContextBuilder/TOOLS.md 不一致，未越界修改。

## 变异验证

故意把 `run()` 生成的 `deadline` 改为 `None` 后运行：

```text
uv run --frozen pytest -q tests/agent/test_runner_stall_recovery.py
1 failed, 12 passed in 1.09s
```

失败项是 `test_run_deadline_caps_repeated_stall_retries`，provider 实际调用 4 次，断言期望 1 次。随后恢复死线生成逻辑。
