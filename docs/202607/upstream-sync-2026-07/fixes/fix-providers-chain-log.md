# 降级链路日志回归 + loguru 全局开关的测试污染

这两件事是一次全量跑串出来的，记在一起。

## 1. 链路日志被重构顺手删了

`fix-providers-c2c3c4.md` 那笔把主备两条路径收敛成候选链时，把「谁失败了、
接下来试谁」的运维日志一并删了。降级链在 journal 里彻底隐身，线上只能看到
最终结果，看不出中途换过几次模型。

修法：每一跳补回一行 `why_here`，说明这一跳为什么会发生（上一跳失败原因，
截断 120 字），配合 `_label()` 区分主模型与备用模型。

新增 `tests/providers/test_fallback_chain_logging.py`，主模型失败 → 备用 A
失败 → 备用 B 成功，断言三跳各自留下可读的因果。

变异验证（副本内）：

```text
M1 删掉 "trying fallback" 那行
FAILED test_each_hop_in_the_chain_says_why_it_is_here
1 failed in 1.18s

M2 把 why_here 换成固定文案 "something went wrong"
FAILED test_each_hop_in_the_chain_says_why_it_is_here
1 failed in 1.15s
```

包级测试为什么没抓到：钉住这行日志的旧测试在 `tests/agent/test_runner_fallback.py`，
而改动方只跑了 `tests/providers`。复核方（我）也只跑了 `tests/providers`。

## 2. 新测试在全量跑里收不到任何日志

新增的链路日志测试单跑绿，全量跑红，日志列表整个是空的。

根因：CLI 的 `--logs/--no-logs` 调的是 `logger.disable("nanobot")`，进程级。
`tests/channels/test_channel_plugins.py` 里几个用例真跑了 `plugins enable`，
把整个包的日志关到 pytest 进程结束，之后任何断言日志的测试都只能拿到空列表。

修法：根 `conftest.py` 加一个 autouse fixture，每个用例结束后
`logger.enable("nanobot")`。没有逐个去改那几个 CLI 用例，因为新写的 CLI
测试会不断把这个坑重新踩出来，闸门该放在 pytest 进程边界上。

变异验证：去掉该 fixture 后混跑：

```text
uv run --frozen pytest -q tests/channels/test_channel_plugins.py tests/providers/test_fallback_chain_logging.py
FAILED tests/providers/test_fallback_chain_logging.py::test_each_hop_in_the_chain_says_why_it_is_here
1 failed, 122 passed in 3.05s
```

加回后同样两个文件：`123 passed in 3.23s`。

## 3. WebUI 自动化列表用例

`test_webui_automations_route_lists_all_jobs_and_allows_user_actions` 原本用
「有 session_key 无 origin」的半绑定任务当未绑定样本，而新的原子校验已经在
新建入口拒绝这种写入。该用例要钉的是 WebUI 不给未绑定任务编造 origin，
换成完全不带会话绑定的任务即可；半绑定存量数据的降级行为由 cron 包测试覆盖。

## Deferred

`nanobot/cli/commands.py` 里 `_set_nanobot_logs` 的进程级副作用本身没动。
它对 CLI 是正确行为，本轮不在改动范围内。
