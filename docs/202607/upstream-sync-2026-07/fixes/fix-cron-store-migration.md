# Cron 存储迁移生产数据回归修复

## 问题

生产存储里存在两类旧数据，新基座加载后行为回归：

1. `payload.kind="agentTurn"` 未在解码边界归一化，绕过迁移与校验，旧投递字段被保留，但运行时已没有旧投递路径。
2. 同时含 `sessionKey`、`model`、`channel/to` 的旧任务被迁成 bound，再因 bound payload 禁止指定 model 而被禁用。

## 修复

`CronPayload.from_store_dict` 统一规范化 kind：

- `agentTurn` 和 `agent_turn` 解码为 `agent_turn`
- `systemEvent` 和 `system_event` 解码为 `system_event`
- 未知值抛出明确的 `ValueError`

旧 agent-turn 迁移采用统一规则：任务级 `model` 意味着 unbound。迁移时清掉孤立的 session/origin binding，保留 model，并清空 legacy 投递字段。无 model 的旧任务继续通过 `channel/to` 补齐 origin，迁为 bound。

创建、更新期原有校验未改：完整 binding 加 model 仍拒绝，残缺 binding 仍拒绝。运行时的 unbound 独立 session 与 bound-turn 路径未改。

## 回归测试

新增 `tests/cron/test_cron_store_migration.py`，使用 `tmp_path` 写入三种生产数据形态，覆盖：

- 驼峰 kind 的 legacy delivery 迁移
- 带 model 与孤立 sessionKey 的 legacy payload 保持 unbound
- 无 model 的 legacy payload 迁为 bound
- 所有任务加载后保持 enabled
- legacy delivery 字段被清空
- kind 别名归一化及未知值拒绝

## TDD 与变异验证

初始红灯：`2 failed`，分别暴露驼峰 kind 未归一化，以及未知 kind 未拒绝。

变异 1：删除 `agentTurn` 映射后运行定向测试，真实输出为 `2 failed`，加载测试因未知 kind 无法读取存储，codec 测试直接失败。

变异 2：删除 model 分支、强制迁为 bound 后运行定向测试，真实输出为 `1 failed, 1 passed`；日志明确显示 `model-session` 因 `bound cron job cannot specify a model` 被禁用。

## 验证

`uv run --frozen pytest -q tests/cron`

结果：`143 passed in 2.84s`。
