"""spawn(model=...) 让子代理跑在另一个 preset 上。

解析必须发生在工具层调用 resolver，subagent 只消费解析完的 runtime——
子代理自己去碰 provider 构造会绕开 preset 目录和 fallback 链。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from nanobot.agent.tools.registry import is_tool_error_result
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.providers.base import GenerationSettings
from nanobot.utils.llm_runtime import LLMRuntime


def _runtime(model: str = "default-model") -> LLMRuntime:
    provider = MagicMock()
    provider.generation = GenerationSettings(temperature=0.1, max_tokens=4096)
    return LLMRuntime.capture(provider, model, context_window_tokens=128_000)


class _Resolver:
    """只认得一个 preset，其余抛 KeyError——对齐真实 resolver 的失败方式。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.presets = {"fast": _runtime("claude-sonnet-5")}

    def resolve_preset(self, name: str) -> LLMRuntime:
        self.calls.append(name)
        if name not in self.presets:
            # 与 model_presets.normalize_preset_name 的真实报错文本保持一致
            raise KeyError(
                f"model_preset {name!r} not found. Available: {', '.join(self.presets)}"
            )
        return self.presets[name]


@pytest.fixture
def manager() -> MagicMock:
    m = MagicMock()
    m.get_running_count.return_value = 0
    m.max_concurrent_subagents = 4
    m.spawn = AsyncMock(return_value="spawned")
    m.run_inline = AsyncMock(return_value="inline result")
    return m


@pytest.fixture
def resolver() -> _Resolver:
    return _Resolver()


@pytest.fixture
def tool(manager: MagicMock, resolver: _Resolver) -> SpawnTool:
    ctx = SimpleNamespace(subagent_manager=manager, runtime_resolver=resolver)
    return SpawnTool.create(ctx)


@pytest.fixture
def parent_runtime() -> LLMRuntime:
    return _runtime()


@pytest.fixture(autouse=True)
def _request_ctx(parent_runtime: LLMRuntime):
    token = bind_request_context(
        RequestContext(channel="discord", chat_id="c1", runtime=parent_runtime)
    )
    yield
    reset_request_context(token)


def _runtime_arg(mock: AsyncMock) -> LLMRuntime:
    return mock.await_args.kwargs["runtime"]


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


def test_schema_exposes_model_parameter(tool: SpawnTool) -> None:
    assert "model" in tool.parameters["properties"]


def test_model_is_optional(tool: SpawnTool) -> None:
    assert "model" not in tool.parameters.get("required", [])


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


async def test_without_model_inherits_parent_runtime(
    tool: SpawnTool, manager: MagicMock, parent_runtime: LLMRuntime, resolver: _Resolver
) -> None:
    await tool.execute(task="do it")

    assert _runtime_arg(manager.spawn) is parent_runtime
    assert resolver.calls == []


async def test_model_resolves_through_resolver(
    tool: SpawnTool, manager: MagicMock, resolver: _Resolver
) -> None:
    await tool.execute(task="do it", model="fast")

    assert resolver.calls == ["fast"]
    assert _runtime_arg(manager.spawn).model == "claude-sonnet-5"


async def test_resolved_runtime_replaces_parent(
    tool: SpawnTool, manager: MagicMock, parent_runtime: LLMRuntime
) -> None:
    await tool.execute(task="do it", model="fast")

    assert _runtime_arg(manager.spawn) is not parent_runtime


async def test_unknown_preset_returns_tool_error(
    tool: SpawnTool, manager: MagicMock
) -> None:
    """未知 preset 是模型写错了参数，要回可读错误，不能把异常抛进 runner。"""
    result = await tool.execute(task="do it", model="nope")

    assert is_tool_error_result(result)
    assert "nope" in result
    manager.spawn.assert_not_awaited()


async def test_unknown_preset_lists_available(tool: SpawnTool) -> None:
    result = await tool.execute(task="do it", model="nope")

    assert "fast" in result


async def test_missing_resolver_falls_back_to_parent(
    manager: MagicMock, parent_runtime: LLMRuntime
) -> None:
    """resolver 没注入时不能崩，继承父 runtime 并忽略 model。"""
    tool = SpawnTool.create(SimpleNamespace(subagent_manager=manager, runtime_resolver=None))

    await tool.execute(task="do it", model="fast")

    assert _runtime_arg(manager.spawn) is parent_runtime


# ---------------------------------------------------------------------------
# 与既有参数共存
# ---------------------------------------------------------------------------


async def test_temperature_still_forwarded(tool: SpawnTool, manager: MagicMock) -> None:
    await tool.execute(task="do it", model="fast", temperature=0.9)

    assert manager.spawn.await_args.kwargs["temperature"] == 0.9


async def test_wait_routes_to_run_inline(tool: SpawnTool, manager: MagicMock) -> None:
    result = await tool.execute(task="do it", model="fast", wait=True)

    assert result == "inline result"
    assert _runtime_arg(manager.run_inline).model == "claude-sonnet-5"
    manager.spawn.assert_not_awaited()


async def test_label_still_forwarded(tool: SpawnTool, manager: MagicMock) -> None:
    await tool.execute(task="do it", model="fast", label="review")

    assert manager.spawn.await_args.kwargs["label"] == "review"


async def test_concurrency_limit_checked_before_resolving(
    tool: SpawnTool, manager: MagicMock, resolver: _Resolver
) -> None:
    manager.get_running_count.return_value = 4

    result = await tool.execute(task="do it", model="fast")

    assert "concurrency limit" in result
    assert resolver.calls == []


async def test_no_runtime_in_context_is_an_error(tool: SpawnTool, manager: MagicMock) -> None:
    token = bind_request_context(RequestContext(channel="discord", chat_id="c1", runtime=None))
    try:
        result = await tool.execute(task="do it", model="fast")
    finally:
        reset_request_context(token)

    assert is_tool_error_result(result)
    manager.spawn.assert_not_awaited()


# ---------------------------------------------------------------------------
# ToolContext 布线
# ---------------------------------------------------------------------------


def test_tool_context_carries_resolver() -> None:
    from nanobot.agent.tools.context import ToolContext

    ctx = ToolContext(config=None, workspace="/tmp", runtime_resolver="sentinel")

    assert ctx.runtime_resolver == "sentinel"


def test_tool_context_resolver_defaults_to_none() -> None:
    from nanobot.agent.tools.context import ToolContext

    assert ToolContext(config=None, workspace="/tmp").runtime_resolver is None
