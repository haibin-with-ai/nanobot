# Spec — Command Rewrite Hook / rtk Migration Replay

> 历史归档，非当前实现。基座为 ba38f908（2026-05-18），与 upstream/main=3f808d0a 之后的结构不再对应。

## 1. 概述

将 fork 中的命令改写能力（`rtk` 集成）以 Hook 形式回放进 upstream，而不是塞进 `ExecTool` 或 `ShellTool`。

核心原则：**横切逻辑不住在工具里**。命令改写是对 `exec` 工具调用参数的转换，属于跨切关注点，应当由 `AgentHook.before_execute_tools()` 拦截并修改，而非让每个 shell-like 工具各自实现。

回放目标版本：upstream/main @ ba38f908（当前 worktree 基线）。

---

## 2. 行为需求

1. 当配置开启时，任何发往 `exec` 工具的 `arguments["command"]` 在被实际执行前，先经 `rtk` 二进制改写。
2. `rtk` 通过子进程调用：stdin 写原始命令，stdout 读改写结果。
3. `rtk` 退出码语义：
   - `0`：stdout 为改写后的命令，替换原命令。
   - `3`：stdout 为改写后的命令，替换原命令（fork 遗留语义，spec 兼容）。
   - 其他非 `0/3`：保留原始命令，不做修改。
4. 超时、二进制缺失、异常：一律保留原始命令，不抛错（fail-safe）。
5. `verbose=True` 时，若发生实际改写，记录日志（loguru debug）。
6. `path_append`（来自 `ExecToolConfig`）需附加到 `rtk` 子进程的 `PATH` 环境变量。
7. Hook 仅修改 `exec` 工具调用；其他工具即使含 `command` 键也不触碰。
8. 主 AgentLoop 和 SubagentManager 中的子 agent 运行均需注入同一 rewrite hook。
9. `ExecTool` 和 `ExecToolConfig` 保持零 `rtk` 感知。

---

## 3. 架构分析

### 3.1 AgentHook 接口与调用协议

文件：`nanobot/agent/hook.py`

```python
class AgentHook:
    async def before_execute_tools(self, context: AgentHookContext) -> None: ...

@dataclass(slots=True)
class AgentHookContext:
    iteration: int
    messages: list[dict[str, Any]]
    response: LLMResponse | None = None
    usage: dict[str, int] = ...
    tool_calls: list[ToolCallRequest] = ...   # mutable, 就地修改
    tool_results: list[Any] = ...
    tool_events: list[dict[str, str]] = ...
    ...

class CompositeHook(AgentHook):
    def __init__(self, hooks: list[AgentHook]) -> None: ...
    # 顺序遍历 hooks，对每个 hook 调用同名方法；单 hook 异常被捕获并隔离
```

关键约束：
- `before_execute_tools` 是 `async` 方法，允许 IO（子进程）。
- `context.tool_calls` 是 `list[ToolCallRequest]`，每个 `ToolCallRequest.arguments` 是 `dict[str, Any]`，可就地修改。
- `CompositeHook` 已保证遍历顺序和异常隔离。无需重新发明组合逻辑。

### 3.2 Runner 中的注入点

文件：`nanobot/agent/runner.py`

在 `AgentRunner._run_agent_loop()` 中，工具执行前精确调用点：

```python
await hook.before_execute_tools(context)
results, new_events, fatal_error = await self._execute_tools(
    spec, response.tool_calls, ...
)
```

这意味着：hook 对 `context.tool_calls` 的修改会直接作用到随后传入 `_execute_tools` 的 `response.tool_calls`（因为 `context.tool_calls = list(response.tool_calls)` 发生在 hook 之前）。**但需注意**：runner 中实际传给 `_execute_tools` 的是 `response.tool_calls`，而 `context.tool_calls` 是它的浅拷贝列表。由于 `ToolCallRequest.arguments` 是可变 dict，就地修改会同步到 runner 实际使用的参数上。

验证方式：读取 `runner.py` line 332 附近，确认 `await hook.before_execute_tools(context)` 紧邻 `_execute_tools` 调用，且中间无 `response.tool_calls` 重新赋值。

### 3.3 AgentLoop 的 hook 组装

文件：`nanobot/agent/loop.py`

- `AgentLoop.__init__(..., hooks: list[AgentHook] | None = None)` 将外部 hooks 存入 `self._extra_hooks`。
- `_run_agent_loop()` 中构造 `AgentRunSpec` 时：
  ```python
  hook: AgentHook = (
      CompositeHook([loop_hook] + self._extra_hooks) if self._extra_hooks else loop_hook
  )
  ```
  随后 `AgentRunSpec(..., hook=hook, ...)` 传给 `AgentRunner.run()`。
- `from_config()` 目前不传入 `hooks` 参数。Pack4 需要让 `from_config()` 读取 `config.tools.command_rewrite`，决定是否实例化 `CommandRewriteHook` 并通过 `hooks=[...]` 传入 `AgentLoop.__init__`。

### 3.4 SubagentManager 的 hook 传播

文件：`nanobot/agent/subagent.py`

当前 `SubagentManager.__init__()` 签名不含 hooks。`_run_subagent_inner()` 中：

```python
result = await self.runner.run(AgentRunSpec(
    ...,
    hook=_SubagentHook(task_id, status),
    ...
))
```

Pack4 需要：
1. `SubagentManager.__init__` 新增可选参数 `extra_hooks: list[AgentHook] | None = None`。
2. `_run_subagent_inner` 中若 `extra_hooks` 存在，用 `CompositeHook([_SubagentHook(...)] + extra_hooks)` 代替裸的 `_SubagentHook`。
3. `AgentLoop.__init__` 在构造 `SubagentManager` 时，把 `self._extra_hooks`（或其中与 command rewrite 相关的 hook）传过去。

### 3.5 ExecTool 基线

文件：`nanobot/agent/tools/shell.py`

- `ExecTool.name == "exec"`。
- `ExecToolConfig` 不含任何 `rtk` 字段。
- `execute()` 接收 `command: str`，在内部用 `asyncio.create_subprocess_shell`（或 `exec`）执行。
- **Spec 保证**：不改 `shell.py` 任何一行。Hook 层修改后的 `command` 自然流进 `ExecTool.execute()`。

### 3.6 Config 基线

文件：`nanobot/config/schema.py`

- `Base` 模型启用 `alias_generator=to_camel, populate_by_name=True`，支持 camelCase 别名。
- `ToolsConfig` 当前字段：`web`, `exec`, `my`, `image_generation`, `restrict_to_workspace`, `mcp_servers`, `ssrf_whitelist`。
- 需新增 `command_rewrite: CommandRewriteConfig`。
- 延迟初始化用 `_lazy_default("module.path:ClassName")`。新字段遵循同一模式。

---

## 4. 技术方案

### 4.1 CommandRewriteConfig 设计

新增配置类，放在 `nanobot/config/schema.py`。

```python
class CommandRewriteConfig(Base):
    """Configuration for command-rewrite via rtk hook."""

    enabled: bool = False
    verbose: bool = False
    timeout: float = 5.0
    binary_path: str = "rtk"
```

字段说明：
- `enabled`：总开关。`False` 时 `CommandRewriteHook` 不被实例化，也不注入 loop。
- `verbose`：为 `True` 且实际发生改写时，通过 `logger.debug` 输出原始命令与改写后命令。
- `timeout`：`rtk` 子进程等待超时秒数。浮点数以支持亚秒级精度。
- `binary_path`：`rtk` 可执行文件路径或名称（在 PATH 中解析）。默认 `"rtk"`。

`ToolsConfig` 新增字段：
```python
class ToolsConfig(Base):
    ...
    command_rewrite: CommandRewriteConfig = Field(
        default_factory=lambda: _lazy_default("nanobot.config.schema:CommandRewriteConfig")
    )
```

由于 `Base` 的 `populate_by_name=True`，YAML/JSON 中可同时支持 `commandRewrite` 和 `command_rewrite`。

### 4.2 CommandRewriteHook 类设计

新增文件：`nanobot/agent/hooks/rewrite.py`

```python
from __future__ import annotations

import asyncio
import os
import shutil
import traceback
from typing import Any

from loguru import logger

from nanobot.agent.hook import AgentHook, AgentHookContext


class CommandRewriteHook(AgentHook):
    """Rewrite exec tool commands via an external rtk binary.

    Implements ``before_execute_tools`` to intercept ``exec`` tool calls
    and mutate ``arguments["command"]`` in place before the runner
    dispatches to ``ExecTool.execute()``.
    """

    __slots__ = ("_enabled", "_verbose", "_timeout", "_binary_path", "_path_append")

    def __init__(
        self,
        *,
        enabled: bool = True,
        verbose: bool = False,
        timeout: float = 5.0,
        binary_path: str = "rtk",
        path_append: str = "",
    ) -> None:
        super().__init__()
        self._enabled = enabled
        self._verbose = verbose
        self._timeout = timeout
        self._binary_path = binary_path
        self._path_append = path_append

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        if not self._enabled:
            return
        for tc in context.tool_calls:
            try:
                if tc.name != "exec":
                    continue
                raw = tc.arguments.get("command")
                if not isinstance(raw, str) or not raw.strip():
                    continue
                rewritten = await self._rewrite(raw)
                if rewritten is not None and rewritten != raw:
                    tc.arguments["command"] = rewritten
                    if self._verbose:
                        logger.debug(
                            "[command-rewrite] {} -> {}",
                            raw.strip().splitlines()[0][:80],
                            rewritten.strip().splitlines()[0][:80],
                        )
            except Exception:
                logger.debug("CommandRewriteHook: unexpected error for tool {}: {}", tc.name, traceback.format_exc())

    @staticmethod
    async def _ensure_killed(proc: asyncio.subprocess.Process) -> None:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass

    async def _rewrite(self, command: str) -> str | None:
        """Call rtk binary. Return rewritten command or None to keep original."""
        # 1. locate binary (fast path: assume in PATH; fallback shutil.which)
        binary = self._binary_path
        if os.name != "nt" and os.sep not in binary:
            # On POSIX, if no path separator, try which for clearer error messages
            resolved = shutil.which(binary)
            if resolved is None:
                return None
            binary = resolved

        env = dict(os.environ)
        if self._path_append:
            env["PATH"] = env.get("PATH", "") + os.pathsep + self._path_append

        try:
            proc = await asyncio.create_subprocess_exec(
                binary,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except Exception:
            return None

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=command.encode("utf-8")),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            await self._ensure_killed(proc)
            return None
        except Exception:
            await self._ensure_killed(proc)
            return None

        if proc.returncode not in (0, 3):
            return None

        try:
            stdout = stdout_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return None

        rewritten = stdout.rstrip("\n\r")
        if not rewritten:
            return None
        return rewritten
```

设计要点：
- **Fail-safe 包裹**：`_rewrite` 内所有 IO 和进程操作被 broad exception 捕获。任何异常都返回 `None`，上层保留原命令。
- **不导入 ExecTool**：Hook 只依赖 `ToolCallRequest` 的公开结构（`name`, `arguments`），不依赖任何 tool 实现。
- **精确匹配**：仅当 `tc.name == "exec"` 且 `arguments["command"]` 为 string 且非空时才处理。避免误改自定义工具或 MCP 工具。
- **PATH 拼接**：`_path_append` 来自 `ExecToolConfig.path_append`。虽然 config 未为 command rewrite 单独开 path 字段，但 fork 行为要求 rtk 能访问 exec 的 path_append。Hook 在构造时接收该值。

### 4.3 rtk 子进程调用协议

| 项目 | 规范 |
|------|------|
| 调用方式 | `asyncio.create_subprocess_exec(binary, stdin=PIPE, stdout=PIPE, stderr=PIPE)` |
| 输入 | 原始命令字符串，以 UTF-8 写入 stdin，含末尾换行由 rtk 自行处理 |
| 输出 | stdout 全部内容去尾换行作为改写后命令 |
| 退出码 0 | 改写成功，stdout 非空则替换 |
| 退出码 3 | 同上（fork 兼容语义） |
| 退出码 1/2/其他 | 保留原始命令 |
| 超时 | `asyncio.wait_for(..., timeout=self._timeout)`；超时后 `proc.kill()` |
| stderr | 忽略，仅用于调试时不影响决策 |
| 环境变量 | 继承 os.environ；若 `path_append` 非空，追加到 PATH |

**注意**：`shutil.which` 调用在主线程同步执行，但仅涉及 PATH 扫描，不启动网络/磁盘重操作，可接受。若未来 upstream 要求严格 async-only，可改为在子进程启动失败后返回 None，不预检存在性。

### 4.4 Hook 精确匹配策略

匹配条件（需同时满足）：
1. `tool_call.name == "exec"`
2. `"command" in tool_call.arguments`
3. `isinstance(tool_call.arguments["command"], str)`
4. `tool_call.arguments["command"].strip() != ""`

为什么不用 `arguments.get("command") is not None`？因为 MCP 或其他工具可能传入 `command: None` 或 list。严格类型检查防止误改。

为什么不匹配 `"shell"` 或其他名字？因为 upstream 基线中只有 `ExecTool.name == "exec"`。如果未来新增其他 shell-like 工具，应扩展配置白名单，而非在 hook 中硬编码更多名称。

### 4.5 Main loop 与 subagent 的 hook 共享

#### 4.5.1 主 Loop

修改 `AgentLoop.from_config()`（`nanobot/agent/loop.py`）：

```python
extra_hooks: list[AgentHook] = []
if config.tools.command_rewrite.enabled:
    from nanobot.agent.hooks.rewrite import CommandRewriteHook
    extra_hooks.append(CommandRewriteHook(
        enabled=True,
        verbose=config.tools.command_rewrite.verbose,
        timeout=config.tools.command_rewrite.timeout,
        binary_path=config.tools.command_rewrite.binary_path,
        path_append=config.tools.exec.path_append,
    ))
```

然后在 `return cls(...)` 调用中注入 `hooks=extra_hooks`（仅当非空时注入，保持与现有代码风格一致）。

#### 4.5.2 SubagentManager

修改 `SubagentManager.__init__`（`nanobot/agent/subagent.py`）新增参数：

```python
extra_hooks: list[AgentHook] | None = None,
```

存储为 `self._extra_hooks = list(extra_hooks or [])`。

修改 `_run_subagent_inner`：

```python
subagent_hook = _SubagentHook(task_id, status)
hook: AgentHook = (
    CompositeHook([subagent_hook] + self._extra_hooks)
    if self._extra_hooks
    else subagent_hook
)
result = await self.runner.run(AgentRunSpec(..., hook=hook, ...))
```

修改 `AgentLoop.__init__` 在构造 `SubagentManager` 时传入 `extra_hooks=self._extra_hooks`。

#### 4.5.3 配置 → Hook 实例化 → 注入的数据流

```
config.toml
  └─ tools.command_rewrite.enabled=True
      └─ nanobot/config/schema.py: CommandRewriteConfig
          └─ AgentLoop.from_config()
              ├─ 实例化 CommandRewriteHook(path_append=config.tools.exec.path_append)
              ├─ 通过 hooks=[...] 传入 AgentLoop.__init__
              │   └─ AgentLoop._extra_hooks
              │       └─ _run_agent_loop: CompositeHook([loop_hook] + _extra_hooks)
              │           └─ AgentRunSpec.hook → AgentRunner.run()
              │               └─ before_execute_tools() 修改 context.tool_calls
              └─ SubagentManager(extra_hooks=self._extra_hooks)
                  └─ CompositeHook([_SubagentHook] + extra_hooks)
                      └─ 子 agent runner 同样获得 rewrite 能力
```

---

## 5. 最小侵入评估

| 改动项 | 类型 | 文件 | 说明 |
|--------|------|------|------|
| `CommandRewriteConfig` | 新增类 | `nanobot/config/schema.py` | 纯新增，不修改现有字段 |
| `ToolsConfig.command_rewrite` | 修改字段 | `nanobot/config/schema.py` | 仅新增一行字段声明 |
| `CommandRewriteHook` | 新增类 | `nanobot/agent/hooks/rewrite.py` | 全新文件 |
| `nanobot/agent/hooks/__init__.py` | 新增文件 | `nanobot/agent/hooks/__init__.py` | 创建 hooks 包，导出 `CommandRewriteHook` |
| `AgentLoop.from_config()` | 修改方法 | `nanobot/agent/loop.py` | 增加条件实例化 hook 并传入 `hooks=` |
| `AgentLoop.__init__` 中 `SubagentManager(...)` | 修改调用 | `nanobot/agent/loop.py` | 新增 `extra_hooks=self._extra_hooks` 关键字参数 |
| `SubagentManager.__init__` | 修改签名 | `nanobot/agent/subagent.py` | 新增 `extra_hooks` 参数及存储 |
| `SubagentManager._run_subagent_inner` | 修改方法 | `nanobot/agent/subagent.py` | 用 `CompositeHook` 组合 `_SubagentHook` 与 `extra_hooks` |

**未改动文件（关键承诺）**：
- `nanobot/agent/tools/shell.py` — `ExecTool` 零变更。
- `nanobot/agent/runner.py` — runner 已存在 `before_execute_tools` 调用点，无需修改。
- `nanobot/agent/hook.py` — `AgentHook` / `CompositeHook` / `AgentHookContext` 接口零变更。

---

## 6. 测试方案

### 6.1 Hook 单元测试

文件：`tests/agent/hooks/test_command_rewrite.py`（新建 `tests/agent/hooks/` 目录）

覆盖矩阵：

| # | 场景 | 断言 |
|---|------|------|
| 1 | `enabled=False` | 任何 tool call 不被修改 |
| 2 | 非 `exec` 工具（如 `list_dir`）含 `command` 参数 | 不被修改 |
| 3 | `exec` 工具但 `command` 为空字符串 | 不被修改 |
| 4 | `exec` 工具但 `command` 为 `None` / list | 不被修改 |
| 5 | `rtk` 退出码 `0`，stdout 非空 | 命令被替换为 stdout |
| 6 | `rtk` 退出码 `3`，stdout 非空 | 命令被替换为 stdout |
| 7 | `rtk` 退出码 `1` | 保留原始命令 |
| 8 | `rtk` 退出码 `2` | 保留原始命令 |
| 9 | `rtk` 进程启动失败（binary 不存在） | 保留原始命令 |
| 10 | `rtk` 超时 | 保留原始命令；确认 `proc.kill()` 被调用 |
| 11 | `rtk` 异常（如 OSError） | 保留原始命令 |
| 12 | `verbose=True` 且实际改写 | 记录 debug 日志 |
| 13 | `path_append` 非空 | 子进程 env PATH 包含追加路径 |
| 14 | 无 tool calls | 不崩溃 |

测试手法：
- 用 `unittest.mock.patch("asyncio.create_subprocess_exec")` mock 子进程，返回一个可控的 `AsyncMock` 对象，其 `communicate()` 协程返回 `(stdout, stderr)`，`returncode` 可配置。
- 不需要真实 `rtk` 二进制。
- 每个测试直接构造 `CommandRewriteHook` 和 `AgentHookContext`，调用 `await hook.before_execute_tools(context)`，然后断言 `context.tool_calls[i].arguments["command"]`。

### 6.2 集成测试：Hook 在 tool call 流程中的位置

文件：`tests/agent/test_loop_rewrite_hook_injection.py`（新建）

测试目标：确认 `AgentLoop` 在 `from_config()` 和直接构造两种路径下，都能把 `CommandRewriteHook` 组装进 `AgentRunSpec.hook`，并最终在 runner 的 `before_execute_tools` 阶段生效。

最小复现（无需真实 provider）：
- 用 `MagicMock` 构造 provider、bus。
- 构造 `AgentLoop(..., tools_config=ToolsConfig(...), hooks=[CommandRewriteHook(...)])`。
- mock `loop.tools.execute` 和 `loop.provider.chat_with_retry`，让 LLM 返回一个含 `exec` tool call 的 `LLMResponse`。
- 运行 `_run_agent_loop`（或更上层的 `_on_message`，视测试粒度而定）。
- 断言 `loop.tools.execute` 收到的参数中 `command` 已被改写。

另一种更轻量的方式：直接 mock `AgentRunner.run`，捕获传入的 `AgentRunSpec`，检查 `spec.hook` 是 `CompositeHook` 且包含 `CommandRewriteHook` 实例。

### 6.3 CompositeHook 顺序与突变测试

文件：补充至 `tests/agent/test_hook_composite.py`（若 upstream 当前无 `before_execute_tools` 顺序测试）

上游现有 `test_composite_fans_out_before_iteration`、`test_composite_error_isolation` 等，但**未覆盖 `before_execute_tools` 的顺序和共享 context 突变**。

需补充测试：

```python
@pytest.mark.asyncio
async def test_composite_before_execute_tools_ordering_and_mutation():
    seen = []

    class H1(AgentHook):
        async def before_execute_tools(self, context):
            context.tool_calls[0].arguments["command"] = "rewritten"
            seen.append("h1")

    class H2(AgentHook):
        async def before_execute_tools(self, context):
            seen.append(context.tool_calls[0].arguments["command"])

    hook = CompositeHook([H1(), H2()])
    ctx = AgentHookContext(
        iteration=0,
        messages=[],
        tool_calls=[ToolCallRequest(id="c1", name="exec", arguments={"command": "original"})],
    )
    await hook.before_execute_tools(ctx)
    assert seen == ["h1", "rewritten"]
```

### 6.4 回归测试：ExecTool 不包含 rtk 逻辑

文件：`tests/agent/tools/test_shell.py`（或新建 `tests/agent/tools/test_exec_no_rtk.py`）

断言：
- `ExecToolConfig` 的字段集合不包含 `rtk_enabled`、`rtk_path`、`command_rewrite` 等关键字。
- `ExecTool.execute()` 的源码/ast 中不引用 `"rtk"` 或 `"rewrite"`。

更简单的方式：直接 `grep` 或 `hasattr` 检查：

```python
def test_exectool_config_has_no_rtk_fields():
    from pathlib import Path
    assert 'rtk' not in Path('nanobot/agent/tools/shell.py').read_text()
```

### 6.5 Subagent 传播测试

文件：补充至 `tests/agent/test_subagent.py` 或新建 `tests/agent/test_subagent_rewrite_hook.py`

测试目标：确认 `SubagentManager` 构造时传入 `extra_hooks`，子 agent runner 的 `AgentRunSpec.hook` 是 `CompositeHook`，且包含 `CommandRewriteHook`。

实现方式：
- mock `AgentRunner.run`，在 `SubagentManager.start_subagent()` 后等待任务完成。
- 捕获 `run` 调用中的 `spec.hook`，用 `isinstance` 链检查。

---

## 7. 向前兼容性

### 7.1 AgentHook 接口变更

如果 upstream 未来修改 `AgentHook.before_execute_tools` 签名（例如新增参数），唯一需要改的地方是 `CommandRewriteHook.before_execute_tools`。`ExecTool` 不受影响。这是 **"横切逻辑不住在工具里"** 的核心收益。

若 `AgentHookContext` 中 `tool_calls` 的类型从 `list[ToolCallRequest]` 变为不可变结构，则 hook 的修改策略需要从"就地 mutation"改为"替换列表元素"，但接口边界仍然是 hook 层。

### 7.2 ToolCallRequest 结构变更

当前依赖 `ToolCallRequest` 的公开字段：
- `.name`（str）
- `.arguments`（dict[str, Any]）

如果 upstream 将 `arguments` 改为 Pydantic model 或冻结类型，需要相应调整赋值方式。建议 spec 实现时用 `tc.arguments = dict(tc.arguments)` 防御性复制后再修改，但当前基线中 `arguments` 是普通 dict，就地修改即可。

### 7.3 SubagentManager 签名变更

`SubagentManager` 未来可能引入自己的 hook 体系。当前方案通过 `extra_hooks: list[AgentHook]` 参数侵入最小，如果 upstream 后续提供正式的 subagent hook 协议，迁移成本仅为：
1. 将 `extra_hooks` 重命名为新协议参数；
2. 调整 `CompositeHook` 组装位置。

### 7.4 rtk 协议演进

如果 fork 的 rtk 未来扩展协议（例如需要 env 变量、需要 stderr 解析），修改范围被限制在 `CommandRewriteHook._rewrite()` 内部。runner、loop、tool 均不受影响。

### 7.5 配置版本兼容

`CommandRewriteConfig` 默认 `enabled=False`，旧配置（无该字段）行为完全不变。这是安全的追加式配置。

---

## 8. 实现顺序

建议按以下顺序实现，每个步骤后可独立运行对应测试验证：

1. **Schema & Config**
   - 在 `nanobot/config/schema.py` 添加 `CommandRewriteConfig` 和 `ToolsConfig.command_rewrite`。
   - 运行 config 解析测试确认 schema 无回归。

2. **CommandRewriteHook（核心）**
   - 创建 `nanobot/agent/hooks/__init__.py`（导出 `CommandRewriteHook`）。
   - 创建 `nanobot/agent/hooks/rewrite.py`，实现 `CommandRewriteHook`。
   - 编写 `tests/agent/hooks/test_command_rewrite.py`，TDD 驱动完成所有单元测试场景。

3. **CompositeHook 顺序测试（如缺失）**
   - 检查 `tests/agent/test_hook_composite.py` 是否已有 `before_execute_tools` 顺序测试。
   - 若无，补充测试并确保通过。

4. **AgentLoop 注入**
   - 修改 `AgentLoop.from_config()` 和 `AgentLoop.__init__`（SubagentManager 调用处）。
   - 编写 `tests/agent/test_loop_rewrite_hook_injection.py`。

5. **SubagentManager 传播**
   - 修改 `SubagentManager.__init__` 和 `_run_subagent_inner`。
   - 编写 subagent 传播测试。

6. **回归 & 完整性检查**
   - 确认 `ExecTool` / `ExecToolConfig` 无 rtk 痕迹。
   - 运行 `pytest tests/agent/ -q` 全量回归。

---

## 附录：参考文件存在性检查

| 文件 | 状态 |
|------|------|
| `nanobot/agent/hook.py` | 存在 |
| `nanobot/agent/loop.py` | 存在 |
| `nanobot/agent/runner.py` | 存在 |
| `nanobot/agent/subagent.py` | 存在 |
| `nanobot/agent/tools/shell.py` | 存在 |
| `nanobot/config/schema.py` | 存在 |
| `tests/agent/test_hook_composite.py` | 存在 |
| `tests/agent/test_runner_hooks.py` | 存在 |
| `tests/agent/hooks/` | **不存在**（需新建） |
| `nanobot/agent/hooks/__init__.py` | **不存在**（需新建） |
