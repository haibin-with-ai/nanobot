# Cron 两条 Critical 修复

## 修法

1. bound job 与 `model` 的组合在存储边界直接拒绝，报错 `bound cron job cannot specify a model`。执行入口也保留同样校验，避免绕过 service 构造的任务静默忽略模型。未绑定任务仍按每次触发创建独立 session；完整绑定任务仍走原 bound-turn 路径。
2. `session_key`、`origin_channel`、`origin_chat_id` 被视为原子 binding。三者部分存在时，新增或更新直接拒绝；加载到历史残缺数据时禁用任务、清空下次运行时间并记录错误；执行入口也拒绝残缺 binding，不再降级到 unbound session。

## TDD 红灯

命令：`uv run --frozen pytest -q tests/cron/test_bound_runner.py tests/cron/test_cron_model_field.py`

真实输出：`5 failed, 31 passed in 0.67s`。失败覆盖 bound model 被忽略、残缺 binding 被降级、存储新增未拒绝、历史残缺数据未禁用。

## 定向绿灯

命令：`uv run --frozen pytest -q tests/cron/test_bound_runner.py tests/cron/test_cron_model_field.py`

真实输出：`33 passed in 0.65s`。

## 变异验证

Critical 1：临时把 bound model 校验改成直接返回。

命令：`uv run --frozen pytest -q tests/cron/test_cron_model_field.py`

真实输出：`1 failed, 14 passed in 0.54s`；`test_add_rejects_model_on_bound_job` 报 `DID NOT RAISE ValueError`。随后恢复实现。

Critical 2：临时把残缺 binding 校验改成直接返回。

命令：`uv run --frozen pytest -q tests/cron/test_cron_model_field.py`

真实输出：`2 failed, 13 passed in 0.71s`；新增残缺 binding 未抛错，加载残缺 binding 后任务仍 enabled。随后恢复实现。

## 最终验证

命令：`uv run --frozen pytest -q tests/cron`

真实输出：`141 passed in 2.78s`。

命令：`uv run --frozen ruff check nanobot/cron/bound_runner.py nanobot/cron/service.py tests/cron/test_bound_runner.py tests/cron/test_cron_model_field.py tests/cron/test_cron_service.py`

真实输出：`All checks passed!`。
