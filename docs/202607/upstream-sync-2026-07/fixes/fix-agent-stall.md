# AgentRunner stall 状态机收敛

对应 `reviews/coding-review-agent.md` 的 I1（双计数器）、I2（跨分支布尔）、
I5（按错误措辞判墙钟超时）以及 stall 提示的静默失败。

## 修法

**单计数器。** `_MAX_TOTAL_STALLS` 与 `consecutive_stalls` 两个计数器合并成一个
`stalls`。判定挪进 `_stall_verdict(stalls, out_of_iterations, out_of_time)`，
返回 `retry` / `notice_retry` / `give_up` 三选一。调用点只剩一次分支。

**放弃分支不再靠布尔穿线。** 原来用 `stall_gave_up` 这个跨分支布尔把状态
带到几十行以外的错误收尾处。现在放弃时直接改写 `clean`，落进既有的
`finish_reason == "error"` 出口，上下文照常落盘。

**墙钟超时改成显式标记。** 原来靠 `content.startswith("Error calling LLM: timed
out after ")` 认自己掐的那一刀，provider 换个措辞就失效。改由 `_WALL_TIMEOUT_KIND`
= `"wall_timeout"` 标记，`_is_wall_timeout()` 只看 `error_kind`。放弃时若是墙钟
超时，保留 provider 带秒数的原文，比统一文案更能说明是谁掐的。

**提示贴不上就如实说。** `_append_stall_notice()` 返回 False 时补一条 warning，
不再让日志显示「已重试」而模型其实没收到任何提示。

**去掉重复的流式判断。** `_model_wall_budget` 与请求路径共用
`_streaming_modes(spec, hook)`。

## 测试

新增 `tests/agent/test_runner_stall_state.py`，11 个用例，覆盖阈值三档、
墙钟标记两条路径、提示贴不上时的日志。

```text
uv run --frozen pytest -q
5778 passed, 15 skipped in 118.03s
```

## 变异验证

三次故意破坏（在 `/root/workspace/tmp/mut2` 副本内，真实 checkout 未动）：

M1 放弃时无条件覆盖文案：

```text
FAILED test_the_runners_own_wall_timeout_keeps_its_own_message
1 failed, 10 passed in 0.41s
```

M2 `_is_wall_timeout` 改回嗅探 `"Error calling LLM: timed out after "` 前缀：

```text
FAILED test_provider_wording_does_not_pass_for_the_runners_own_wall_clock
1 failed, 10 passed in 0.45s
```

M3 提示贴不上时不记日志：

```text
FAILED test_a_notice_that_cannot_be_attached_is_reported
1 failed, 10 passed in 0.34s
```

## Deferred

`loop.py` 的 5 元组返回值改 dataclass（review I4）本轮不做。它牵动 loop.py
一大片测试，且失效场景要求任务在 `.set()` 之前创建，不属于切换阻断项。
