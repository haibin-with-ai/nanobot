# Providers C1 修复记录

## 目标

修复 Anthropic provider 无回调流式路径把 idle timeout 错当整段生成墙钟的问题。

## 根因

`_stream_once` 只在至少一个 delta 回调存在时消费流事件。三个回调均为 `None` 时，它直接用 `idle_timeout_s` 包住 `get_final_message()`，导致持续产出 chunk 的长生成仍会在固定墙钟时间后被误杀。

## 改动

`nanobot/providers/anthropic_provider.py` 现在无论是否注册回调，都会迭代流事件，并对每次读取单独应用 `idle_timeout_s`。回调仅控制 delta 转发，不再控制流消费。事件流耗尽后直接读取已完成的 final message，不再给整段生成套 idle timeout。

保留对不实现 `__anext__` 的既有测试替身兼容路径；真实 Anthropic 流仍按 chunk 消费。

未改超时数值，未改身份注入、冷却隔离、拒答熔断和已输出内容后的 401 重试语义。

## TDD 证据

修复前运行：

`uv run --frozen pytest -q tests/providers/test_anthropic_stream_idle.py`

结果：`1 failed, 3 passed in 0.31s`。失败断言为 `assert 0 == 2`，证明无回调路径没有消费两个 chunk。

修复后定向测试：`4 passed in 2.09s`。

## 变异验证

故意把消费循环条件改回依赖任一回调存在，再运行同一测试文件。

结果：`1 failed, 3 passed in 0.30s`，同一断言再次得到 `assert 0 == 2`。随后恢复无条件消费实现。

## Provider 回归

`uv run --frozen pytest -q tests/providers`

结果：`931 passed in 11.78s`。
