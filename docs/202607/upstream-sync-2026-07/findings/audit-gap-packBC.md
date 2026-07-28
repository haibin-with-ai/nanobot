# Pack B / Pack C 覆盖率审计（12 commit）

审计对象：Pack B（Anthropic OAuth 8 笔）+ Pack C（Discord 4 笔）。
基座：`/root/git_code/nanobot/.worktrees/sync-2026-07` @ `3f808d0a`（已核实 HEAD）。
计划：`docs/202607/upstream-sync-2026-07/plan.md`（718 行）。
本文件只读产出，未改动任何既有代码或文档。

判定口径：COVERED（plan 有明确落点句）/ DROPPED-OK（a=上游已有等价实现，读上游代码验证；b=已拍板砍掉）/ GAP。

---

## Pack B：Anthropic Claude Code OAuth

### 9edd4f90 —— OAuth provider 主干

拆出的独立行为：

1. **`oauth_store.py`：FileTokenStorage 读写 + 迁移 + 刷新** —— COVERED。
   plan Task 2.1：「`FileTokenStorage` 只做读写与过期判定」「从 `~/.claude/.credentials.json` 迁移一次并写回自有存储」「文件锁保证多进程刷新不相互覆盖」。
2. **registry 新增 `anthropic_claude_code` spec** —— COVERED。
   plan Task 2.2：「registry 新增 `anthropic_claude_code` spec，使用 `is_oauth=True`。」
3. **factory 分支把 OAuth token 交给 AnthropicProvider（`auth_token=` 而非 `api_key=`）** —— **GAP-B3（写明度）**。见下文 GAP 汇总。
4. **`product_mode="claude_code"` 下注入 Claude Code system block** —— **GAP-B1**。见下文。
5. **`product_mode="claude_code"` 下 `_normalize_tool_names()` 规范化工具名** —— DROPPED-OK(a)。
   上游在 MCP 工具注册处已统一清洗工具名：`nanobot/agent/tools/mcp.py:150`
   `return _SANITIZE_RE.sub("_", re.sub(r"[^a-zA-Z0-9_-]", "_", name))`
   到达 Anthropic 的 tool name 已满足 `^[a-zA-Z0-9_-]{1,64}$`，provider 层再洗一遍是冗余。
6. **`ProvidersConfig` 新增 `anthropic_claude_code` 字段（`config/schema.py`）** —— **GAP-B2**。见下文。
7. **CLI `login/logout` 注册** —— COVERED。
   plan Task 2.2：「CLI login/logout 接入上游 `_register_login` 分发和 `--set-main` 语义。」
   依赖核实：授权流本体来自外部包 `oauth_cli_kit`（本地 `nanobot/cli/commands.py:1693` 起只做装配），该依赖在新基座 `pyproject.toml` 中仍在，无需重放 PKCE 流程本身。

### 06eaa4c4 —— 移除过期 beta header `token-efficient-tools-2025-02-19`

COVERED。plan Task 2.2 原句：「不恢复已过期 `token-efficient-tools-2025-02-19`。」
（plan 中写作 `-02-19`，与本地常量一致，无歧义。）

### 248d459b —— Claude Code 身份头（beta / user-agent / x-app）

COVERED。plan Task 2.2：「静态 Claude Code 身份头放进 `ProviderSpec.default_extra_headers`，不要在 provider 内散落硬编码。」
上游承接点已验证：`registry.py:56` `default_extra_headers: tuple[...]`，`factory.py:33-38` `_provider_extra_headers()` 会把 spec 默认头并入。
语义差异（可接受）：本地是「auth_mode==oauth 时才加」，plan 改为 spec 维度常驻——该 spec 本就只有 OAuth 一种用法，等价。

### d9a16a0a —— 迁移来源扩充 + 到期余量

1. **`~/.claude/.credentials.json`（Claude CLI v2.1+）迁移** —— COVERED（Task 2.1 原句见上）。
2. **到期前 5 分钟即视为需刷新** —— COVERED。plan Task 2.1：「到期前 5 分钟视为需要刷新。」
3. **env `CLAUDE_CODE_OAUTH_TOKEN` 与 legacy `~/.claude.json` 两条来源** —— **GAP-B4**。见下文。

### 948c2163 —— config 同目录 `oauth_credentials.json` 迁移

COVERED。plan Task 2.1：「从 config 同目录 `oauth_credentials.json` 迁移一次。」

### 6e035f09 —— 401 恢复 + 错误分类（重点核验项）

四条行为，逐条判：

1. **401/403 后恢复一次并重试** —— COVERED。
   plan Task 2.2：「401/403 时只刷新一次 token 并重建 client，刷新形态对齐上游 xAI 的 `force_refresh + FileLock`；刷新失败保留真实认证错误。」
   **口径差异需要重放者知情**：本地实现是 `_reload_token_from_store()`（`anthropic_provider.py:707`，被 `:762` 与 `:896` 两处调用），即「重读磁盘，看别的进程是不是已经换了新 token」，只在 store 中 token 与内存不同时才重试；plan 指定的是 xAI 式 `force_refresh`（`xai_oauth.py:354-378`），force_refresh=True 会跳过新鲜度双检直接发刷新请求。两者都能从跨进程竞争中恢复，但 plan 形态多一次网络刷新、且在「别的进程刚换好」的场景下会白刷一次 refresh_token。属于有意的形态对齐，不是 GAP。
2. **auth error 归入可 fallback 类别** —— DROPPED-OK(a)。
   上游 `nanobot/providers/fallback_provider.py:24-31` 已有 `_AUTHENTICATION_ERROR_KINDS` / `_AUTHENTICATION_ERROR_TOKENS`（含 `authentication_error`、`unauthorized`），并在 `:348` `if kind in _AUTHENTICATION_ERROR_KINDS:` 与 `:366` 文本兜底两处消费。分类能力上游已具备。
3. **已流出内容后禁止重试（`error_should_retry = False`）** —— DROPPED-OK(a)。
   上游改成了更干净的守卫：`nanobot/providers/base.py:704-735`，`has_streamed_content` 标志 + `should_retry_guard=lambda: not has_streamed_content`。本地那个 hack 是对同一问题的旧解法。
4. **`_sanitize_tool_id` 正则收紧** —— DROPPED-OK(a)。
   上游 `anthropic_provider.py:35` 已有 `_sanitize_tool_id`，并在 `:166/:186/:194/:247/:301` 五处消费，覆盖面比本地版本大。

→ 特别核验结论：**401 刷新与 auth error fallback 分类两者都有落点**，前者在 plan Task 2.2，后者在上游基座。

### 9e251310 —— `provider/model` 前缀在 factory 边界剥离

COVERED。plan Task 2.2 原句：「在 factory 解析边界处理 `provider/model`：只在 provider 未显式给出且前缀命中 registry 时拆分，保护 OpenRouter 的 `google/gemini-*` 这类模型名。」

上游边界现状（已读代码确认，说明这条**必须重放**）：

- `factory.py:24-30` `_resolve_model_preset()` 只做 `config.resolve_preset()`，**没有任何 `/` 拆分**。
- `config/schema.py:495` 起的 `_match_provider` 用前缀来**选路由**，但不改写 model 字符串——也就是说前缀会原样进入 API 请求体。
- 上游只有个别 provider 自己剥前缀（`openai_codex_provider.py`、`xai_grok_provider.py` 各有一处 `split("/", 1)`），`anthropic_provider.py` **没有**。所以在上游基座上，`anthropic_claude_code/claude-sonnet-4-6` 会被原样发给 Anthropic。

### 182ea6b8 —— loop 层的前缀剥离（早期形态）

DROPPED-OK：被 9e251310 取代，plan 明确只保留 factory 边界一处。plan Task 3.1 验收另有一句兜底：「`provider/model` 前缀不进入最终 API model 字符串。」两处相互印证，无遗漏。

---

## Pack C：Discord

### 2ace8c8d —— 混合 commit，拆四条

1. **TTS（`send()` 注入、`config/schema.py` TTS 配置、`command/builtin.py` 的 `/tts` spec）** —— DROPPED-OK(b)。PHASE2-SPEC 第 3 节「TTS 全线」。已核对该 commit 对 `config/schema.py` 与 `command/builtin.py` 的改动**全部**是 TTS，无夹带。
2. **mention 防串线** —— COVERED。
   plan Task 5.1：「在 `_should_respond_in_group` 的 open 分支前插入 `_mentions_other_bot_only`……判定只依赖 `message.mentions` / `raw_mentions` / 文本 `<@id>` 三条，外加 reference 指向本 bot 的消息则视为叫我。」
   上游确认缺失：`channels/discord/runtime.py` 的 `_should_respond_in_group` 在 `group_policy == "open"` 时直接 `return True`，无任何其他 bot 判断。
   **口径差异需知情**：本地把这道闸放在消息 handler 顶层（DM 也生效），plan 放进 `_should_respond_in_group` 的 open 分支（只管群）。plan 的位置更合理，代价是 DM 里的 @其他bot 消息不再被拦——影响可忽略，不记 GAP。
3. **语音附件转写** —— COVERED。
   plan Task 5.2：「音频附件走上游 `BaseChannel.transcribe_audio`；转写成功用文本替换附件，失败保留附件并照常处理。」
   上游承接点已验证：`channels/base.py:48` `async def transcribe_audio(...)`，内部走 `nanobot/audio/transcription.py`；而 `channels/discord/*.py` 里 grep `transcri|is_audio` **零命中**，说明 Discord 侧确实要接线。
   未写明的小细节（不记 GAP，重放时按本地实现即可）：音频扩展名集合、转写文本的包裹标记文案。
4. **`_outbound_*` 元数据从 session.metadata 传到出站 message tool** —— COVERED（弱）。
   plan Task 6.1：「loop 负责把本轮 request context 传给出站 message tool；整条链路不允许出现 Discord 特判。」并含 `tests/agent/test_loop_outbound_propagation.py`。
   **但要点明**：本地这条通路的**唯一生产者是 `/tts`（写 `_outbound_tts`）、唯一消费者是 `tts/service.py`**（全仓 grep `_outbound_` 仅命中这两端）。TTS 砍掉后，机制在本地已无实际用户；plan 6.1 是给它换了新主人（request context / identity）。因此这不是「重放本地能力」，而是「保留机制、更换载荷」。plan 未写明 `_outbound_` 前缀约定与 session.metadata 作为来源，重放者很可能实现成另一套 key——建议 Task 6.1 补一句把前缀约定钉死。

### 5132903d —— 混合 commit，拆四条

1. **`/tts` slash 命令** —— DROPPED-OK(b)（TTS 全线）。
2. **`/model` 带 preset 下拉选项** —— DROPPED-OK(b)。
   plan Task 5.4：「已手写的 `model`、`trigger`、`help` 保持原样。」PHASE2-SPEC 亦列「本地 `/model` 实现」为已砍。
   代价已核实并被接受：上游 `channels/discord/runtime.py` 的 `/model` 是自由文本参数，没有 preset 选项下拉，UX 会回退。
3. **动态 skill slash 命令（`_sanitize_command_name` + 冲突跳过 + 描述截断 + handler 冻结 skill 名）** —— COVERED。
   plan Task 5.4：「命令名做 Discord 合法化，与 builtin 或已注册名冲突时确定性跳过并记日志，描述截到 100 字符，handler 用默认参数冻结 skill name。」以及「从上游共享的 `SkillsLoader` / workspace 实例读 skills，不重放本地 `_make_skills_loader` 修复」。
4. **`slash_commands: bool = True` 配置开关（关掉则跳过 `tree.sync`）** —— DROPPED-OK(b)。
   plan Task 5.4 明文否掉：「注册 slash command 不产生新的用户可配置项。」这是 plan 自身的拍板，不是遗漏。

### 128eb335 —— 引用消息内容带入上下文

COVERED。plan Task 5.3：「只用 `message.reference.resolved`，取不到就跳过；引用块拼在正文前，不改上游 attachment marker 顺序。」
上游确认缺失：`channels/discord/runtime.py` 中 `reference` / `resolved` 无入站取用代码。

### 5ceba799 —— `/dream` 与 `/dream-log`（后者带可选 sha）

COVERED。plan Task 5.4：「Discord slash 命令从上游 `BUILTIN_COMMAND_SPECS` 派生……`accepts_args` 的命令带一个可选 `args` 参数。」
上游承接点已验证：`nanobot/command/builtin.py` 中 `dream` / `dream-log` 两条 spec 均在，`dream-log` 带 `accepts_args`，派生法能自动覆盖本地手写的这两条，且比手写更抗漂移。

---

## GAP 汇总

### GAP-B1（高）Claude Code system block 未被重放

- 丢失行为：OAuth 模式下，请求 `system` 数组首位必须插入 `{"type":"text","text":"You are Claude Code, Anthropic's official CLI for Claude."}`；`system` 为字符串时先转成 block 数组。
- 本地位置：`nanobot/providers/anthropic_provider.py:21-24` 定义 `_CLAUDE_CODE_SYSTEM_BLOCK`，`:545-551` 在 `product_mode == "claude_code"` 时 `system.insert(0, ...)`。
- 上游不覆盖的证据：`.worktrees/sync-2026-07` 全仓 `grep -rn "official CLI for Claude|Claude Code, Anthropic" nanobot/` 零命中；`anthropic_provider.py` grep `product_mode` 零命中。
- 为什么 plan 没接住：plan Task 2.2 只说「静态 Claude Code 身份头放进 `ProviderSpec.default_extra_headers`」。**header 不等于 system block**，`default_extra_headers` 机制（`registry.py:56` → `factory.py:33-38`）只能注入 HTTP 头，无法碰请求体。这条按现 plan 重放会漏。
- 建议落点：plan §2 Task 2.2，在身份头那条之后补一条独立 bullet，并在测试清单 `test_anthropic_oauth_client.py` 里加一条断言首个 system block 的用例。

### GAP-B2（高）`ProvidersConfig` 缺 `anthropic_claude_code` 字段

- 丢失行为：`config/schema.py` 的 `ProvidersConfig` 需要显式声明 `anthropic_claude_code` 字段。
- 本地位置：`nanobot/config/schema.py` 的 `ProvidersConfig`（9edd4f90 改动之一）。
- 上游不覆盖的证据：上游 `ProvidersConfig` 是 `model_config = ConfigDict(extra="allow")`（`config/schema.py:239`），但 `convert_extra_providers` 校验器（`:286-296`）对 extra key 做冲突检查：

  ```python
  for key, value in self.model_extra.items():
      if spec := find_by_name(key):
          raise ValueError(
              f"providers.{key} conflicts with built-in provider {spec.name!r}; ..."
          )
  ```

  也就是说，一旦 registry 里注册了 `anthropic_claude_code` spec（Task 2.2 第一条要做的事），而 `ProvidersConfig` 上没有对应字段，用户配置里写 `providers.anthropicClaudeCode` 就会**直接抛 ValueError，配置加载失败**。两件事必须同一笔提交完成。
- 为什么 plan 没接住：plan §2 的 **Files 清单与 Task 2.2 的 `git add` 列表都没有 `nanobot/config/schema.py`**（plan.md:143 那处 schema.py 属于 §3 subagent，不是本节）。
- 建议落点：plan §2 Files 补 `Modify: nanobot/config/schema.py`，Task 2.2 补一条 bullet，`git add` 列表同步。

### GAP-B3（中，写明度问题）token 注入路径无落点句

- 丢失行为：`AnthropicProvider.__init__` 接受 `auth_token` / `credential_store` / `product_mode`，并在 `_build_client()` 里以 `client_kw["auth_token"] = ...` 而非 `api_key` 构造 `AsyncAnthropic`；factory 的 anthropic 分支据 `spec.is_oauth` 取 token 后透传这些参数。
- 本地位置：`nanobot/providers/anthropic_provider.py:112`（签名）、`:119-122`（`auth_mode` 推导）、`:130-131`（`client_kw["auth_token"]`）、`:79`（`AsyncAnthropic(**client_kw)`）。
- 上游不覆盖的证据：上游 `anthropic_provider.py` grep `auth_token|auth_mode|oauth` **零命中**，`_build_client` 只走 `api_key`；上游 factory 的 anthropic 分支不传任何 auth 参数。
- 为什么 plan 没接住：plan Task 2.2 的 bullet 覆盖了 registry、headers、`_provider_extra_headers`、`_normalize_base_url`、401 刷新、CLI、前缀剥离——**唯独没有一句说 provider 怎么拿到并使用 token**。这是整节的接线主干，靠标题「实现 Claude Code OAuth provider 最终态」隐含，但重放时最容易被当成「已有」而跳过。
- 建议落点：plan §2 Task 2.2，在 registry 条与 header 条之间插一条：provider 以 `auth_token` 构造 client，token 由 factory 从 `FileTokenStorage` 取得并注入，`api_key` 路径保持不变。

### GAP-B4（低）凭据迁移少了两条来源

- 丢失行为：迁移优先级共三级，本地 `oauth_store.py:76-78` 的 docstring 写死为
  1. `CLAUDE_CODE_OAUTH_TOKEN` 环境变量（`:80` `os.environ.get(_CLAUDE_CODE_ENV_KEY)`）
  2. `~/.claude/.credentials.json`（`:19`、`:89`）
  3. `~/.claude.json` legacy 配置（`:18`）

  plan 只写了第 2 条（Task 2.1「从 `~/.claude/.credentials.json` 迁移一次并写回自有存储」）与 948c2163 的 `oauth_credentials.json`。
- 上游不覆盖的证据：`.worktrees/sync-2026-07` 全仓 `grep -rn "CLAUDE_CODE_OAUTH_TOKEN|claudeAiOauth" nanobot/` 零命中。
- 也不在已砍清单（PHASE2-SPEC 第 3 节 / CLASSIFICATION 已拍板项均未提及凭据来源）。
- 建议落点：plan §2 Task 2.1，把「迁移来源」那条扩成有序三级（env → `~/.claude/.credentials.json` → `~/.claude.json`），测试清单里 `test_oauth_store.py` 加一条 env 优先的用例。

### 非 GAP、但建议 plan 补一句的两点

- **Task 6.1 需钉死 `_outbound_` 前缀约定与 session.metadata 来源**。现有措辞「把本轮 request context 传给出站 message tool」不足以让重放者还原出同一套 key 协议（详见 2ace8c8d 第 4 条）。
- **Task 2.2 的 401 恢复语义换了形态**（`_reload_token_from_store` → xAI 式 `force_refresh`），是有意选择，但测试 `test_anthropic_token_refresh.py` 的断言要按新形态写，别照抄本地用例。

### 复核结论

12 个 commit 拆出 28 条独立行为：COVERED 15 条，DROPPED-OK(a) 5 条（均已在上游 `3f808d0a` 读代码验证），DROPPED-OK(b) 4 条（TTS 全线、本地 `/model`、`slash_commands` 开关、`_make_skills_loader` 修复），GAP 4 条。
Pack C 无 GAP——四笔 Discord commit 的非砍项在 plan §5 全部有明确落点。**GAP 全部集中在 Pack B，且其中两条（B1、B2）会导致重放后功能不可用而非仅退化。**
