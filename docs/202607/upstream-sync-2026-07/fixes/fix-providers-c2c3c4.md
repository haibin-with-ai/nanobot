# Providers C2/C3/C4 修复

## 范围

只改了 `nanobot/providers/anthropic_provider.py`、`nanobot/providers/fallback_provider.py`，并新增 `tests/providers/test_fallback_provider_async_factory.py`。C1 与 providers 外代码未动。

## C4：OAuth 凭据阻塞 IO

Claude Code fallback provider 的构造会进入凭据加载、文件锁及同步刷新链路。现在 `_resolve_provider` 用 `asyncio.to_thread` 调用 provider factory，阻塞工作不再占用事件循环。

保留原语义：认证重置逻辑未改，仍只替换实例引用，不关闭可能共享的旧客户端；Anthropic 流式 401 规则未改，已输出内容后不重试。

TDD 红灯：新增线程身份测试，修改前输出为 `1 failed`，断言显示 factory 线程与 caller 线程相同。

变异验证：临时把 `await asyncio.to_thread(...)` 改回同步 factory 调用，运行新测试得到 `1 failed`，失败断言为 `factory_threads[0] != caller_thread`；随后恢复实现。

## C2：拆分 `_stream_once`

既有 `test_anthropic_stream_idle.py` 已覆盖任意 SSE chunk 重置 idle timeout、thinking/text/tool delta 分发、超时及流关闭行为。重构前相关 providers 定向基线为 `46 passed`。

把 chunk 的职责拆进 `_dispatch_stream_chunk`；`_stream_once` 只负责流生命周期、逐 chunk idle timeout 和最终响应解析。重复的 delta 分支被统一，stall 计时只有一条路径。

重构后定向验证输出：`47 passed in 2.78s`。

## C3：收敛 fallback 主备路径

既有 `test_fallback_provider.py` 覆盖拒答不累计主模型故障、429/rate_limit 冷却、provider+model 隔离、备用全冷却返回主错误、请求预算最多两次 timeout，以及流式输出后的失败规则。

新增 `_Candidate`，由 `_candidates` 把主模型和备选模型归一成同一种尝试；`_try_with_fallback` 只保留一条遍历与调用路径。provider 构造、流恢复、主模型故障计数分别下沉到小函数。主模型真实错误在无可尝试备用时仍保留返回。

变异验证：临时让 refusal 也累计故障，运行 fallback 测试得到 `1 failed, 15 passed`；失败用例 `test_repeated_refusals_keep_probing_the_primary` 实际返回 `fallback ok` 而非 `back`。随后恢复拒答豁免。

## 最终验证

`uv run --frozen pytest -q tests/providers`：`932 passed in 10.62s`。

`uv run --frozen ruff check nanobot/providers/anthropic_provider.py nanobot/providers/fallback_provider.py tests/providers/test_fallback_provider_async_factory.py`：`All checks passed!`

## Deferred

无。未跑全量 pytest，按任务要求只跑 providers 测试集。
