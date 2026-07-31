# agent 包 Important 收尾（I3 / I5 / I6）

承接 `fix-agent-c1.md` 与 `fix-agent-stall.md`，把 `reviews/coding-review-agent.md` 剩下的
Important 条目对完。提交：`c2272c50`、`158da304`。

## I3 已在上一轮修掉，本轮只做复核

审查说 `_append_stall_notice` 静默失效而调用点照样打日志。复核当前代码：函数签名已经是
`-> bool`，末尾 `return True`，无法追加时 `return False`；调用点按真实结果分流，
失败时打的是 "Stall notice could not be attached"。这一项在 `14a5f4f2` 一并解决，
审查报告写于修复之前，属于过期条目。本轮无改动。

## I5 读文件去重状态交给 FileStates

`filesystem.py` 里三处 `entry.can_dedup = False` 全是死代码：`FileStates.record_read`
新建 ReadState 替换字典槽位，不做就地修改，赋值落在已经不在字典里的旧对象上。
实际行为与注释宣称的相反，一次 `force=True` 的读之后，下一次 `force=False` 仍能命中去重。

`record_read` 新增 keyword-only 的 `dedupable: bool = True`，调用点改成
`record_read(fp, offset=offset, limit=limit, dedupable=not force)`，三处对 entry 的赋值删除。
docstring 里写清「调用方不要自己改 ReadState，每次 record_read 都换掉槽位」。

测试 `tests/agent/test_file_dedup_state.py`（3 例）。变异（让 `record_read` 忽略 `dedupable`，
恒定写 `can_dedup=True`）：`2 failed, 1 passed`，报 `assert True is False` 与去重桩串命中。

## I6 cron add 的 model preset 当场校验

`cron.py` 的 `_add_job` 把 model 当裸字符串存进 payload，preset 名拼错时 `cron add`
返回成功，任务要到真正触发那一刻才抛异常。spawn 早就在参数校验阶段 resolve，
认不出来立刻回 "unknown preset, available: ..."。

新增 `nanobot/agent/tools/presets.py`：`UnknownPresetError` 与 `resolve_preset(resolver, model, default)`。
spawn 的 `_resolve_runtime` 缩成一行调用，私有异常类删除；`CronTool` 接 `runtime_resolver`
（由 `create` 从 ctx 取），`_add_job` 在建任务前先 resolve，失败直接返回错误文案。
两处共用同一份报错文本，不再各拼一遍。

测试 `tests/agent/test_cron_tool_model_validation.py`（3 例，含「没有 resolver 时保持宽松」
这条与 spawn 对齐的行为）。红：`2 failed, 1 passed`，未知 preset 时
`assert 'available' in "Created job 'nightly'..."` 失败，且 `add_job` 仍被调用。
绿：`3 passed in 0.07s`。变异（删掉校验）：`2 failed, 1 passed`。

## 收口

`uv run --frozen pytest -q tests/agent` → `1525 passed`；
`uv run --frozen ruff check nanobot/agent` → `All checks passed!`；
全量 `uv run --frozen pytest -q` → `5834 passed, 15 skipped in 135.48s`。

## Deferred

- I4：`_run_agent_loop` 的 5 元组返回值改 dataclass，本轮不做，理由见 `fix-agent-stall.md`。
