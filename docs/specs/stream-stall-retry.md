# Spec: Stream Stall Auto-Retry

基于 `docs/plans/stream-stall-retry.md`。

## 变更文件

`nanobot/providers/anthropic_provider.py` — 仅此一个文件。

## 接口变更

### `_do_stream` 签名变更

```python
# Before
async def _do_stream(self, kwargs, idle_timeout_s, on_content_delta, on_thinking_delta) -> LLMResponse

# After
async def _do_stream(self, kwargs, idle_timeout_s, on_content_delta, on_thinking_delta) -> tuple[LLMResponse, bool]
```

返回值从 `LLMResponse` 改为 `tuple[LLMResponse, bool]`。第二个元素 `had_content` 表示是否有 text_delta 被推送过。

### `_do_stream` 内部实现

在 chunk 循环中，遇到 `text_delta` 且 `text` 非空时，将局部变量 `had_content` 设为 `True`。

```python
async def _do_stream(self, kwargs, idle_timeout_s, on_content_delta=None, on_thinking_delta=None):
    had_content = False
    async with self._client.messages.stream(**kwargs) as stream:
        if on_content_delta or on_thinking_delta:
            while True:
                try:
                    chunk = await asyncio.wait_for(stream.__anext__(), timeout=idle_timeout_s)
                except StopAsyncIteration:
                    break
                if chunk.type == "content_block_delta" and getattr(chunk.delta, "type", None) == "thinking_delta":
                    piece = getattr(chunk.delta, "thinking", None) or ""
                    if piece and on_thinking_delta:
                        await on_thinking_delta(piece)
                elif chunk.type == "content_block_delta" and getattr(chunk.delta, "type", None) == "text_delta":
                    text = getattr(chunk.delta, "text", None) or ""
                    if text:
                        had_content = True
                        if on_content_delta:
                            await on_content_delta(text)
        response = await asyncio.wait_for(stream.get_final_message(), timeout=idle_timeout_s)
    return self._parse_response(response), had_content
```

注意：`had_content` 只跟踪 `text_delta`，不跟踪 `thinking_delta`。原因：thinking delta 不会推送给用户（不会造成重复内容问题），只有 content delta 影响 fallback 决策。

### `chat_stream` 重试逻辑

```python
async def chat_stream(self, messages, tools=None, model=None, max_tokens=4096,
                      temperature=0.7, reasoning_effort=None, tool_choice=None,
                      on_content_delta=None, on_thinking_delta=None) -> LLMResponse:
    await self._ensure_valid_token()
    kwargs = self._build_kwargs(messages, tools, model, max_tokens, temperature,
                                reasoning_effort, tool_choice)
    idle_timeout_s = int(os.environ.get("NANOBOT_STREAM_IDLE_TIMEOUT_S", "90"))
    try:
        response, _ = await self._do_stream(kwargs, idle_timeout_s, on_content_delta, on_thinking_delta)
        return response
    except asyncio.TimeoutError as timeout_exc:
        had_content = getattr(timeout_exc, "_had_content", False)
        if not had_content:
            # 零内容 stall → 重试一次
            logger.warning("Stream stalled with no content after {}s, retrying once", idle_timeout_s)
            try:
                response, _ = await self._do_stream(kwargs, idle_timeout_s, on_content_delta, on_thinking_delta)
                return response
            except asyncio.TimeoutError:
                logger.error("Stream stall retry also timed out after {}s", idle_timeout_s)
                return LLMResponse(
                    content=f"Error calling LLM: stream stalled for more than {idle_timeout_s} seconds (retried once)",
                    finish_reason="error",
                    error_kind="timeout",
                    error_should_retry=True,   # 允许 fallback
                )
            except Exception as e2:
                return self._handle_error(e2)
        else:
            # 部分内容 stall → 不重试，不允许 fallback
            logger.warning("Stream stalled after partial content ({}s), not retrying", idle_timeout_s)
            return LLMResponse(
                content=f"Error calling LLM: stream stalled for more than {idle_timeout_s} seconds",
                finish_reason="error",
                error_kind="timeout",
                error_should_retry=False,  # 已有部分内容推送，禁止 fallback
            )
    except Exception as e:
        # auth error 重试逻辑保持不变
        ...
```

**问题**：`asyncio.TimeoutError` 从 `asyncio.wait_for` 抛出时不携带 `had_content` 信息。需要用另一种方式传递。

**方案**：不在 exception 上附属性。改为在 `_do_stream` 中用 wrapper 捕获 TimeoutError，附加 `had_content` 后重新抛出自定义异常。

### 自定义异常

```python
class _StreamStall(Exception):
    """Internal: stream idle timeout with content tracking."""
    def __init__(self, idle_timeout_s: int, had_content: bool):
        self.idle_timeout_s = idle_timeout_s
        self.had_content = had_content
        super().__init__(f"stream stalled for {idle_timeout_s}s (had_content={had_content})")
```

### `_do_stream` 改为捕获 TimeoutError 并抛 `_StreamStall`

```python
async def _do_stream(self, kwargs, idle_timeout_s, on_content_delta=None, on_thinking_delta=None):
    had_content = False
    try:
        async with self._client.messages.stream(**kwargs) as stream:
            if on_content_delta or on_thinking_delta:
                while True:
                    try:
                        chunk = await asyncio.wait_for(stream.__anext__(), timeout=idle_timeout_s)
                    except StopAsyncIteration:
                        break
                    # ... chunk 处理同上，text_delta 时设 had_content = True ...
            response = await asyncio.wait_for(stream.get_final_message(), timeout=idle_timeout_s)
        return self._parse_response(response)
    except asyncio.TimeoutError:
        raise _StreamStall(idle_timeout_s, had_content)
```

返回值恢复为 `LLMResponse`（不再是 tuple），stall 信息通过异常传递。

### `chat_stream` 最终版

```python
async def chat_stream(self, ...) -> LLMResponse:
    await self._ensure_valid_token()
    kwargs = self._build_kwargs(...)
    idle_timeout_s = int(os.environ.get("NANOBOT_STREAM_IDLE_TIMEOUT_S", "90"))
    try:
        return await self._do_stream(kwargs, idle_timeout_s, on_content_delta, on_thinking_delta)
    except _StreamStall as stall:
        if not stall.had_content:
            logger.warning("Stream stalled with no content after {}s, retrying once", stall.idle_timeout_s)
            try:
                return await self._do_stream(kwargs, idle_timeout_s, on_content_delta, on_thinking_delta)
            except _StreamStall:
                logger.error("Stream stall retry also timed out after {}s", idle_timeout_s)
                return LLMResponse(
                    content=f"Error calling LLM: stream stalled for more than {idle_timeout_s} seconds (retried once)",
                    finish_reason="error",
                    error_kind="timeout",
                    error_should_retry=True,
                )
            except Exception as e2:
                return self._handle_error(e2)
        else:
            logger.warning("Stream stalled after partial content ({}s), not retrying", idle_timeout_s)
            return LLMResponse(
                content=f"Error calling LLM: stream stalled for more than {idle_timeout_s} seconds",
                finish_reason="error",
                error_kind="timeout",
                error_should_retry=False,
            )
    except Exception as e:
        if self._is_auth_error(e) and self._reload_token_from_store():
            logger.info("Retrying chat_stream after reloading OAuth token from store")
            kwargs = self._build_kwargs(...)
            try:
                return await self._do_stream(kwargs, idle_timeout_s, on_content_delta, on_thinking_delta)
            except Exception as e2:
                return self._handle_error(e2)
        return self._handle_error(e)
```

## 行为矩阵

| 场景 | 第一次结果 | 动作 | 最终 error_should_retry |
|---|---|---|---|
| 零内容 stall，重试成功 | TimeoutError (had_content=False) | 重试 → 成功 | N/A（正常返回） |
| 零内容 stall，重试也 stall | TimeoutError × 2 | 重试 → 再次超时 | `True`（允许 fallback） |
| 零内容 stall，重试其他异常 | TimeoutError → Exception | 重试 → _handle_error | 由 _handle_error 决定 |
| 部分内容 stall | TimeoutError (had_content=True) | 不重试 | `False`（禁止 fallback） |
| 正常完成 | 无异常 | 直接返回 | N/A |

## 测试用例

文件：`tests/providers/test_stream_stall_retry.py`

1. **test_no_content_stall_retry_succeeds** — 第一次 _do_stream 抛 _StreamStall(had_content=False)，第二次正常返回 → 验证最终返回正常 response
2. **test_no_content_stall_retry_also_stalls** — 两次都抛 _StreamStall(had_content=False) → 验证返回 error，error_should_retry=True
3. **test_partial_content_stall_no_retry** — 抛 _StreamStall(had_content=True) → 验证返回 error，error_should_retry=False，且 _do_stream 只被调用一次
4. **test_no_content_stall_retry_other_error** — 第一次 stall，第二次抛其他 Exception → 验证走 _handle_error
5. **test_normal_stream_no_retry** — 正常返回 → 验证不触发任何重试逻辑
