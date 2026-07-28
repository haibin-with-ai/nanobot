# Pack B — Anthropic Claude Code OAuth provider（8 笔）

调查范围：`ba38f908..upstream/main`（upstream provider 层 143 笔 commit），全程只读。

## 三个重点核实结论（先给答案）

**1. 上游 provider 层的注册/解析机制没有被推翻，本地 OAuth 接入点全部还在。**
`nanobot/providers/registry.py` 的 `ProviderSpec` dataclass 与 `find_by_name()` 仍在（25 笔 commit，+271 行，全是加字段/加 spec）；
`factory.py` 的 `_resolve_model_preset()` / `_make_provider_core()` 双函数结构不变（17 笔，+109 行），
`elif backend == "anthropic":` 分支仍在（`git show upstream/main:nanobot/providers/factory.py` line 118）。
`merge-tree --write-tree main upstream/main` 的冲突清单里**没有 registry.py**——本地那条 `anthropic_claude_code` spec 能干净合入。
上游还独立加了两个对本地有利的东西：`ProviderSpec.is_oauth`（registry.py:74，本地这笔用的正是它，说明字段是基座就有的，上游继续在用：xai / codex / copilot 三处 `is_oauth=True`）、
以及 `ProviderSpec.default_extra_headers` + `factory._provider_extra_headers()`——spec 级声明式请求头通道。

**2. 上游没有「OAuth 基类」，但有一个外部包 `oauth-cli-kit`，而本地 oauth_store.py 已经在复用它。**
`git grep -n oauth_cli_kit upstream/main` → `openai_codex_provider.py:13`、`github_copilot_provider.py`、`cli/commands.py`；
基座 ba38f908 就已依赖（本地 pyproject `oauth-cli-kit>=0.1.3`，上游已提到 `>=0.1.6`，`FileTokenStorage.__init__` / `OAuthToken` 字段两版一致，升级无破坏）。
我把 0.1.6 的 wheel 解出来看了：只有 `providers/openai_codex.py`，**没有 anthropic/claude 模块**，CLI 凭据导入函数 `_try_import_codex_cli_token` 硬编码 codex 路径、不可插拔。
所以本地 `oauth_store.py`（`OAuthCredentialStore` 包 `FileTokenStorage` + `_try_migrate_from_claude_cli`）不是重复造轮子，它就是在 kit 上补 Anthropic 那一块。**结论：不改用上游设施，保留本地实现。**
反倒是上游新增的 `xai_oauth.py` 自己手写了一份（`from filelock import FileLock`、`get_xai_oauth_token(force_refresh=...)`），根本没走 kit——上游自己都没统一，没有可复用基础设施可言。

**3. 上游没有通用的 provider/model 斜杠剥离；9e251310 / 182ea6b8 没有被吸收。**
上游只有三处半吊子：`config/schema.py` 用前缀**推断 provider**（但不改 model 串）、
`ProviderSpec.strip_model_prefix` 只被 `openai_compat_provider.py` 和 codex 消费（`git grep -n strip_model_prefix upstream/main` 结果不含 anthropic_provider）、
`anthropic_provider.py:146` 的 `_strip_prefix()` 只认字面量 `"anthropic/"`（基座就有，未变）。
即 `anthropic_claude_code/claude-sonnet-4-6` 这种形式在上游依然会把前缀发给 API。本地那笔仍然必要。

---

### 9edd4f90 feat(provider): add Anthropic Claude Code OAuth provider (Spec1)
- 分类：**[3] 纯本地**（重放难度：中）
- 本地做了什么：新增 `providers/oauth_store.py`（凭据存取 + 刷新）、registry 加 `anthropic_claude_code` spec（`is_oauth=True`, backend=anthropic）、factory 的 anthropic 分支按 `spec.is_oauth` 走 OAuth 构造、`AnthropicProvider` 加 `auth_token` / `product_mode` / `credential_store` 参数并抽出 `_build_client()`、CLI 挂 `provider login` 处理器。
- 上游现状：`git grep -rn -i "anthropic_claude_code|anthropic-claude-code" upstream/main -- nanobot/` **零命中**；上游 OAuth 只有 codex / copilot / xai 三家。承载结构全在：`ProviderSpec.is_oauth`(registry.py:74)、factory anthropic 分支(line 118)、CLI 的 `_register_login` 装饰器分发（上游 commands.py 同样存在）。
- 判定理由：上游没有 Claude Code OAuth，必须重放；接入点未被重构，属于加分支而非改架构。
- 风险/注意：
  - `anthropic_provider.py` 上游 16 笔 commit / +203 行，`__init__` 被改过：新增 `_normalize_base_url(self.api_base)` 和 default_model `claude-sonnet-4-6`。本地把 client 构造抽成 `_build_client()`，**重放时必须把 `_normalize_base_url` 折进 `_build_client`**，否则丢掉上游对第三方 anthropic 端点的 base_url 规整。
  - factory 的 anthropic 分支上游改成了走 `_provider_extra_headers(spec, p)`，本地那段 if/else 两边都要带上这个新参数。
  - CLI commands.py 上游新增 xai login/logout 与 `--set-main`，本地 login handler 是加一个装饰器函数，冲突可机械解。

### 06eaa4c4 fix: remove expired token-efficient-tools beta header
- 分类：**[3] 纯本地**（重放难度：低）
- 本地做了什么：从 Claude Code 请求头里删掉过期的 `token-efficient-tools-2025-02-19` beta。
- 上游现状：该 beta 串在上游全仓无引用（依赖 9edd4f90 才存在）。
- 判定理由：是对本地自己那笔的修正，随 9edd4f90 一起以「最终态」重放即可，不必逐笔回放。
- 风险/注意：建议把 06eaa4c4 + 248d459b 折叠进 9edd4f90 的最终代码，减少 3 次头部改动的中间态。

### 248d459b fix: Claude Code OAuth headers（claude-code-20250219 / oauth-2025-04-20 / user-agent / x-app）
- 分类：**[3] 纯本地**（重放难度：低）
- 本地做了什么：在 provider 内硬编码一组 Claude Code 身份头（anthropic-beta、User-Agent、X-App 等）。
- 上游现状：上游给了更合适的落点——`ProviderSpec.default_extra_headers`，并已有先例：`registry.py:530` 的 `kimi_coding` spec（backend=anthropic）就是靠 `default_extra_headers=(("User-Agent", "claude-code/0.1.0"),)` 注入的，由 `factory._provider_extra_headers()` 统一下发。
- 判定理由：功能上游没有（必须重放），但**实现方式建议改造**：静态那几个头搬进 `anthropic_claude_code` spec 的 `default_extra_headers`，provider 里只留与 auth 模式相关的动态部分。
- 风险/注意：这是本 pack 唯一一处「重放时顺手对齐上游惯例」的机会，成本极低。

### d9a16a0a fix: OAuth migration 读 ~/.claude/.credentials.json，提前 5 分钟视为过期
- 分类：**[3] 纯本地**（重放难度：低）
- 本地做了什么：`oauth_store._try_migrate_from_claude_cli()` 从 Claude CLI 凭据文件迁移；`anthropic_provider.py:690` 用 `expires_at - now > 300_000` 做 5 分钟提前量。
- 上游现状：`oauth_cli_kit` 0.1.6 的 CLI 导入只覆盖 codex（`_try_import_codex_cli_token`），无 Anthropic 路径、无扩展点；上游 provider 层也无 `.claude/.credentials.json` 引用。
- 判定理由：kit 帮不上忙，纯本地逻辑，落在上游不存在的新文件 `oauth_store.py` 上（merge 无冲突）。

### 948c2163 fix: OAuth migration 也读 config 旁边的 oauth_credentials.json
- 分类：**[3] 纯本地**（重放难度：低）
- 本地做了什么：`_try_load_flat_credentials()` 增加第二个迁移来源。
- 上游现状：同上，上游无对应实现。
- 判定理由：同一文件的增量补丁，与 d9a16a0a 一起以最终态重放。

### 6e035f09 fix: reload OAuth token from store on auth error + allow auth errors to fallback
- 分类：**[2] 平行实现**（拆两半看）
- 本地做了什么：① provider 侧加 `_reload_token_from_store()` / `_is_auth_error()`，chat / chat_stream 遇 401 重载凭据重试一次，并抽出 `_do_stream()` 去重；② fallback_provider 把 `authentication/auth/permission` 从 `_NON_FALLBACK_ERROR_KINDS` 挪到 `_FALLBACK_ERROR_KINDS`；③ 顺带一处 `error_should_retry = False`。
- 上游现状：
  - ② **上游已独立实现且更强**：commit `15de6be0 fix(providers): fall back on authentication errors`，新增 `_AUTHENTICATION_ERROR_KINDS` + 13 条 `_AUTHENTICATION_ERROR_TOKENS`（invalid_api_key / expired credential …）+ `status in {401,403}` 判定（fallback_provider.py:24-41, 348-367）。
  - ① 上游对应模式在 `xai_grok_provider.py:154-162`：401 → `get_xai_oauth_token(force_refresh=True)` 重取并重试，且 `xai_oauth.get_xai_oauth_token` 用 `filelock.FileLock` 做**跨进程**刷新锁。本地只是重读凭据文件（进程内），弱于上游模式——但上游没有 Anthropic 版本。
  - ③ 上游 fallback_provider.py:216 那段仍只 `return response`，无 `error_should_retry = False`。
- 判定理由：②丢弃，直接吃上游 `15de6be0`；①必须重放（上游无 Anthropic OAuth），但建议照 xai 的 `force_refresh` + FileLock 形态重写，因为本地这笔的动机（两进程共享凭据竞态）正是 FileLock 解决的问题；③保留，4 行。
- 风险/注意：本地把 `refusal` 等的归类也动过（其他 pack 的痕迹），合 fallback_provider 时以上游分类表为准，别把上游的 token 列表覆盖掉。

### 9e251310 重构: provider/model 斜杠前缀剥离下沉到 factory 解析边界
- 分类：**[3] 纯本地**（重放难度：低）
- 本地做了什么：factory 新增 `_split_inline_provider()`，在 `_resolve_model_preset()` 出口把 `provider/model` 拆成显式 provider + 裸 model，只在 provider 未指定且前缀命中 `find_by_name()` 时生效（保护 openrouter 的 `google/gemini-*`）。
- 上游现状：上游 `_resolve_model_preset()` 仍是单行 `return preset if preset is not None else config.resolve_preset(preset_name)`（未变），锚点完好；上游的三处前缀处理都不等价（见开头第 3 点）。
- 判定理由：上游未吸收，且本地这版是唯一在解析边界一次性收口的实现，比上游散在 openai_compat/codex/anthropic 三处的 strip 更干净。
- 风险/注意：合入后与上游 `schema.py` 的前缀推断有轻微功能重叠（都做 provider 推断），不冲突但需确认只留一处生效路径。

### 182ea6b8 修复: subagent 模型用 provider/model 斜杠形式时把前缀当模型名发给 API
- 分类：**[3] 纯本地**（重放难度：高）
- 本地做了什么：修 subagent 独立 provider 快照路径下的前缀泄漏（`nanobot/agent/loop.py` 的 `subagent_provider_snapshot` 相关代码）。
- 上游现状：`git grep -n "subagent_provider_snapshot|subagent.model" upstream/main -- nanobot/` 零命中——**subagent 独立 provider 这个特性整体是本地的**（Spec5 系列）；同时上游把 `loop.py` 大改（+1798/-…，抽出 `agent/model_runtime.py`）。
- 判定理由：上游没有这个特性，必须重放；但承载它的 loop.py 已被上游重构，落点要重新找（大概率落到 `model_runtime.py`）。
- 风险/注意：这笔的难度不来自它本身（改动很小），而来自它依赖的 Spec5 subagent 特性能否先在新 loop/model_runtime 结构上重建。**排期上应挂在 subagent pack 后面，不要在 Pack B 里单独动。**

---

## 小结

**整体建议：逐条挑，主体重放。** 8 笔里 6 笔纯本地、1 笔平行（半吸收）、0 笔完全吸收。

具体处置：

- **折叠重放为一笔**：9edd4f90 + 06eaa4c4 + 248d459b + d9a16a0a + 948c2163 + 6e035f09 的 provider 侧 → 一个「Anthropic Claude Code OAuth provider」commit，直接落最终态。重放时做三处对齐上游：`_build_client` 里保留 `_normalize_base_url`、静态头改用 `ProviderSpec.default_extra_headers`（照 `kimi_coding` 先例）、401 恢复照 `xai_oauth` 的 `force_refresh` + FileLock 形态而不是本地的纯重读。
- **丢弃**：6e035f09 的 fallback_provider 分类改动，改吃上游 `15de6be0`（更全）。同笔 diff 开头那段 `_sanitize_tool_id` 重写（加 `_TOOL_ID_RE = re.compile(r"[^a-zA-Z0-9-]")`，把 OpenAI 风格 `call_abc123` 的下划线换成连字符）也丢弃——上游 `4d7c2074 fix(anthropic): sanitize tool_use/tool_result IDs to API pattern` 做了同一件事，`0e986155` 还补了重复 ID 修复。
- **独立重放**：9e251310（低成本，可随 OAuth 一起进）。
- **延后**：182ea6b8，挂到 subagent pack 之后。

**重放会碰到的上游文件**（及冲突状况，据 `git merge-tree --write-tree main upstream/main`）：

| 文件 | 上游改动量 | 冲突 | 说明 |
|---|---|---|---|
| `nanobot/providers/registry.py` | 25 commits / +271 | 否 | 加一条 spec 即可，最省事 |
| `nanobot/providers/factory.py` | 17 commits / +109 | 是 | anthropic 分支 + `_resolve_model_preset`，锚点都在，机械解 |
| `nanobot/providers/anthropic_provider.py` | 16 commits / +203 | 是 | 本 pack 主战场，`__init__` / 请求构造 / 流式路径三处交叠 |
| `nanobot/providers/fallback_provider.py` | +145 | 是 | 以上游为准，只保留 `error_should_retry=False` 那 4 行 |
| `nanobot/providers/oauth_store.py` | 上游无此文件 | 否 | 原样落地 |
| `nanobot/cli/commands.py` | 大改（新增 xai login / `--set-main`） | 是 | `_register_login` 分发机制上游仍在，加一个 handler |
| `nanobot/agent/loop.py` → `agent/model_runtime.py` | +1798，结构重排 | 是 | 仅 182ea6b8 需要，延后处理 |

依赖提醒：上游 `pyproject.toml` 把 `oauth-cli-kit` 提到 `>=0.1.6,<1.0.0`（本地 `>=0.1.3`），我核对过 `FileTokenStorage.__init__` 与 `OAuthToken` 两版签名一致，本地 `oauth_store.py` 直接跟进升级无破坏。
