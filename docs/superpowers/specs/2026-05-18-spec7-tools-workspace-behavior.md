# Spec7 — Tools and Workspace Operational Behavior

## 1. 概述

本 spec 对应 Plan7（`2026-05-18-pack7-tools-workspace-behavior.md`），目标是在 upstream 工作树（`sync-upstream-2026-05-replay`）中 replay fork 的工具层与工作空间操作行为差异，同时保持最小侵入、面向复用、优雅实现。

范围严格限定为：
- `grep` 搜索工具（ripgrep 优先 + 系统 grep fallback）
- `extra_allowed_paths` 公共配置的废弃与内部机制替代
- `MessageTool` 的 deliver / suppress 语义补全
- 工作空间 / 临时 / 输出路径策略的测试加固
- `SpawnTool` 的 `timeout_seconds` 参数透传
- Shell 执行边界行为（验证已有实现，补充测试）

不在本 spec 范围内：provider routing、Discord/TTS/transcription UX、runtime/session metadata、command rewrite、subagent trace logging、memory consolidation/pruning、bootstrap、SOUL、docs migration。

## 2. 行为需求

### 2.1 Grep — ripgrep 优先，系统 grep fallback

| # | 需求 | 优先级 |
|---|------|--------|
| G1 | 当系统存在可执行 `rg` 时，`grep` 工具优先调用 ripgrep（`rg --json`）进行内容搜索 | Must |
| G2 | `rg` 不可用时，fallback 到系统 `grep`（`/usr/bin/grep` 或 `grep` in PATH）；grep 功能不得完全不可用 | Must |
| G3 | ripgrep 输出与系统 grep 输出在格式、分页、截断、统计注释上保持兼容 | Must |
| G4 | 支持 `content` / `files_with_matches` / `count` 三种 `output_mode` | Must |
| G5 | 支持 `glob` / `type` / `case_insensitive` / `fixed_strings` / `context_before` / `context_after` / `limit` / `offset` | Must |
| G6 | 保持 `_MAX_FILE_BYTES`（2 MB）和 binary 跳过行为 | Must |
| G7 | `restrict_to_workspace=True` 时，搜索路径仍受 `resolve_workspace_path` 约束 | Must |

### 2.2 `extra_allowed_paths` — 不复活

| # | 需求 | 优先级 |
|---|------|--------|
| E1 | **不**在 `ToolsConfig` 中添加 `extra_allowed_paths` 公共配置字段 | Must |
| E2 | 内部只读目录（如 `BUILTIN_SKILLS_DIR`）通过 `_FsTool(extra_allowed_dirs=[...])` 传入，保持现有机制 | Must |
| E3 | `resolve_workspace_path(..., extra_allowed_dirs=...)` 继续作为唯一路径边界评估入口 | Must |

### 2.3 MessageTool — deliver / suppress 语义与边界

| # | 需求 | 优先级 |
|---|------|--------|
| M1 | `message` 工具发送到**当前 target**（`channel == default_channel && chat_id == default_chat_id`）时，`_sent_in_turn = True` | Must |
| M2 | `_sent_in_turn = True` 时，AgentLoop `_assemble_outbound()` 在 `!had_injections` 或 `stop_reason == "empty_final_response"` 时 suppress final reply | Must |
| M3 | `message` 工具发送到**不同 target**（cross-channel / proactive）时，**不** suppress final reply | Must |
| M4 | `message` 工具 send callback 失败时，**不** suppress final reply（`_sent_in_turn` 不置位） | Must |
| M5 | `message` 工具带 `media` 发送到当前 target 时，记录 `turn_delivered_media_paths()` 并返回包含 media count 的成功文案 | Must |
| M6 | WebSocket 场景下 `chat_id` 显式传入 client-side `anon-…` 值时返回错误，**不**调用 send callback | Must |
| M7 | 省略 `channel` / `chat_id` 时使用 `RequestContext` 的当前 runtime target | Must |
| M8 | 每 turn 开始时 `MessageTool.start_turn()` 重置 `_sent_in_turn` 和 `_turn_delivered_media_var` | Must |

### 2.4 Workspace / temp / output 路径策略

| # | 需求 | 优先级 |
|---|------|--------|
| W1 | `get_data_dir()` 返回 active config file 的 parent | Must |
| W2 | `get_runtime_subdir(name)` 返回 `get_data_dir() / name` | Must |
| W3 | `get_media_dir(channel=None)` 返回 `get_runtime_subdir("media")`，可选 channel 子目录 | Must |
| W4 | `get_logs_dir()` 返回 `get_runtime_subdir("logs")` | Must |
| W5 | `get_workspace_path(workspace=None)` 返回显式 workspace 或 `~/.nanobot/workspace`，**不**默认到生产 checkout | Must |
| W6 | 工具创建的 durable 路径（media、log、temp、output）必须来自 workspace / config-root runtime dirs，不得指向 `/tmp` 或生产 checkout | Must |

### 2.5 Spawn timeout 参数

| # | 需求 | 优先级 |
|---|------|--------|
| S1 | `SpawnTool` schema 暴露 `timeout_seconds` 参数（可选 `int`） | Must |
| S2 | `SpawnTool.execute()` 将 `timeout_seconds` 透传给 `SubagentManager.spawn()` | Must |
| S3 | `SubagentManager.spawn()` 接收 `timeout_seconds` 并传递给 `_run_subagent()` | Must |
| S4 | `_run_subagent()` 在 `timeout_seconds` 指定且 > 0 时，用 `asyncio.wait_for(runner.run(...), timeout=timeout_seconds)` 包裹总执行 | Must |
| S5 | 超时后抛出 `asyncio.TimeoutError`，由 `_run_subagent()` 的 `except Exception` 捕获并作为 error 状态 announce | Must |

### 2.6 Shell 边界行为

| # | 需求 | 优先级 |
|---|------|--------|
| SH1 | `restrict_to_workspace=True` 时拒绝 `working_dir` 在 workspace 之外 | Must |
| SH2 | `restrict_to_workspace=True` 时拒绝命令中包含 workspace/media 之外的绝对路径 | Must |
| SH3 | `/dev/null`、`/dev/stdin`、`/dev/stdout`、`/dev/stderr`、`/dev/fd/*` 等 benign device paths 不被拦截 | Must |
| SH4 | 边界错误返回文案包含 `_WORKSPACE_BOUNDARY_NOTE`，明确告知模型不可 retry | Must |

## 3. 架构分析（Upstream 当前状态）

### 3.1 文件清单与存在性确认

以下文件在 replay worktree 中存在并已 inspect：

- `nanobot/agent/tools/search.py` ✅ — 原有内联搜索实现，无 rg 支持
- `nanobot/agent/tools/filesystem.py` ✅ — `_FsTool` 含 `extra_allowed_dirs`
- `nanobot/agent/tools/message.py` ✅ — ContextVar 驱动的 per-turn tracking
- `nanobot/agent/tools/spawn.py` ✅ — 无 `timeout_seconds`
- `nanobot/agent/tools/shell.py` ✅ — `ExecTool` 含 `_guard_command` 与 `restrict_to_workspace`
- `nanobot/agent/tools/path_utils.py` ✅ — `resolve_workspace_path`
- `nanobot/config/schema.py` ✅ — `ToolsConfig`（无 `extra_allowed_paths`）
- `nanobot/config/paths.py` ✅ — runtime path helpers
- `nanobot/utils/path.py` ✅ — `abbreviate_path`
- `nanobot/agent/loop.py` ✅ — `_assemble_outbound` suppression 逻辑
- `nanobot/agent/subagent.py` ✅ — `SubagentManager`，`spawn()` 无 timeout
- `nanobot/agent/tools/context.py` ✅ — `ToolContext` / `RequestContext`
- `nanobot/agent/tools/loader.py` ✅ — `ToolLoader`
- `nanobot/agent/tools/base.py` ✅ — `Tool` 基类
- `nanobot/agent/runner.py` ✅ — `AgentRunSpec` 含 `llm_timeout_s`

测试文件存在性确认：

- `tests/tools/test_search_tools.py` ✅
- `tests/tools/test_message_tool_suppress.py` ✅
- `tests/tools/test_exec_allow_patterns.py` ✅
- `tests/tools/test_exec_env.py` ✅
- `tests/tools/test_exec_platform.py` ✅
- `tests/tools/test_exec_security.py` ✅
- `tests/agent/tools/test_subagent_tools.py` ✅
- `tests/config/test_config_paths.py` ✅

以下文件**不存在**于 replay worktree（已确认）：
- `tests/tools/test_shell_tool.py` ❌
- `tests/tools/test_shell.py` ❌
- `nanobot/workspace/layout.py` ❌

### 3.2 关键代码现状

#### 3.2.1 GrepTool（search.py）

- 继承链：`GrepTool -> _SearchTool -> _FsTool -> Tool`
- `_iter_files()` 使用 `os.walk()`，`_IGNORE_DIRS` 来自 `ListDirTool`
- `execute()` 内联实现完整搜索逻辑：regex 编译、binary detection（`null_byte` heuristic）、大文件跳过、context lines、pagination、truncation notes
- **无 subprocess / shutil.which / ripgrep 引用**
- `_MAX_FILE_BYTES = 2_000_000`，`_MAX_RESULT_CHARS = 128_000`
- `_display_path()` 优先用 `self._workspace` relative，否则用搜索 root relative

#### 3.2.2 MessageTool（message.py）

- 使用 5 个 `ContextVar`：`_sent_in_turn_var`、`_turn_delivered_media_var`、`_default_channel`、`_default_chat_id`、`_default_message_id`、`_default_metadata`、`_record_channel_delivery_var`
- `start_turn()` 重置 `_sent_in_turn` 和 `_turn_delivered_media_var`
- `execute()` 中 `same_target = (channel == default_channel and chat_id == default_chat_id)`，仅在 same-target 成功 send 后置位 `_sent_in_turn`，并累加 media paths
- `set_send_callback()` 由 `AgentLoop` 在初始化时注入（`ctx.bus.publish_outbound`）
- `create()` 接收 `workspace` 和 `restrict_to_workspace`，用于 media path 边界校验

#### 3.2.3 AgentLoop `_assemble_outbound()`（loop.py）

```python
if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
    if not had_injections or stop_reason == "empty_final_response":
        return None
```

- suppression 条件：message 发过了 + （无 injections 或 stop_reason 为空响应）
- `_run_agent_loop` 在 turn 开始时调用 `message_tool.start_turn()`

#### 3.2.4 SpawnTool（spawn.py）

- schema 只有 `task` 和 `label`
- `execute()` 检查 `max_concurrent_subagents`，然后调用 `self._manager.spawn(task, label, origin_channel, origin_chat_id, session_key, origin_message_id)`
- **无 `timeout_seconds`**

#### 3.2.5 SubagentManager（subagent.py）

- `spawn()` 签名无 timeout 参数
- `_run_subagent()` 创建 `AgentRunSpec` 时设置了 `llm_timeout_s`（来自 `_llm_wall_timeout_for_session`），但这是 LLM 单请求超时，不是 subagent 总超时
- `_run_subagent()` 未被 `asyncio.wait_for` 包裹

#### 3.2.6 ExecTool（shell.py）

- `create()` 从 `ctx.config.exec` 读取 timeout、sandbox、path_append、allowed_env_keys、allow_patterns、deny_patterns
- `restrict_to_workspace` 在 `create()` 和 `_guard_command()` 中生效
- `_guard_command()` 已实现：path traversal (`../`)、绝对路径 outside cwd/media、deny patterns、allow patterns、internal URL
- `_is_benign_device_path()` 已覆盖 `/dev/null`、`/dev/stdin`、`/dev/stdout`、`/dev/stderr`、`/dev/fd/*`
- `_WORKSPACE_BOUNDARY_NOTE` 已存在

#### 3.2.7 Path helpers（config/paths.py）

- `get_data_dir()` -> `get_config_path().parent`
- `get_runtime_subdir(name)` -> `get_data_dir() / name`
- `get_media_dir(channel=None)` -> `get_runtime_subdir("media")` [ / channel ]
- `get_logs_dir()` -> `get_runtime_subdir("logs")`
- `get_workspace_path(workspace=None)` -> 显式路径或 `~/.nanobot/workspace`
- `is_default_workspace()` 比较 resolved path

#### 3.2.8 `_FsTool.create()`（filesystem.py）

```python
allowed_dir = Path(ctx.workspace) if ctx.config.restrict_to_workspace else None
extra_read = [Path(BUILTIN_SKILLS_DIR)] if BUILTIN_SKILLS_DIR else None
return cls(
    workspace=Path(ctx.workspace),
    allowed_dir=allowed_dir,
    extra_allowed_dirs=extra_read,
    file_states=ctx.file_state_store,
)
```

- 内部只读目录已通过 `extra_allowed_dirs` 传入，无需公共配置

## 4. 技术方案

### 4.1 ripgrep 优先 + 系统 grep fallback

#### 4.1.1 设计原则

- **不魔改工具架构**：`GrepTool` 保持继承 `_SearchTool -> _FsTool`，schema 不变。
- **系统 grep fallback**：`rg` 检测在 `execute()` 入口完成；不可用时调用系统 `grep`（通过 `asyncio.create_subprocess_exec`），不再维护纯 Python 搜索实现。系统 grep 在所有 POSIX 环境（Linux/macOS）标配，无需额外安装，性能远超纯 Python 逐行遍历。
- **输出兼容**：ripgrep 的 `--json` 输出与系统 grep 的 `file:line:content` 输出，经解析后复用完全相同的格式化、分页、截断、统计注释逻辑。
- **统一格式化**：rg 和 grep 都输出 `list[SearchMatch]`，由 `_format_search_results` 统一处理。只维护一套格式化逻辑。
- **极端错误**：若系统 `rg` 和 `grep` 均不可用，raise 明确错误而不是静默 fallback。

#### 4.1.2 实现位置

修改文件：`nanobot/agent/tools/search.py`

新增类型与方法（均在 `GrepTool` 所在模块）：

```python
from dataclasses import dataclass
import functools

@dataclass(frozen=True)
class SearchMatch:
    path: str
    line_num: int | None = None
    line_text: str | None = None
    submatches: list[tuple[int, int]] | None = None

@functools.lru_cache(maxsize=1)
def _rg_available() -> bool:
    return shutil.which("rg") is not None

def _grep_available() -> bool:
    return shutil.which("grep") is not None

def _rg_params_supported(type_key: str | None) -> bool:
    """Return True if all query parameters are within rg's supported subset."""
    if type_key and type_key not in _TYPE_GLOB_MAP:
        return False
    return True

def _format_search_results(
    matches: list[SearchMatch],
    mode: str,
    limit: int,
    offset: int,
) -> str:
    """Single formatter used by rg, grep, and any future search backend.

    mode: 'content' | 'files_with_matches' | 'count'
    Pagination, truncation notes, and sort order are applied here and only here.
    """

async def _run_rg_search(
    self,
    target: Path,
    pattern: str,
    root: Path,
    *,
    glob: str | None,
    type_key: str | None,
    case_insensitive: bool,
    fixed_strings: bool,
    context_before: int,
    context_after: int,
) -> list[SearchMatch]:
    """Return parsed SearchMatch list from rg --json output."""

async def _parse_rg_json(
    self,
    json_lines: list[str],
    root: Path,
    *,
    context_before: int,
    context_after: int,
) -> list[SearchMatch]:
    """Parse rg --json lines into structured SearchMatch objects."""

async def _run_grep_search(
    self,
    target: Path,
    pattern: str,
    root: Path,
    *,
    glob: str | None,
    type_key: str | None,
    case_insensitive: bool,
    fixed_strings: bool,
    context_before: int,
    context_after: int,
    limit: int,
    offset: int,
    output_mode: str,
) -> list[SearchMatch]:
    """Run system grep via asyncio subprocess and parse output into SearchMatch list.

    grep args mapping:
      -rn  -> recursive + line numbers
      -i   -> case_insensitive
      -l   -> files_with_matches mode
      -c   -> count mode
      --include -> glob filtering
    Output format: file:line:content, parsed into SearchMatch shared with rg path.
    """

def _parse_grep_output(
    self,
    stdout: bytes,
    root: Path,
    *,
    output_mode: str,
) -> list[SearchMatch]:
    """Parse grep `file:line:content` output into structured SearchMatch objects."""
```

#### 4.1.3 rg 命令构造

```python
cmd = [
    "rg",
    "--json",
    "--max-filesize", str(self._MAX_FILE_BYTES),
    "-C", f"{context_before},{context_after}",
]
if case_insensitive:
    cmd.append("-i")
if fixed_strings:
    cmd.append("-F")
if glob:
    cmd.extend(["-g", glob])
if type_key:
    mapped = _TYPE_GLOB_MAP.get(type_key)
    if mapped:
        for g in mapped:
            cmd.extend(["-g", g])
cmd.append(pattern)
cmd.append(str(target))
```

**关键决策**：`type` 参数映射为 glob tuple；在 rg 中通过 `-g` 多传入实现相同过滤，在 grep 中通过 `--include` 实现。rg 是否适用的判断在 `execute()` 入口处统一完成（`_rg_available() and _rg_params_supported(type_key)`）；rg 不可用时直接进入 grep 路径，不在 rg 路径内部再做退出决策。

#### 4.1.4 rg JSON 解析

rg `--json` 输出每行是一个 JSON object，type 包括 `begin`、`match`、`end`、`summary` 等。

`_parse_rg_json` 解析逻辑：
1. 按 file 分组收集 matches
2. 每个 match 的 lines.text 取匹配行，配合 `submatches` 中的 start/end 做高亮（可选）
3. `context` 行与原有实现中 `context_before`/`context_after` 语义一致
4. 跳过 binary files（rg 不会搜索 binary，但会输出 `binary` type，忽略即可）
5. 大文件由 `--max-filesize` 控制，rg 自动跳过，需统计 skipped_large（从 summary 或 stderr 提取）

解析结果返回 `list[SearchMatch]`，不做任何格式化。最终输出统一由 `_format_search_results` 生成。

#### 4.1.5 execute() 入口改造

```python
async def execute(self, pattern, path=".", ...):
    target = self._resolve(path or ".")
    if not target.exists():
        return f"Error: path does not exist: {path}"
    if not (target.is_dir() or target.is_file()):
        return f"Error: path is not a file or directory: {path}"

    # 决策点 1：rg 可用且参数支持
    use_rg = _rg_available() and _rg_params_supported(type_key)
    if use_rg:
        try:
            rg_matches = await self._run_rg_search(
                target, pattern, root,
                glob=glob, type_key=type_key,
                case_insensitive=case_insensitive,
                fixed_strings=fixed_strings,
                context_before=context_before,
                context_after=context_after,
            )
            return _format_search_results(
                rg_matches, mode=output_mode, limit=limit, offset=offset
            )
        except SearchBackendError:
            pass  # fallback to grep

    # 决策点 2：rg 不可用或失败，fallback 到系统 grep
    if _grep_available():
        grep_matches = await self._run_grep_search(
            target, pattern, root,
            glob=glob, type_key=type_key,
            case_insensitive=case_insensitive,
            fixed_strings=fixed_strings,
            context_before=context_before,
            context_after=context_after,
            limit=limit,
            offset=offset,
            output_mode=output_mode,
        )
        return _format_search_results(
            grep_matches, mode=output_mode, limit=limit, offset=offset
        )

    # 极端情况：rg 和 grep 均不可用
    raise RuntimeError(
        "No search backend available: install ripgrep (`rg`) or ensure "
        "system `grep` is in PATH."
    )
```

**向前兼容标注**：`_rg_available()` 和 `_grep_available()` 使用模块级 `functools.lru_cache(maxsize=1)`，避免每次搜索都 `which`。若用户环境中 `rg` 后续被卸载，进程生命周期内仍视为可用（直到重启）。这是可接受的 trade-off，因为工具实例通常在 loop 生命周期内复用。若 rg 在运行时消失，首次调用失败后会进入 grep fallback（`_run_rg_search` 内部捕获异常并 raise `SearchBackendError`，由 `execute()` 进入 grep 路径）。

#### 4.1.6 错误处理

rg 调用可能失败的情况：
- `subprocess.CalledProcessError`（返回码 1 表示无 match，这是正常结果，需与 2 区分）
- `subprocess.TimeoutExpired`（设置合理 timeout，如 60s）
- `FileNotFoundError`（rg 被删除）
- JSON 解析失败

以上任一情况，`_run_rg_search` 捕获后 raise `SearchBackendError`，由 `execute()` 捕获并进入系统 grep fallback 路径。若 grep 同样失败或不可用，最终 raise 明确错误。格式化失败（如 mode 不合法）不属于搜索后端错误，由 `_format_search_results` 统一处理。

系统 grep 调用失败的情况与 rg 类似，但输出解析更简单（`file:line:content` 格式）。grep 路径同样设置 60s timeout，返回码 1（无 match）视为正常空结果。

### 4.2 `extra_allowed_paths` 不复活的决策记录

| 维度 | 分析 |
|------|------|
| Fork 历史 | `extra_allowed_paths` 在 fork 的 `b19d219b` 引入，后在 `e99b4f4c` 被 fork 自己删除，commit message 明确说 "This feature is no longer needed" |
| Upstream 现状 | `_FsTool.__init__` 已有 `extra_allowed_dirs` 参数；`_FsTool.create()` 已将 `BUILTIN_SKILLS_DIR` 作为 `extra_allowed_dirs` 传入；`resolve_workspace_path()` 已支持 `extra_allowed_dirs` |
| 行为等价性 | 上游内部机制已覆盖 fork 原先用 `extra_allowed_paths` 解决的需求（允许 builtins skills 只读访问） |
| 公共配置风险 | 添加 `tools.extra_allowed_paths` 到 schema 会扩大攻击面，且与 fork 自身的删除方向矛盾 |

**决策**：Spec7 **不实现** `extra_allowed_paths` 公共配置，也不添加兼容别名。若未来有合法需求需要用户配置额外允许目录，应在独立的 config/security pack 中重新设计，而非复活 fork 已废弃的字段。

### 4.3 MessageTool deliver/suppress 语义和边界

> **Upstream 已有**：`MessageTool._sent_in_turn_var` ContextVar 已在 upstream 实现（第 76 行），suppress 语义已正确。无需修改实现代码，仅补充缺失的边界测试（same-target、cross-target、callback failure、media 累加）。

#### 4.3.1 当前语义矩阵

| 场景 | `_sent_in_turn` | suppress final reply | 已有测试 |
|------|----------------|---------------------|----------|
| same-target, success send | ✅ True | ✅ yes (if no injections) | `test_suppress_when_sent_to_same_target` |
| same-target, with injections | ✅ True | ❌ no | `test_not_suppress_when_injections` |
| cross-target (channel/chat_id 不同) | ❌ False | ❌ no | 缺失 |
| send callback raises Exception | ❌ False（execute 中 try/except 包裹，成功 send 前的异常不会置位） | ❌ no | 缺失 |
| same-target + media | ✅ True + media recorded | ✅ yes (if no injections) | 缺失（media count 文案） |
| WebSocket `chat_id` 显式传入 `anon-…` | execute 返回 error | ❌ n/a | 缺失 |
| omitted channel/chat_id | 使用 default | depends | 缺失 |

#### 4.3.2 需要补充的测试（不修改实现，除非测试暴露 bug）

在 `tests/tools/test_message_tool_suppress.py` 追加：

1. `test_not_suppress_when_cross_target`：message 发到不同 channel，final reply 不 suppress
2. `test_not_suppress_when_send_callback_fails`：mock callback 抛异常，`_sent_in_turn` 保持 False，final reply 不 suppress
3. `test_same_target_media_tracks_delivered_paths`：media 发到 same target，`_turn_delivered_media` 记录路径，返回文案包含 media count
4. `test_omitted_channel_uses_default_context`：不传 channel/chat_id，使用 `set_context` 注入的默认值
5. `test_websocket_chat_id_mismatch_returns_error`：显式传 `anon-123` 时返回 error 且 callback 未被调用

**实现修改评估**：当前代码逻辑已正确覆盖上述场景（cross-target 不置位 `_sent_in_turn`，callback 异常被 catch 不置位，media 累加，default context fallback，`anon-` 检查）。本 spec 以**补测试**为主，不主动修改 `message.py`；若测试失败，再 surgical fix。

### 4.4 workspace / temp / output 路径策略

#### 4.4.1 当前路径体系

- **workspace**：`get_workspace_path()` -> `~/.nanobot/workspace`（或显式路径）
- **data dir**：`get_data_dir()` -> config file parent
- **media**：`get_media_dir()` -> `get_data_dir() / media`
- **logs**：`get_logs_dir()` -> `get_data_dir() / logs`
- **cron**：`get_cron_dir()` -> `get_data_dir() / cron`
- **webui**：`get_webui_dir()` -> `get_data_dir() / webui`

#### 4.4.2 需要补充的测试

在 `tests/config/test_config_paths.py` 追加：

1. `test_get_logs_dir_follows_config_path`：monkeypatch config path 后 `get_logs_dir()` 正确指向 config parent / logs
2. `test_get_workspace_path_explicit_not_repo`：显式传 `/tmp/custom`，确认返回 `/tmp/custom`，不指向 `/root/git_code/nanobot`
3. `test_runtime_subdir_tmp_under_config`：`get_runtime_subdir("tmp")` 指向 config parent / tmp（展示 temp 策略）
4. `test_media_dir_with_channel_namespace`：已存在，保留

#### 4.4.3 策略声明

Spec7 **不引入** `nanobot/workspace/layout.py`。
- 所有 durable 路径由 `config/paths.py` 统一表达。
- `tmp` / `output` 如需在工具层使用，调用方应使用 `get_runtime_subdir("tmp")` / `get_runtime_subdir("output")`。
- 当前代码未发现工具写 `/tmp` 或生产 checkout 的漏洞；测试用于建立 regression guard。

### 4.5 Spawn timeout 参数

#### 4.5.1 实现位置

- `nanobot/agent/tools/spawn.py`：schema + execute
- `nanobot/agent/subagent.py`：spawn() 签名 + _run_subagent() 超时包裹

#### 4.5.2 schema 变更

```python
@tool_parameters(
    tool_parameters_schema(
        task=StringSchema("The task for the subagent to complete"),
        label=StringSchema("Optional short label for the task (for display)"),
        timeout_seconds=IntegerSchema(
            "Optional maximum time in seconds for the subagent to complete. "
            "If omitted, the subagent runs until completion or LLM-level timeouts."
        ),
        required=["task"],
    )
)
```

#### 4.5.3 调用链变更

`SpawnTool.execute()`：
```python
return await self._manager.spawn(
    task=task,
    label=label,
    origin_channel=self._origin_channel.get(),
    origin_chat_id=self._origin_chat_id.get(),
    session_key=self._session_key.get(),
    origin_message_id=self._origin_message_id.get(),
    timeout_seconds=timeout_seconds,
)
```

`SubagentManager.spawn()`：
```python
async def spawn(
    self,
    task: str,
    label: str | None = None,
    origin_channel: str = "cli",
    origin_chat_id: str = "direct",
    session_key: str | None = None,
    origin_message_id: str | None = None,
    timeout_seconds: int | None = None,
) -> str:
    ...
    bg_task = asyncio.create_task(
        self._run_subagent(..., timeout_seconds=timeout_seconds)
    )
```

`SubagentManager._run_subagent()`：
```python
async def _run_subagent(
    self,
    task_id: str,
    task: str,
    label: str,
    origin: dict[str, str],
    status: SubagentStatus,
    origin_message_id: str | None = None,
    timeout_seconds: int | None = None,
) -> None:
    ...
    run_coro = self.runner.run(AgentRunSpec(...))
    if timeout_seconds and timeout_seconds > 0:
        run_coro = asyncio.wait_for(run_coro, timeout=timeout_seconds)
    result = await run_coro
    ...
```

**注意**：`AgentRunSpec.llm_timeout_s` 与 `timeout_seconds` 是独立概念。
- `llm_timeout_s`：单次 LLM API 调用墙钟超时（默认 300s），由 `runner.run` 内部处理。
- `timeout_seconds`：整个 subagent 任务（含多轮 tool 调用）的总超时，由 `_run_subagent` 外层 `asyncio.wait_for` 控制。

若 `timeout_seconds` 触发，`asyncio.wait_for` 抛出 `TimeoutError`，被 `_run_subagent` 的 `except Exception` 捕获，最终 announce `"Error: timed out after {timeout_seconds}s"`。

#### 4.5.4 测试

在 `tests/agent/tools/test_subagent_tools.py` 追加：

```python
@pytest.mark.asyncio
async def test_spawn_tool_accepts_timeout_seconds(tmp_path):
    ...
```

### 4.6 Shell 边界行为

#### 4.6.1 评估结论

Upstream 已实现完整的 shell 边界：
- `_guard_command()` 在 `execute()` 中被调用
- `restrict_to_workspace` 拒绝 `../`、绝对路径 outside cwd/media
- `_is_benign_device_path()` 放行 `/dev/*`
- `_WORKSPACE_BOUNDARY_NOTE` 已存在

`test_exec_security.py` 已覆盖：
- working_dir outside workspace blocked
- absolute path outside workspace blocked
- benign device paths allowed
- format command blocked

#### 4.6.2 决策

- **不新建** `tests/tools/test_shell_tool.py`（plan 已指出 implementation likely unnecessary）
- 在现有 `test_exec_security.py` 中追加一条：
  `test_exec_allows_benign_device_paths`：显式验证 `cat /dev/null`、`echo > /dev/stderr` 不被 `_guard_command` 拦截

## 5. 最小侵入评估

| 修改点 | 侵入度 | 说明 |
|--------|--------|------|
| `search.py` 新增 `SearchMatch`、`_format_search_results`、`_run_rg_search`、`_parse_rg_json`、`_run_grep_search`、`_parse_grep_output`、`_rg_params_supported` | 中 | 纯新增；`execute()` 先判断 rg，再判断 grep；rg/grep 均输出 `list[SearchMatch]`，由统一 formatter 生成字符串；删除纯 Python 内联搜索逻辑 |
| `spawn.py` 新增 `timeout_seconds` 参数 | 低 | schema + execute 参数透传 |
| `subagent.py` spawn / `_run_subagent` 新增 timeout | 低 | 签名扩展 + 外层 `asyncio.wait_for` |
| `message.py` | **无修改**（仅补测试） | 当前实现已满足语义 |
| `shell.py` | **无修改**（仅补测试） | 当前实现已满足语义 |
| `config/schema.py` | **无修改** | 不添加 `extra_allowed_paths` |
| `config/paths.py` | **无修改**（仅补测试） | 当前实现已满足语义 |
| `filesystem.py` | **无修改** | 内部机制已足够 |

## 6. 测试方案

### 6.1 测试文件清单

| 测试文件 | 测试内容 | 动作 |
|----------|----------|------|
| `tests/tools/test_search_tools.py` | rg 可用时优先调用 rg；rg 不可用时 fallback 到系统 grep；输出格式兼容；glob/type 过滤；context lines；pagination；binary/large skip；rg/grep 均不可用时报错 | 追加 |
| `tests/tools/test_message_tool_suppress.py` | cross-target 不 suppress；callback fail 不 suppress；media tracking；default context；websocket chat_id mismatch | 追加 |
| `tests/tools/test_exec_security.py` | benign device paths 显式允许 | 追加 |
| `tests/config/test_config_paths.py` | logs/tmp/workspace explicit 路径策略 | 追加 |
| `tests/agent/tools/test_subagent_tools.py` | `timeout_seconds` 参数透传与超时行为 | 追加 |

### 6.2 ripgrep 测试策略

**问题**：CI / 开发环境可能无 `rg`，但 POSIX 环境通常有系统 `grep`。

**方案**：
1. 使用 `pytest.mark.skipif(not shutil.which("rg"), reason="ripgrep not installed")` 包裹 rg-specific 测试。
2. 对于 rg 输出格式测试，可以 mock `subprocess.run` 返回预录的 `rg --json` 输出，验证 `_parse_rg_json` 正确性，这样无需真实 rg。
3. grep fallback 测试：mock `shutil.which` 让 `rg` 不可用但 `grep` 可用，验证 `_run_grep_search` 被调用且输出格式正确。
4. 双不可用测试：mock `shutil.which` 让 `rg` 和 `grep` 都不可用，验证 `execute()` raise 明确错误。
5. 所有后端（rg/grep）的格式化测试复用同一套 `_format_search_results` 断言。

### 6.3 MessageTool 测试策略

- 使用 `unittest.mock.AsyncMock` mock `_send_callback`
- 使用 `AgentLoop` fixture（与现有测试一致）验证 `_assemble_outbound` suppression
- 单独对 `MessageTool` 实例做单元测试验证 `turn_delivered_media_paths()`

### 6.4 Spawn timeout 测试策略

- mock `self._manager.spawn` 或使用真实 `SubagentManager` + mock `runner.run`
- 验证 `SpawnTool.execute(task="x", timeout_seconds=30)` 正确传递参数
- 对 `SubagentManager`：mock `runner.run` 为 `asyncio.sleep(999)`，设置 `timeout_seconds=0.1`，验证 `asyncio.wait_for` 触发 TimeoutError 且结果 announce 包含 timeout 信息

## 7. 向前兼容性

| 决策 | 兼容性影响 | 缓解措施 |
|------|-----------|----------|
| rg 输出格式与 grep 严格一致 | 未来 rg 版本 `--json` schema 变化可能破坏解析 | 解析逻辑集中在一个方法；rg 失败 fallback 到系统 grep；spec 要求在 rg 解析异常时进入 grep fallback |
| `timeout_seconds` 新增为可选参数 | 旧调用（无 timeout）行为不变 | 参数默认 `None`，不传时不加 `asyncio.wait_for` |
| `_rg_available()` 使用模块级 `lru_cache` | 进程生命周期内 rg 可用性不变 | 可接受；如需热切换，可改为无缓存检查，但性能有损 |

**依赖 upstream 特定版本实现细节的设计决策**：
1. `AgentRunSpec` 在当前 upstream 中已有 `llm_timeout_s` 字段；`timeout_seconds` 是 subagent 总超时，与其独立。若 future upstream 重构 `AgentRunSpec` 为 dataclass 并改变字段语义，spawn timeout 实现需相应调整。
2. `MessageTool._sent_in_turn` 和 `_turn_delivered_media_var` 使用 `ContextVar`，这是 upstream 的并发安全设计。若 future upstream 改回 instance field，本 spec 的 suppression 测试需重写。
3. `_FsTool.create()` 的 `extra_allowed_dirs=[BUILTIN_SKILLS_DIR]` 机制是当前 upstream 特有；若 future upstream 将 BUILTIN_SKILLS_DIR 移入其他模块，import 路径需更新。

## 8. 实现顺序

1. **Phase 1 — 基础设施与测试先行**
   - 读取 Plan，确认本 spec 范围
   - 在 `tests/config/test_config_paths.py` 追加 workspace 路径测试
   - 在 `tests/tools/test_message_tool_suppress.py` 追加缺失场景测试
   - 在 `tests/tools/test_exec_security.py` 追加 benign device path 测试
   - **运行测试，确认 baseline（部分新测试预期失败或跳过）**

2. **Phase 2 — ripgrep 集成 + grep fallback**
   - 实现模块级 `_rg_available()`、`_grep_available()`、`_format_search_results()`、`_run_rg_search()`、`_parse_rg_json()`、`_run_grep_search()`、`_parse_grep_output()`、`_rg_params_supported()`
   - 修改 `GrepTool.execute()`：rg 优先，rg 不可用时 fallback 到系统 grep，两者均不可用时 raise 错误
   - 删除原有的纯 Python 内联搜索逻辑
   - 在 `tests/tools/test_search_tools.py` 追加 rg 优先、grep fallback、双不可用报错测试
   - 运行测试，修复输出格式差异

3. **Phase 3 — Spawn timeout**
   - 修改 `SpawnTool` schema 和 `execute()`
   - 修改 `SubagentManager.spawn()` 和 `_run_subagent()`
   - 在 `tests/agent/tools/test_subagent_tools.py` 追加 timeout 测试
   - 运行测试

4. **Phase 4 — 回归验证**
   - 运行全部相关测试套件：
     ```bash
     pytest tests/tools/test_search_tools.py \
            tests/tools/test_message_tool_suppress.py \
            tests/tools/test_exec_allow_patterns.py \
            tests/tools/test_exec_env.py \
            tests/tools/test_exec_platform.py \
            tests/tools/test_exec_security.py \
            tests/agent/tools/test_subagent_tools.py \
            tests/config/test_config_paths.py
     ```
   - 确认 `git status --short` 仅包含 Pack7 文件，无生产 checkout 修改

## 9. 关键设计决策汇总

1. **ripgrep 失败 fallback 到系统 grep**：rg 调用任何异常（包括缺失、返回码非 1/0、JSON 解析失败）由 `execute()` 捕获并进入系统 grep 路径。系统 grep 在所有 POSIX 环境（Linux/macOS）标配，无需额外安装，性能远超纯 Python 逐行遍历。若 grep 同样不可用，raise 明确错误。不再维护纯 Python 搜索实现。
2. **`extra_allowed_paths` 不复活**：fork 自己删了它，upstream 内部机制已覆盖。不添加公共配置，不添加兼容别名。
3. **MessageTool 以补测试为主**：upstream 语义已实现正确，测试是主要的 deliverable。避免为测试而改实现。
4. **Spawn timeout 是总超时，不是 LLM 超时**：通过 `_run_subagent` 外层 `asyncio.wait_for` 实现，与 `AgentRunSpec.llm_timeout_s` 保持独立。
5. **Shell 边界不改代码**：已有 `_guard_command` 和 4 个 exec 测试文件覆盖，只补一条 benign device path 测试。
6. **不引入 `nanobot/workspace/layout.py`**：`config/paths.py` 已足够表达路径策略。

## 10. 不确定点

1. **rg `--json` 的 `summary` 统计**：不同 rg 版本对 skipped large / binary 的 summary 字段格式是否一致？如果 summary 不可靠，可能需要用 `len(binary)` 和 `len(large)` 在 parse 阶段通过 heuristics 估算，而非精确数字。需在实现时验证。
2. **Spawn timeout 与 subagent 内部 checkpoint 的交互**：如果 `asyncio.wait_for` 在 `_run_subagent` 外层触发 TimeoutError，subagent 的 `AgentRunner` 内部状态（如 file states、session）是否处于不一致状态？当前 upstream 无 subagent 事务回滚机制，timeout 后状态可能残留。需在测试中观察，并在注释中标注为已知限制。
3. **MessageTool WebSocket `chat_id` mismatch 的具体错误返回**：当前 `message.py` 中对 `anon-` 前缀的检查逻辑是否存在？plan 提到此场景，但在当前 upstream 代码中未看到显式的 `anon-` 拒绝逻辑。需在实现阶段 inspect message.py 的 channel-specific 路径。如果 upstream 未实现，spec 要求"返回当前错误"可能意味着需要引入一个小检查，需评估其侵入度。
4. **rg 的 `--max-filesize` 单位**：rg 使用 `--max-filesize` + suffix（如 `2M`），与原有实现中的 `_MAX_FILE_BYTES = 2_000_000` 不完全等价（`2M` = 2 * 1024 * 1024 = 2,097,152）。差异可接受，但需在测试中说明。
