# Spec 1 — Anthropic Claude Code OAuth / Provider Routing

> 历史归档，非当前实现。基座为 ba38f908（2026-05-18），与 upstream/main=3f808d0a 之后的结构不再对应。

## 1. 概述

将 fork 中生产级的 Anthropic Claude Code OAuth 行为（Bearer 认证、beta header 注入、token 自动刷新、credential store 迁移）以最小侵入方式 replay 到 upstream `main`，使 `anthropic_claude_code` 成为与 `openai_codex`、`github_copilot` 并列的一等 OAuth provider。

---

## 2. 行为需求（从 plan 提炼）

| # | 需求 | 优先级 |
|---|---|---|
| BR-1 | `anthropic_claude_code` 在 registry、config schema、CLI status 中作为一等 OAuth provider 注册。 | P0 |
| BR-2 | 当使用 OAuth token（`sk-ant-oat...`）时，HTTP 认证方式为 `Authorization: Bearer <token>`，而非 `x-api-key`。 | P0 |
| BR-3 | Claude Code 所需的 beta headers（`anthropic-beta`）仅在 Claude Code 产品模式下自动注入；普通 Anthropic API key 模式不受影响。 | P0 |
| BR-4 | Claude Code 身份 system prompt 仅在 Claude Code 产品模式下自动 prepend；普通 API key 模式不受影响。 | P0 |
| BR-5 | OAuth credentials 可从 nanobot 自有 credential store（`~/.nanobot/oauth_credentials.json`）加载，并支持从 Claude CLI store（`~/.claude/.credentials.json`）只读迁移。 | P0 |
| BR-6 | 过期或临近过期（5 分钟 margin）的 OAuth token 在每次请求前自动刷新，并发请求只触发一次刷新。刷新后的 token 写回 store。 | P0 |
| BR-7 | Claude Code 产品模式下 tool names 自动规范化到 Claude Code canonical casing（如 `read` → `Read`）；非 Claude Code 模式下保持原样。 | P0 |
| BR-8 | `provider login anthropic-claude-code` 与 `provider logout anthropic-claude-code` 在 CLI 中可用（若 upstream 无 generic OAuth hook，则使用现有 per-provider 注册机制）。 | P1 |
| BR-9 | 刷新失败时，通过现有 provider error path 返回/抛出，不破坏旧 credentials。 | P0 |

---

## 3. 架构分析

### 3.1 相关模块与职责

| 模块 | 职责 | 与 OAuth 的关系 |
|---|---|---|
| `nanobot/providers/registry.py` | `ProviderSpec` dataclass + `PROVIDERS` 列表。决定 provider 身份、匹配优先级、backend 类型、OAuth 标志。 | 已有 `is_oauth` 字段，新增 spec 即可。 |
| `nanobot/config/schema.py` | `ProvidersConfig` Pydantic model，每个 provider 一个字段。`get_provider()` / `get_provider_name()` 做 model→provider 路由。 | 已有 `exclude=True` 的 OAuth provider 字段（`openai_codex`、`github_copilot`），可复用模式。 |
| `nanobot/providers/factory.py` | `_make_provider_core()` 根据 backend 字符串分发，构造具体 provider 实例。 | 对 `backend == "anthropic"` 分支需增加 OAuth 路径：加载 credential store，传 `auth_token` 而非 `api_key`。 |
| `nanobot/providers/anthropic_provider.py` | `AnthropicProvider` 类。封装 AsyncAnthropic SDK，处理消息转换、thinking、tool calls、streaming。 | 核心修改点：增加 OAuth 模式检测、header 注入、system prompt prepend、token refresh、tool name normalization。 |
| `nanobot/providers/base.py` | `LLMProvider` 基类，定义 `chat()` / `chat_stream()` 接口和重试逻辑。 | 无需修改。OAuth 刷新应在具体 provider 的 `chat()` / `chat_stream()` 入口调用。 |
| `nanobot/cli/commands.py` | CLI 命令，包括 `provider login/logout/status`。 | 已有 `_register_login` / `_register_logout` 装饰器模式，可复用。 |

### 3.2 上游现有扩展点

1. **Registry 扩展**：`ProviderSpec(..., is_oauth=True)` 已是公开接口。
2. **Config 扩展**：`ProvidersConfig` 中添加 `Field(default_factory=ProviderConfig, exclude=True)` 已是现有 OAuth provider 的通行做法。
3. **Provider 构造扩展**：`factory.py` 的 `if backend == "anthropic"` 分支是显式分发点，可安全扩展。
4. **CLI 注册扩展**：`_register_login(name)` / `_register_logout(name)` 装饰器是现有机制。
5. **SDK `auth_token`**：`AsyncAnthropic.__init__` 已支持 `auth_token` 参数（`anthropic>=0.50`），这是 upstream 依赖的公开 SDK API。

### 3.3 上游缺少的扩展点

- **Credential store 抽象**：upstream 没有统一的 OAuth credential store 接口。`openai_codex` 依赖外部包 `oauth_cli_kit` 的 storage；`github_copilot` 自建 `FileTokenStorage`。本次需新增最小 store 模块。
- **Token refresh hook**：upstream `LLMProvider` 基类没有 `pre_request_hook` 或类似的扩展点。refresh 逻辑必须内嵌在 `AnthropicProvider.chat()` / `chat_stream()` 中。

---

## 4. 技术方案

### 4.1 模块改动总览

| 文件 | 动作 | 侵入度 |
|---|---|---|
| `nanobot/providers/registry.py` | 修改：在 `PROVIDERS` 列表末尾追加 `ProviderSpec(name="anthropic_claude_code", ..., is_oauth=True)` | 低（新增 registry entry） |
| `nanobot/config/schema.py` | 修改：在 `ProvidersConfig` 中新增 `anthropic_claude_code` 字段 | 低（新增 config field，与现有 OAuth provider 同模式） |
| `nanobot/providers/oauth_store.py` | **新增** | 低（纯新增，无既有代码耦合） |
| `nanobot/providers/anthropic_provider.py` | 修改 | 中（增加 OAuth 分支，不改动 API key 分支） |
| `nanobot/providers/factory.py` | 修改：在 `backend == "anthropic"` 分支中增加 OAuth 路径 | 低（扩展已有分发分支） |
| `nanobot/cli/commands.py` | 修改：`_PROVIDER_DISPLAY` + login/logout handlers | 低（复用现有注册机制） |

### 4.2 新增模块：`nanobot/providers/oauth_store.py`

**设计原则**：
- 不依赖 `oauth_cli_kit`，避免引入额外依赖。
- 两个只读源（nanobot store、Claude CLI store），一个写入目标（nanobot store）。
- 数据模型自包含，不耦合 provider 实现。

```python
# 公开接口（承诺稳定）

from dataclasses import dataclass
from pathlib import Path

@dataclass
class OAuthCredentials:
    access_token: str
    refresh_token: str
    expires_at_ms: int          # Unix timestamp in milliseconds

    def is_expired(self, margin_ms: int = 5 * 60 * 1000) -> bool: ...

class OAuthCredentialStore:
    """Read/write OAuth credentials from disk.

    Two read sources (priority order):
      1. ~/.nanobot/oauth_credentials.json  (read/write)
      2. ~/.claude/.credentials.json        (read-only migration)
    """

    def __init__(
        self,
        store_path: Path | None = None,          # default: get_data_dir() / "oauth_credentials.json"
        cli_creds_path: Path | None = None,       # default: Path.home() / ".claude" / ".credentials.json"
    ) -> None: ...

    def load(self) -> OAuthCredentials | None: ...
    def save(self, creds: OAuthCredentials) -> None: ...

async def refresh_anthropic_token(refresh_token: str) -> OAuthCredentials:
    """Exchange refresh token for new access token via Anthropic OAuth token endpoint.

    Raises RuntimeError on failure (network or non-2xx).
    """
    ...
```

**实现要点**：
- `load()` 逻辑：先读 nanobot store；若无有效 credentials，再读 Claude CLI store（解析 `claudeAiOauth` 字段）。
- `save()` 只写 nanobot store，格式为 JSON，包含 `access_token`、`refresh_token`、`expires_at_ms`。
- `refresh_anthropic_token()` 使用 `httpx.AsyncClient` POST 到 `https://api.anthropic.com/v1/oauth/token`（client_id 固定为 `claude-cli`）。

### 4.3 修改 `AnthropicProvider`

#### 4.3.1 `__init__` 签名扩展

```python
def __init__(
    self,
    api_key: str | None = None,
    api_base: str | None = None,
    default_model: str = "claude-sonnet-4-20250514",
    extra_headers: dict[str, str] | None = None,
    auth_token: str | None = None,                     # NEW — bearer token 认证
    product_mode: str = "default",                     # NEW — "default" | "claude_code"
    credential_store: OAuthCredentialStore | None = None,  # NEW
) -> None:
```

**初始化逻辑**：
**认证模式与产品模式正交分离**（review 修正）：

1. `self._auth_mode = "bearer_token" if auth_token else "api_key"` — 控制 SDK 构造和 token refresh。
2. `self._product_mode = product_mode` — 控制 beta headers、system prompt cc_block、tool name normalization。
3. OAuth 检测唯一来源是 `auth_token is not None`。**删除** `api_key.startswith("sk-ant-oat")` 前缀猜测——env var 场景由 factory 层处理（读取 env 后作为 `auth_token` 传入），不在 provider 内部做前缀检测。
4. factory.py 中根据 `spec.is_oauth` 同时设两者（`auth_token=...`, `product_mode="claude_code"`），默认行为不变。但两者是独立参数，未来可以组合出"API key + Claude Code 模式"或"OAuth + 非 Claude Code 模式"。
3. 否则 → 普通 API key 模式。

**Client 构造**：
- API key 模式：`AsyncAnthropic(api_key=api_key, base_url=api_base, default_headers=extra_headers, max_retries=0)`（与现有逻辑一致）。
- OAuth 模式：`AsyncAnthropic(auth_token=auth_token, base_url=api_base, default_headers=merged_headers, max_retries=0)`。
  - `merged_headers` 在 `extra_headers` 基础上注入 `anthropic-beta`（见 4.3.3）。

#### 4.3.2 Token refresh 机制

```python
class AnthropicProvider:
    ...
    _refresh_lock: asyncio.Lock          # NEW
    _token_expires_at_ms: int = 0        # NEW
    _credential_store: OAuthCredentialStore | None = None   # NEW

    def _update_oauth_token(self, new_access_token: str) -> None:
        """Hot-swap the auth token on the existing SDK client.
        
        Fallback: if SDK property assignment fails (future SDK version
        makes auth_token read-only), rebuild the client entirely.
        """
        try:
            self._client.auth_token = new_access_token
        except (AttributeError, TypeError):
            logger.debug("auth_token property assignment failed, rebuilding client")
            self._client = self._build_client(auth_token=new_access_token)

    async def _ensure_valid_token(self) -> None:
        """Refresh OAuth token if expired or near expiry.

        Must be called at the top of chat() and chat_stream().
        Concurrent callers are serialized by _refresh_lock; after lock
        acquisition, re-check expiry to avoid redundant refresh.
        """
        if self._auth_mode != "bearer_token" or self._credential_store is None:
            return
        now_ms = int(time.time() * 1000)
        if now_ms < self._token_expires_at_ms - TOKEN_REFRESH_MARGIN_MS:
            return

        async with self._refresh_lock:
            # Double-check after acquiring lock
            now_ms = int(time.time() * 1000)
            if now_ms < self._token_expires_at_ms - TOKEN_REFRESH_MARGIN_MS:
                return

            creds = self._credential_store.load()
            if creds is None:
                logger.warning("AnthropicProvider: no OAuth credentials found, skipping refresh")
                return

            try:
                logger.info("AnthropicProvider: OAuth token expired, refreshing...")
                new_creds = await refresh_anthropic_token(creds.refresh_token)
                self._token_expires_at_ms = new_creds.expires_at_ms
                self._update_oauth_token(new_creds.access_token)
                self._credential_store.save(new_creds)
                logger.info("AnthropicProvider: OAuth token refreshed successfully")
            except Exception as e:
                logger.error("AnthropicProvider: OAuth token refresh failed: {}", e)
                # 不抛异常：让请求继续用旧 token，SDK 会在收到 401 时走正常 error path
```

**关键决策**：refresh 失败时不阻断请求。原因：
- 如果 token 只是临近过期而非已过期，旧 token 可能仍可用。
- 如果 token 已过期，SDK 请求会返回 401，走 `_handle_error` → 用户看到正常错误提示。
- 这比在 refresh 网络故障时直接抛异常更优雅。

#### 4.3.3 `_build_kwargs` 的 OAuth 注入

在 `_build_kwargs` 中，按 `_product_mode` 和 `_auth_mode` 分别控制行为（review 修正：认证和产品行为正交分离）：

1. **System prompt prepend**（由 `_product_mode` 控制，不由 `_auth_mode` 控制）：
   ```python
   if self._product_mode == "claude_code":
       cc_block = {"type": "text", "text": _CLAUDE_CODE_SYSTEM_PROMPT}
       # 先规范化为 list，再 prepend（消除三层分支）
       if isinstance(system, str) and system:
           system = [{"type": "text", "text": system}]
       elif not isinstance(system, list):
           system = []
       kwargs["system"] = [cc_block, *system]
   ```

2. **Beta headers per-request**（由 `_product_mode` 控制）：
   ```python
   request_headers = dict(self.extra_headers)
   if self._product_mode == "claude_code":
       request_headers = _merge_beta_header(request_headers, _CLAUDE_CODE_BETAS)
   if request_headers:
       kwargs["extra_headers"] = request_headers
   ```

3. **Tool name normalization**（在 `_convert_tools` 中，由 `_product_mode` 控制）：
   ```python
   name = _to_claude_code_name(raw_name) if self._product_mode == "claude_code" else raw_name
   ```

4. **Token refresh**（由 `_auth_mode` 控制，与产品模式无关）：
   `_ensure_valid_token()` 仅在 `self._auth_mode == "bearer_token"` 时执行。

#### 4.3.4 `chat()` / `chat_stream()` 入口

在两个方法的开头插入：
```python
await self._ensure_valid_token()
```

**与上游 retry 机制的兼容性**：
上游 `AnthropicProvider.chat()` 内部使用 `self._run_with_retry(lambda: self._client.messages.create(...))`。refresh 放在 `_run_with_retry` 外层（即方法最开头），确保：
- refresh 只执行一次，不会被 retry 重复触发。
- refresh 本身的异常不影响 retry 语义。

### 4.4 修改 `factory.py`

在 `_make_provider_core` 的 `backend == "anthropic"` 分支中：

```python
elif backend == "anthropic":
    from nanobot.providers.anthropic_provider import AnthropicProvider
    from nanobot.providers.oauth_store import OAuthCredentialStore

    # 判断是否为 anthropic_claude_code OAuth provider
    is_oauth = spec is not None and getattr(spec, "is_oauth", False)

    if is_oauth:
        credential_store = OAuthCredentialStore()
        creds = credential_store.load()
        auth_token = creds.access_token if creds else None
        provider = AnthropicProvider(
            api_key=None,                       # 不传 api_key，避免 SDK 优先使用 x-api-key
            api_base=api_base,
            default_model=model,
            extra_headers=extra_headers,
            auth_token=auth_token,
            credential_store=credential_store,
        )
    else:
        provider = AnthropicProvider(
            api_key=api_key,
            api_base=api_base,
            default_model=model,
            extra_headers=extra_headers,
        )
```

**注意**：`api_key` 和 `auth_token` 必须互斥传入 SDK。若 `auth_token` 存在，必须显式置 `api_key=None`，否则 SDK 可能优先使用 `api_key` 的 `x-api-key` 认证。

### 4.5 修改 `registry.py`

在 `PROVIDERS` 列表的 OAuth provider 区域（`github_copilot` 之后）追加：

```python
ProviderSpec(
    name="anthropic_claude_code",
    keywords=("claude-code", "anthropic_claude_code"),
    env_key="ANTHROPIC_OAUTH_TOKEN",          # 允许通过 env var 直接传入 token
    display_name="Anthropic Claude Code",
    backend="anthropic",
    default_api_base="https://api.anthropic.com",
    is_oauth=True,
),
```

### 4.6 修改 `config/schema.py`

在 `ProvidersConfig` 中，与 `github_copilot` 相邻位置新增：

```python
anthropic_claude_code: ProviderConfig = Field(default_factory=ProviderConfig, exclude=True)
```

### 4.7 修改 `cli/commands.py`

1. **`_PROVIDER_DISPLAY`** 中新增：
   ```python
   "anthropic_claude_code": "Anthropic Claude Code",
   ```

2. **Login handler**（最小实现，不引入 device flow）：
   ```python
   @_register_login("anthropic_claude_code")
   def _login_anthropic_claude_code() -> None:
       from nanobot.providers.oauth_store import OAuthCredentialStore

       store = OAuthCredentialStore()
       creds = store.load()
       if creds:
           console.print(f"[green]✓ Already authenticated with Anthropic Claude Code[/green]")
           console.print("[dim]Credentials loaded from nanobot store or migrated from Claude CLI.[/dim]")
           return

       console.print("[yellow]! No credentials found.[/yellow]")
       console.print("[dim]Please authenticate using the Claude CLI first:[/dim]")
       console.print("  [bold]claude login[/bold]")
       console.print("Then run this command again to migrate the credentials.")
       raise typer.Exit(1)
   ```

3. **Logout handler**：
   ```python
   @_register_logout("anthropic_claude_code")
   def _logout_anthropic_claude_code() -> None:
       from nanobot.config.paths import get_data_dir
       from nanobot.providers.oauth_store import OAuthCredentialStore

       store = OAuthCredentialStore()
       paths = [store.store_path]          # store_path 是 Path 属性，需暴露
       _remove_oauth_files(paths, provider_label="Anthropic Claude Code")
   ```

### 4.8 数据流与调用链

```
用户输入消息
    ↓
AgentLoop → ProviderSnapshot → make_provider(config)
    ↓
factory._make_provider_core()
    ├── 模型路由：config.get_provider_name(model) → "anthropic_claude_code"
    ├── registry.find_by_name("anthropic_claude_code") → spec.is_oauth == True
    └── backend == "anthropic" 分支
        ├── OAuthCredentialStore.load() → 读取 ~/.nanobot/oauth_credentials.json
        │   └── fallback: 读取 ~/.claude/.credentials.json (read-only)
        └── AnthropicProvider(auth_token=..., credential_store=...)
            ├── __init__ 构造 AsyncAnthropic(auth_token=...)
            └── 设置 _auth_mode="bearer_token", _product_mode="claude_code"
    ↓
AnthropicProvider.chat() / chat_stream()
    ├── await _ensure_valid_token()
    │   ├── 检查 token 是否临近过期
    │   ├── 是 → async with _refresh_lock:
    │   │   ├── refresh_anthropic_token(refresh_token) → HTTP POST
    │   │   ├── _update_oauth_token(new_access_token)
    │   │   └── store.save(new_creds)
    │   └── 否 → 直接返回
    ├── _build_kwargs() → 注入 system prompt、beta headers、normalize tool names
    └── _client.messages.create() / stream()
```

### 4.9 错误处理策略

| 场景 | 行为 |
|---|---|
| OAuth credentials 不存在 | `_ensure_valid_token()` 记录 warning，请求继续。SDK 返回 401，走 `_handle_error` → 用户看到 auth error。 |
| Token refresh 网络失败 | 记录 error，请求继续用旧 token。不破坏 store 中的旧 credentials。 |
| Token refresh 返回 4xx | `refresh_anthropic_token()`  raise `RuntimeError`，被 `_ensure_valid_token()` catch 并记录 error。请求继续。 |
| 并发 refresh | `_refresh_lock` 保证串行化；获得锁后 double-check expiry，避免重复刷新。 |
| Claude CLI store 不存在 | `load()` 静默跳过，不影响功能。 |

---

## 5. 最小侵入评估

| 改动点 | 侵入度 | 说明 / 替代方案 |
|---|---|---|
| `registry.py` 新增 `ProviderSpec` | **新增文件内容** | 零侵入，复用 `is_oauth` 字段。 |
| `config/schema.py` 新增字段 | **新增文件内容** | 零侵入，复用 `exclude=True` 模式。 |
| `factory.py` anthropic 分支扩展 | **扩展已有接口** | 侵入度低。若 upstream 未来引入 provider 构造插件机制，可将 OAuth 逻辑抽成 factory hook。**Review point**。 |
| `AnthropicProvider.__init__` 新增参数 | **修改已有实现** | 侵入度中。新增可选参数 `auth_token`、`credential_store`，默认值保持向后兼容。 |
| `AnthropicProvider.chat/chat_stream` 插入 refresh | **修改已有实现** | 侵入度中。两行 `await self._ensure_valid_token()`。若 upstream 未来提供 `pre_request` hook，可迁移到 hook 中。**Review point**。 |
| `AnthropicProvider._build_kwargs` OAuth 注入 | **修改已有实现** | 侵入度中。增加条件分支，不影响 API key 路径。 |
| `AnthropicProvider._convert_tools` 规范化 | **修改已有实现** | 侵入度低。一行条件表达式。 |
| 新增 `oauth_store.py` | **新增文件** | 零侵入。 |
| `cli/commands.py` login/logout | **扩展已有接口** | 侵入度低。复用 `_register_login` / `_register_logout` 装饰器。 |

---

## 6. 测试方案

### 6.1 测试文件清单

| 测试文件 | 覆盖范围 |
|---|---|
| `tests/providers/test_anthropic_oauth_client.py` | OAuth 检测、header 注入、system prompt、tool name normalization、`_build_kwargs` 分支 |
| `tests/providers/test_anthropic_token_refresh.py` | `_ensure_valid_token` 集成：valid token、expired refresh、并发 serialize、save persistence、refresh failure |
| `tests/providers/test_anthropic_provider.py` | 已有测试的回归：确认 API key 模式不受 OAuth 改动影响 |
| `tests/providers/test_oauth_store.py` | `OAuthCredentialStore.load/save`、Claude CLI migration、边界情况 |
| `tests/cli/test_commands_provider.py` | `provider login/logout/status` 对 `anthropic_claude_code` 的识别 |

### 6.2 具体测试用例

#### `tests/providers/test_anthropic_oauth_client.py`

```python
class TestOAuthDetection:
    def test_bearer_auth_when_auth_token_provided(self): ...
    def test_api_key_auth_when_no_auth_token(self): ...
    def test_product_mode_claude_code_enables_betas(self): ...
    def test_product_mode_default_skips_betas(self): ...
    def test_rejects_none(self): ...

class TestOAuthHeaders:
    def test_oauth_mode_uses_bearer_auth(self):
        # mock AsyncAnthropic init, assert auth_token=..., api_key=None
        ...
    def test_api_key_mode_uses_x_api_key(self):
        ...
    def test_oauth_injects_beta_headers(self):
        # call _build_kwargs, assert "anthropic-beta" contains expected betas
        ...
    def test_api_key_mode_no_beta_headers(self):
        ...
    def test_oauth_merge_existing_beta_header(self):
        # extra_headers already has anthropic-beta, assert dedup & preserve order
        ...

class TestOAuthSystemPrompt:
    def test_oauth_prepends_claude_code_identity(self):
        ...
    def test_oauth_prepends_before_existing_system(self):
        ...
    def test_api_key_mode_no_system_prompt_injection(self):
        ...

class TestOAuthToolNames:
    def test_oauth_normalizes_known_tools(self):
        # "read" -> "Read", "bash" -> "Bash"
        ...
    def test_oauth_leaves_unknown_tools_unchanged(self):
        ...
    def test_api_key_mode_no_normalization(self):
        ...
```

#### `tests/providers/test_anthropic_token_refresh.py`

```python
@pytest.mark.asyncio
async def test_no_refresh_when_token_valid(): ...

@pytest.mark.asyncio
async def test_expired_token_refreshes_before_request(): ...

@pytest.mark.asyncio
async def test_concurrent_requests_refresh_only_once(): ...

@pytest.mark.asyncio
async def test_refreshed_credentials_are_saved_to_store(): ...

@pytest.mark.asyncio
async def test_refresh_failure_does_not_corrupt_old_credentials(): ...

@pytest.mark.asyncio
async def test_refresh_failure_allows_request_to_continue(): ...
```

#### `tests/providers/test_oauth_store.py`

```python
class TestLoad:
    def test_loads_from_nanobot_store(self): ...
    def test_migrates_from_claude_cli_store(self): ...
    def test_prefers_nanobot_store_over_cli_store(self): ...
    def test_returns_none_when_both_missing(self): ...
    def test_returns_none_when_cli_store_malformed(self): ...

class TestSave:
    def test_saves_to_nanobot_store_only(self): ...
    def test_overwrites_existing(self): ...

class TestIsExpired:
    def test_not_expired_when_far_future(self): ...
    def test_expired_when_past(self): ...
    def test_expired_within_margin(self): ...
```

#### `tests/cli/test_commands_provider.py`

```python
def test_status_shows_oauth_for_anthropic_claude_code(): ...
def test_login_handler_registered_for_anthropic_claude_code(): ...
def test_logout_handler_registered_for_anthropic_claude_code(): ...
```

### 6.3 Mock 策略

- **SDK client**：mock `AsyncAnthropic` 的 `messages.create` / `stream`，验证传入的 `auth_token` / `api_key`。
- **HTTP refresh**：mock `httpx.AsyncClient.post`（`refresh_anthropic_token` 内部），返回可控的 JSON 响应。
- **Credential store**：使用 `MagicMock(spec=OAuthCredentialStore)` 或直接操作 `tmp_path` 上的文件。
- **时间**：patch `time.time()` 以控制 token expiry 判定。
- **并发 refresh**：使用 `asyncio.gather` 并发调用 `provider.chat()`，配合 `asyncio.sleep(0.01)` 的 mock refresh 函数，断言 refresh 调用次数为 1。

---

## 7. 向前兼容性

| 设计决策 | 依赖的 upstream 特定版本细节 | 未来升级 review point |
|---|---|---|
| `AsyncAnthropic` 支持 `auth_token` 参数 | `anthropic` SDK >= 0.50 | 若 SDK 移除 `auth_token`，需改用 `default_headers={"Authorization": "Bearer ..."}`。 |
| `self._client.auth_token = new_access_token` | SDK 公开属性赋值 | 已在 `_update_oauth_token` 中加 try/except fallback 到 `_build_client()` 重建，降低风险。 |
| `AnthropicProvider.__init__` 新增可选参数 | 当前 upstream 的构造函数签名 | 若 upstream 重构 provider 为 dataclass 或 pydantic model，需调整参数注入方式。 |
| `factory.py` 硬编码 `backend == "anthropic"` 分支 | upstream 的 provider 构造分发模式 | 若 upstream 引入 provider factory registry / plugin 机制，应将 OAuth 逻辑迁移到 plugin。 |
| `AnthropicProvider.chat/chat_stream` 中内嵌 refresh | 当前 upstream 无 `pre_request` hook | 若 upstream 基类增加 `pre_request()` lifecycle hook，应将 `_ensure_valid_token()` 迁移到 hook。 |
| `ProviderSpec.is_oauth` 字段 | upstream registry schema | 若 upstream 重命名或移除该字段，需同步修改。 |
| `oauth_store.py` 的 store 路径 | `nanobot.config.paths.get_data_dir()` | 若 upstream 改变 data dir 布局，需同步修改。 |
| `_merge_beta_header` 注入 `anthropic-beta` | Anthropic Messages API 当前行为 | Anthropic 可能在未来版本移除这些 beta flags，届时需调整 `_OAUTH_BETAS` 列表。 |

---

## 8. 实现顺序

按依赖关系排序，每步完成后可独立验证：

1. **新增 `oauth_store.py`** + `test_oauth_store.py`
   - 不依赖任何现有 provider 逻辑，可先实现。
   - 验证：store load/save/migration 全部通过。

2. **Registry + Config 注册** + `test_cli/test_commands_provider.py`
   - 在 `registry.py` 和 `schema.py` 中新增 `anthropic_claude_code`。
   - 验证：`provider status` 能正确识别新 provider；config 解析无报错。

3. **`AnthropicProvider` OAuth 模式扩展** + `test_anthropic_oauth_client.py`
   - 修改 `__init__`、`_build_kwargs`、`_convert_tools`。
   - 验证：header 注入、system prompt、tool normalization、Bearer auth。

4. **`factory.py` OAuth 路径** + 集成验证
   - 在 `backend == "anthropic"` 分支中增加 OAuth 路径。
   - 验证：通过 `make_provider(config)` 构造出的 `AnthropicProvider` 在 OAuth 模式下正确加载 credential store。

5. **Token refresh 集成** + `test_anthropic_token_refresh.py`
   - 在 `chat()` / `chat_stream()` 中插入 `_ensure_valid_token()`。
   - 验证：过期刷新、并发 serialize、失败不阻断。

6. **CLI login/logout handlers**
   - 在 `commands.py` 中注册 handlers。
   - 验证：`provider login anthropic-claude-code`、`provider logout anthropic-claude-code` 可用。

---

## 9. 报告

### 9.1 文件路径

```
/root/git_code/nanobot/.worktrees/sync-upstream-2026-05-merge/docs/superpowers/specs/2026-05-18-spec1-anthropic-oauth-provider-routing.md
```

### 9.2 关键设计决策

1. **OAuth token 通过 SDK `auth_token` 参数传递**：这保证了 Anthropic SDK 内部自动使用 `Authorization: Bearer ...`，而不是我们手动构造 header。这是比 fork 旧代码更优雅的实现，因为它利用了 SDK 的公开接口。
2. **Refresh 失败不阻断请求**：避免了网络抖动导致服务完全不可用。过期 token 会让 SDK 返回 401，走正常 error path。
3. **Credential store 独立成模块**：不耦合 `oauth_cli_kit`，也不依赖 provider 实现。这个模块未来可被其他 OAuth provider 复用。
4. **Login handler 为 migration-only**：不实现完整的 OAuth device flow（这需要一个 OAuth app registration 和回调服务器），而是依赖用户先通过 Claude CLI 登录，然后 nanobot 读取并迁移 credentials。这符合最小侵入原则。
5. **Tool name normalization 在 `_convert_tools` 中条件执行**：只在 Claude Code 产品模式生效（`_product_mode == "claude_code"`），不影响普通 API key 用户。
6. **认证模式与产品模式正交分离**（review 修正）：`_auth_mode` 控制 SDK 构造和 token refresh，`_product_mode` 控制 beta headers/system prompt/tool normalization。两者独立，不再用一个 `_is_oauth` 焊死。

### 9.3 不确定点

1. **Login handler 的完整度**：如果产品要求 `provider login anthropic-claude-code` 实现完整的 device flow（不依赖 Claude CLI），则需要一个独立的 OAuth 登录实现（类似 `github_copilot_provider.py` 的 device flow）。当前 spec 将其降级为 migration-only，是否满足需求需产品确认。
2. **`auth_token` 属性赋值**：已在 `_update_oauth_token` 中加 try/except fallback 到 `_build_client()` 重建，即使 SDK 未来限制该属性为只读也不会运行时崩溃。
3. **Beta headers 列表**：`_OAUTH_BETAS` 的具体值来源于 fork 的生产代码。Anthropic 可能在未来 API 版本中移除某些 beta flag，需要持续跟踪。
4. **Upstream 测试覆盖度**：upstream 当前的 `tests/providers/test_anthropic_provider.py` 是否存在（工作目录中未找到）。如果不存在，OAuth 回归测试需要覆盖更多 API key 模式的基础 case，以防止 OAuth 改动破坏普通 Anthropic 用户。
