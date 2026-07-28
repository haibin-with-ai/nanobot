# Plan: Stream Stall Auto-Retry & Fallback

## 问题

Anthropic API 间歇性出现 stream stall——连接建立后长时间（>90s）不吐任何 SSE chunk。
当前行为：超时后直接返回 error，不重试、不 fallback。

两种场景：
1. **零内容 stall**：连接建立后一个 token 都没吐就卡死。应该重试/fallback。
2. **部分内容 stall**：已吐了一些 content delta，中途卡死。已有 `has_streamed` 逻辑阻止 fallback（避免重复推送），但也没有重试。

## 现状分析

- `anthropic_provider.chat_stream()` 捕获 `asyncio.TimeoutError`，直接返回 `LLMResponse(finish_reason="error", error_kind="timeout")`
- `fallback_provider._try_with_fallback()` 有 `has_streamed` 跟踪——如果已推送过 content delta，直接 `error_should_retry = False`，不 fallback
- `base.py._is_transient_response()` 认为 `error_kind="timeout"` 是 transient → `_run_with_retry` 会在 base 层重试
- 但 base 层重试等于整个 fallback_provider 级别重试，不是 anthropic_provider 内部重试
- stream stall 是 Anthropic 侧问题，通常是瞬时的，同模型重试一次大概率成功

## 改动方案

### 改动 1：anthropic_provider 内部 stream stall 重试

在 `chat_stream()` 中，捕获 `asyncio.TimeoutError` 后：
- 如果 `on_content_delta` **从未被调用过**（零内容 stall）→ **重试一次**（同参数）
- 如果已有 content delta 推送 → 不重试，直接返回 error（和当前一样）

实现：在 `_do_stream()` 中跟踪是否有 content delta 产生，通过 wrapper 或返回值传递。

### 改动 2：stream stall error 标记 `error_should_retry`

- 零内容 stall（provider 内部已重试过一次仍失败）→ `error_should_retry=True`，允许 fallback
- 部分内容 stall → `error_should_retry=False`（已有逻辑，保持不变）

这样 fallback_provider 可以正确决策。

### 不改

- `fallback_provider` 逻辑不改——它已经正确地根据 `has_streamed` 和 `error_should_retry` 判断
- `base.py` retry 逻辑不改——`error_kind="timeout"` 已在 `_TRANSIENT_ERROR_KINDS` 中

## 改动范围

| 文件 | 改动 |
|---|---|
| `anthropic_provider.py` | `_do_stream` 返回是否有 content；`chat_stream` 加重试逻辑 |

单文件改动，约 30 行。

## 风险

- 重试增加一次 API 调用延迟（最多 +90s），但只在 stall 时触发，频率极低
- 部分内容 stall 不重试，避免用户看到重复内容

## 验证

- 单元测试：mock stream stall（零内容 + 部分内容两种场景）
- 集成验证：观察日志确认重试行为
