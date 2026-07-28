<!-- polished -->
# nanobot 上游同步阶段二实施计划

> **执行原则：** 基座是 `upstream/main=3f808d0a`。所有改动发生在隔离 worktree `/root/git_code/nanobot/.worktrees/sync-2026-07` 的 `sync-upstream-2026-07` 分支，按能力重写而非回放旧 diff。生产 checkout 全程不做 merge。

## 0. 目标、边界与完成条件

目标是把 haibin 已拍板保留的 fork 能力重放到当前上游架构，并用新分支替换现有 `main`。

生产代码只在隔离 worktree 修改。`/root/git_code/nanobot` 在最终切换前保持可运行，不出现未完成 merge、冲突标记或半成品代码。

每个任务遵循 TDD：先写失败测试并确认因预期行为缺失而失败，再做最小实现，跑针对性测试，最后单独提交。commit message 使用中文，只 stage 本任务文件，禁止 `git add -A`。

完成条件：

1. A1 到 A12 的保留能力全部实现并有针对性测试。
2. 全量 `pytest` 通过。
3. 生产 `config.json` 能被新基座加载。
4. 已砍能力没有被误带回：TTS、ContextPruner、rtk、TraceHook、LLM 请求响应日志、spawn timeout。
5. `nanobot-gateway` 切换后启动正常，Discord 实际完成一轮收发和 slash command 验证。
6. 当前生产版本有可一键回滚的 tag。

## 1. 基线与执行前保护

**Files:**

- Modify: `.gitignore`
- Restore from `main`: `docs/superpowers/`、`docs/plans/`、`docs/specs/`
- Copy into worktree: `docs/202607/upstream-sync-2026-07/`
- Verify only: `/root/workspace/nanobot_config/config.json`
- Verify only: systemd user service definition for `nanobot-gateway`

### Task 1.1：固化基线

在隔离 worktree 执行：

```bash
git rev-parse --short HEAD
git status --porcelain
git branch --show-current
```

预期：`3f808d0a`、空输出、`sync-upstream-2026-07`。

在生产 checkout 记录当前状态：

```bash
cd /root/git_code/nanobot
git rev-parse --short HEAD
git status --porcelain nanobot tests pyproject.toml
git tag backup/2026-07-27-pre-upstream 9ca8c42d
```

若 tag 已存在，先核对它确实指向 `9ca8c42d`，不得强制覆盖。

### Task 1.2：只追加仍有效的本地忽略项

保留上游 `.gitignore` 全文，只追加当前仍会产生的本地生成物：`data-gym-cache/`、`graphify-out/`、`pytest-of-root/`、`tmp*.jpg`、`tmp*.png`。不回放 `78dc871d` 的旧文件。

验证：

```bash
git check-ignore -v data-gym-cache/x graphify-out/x pytest-of-root/x tmp1.jpg tmp1.png
```

提交：

```bash
git add .gitignore
git commit -m "chore: 保留本地生成物忽略规则"
```

### Task 1.3：把本地历史文档搬到新基座

新基座是从 `upstream/main` 拉的干净分支，上游 `docs/` 有 53 个自己的文件，且**不含**以下任何目录。不做这一步，切换的瞬间这些全部消失：

```
docs/superpowers   本地 17 文件 / 上游 0   ← Pack A 那 14 笔的全部产出
docs/plans         本地  1 文件 / 上游 0
docs/specs         本地  1 文件 / 上游 0
docs/202607        本地  2 文件 / 上游 0   （已提交部分）
```

另外本次同步自己产出的 22 个文件（`docs/202607/upstream-sync-2026-07/` 下的 spec、plan、CLASSIFICATION、DECISIONS、findings 全套）目前还是未跟踪状态，同样要一并带过去——包括你正在读的这份 plan。

在隔离 worktree 里执行：

```bash
cd /root/git_code/nanobot/.worktrees/sync-2026-07
git checkout main -- docs/superpowers docs/plans docs/specs
mkdir -p docs/202607
cp -r /root/git_code/nanobot/docs/202607/upstream-sync-2026-07 docs/202607/
```

`docs/superpowers/` 下的旧同步方案（尤其 `2026-05-18-upstream-sync.md`）描述的是 `ba38f908` 时代的代码结构，与新基座已经对不上。在这些文件顶部加一行归档标记，避免以后有人照着执行：

```
> 历史归档，非当前实现。基座为 ba38f908（2026-05-18），与 upstream/main=3f808d0a 之后的结构不再对应。
```

验证与提交：

```bash
ls docs/superpowers/plans docs/superpowers/specs docs/202607/upstream-sync-2026-07
git add docs/
git commit -m "docs: 归档历史同步文档与本次同步方案"
```

## 2. Anthropic Claude Code OAuth（A1）

Task 3 的跨 provider spawn 依赖本节完成。上游已有 `ProviderSpec.is_oauth`、`default_extra_headers`、factory/provider registry 和 CLI login 分发；缺的是 Claude Code OAuth 实现。按上游接入点补能力，不覆盖上游 Codex、Copilot、xAI OAuth。

**Files:**

- Create: `nanobot/providers/oauth_store.py`
- Modify: `nanobot/providers/registry.py`
- Modify: `nanobot/providers/factory.py`
- Modify: `nanobot/providers/anthropic_provider.py`
- Modify: `nanobot/config/schema.py`
- Modify: `nanobot/cli/commands.py`
- Create: `tests/providers/test_oauth_store.py`
- Create: `tests/providers/test_anthropic_oauth_client.py`
- Create: `tests/providers/test_anthropic_token_refresh.py`

上游 `tests/providers/` 下没有这三个文件，全部新建。上游已有的 `tests/providers/test_xai_oauth.py` 是刷新加锁形态的参照，不要改它。

### Task 2.1：先锁定凭据存取和迁移行为

测试覆盖：

- 读取/写入 `oauth-cli-kit` 的 `FileTokenStorage`。
- 凭据来源按三级优先级解析，缺一不可：`CLAUDE_CODE_OAUTH_TOKEN` 环境变量 → `~/.claude/.credentials.json` → legacy `~/.claude.json`。本地实现见 `nanobot/providers/oauth_store.py:76-80`，三条都要有对应用例。
- 从 config 同目录 `oauth_credentials.json` 迁移。
- 到期前 5 分钟视为需要刷新。
- 文件锁保证多进程刷新不相互覆盖。

运行并确认 RED：

```bash
uv run pytest -q tests/providers/test_oauth_store.py
```

### Task 2.2：实现 Claude Code OAuth provider 最终态

- registry 新增 `anthropic_claude_code` spec，使用 `is_oauth=True`。
- **同一笔提交内**给 `ProvidersConfig` 加 `anthropic_claude_code` 字段（带 `AliasChoices("anthropicClaudeCode", ...)`）。上游 `nanobot/config/schema.py:286` 的 `convert_extra_providers` 会对落进 `model_extra` 又能被 `find_by_name` 命中的 key 直接 `raise ValueError`：registry 注册了 spec 而 schema 没有对应字段，用户只要在配置里写 `anthropicClaudeCode` 就会在加载阶段崩溃。两件事拆成两笔提交，中间那个状态是坏的。
- 静态 Claude Code 身份头放进 `ProviderSpec.default_extra_headers`，不要在 provider 内散落硬编码；不恢复已过期 `token-efficient-tools-2025-02-19`。
- **身份 system block 与身份 header 是两条独立链路，都要做。** `default_extra_headers` 经 `registry.py:56` → `factory.py:33-38` 只能注 HTTP 头，碰不到请求体。另需在 `AnthropicProvider` 内于 `product_mode == "claude_code"` 时，把 `You are Claude Code, Anthropic's official CLI for Claude.` insert 到 system 数组首位（本地实现见 `anthropic_provider.py:23` 常量与 545-551 行注入点）。上游全仓 grep 该字符串零命中，不做这步 OAuth 能握手但请求会被拒。
- token 注入走 `AsyncAnthropic(auth_token=...)`，不是 `api_key=...`（本地 `anthropic_provider.py:130-131`）。上游 anthropic_provider grep `auth_token|auth_mode|oauth` 零命中，这条接线主干必须显式实现，不要因为本节标题写着 OAuth 就默认上游已有。
- factory 的 Anthropic 分支继续调用上游 `_provider_extra_headers()`。
- `AnthropicProvider._build_client()` 必须保留上游 `_normalize_base_url()`。
- 401/403 时只刷新一次 token 并重建 client，刷新形态对齐上游 xAI 的 `force_refresh + FileLock`；刷新失败保留真实认证错误。
- CLI login/logout 接入上游 `_register_login` 分发和 `--set-main` 语义。
- 在 factory 解析边界处理 `provider/model`：只在 provider 未显式给出且前缀命中 registry 时拆分，保护 OpenRouter 的 `google/gemini-*` 这类模型名。

测试：

```bash
uv run pytest -q \
  tests/providers/test_oauth_store.py \
  tests/providers/test_anthropic_oauth_client.py \
  tests/providers/test_anthropic_token_refresh.py
```

提交：

```bash
git add nanobot/providers/oauth_store.py \
  nanobot/providers/registry.py \
  nanobot/providers/factory.py \
  nanobot/providers/anthropic_provider.py \
  nanobot/config/schema.py \
  nanobot/cli/commands.py \
  tests/providers/test_oauth_store.py \
  tests/providers/test_anthropic_oauth_client.py \
  tests/providers/test_anthropic_token_refresh.py
git commit -m "feat: 接入 Anthropic Claude Code OAuth"
```

## 3. subagent 独立模型与 provider（A2）

上游已用 `LLMRuntime` / `ProviderSnapshot` 取代本地旧式手工换 provider。保留的产品能力是「spawn 可选择独立 preset/model」，实现必须落到上游 runtime 抽象，不能恢复旧 `AgentRunSpec(model=...)`。

**Files:**

- Modify: `nanobot/config/schema.py`
- Modify: `nanobot/agent/tools/spawn.py`
- Modify: `nanobot/agent/subagent.py`
- Modify: `nanobot/agent/loop.py`
- Modify: `nanobot/agent/model_runtime.py`
- Modify: `tests/agent/test_subagent.py`
- Modify: `tests/test_tool_contextvars.py`
- Modify or create: spawn schema/runtime tests adjacent to upstream spawn tests

### Task 3.1：定义模型选择契约

先写测试：

- spawn 不传 model 时继承 `current_request_context().runtime`。
- 传 preset 时由上游 `RuntimeResolver` 得到完整 `LLMRuntime`。
- 跨 provider preset 同时切换 provider、model、reasoning 参数。
- `provider/model` 前缀不进入最终 API model 字符串。
- 保留上游 `temperature` 和 `wait` 参数；不恢复 `timeout_seconds`。
- 传未知 preset/alias 时返回 `ToolResult.error`，不抛异常。上游 `model_presets.py:76-82` 对未知名 `raise KeyError`，`model_runtime.py:116` 直接透传，而 `spawn.py` 全文无 try/except——不补这条，主 agent 打错一个 preset 名整轮就崩。
- 不恢复 TraceHook 与 LLM request/response logging。

运行并确认 RED：

```bash
uv run pytest -q tests/agent/test_subagent.py tests/test_tool_contextvars.py
```

### Task 3.2：在 runtime 边界实现

- spawn schema 增加可选 `model`，保持 `task / label / temperature / wait`。
- 解析发生在 loop/model runtime 层；subagent 只消费已经完整解析的 runtime。
- spawn 工具边界捕获 preset 解析的 `KeyError` / `ValueError`，转成 `ToolResult.error` 并带上出错的 preset 名。捕获点放在工具层，不要去改 `model_presets.py` 让它对未知名返回 None——那会把错误推迟到更远的地方。
- 独立 runtime 从 `ProviderSnapshot` 构造，不能就地修改调用方 runtime 或共享 provider client。
- 配置默认值如保留，必须作为 preset 名解析；裸 provider/model 走 Task 2 的解析边界。

测试并提交：

```bash
uv run pytest -q tests/agent/test_subagent.py tests/test_tool_contextvars.py
git add nanobot/config/schema.py nanobot/agent/tools/spawn.py \
  nanobot/agent/subagent.py nanobot/agent/loop.py \
  nanobot/agent/model_runtime.py tests/agent/test_subagent.py \
  tests/test_tool_contextvars.py
git commit -m "feat: 支持 subagent 独立模型与 provider"
```

## 4. 两个低风险工具层补丁（A3、A4）

### Task 4.1：read_file 默认总返回全文

**Files:**

- Modify: `nanobot/agent/tools/filesystem.py`
- Modify: `tests/tools/test_file_edit_coding_enhancements.py`
- Modify: `tests/tools/test_filesystem_tools.py`

上游已有完整 dedup 与 `force` 机制，也已有 `test_read_file_force_bypasses_dedup`。本任务只翻转默认值，不新建测试文件。

先改测试：把「不传 `force` 时第二次读返回 dedup 提示」的现有断言翻转为「两次均返回完整内容」；补充显式 `force=False` 仍走 dedup；保留 mtime 不变但内容变化时的哈希兜底断言。

实现改三处，缺一处就会出现代码与描述不一致：

1. `ReadFileTool.execute(..., force: bool = False)` 默认值改为 `True`（`filesystem.py:282`）。
2. schema 里 `force=BooleanSchema(..., default=False)` 同步改为 `default=True`（`filesystem.py:237`）。
3. tool description 中 `"Use force=true to re-read content even if unchanged. "`（`filesystem.py:268`）改写为说明默认返回完整内容、传 `force=false` 才启用去重。

哈希、mtime、`force` 参数和 dedup 代码全部保留。

```bash
uv run pytest -q tests/tools/test_file_edit_coding_enhancements.py tests/tools/test_filesystem_tools.py
uv run pytest -q tests/tools/test_read_enhancements.py tests/tools/test_tool_descriptions.py
git add nanobot/agent/tools/filesystem.py \
  tests/tools/test_file_edit_coding_enhancements.py \
  tests/tools/test_filesystem_tools.py
git commit -m "fix: read_file 默认返回完整内容"
```

### Task 4.2：gitstore 行龄改用原生 git blame

**Files:**

- Modify: `nanobot/utils/gitstore.py`
- Modify: `tests/utils/test_gitstore.py`

测试覆盖：多次 commit 后每行得到各自 committer time；未跟踪文件返回空；路径含空格；`git blame` 失败不被伪造成成功数据。

实现用 `git blame --porcelain -- <file>`，解析 `committer-time`；不要复制本地吞异常的兜底，遵循上游 `dfc3919b`「不掩盖 runtime failure」的方向。

```bash
uv run pytest -q tests/utils/test_gitstore.py
git add nanobot/utils/gitstore.py tests/utils/test_gitstore.py
git commit -m "fix: 使用原生 git blame 计算行龄"
```

### Task 4.3：补 message 工具的发送失败路径测试

**Files:**

- Modify: `tests/tools/test_message_tool_suppress.py`

只补测试，不改实现。上游 `message.py` 里 `_sent_in_turn` 的赋值已经在 `await` 之后，行为本身是对的，但上游 `tests/tools/test_message_tool*.py` 没有任何 `side_effect` 注入用例——这条不变量目前没人守。

断言：`send_callback` 抛异常时返回值以 `Error sending message:` 开头，且 `_sent_in_turn` 保持未置位（后续同轮回复不被误判为已发送）。

```bash
uv run pytest -q tests/tools/test_message_tool_suppress.py
git add tests/tools/test_message_tool_suppress.py
git commit -m "test: 覆盖 message 工具发送失败路径"
```

## 5. Discord 插件包增量（A5、A6、A8、A9）

上游已把渠道改造成自包含插件包：`manifest.py` 负责声明、依赖、配置与 WebUI，`runtime.py` 负责运行逻辑，`validation.py` 负责校验，测试放在插件包内。不要把旧 `nanobot/channels/discord.py` 搬回来。

上游已具备普通 @ 判定、回复 bot 消息判定、出站 reply context，以及 `/model`、`/trigger`、`/help`。这些都不重放。

### Task 5.1：只 @ 其他 bot 时防串线（A5）

**Files:**

- Modify: `nanobot/channels/discord/runtime.py`
- Modify: `nanobot/channels/discord/tests/test_discord_channel.py`

在 `_should_respond_in_group()` 的入口边界增加纯判断：消息仅 mention 其他 bot 且未 mention 当前 bot 时返回 false。复用/重写本地 `_resolve_bot_user_id` 与 `_mentions_other_bot_only` 的意图，保留上游 `_references_bot_message`。

测试：

- 只 mention 另一个 bot → 不响应。
- mention 当前 bot → 响应。
- 同时 mention 当前 bot 和其他 bot → 响应。
- 普通文本、DM、回复当前 bot → 保持上游行为。
- bot user 尚未 ready 时不误杀合法消息。

```bash
uv run pytest -q nanobot/channels/discord/tests -k 'mention or respond'
git add nanobot/channels/discord/runtime.py nanobot/channels/discord/tests
git commit -m "fix: 阻止 Discord 机器人 mention 串线"
```

### Task 5.2：Discord 接入上游语音转写（A6）

**Files:**

- Modify: `nanobot/channels/discord/runtime.py`
- Modify: `nanobot/channels/discord/tests/test_discord_channel.py`

上游 `BaseChannel.transcribe_audio(path)` 与 `nanobot/audio/transcription.py` 已存在，Feishu、Matrix、Telegram、WebSocket、WeCom 均已接入。Discord 只需要在附件下载流程中识别语音附件，落临时文件后调用 `self.transcribe_audio()`，把结果并入入站正文，并确保临时文件清理。

不迁移本地 `_transcribe_audio`，不加入 edge-tts、TTS 配置或 `/tts`。

测试：语音附件成功转写；转写失败保留附件而不炸掉整条消息；非语音附件不调用转写；临时文件在成功/失败后均删除。

```bash
uv run pytest -q nanobot/channels/discord/tests -k 'audio or transcri or attachment'
git add nanobot/channels/discord/runtime.py nanobot/channels/discord/tests
git commit -m "feat: 为 Discord 接入统一语音转写"
```

### Task 5.3：恢复入站回复引用正文（A8）

**Files:**

- Modify: `nanobot/channels/discord/runtime.py`
- Modify: `nanobot/channels/discord/tests/test_discord_channel.py`

在入站消息转换处读取 `message.reference.resolved`：被引用消息有正文时追加稳定的 quoted context；缺失、删除、未 resolve、附件-only 时安全降级。不要碰上游出站 `_build_reply_context()`。

测试：引用用户正文、引用 bot 正文、引用为空、reference 未解析、原始消息正文与引用顺序。

```bash
uv run pytest -q nanobot/channels/discord/tests -k 'reply or quote or reference'
git add nanobot/channels/discord/runtime.py nanobot/channels/discord/tests
git commit -m "fix: 保留 Discord 入站回复引用正文"
```

### Task 5.4：动态 `/skill` 与 Dream slash commands（A9）

**Files:**

- Modify: `nanobot/channels/discord/runtime.py`
- Modify: `nanobot/channels/discord/tests/test_discord_channel.py`

不动 `manifest.py` 和 `webui/locales/*.json`。注册 slash command 不产生新的用户可配置项。

先厘清真实增量。上游 `nanobot/command/builtin.py:59` 的 `BUILTIN_COMMAND_SPECS` 已经声明了 `/dream`、`/dream-log`、`/dream-restore`、`/dream-prompt`、`/skill` 等 15 条命令，`cmd_dream`、`cmd_skill` 也都实现了。Discord 侧缺的只是暴露：`runtime.py:194` 的 `_register_app_commands()` 用一个硬编码五元组 `("new", "stop", "restart", "status", "history")` 注册，Dream 系列和 skill 都没进去。

所以这个任务不写任何命令逻辑，只做两件事。

第一，把硬编码元组换成从 `BUILTIN_COMMAND_SPECS` 派生，消掉「手写列表 + builtin 表」两处维护。分流规则用 spec 自带字段，不要新加特判：`accepts_args=False` 的直接注册无参命令；`accepts_args=True` 的按 `usage` 生成一个可选 string 参数拼进 command text。已手写的 `model`、`trigger`、`help` 保持原样，派生时跳过同名项。

第二，动态 skill slash。上游 `/skill` 只是列出清单，本地要的是每个 skill 一个可点选命令，两者并存不冲突。从上游共享的 `SkillsLoader`/workspace 实例读 skills，命令名做 Discord 合法化，与 builtin 或已注册名冲突时确定性跳过并记日志，描述截到 100 字符，handler 用默认参数冻结 skill name（照 `runtime.py:204` 已有的 `_command_text` 写法）。

不重放本地 `/model` 和 `/tts`。

测试：派生列表包含 dream 系列、跳过已手写命令、`accepts_args` 两条分支各一例、skill 名合法化、与 builtin 冲突时跳过、重复 skill、超长描述截断、闭包绑定、无 skills 时正常启动。

```bash
uv run pytest -q nanobot/channels/discord/tests -k 'slash or skill or dream or command'
uv run pytest -q tests/command/test_skill_command.py tests/command/test_builtin_dream.py
git add nanobot/channels/discord/runtime.py nanobot/channels/discord/tests
git commit -m "feat: 恢复 Discord skill 与 Dream slash 命令"
```

## 6. runtime identity、outbound metadata 与耗时（A10）

A7 并入本节。上游已有持久 `RuntimeContextProvider`，身份信息不再拼进 `ContextBuilder`，而是从本轮 `InboundMessage.metadata` 生成 metadata-only block。

身份注入和耗时统计是两件无关的事，拆成两笔提交。

### Task 6.1：通用身份块与出站 metadata

**Files:**

- Create: `nanobot/agent/identity_context.py`
- Modify: `nanobot/agent/loop.py`
- Modify: `nanobot/channels/discord/runtime.py`
- Modify: `tests/agent/test_runner_injections.py`
- Create: `tests/agent/test_loop_outbound_propagation.py`
- Modify: `nanobot/channels/discord/tests/test_discord_channel.py`

`nanobot/runtime_context.py` 不要改。它只是机制：`RuntimeContextProvider` 是个 `Callable` 类型别名，扩展点是 `loop.py:616` 的 `register_runtime_context_provider()` 和 `tools/registry.py:44` 的 tool 自带 provider。身份块写成独立 callable，在 gateway 组装时注册。

行为：身份块包含 current time、channel、chat ID、sender ID/name、channel name；缺字段时省略而非编造；块明确标为 metadata only，不是指令。Discord ingress 负责把真实 sender/channel 字段放进 `InboundMessage.metadata`，loop 负责把本轮 request context 传给出站 message tool。整条链路不允许出现 Discord 特判。

```bash
uv run pytest -q \
  tests/agent/test_runner_injections.py \
  tests/agent/test_loop_outbound_propagation.py \
  nanobot/channels/discord/tests
git add nanobot/agent/identity_context.py nanobot/agent/loop.py \
  nanobot/channels/discord/runtime.py \
  tests/agent/test_runner_injections.py \
  tests/agent/test_loop_outbound_propagation.py \
  nanobot/channels/discord/tests
git commit -m "feat: 注入通用运行身份上下文"
```

### Task 6.2：AgentRunResult 耗时

**Files:**

- Modify: `nanobot/agent/runner.py`
- Modify: `nanobot/agent/loop.py`
- Create: `tests/agent/test_runner_timing.py`
- Modify: `tests/agent/test_loop_persistence.py`

给 `AgentRunResult`（`runner.py:105`）增加 `elapsed_ms`、`llm_elapsed_ms` 两个带默认值的字段，跟已有 `usage`、`tool_events` 并列，不改构造签名顺序。总耗时覆盖 run 全周期，LLM 耗时只累加 `_request_model()`。时钟可注入或 patch，测试禁止 sleep。异常、fallback 重试、tool 执行都不得污染 LLM 口径。

字段加完必须给出落库出口，否则只是加了两个没人读的属性。上游 `loop.py:1851` 落 assistant 消息时只写 `latency_ms`，需要一并持久化 `model`、`usage`、`elapsed_ms`、`llm_elapsed_ms`；user 消息侧补 `sender_id`、`sender_name`（来源与 Task 6.1 的 `InboundMessage.metadata` 同一份，不要另起字段名）。

测试断言落库后的记录里这些字段存在且非空，不要只断言 `AgentRunResult` 上有值。

```bash
uv run pytest -q tests/agent/test_runner_timing.py tests/agent/test_runner_fallback.py \
  tests/agent/test_loop_persistence.py
git add nanobot/agent/runner.py nanobot/agent/loop.py \
  tests/agent/test_runner_timing.py tests/agent/test_loop_persistence.py
git commit -m "feat: 记录并持久化 agent 运行与模型耗时"
```

## 7. bootstrap 与 Dream 增量（A11）

### Task 7.1：SOUL-first、soul anchor、subagent bootstrap 与 Discord 格式提示

**Files:**

- Modify: `nanobot/agent/context.py`
- Modify: `nanobot/agent/subagent.py`
- Modify: `nanobot/templates/agent/identity.md`
- Modify: `nanobot/templates/agent/subagent_system.md`
- Modify: `tests/agent/test_context_builder.py`
- Modify: `tests/agent/test_subagent.py`

行为：

- 主 agent bootstrap 文件清单固定为 `SOUL.md → AGENTS.md → USER.md → TOOLS.md`，四个。上游 `nanobot/agent/context.py:57` 的 `BOOTSTRAP_FILES` 只有前三个，`TOOLS.md` 必须补进去——生产 `/root/workspace/TOOLS.md` 是现役文件，承载临时文件目录约定与 llm-note 交付走 write 技能的规则，掉出上下文不会报错，只会让 agent 悄悄不守规矩。
- 顺序相对原实现有一处有意改动：原为 `SOUL → USER → AGENTS`，这里改成 `SOUL → AGENTS → USER`，让人格、机制、用户画像按「谁约束谁」排布。这是拍板过的偏离，不是笔误，测试按新顺序断言。
- system prompt 尾部追加精简 soul anchor；不能重复整个 SOUL.md。判据落在测试上：anchor 长度不超过 SOUL.md 全文的 30%，且必须包含 Prime Directive 段。不设主观的「精简即可」。
- subagent 注入 workspace 的 `SOUL.md` 与 `TOOLS.md`，同时保留上游 project/agent workspace、untrusted-content 和 skills summary 结构。
- Discord 格式提示明确禁止 Markdown pipe table；不整体覆盖上游 identity 模板。

测试顺序、缺文件降级、anchor 只出现一次、subagent 两文件内容/顺序、上游已有 prompt 段不丢失。

```bash
uv run pytest -q tests/agent/test_context_builder.py tests/agent/test_subagent.py
git add nanobot/agent/context.py nanobot/agent/subagent.py \
  nanobot/templates/agent/identity.md \
  nanobot/templates/agent/subagent_system.md \
  tests/agent/test_context_builder.py tests/agent/test_subagent.py
git commit -m "feat: 恢复 SOUL 优先级与 subagent 启动约束"
```

### Task 7.2：只摘取 Dream 的两个本地增量

**Files:**

- Modify: `nanobot/agent/memory.py`
- Modify: `nanobot/templates/agent/dream.md`
- Modify: `tests/agent/test_dream.py`

保留上游完整单阶段 Dream、受限工具集、cursor 推进、workspace prompt override、ephemeral session、token usage 与 dream session 清理；只补：

1. `_annotate_memory_line_ages` + `stale_threshold_days`，在 `build_dream_prompt()` 中提供 Target Cursor 行龄。
2. 本地 `dream.md` 独有的短会话噪声防护、Completion contract、截断感知编辑说明。

测试行龄有/无 git 历史、stale 阈值、prompt Target Cursor，以及三段模板约束存在。不要重放本地旧 Dream 主流程和旧测试套件。

```bash
uv run pytest -q tests/agent/test_dream.py tests/agent/test_dream_tools.py
git add nanobot/agent/memory.py nanobot/templates/agent/dream.md tests/agent/test_dream.py
git commit -m "feat: 为 Dream 增加记忆行龄与完成约束"
```

## 8. fallback、stall 与 Anthropic 模型能力（A12.1）

这是最高风险代码块之一。runner 只负责 stall 状态机，模型选择只归 `FallbackProvider`；禁止形成 `runner retries × fallback models` 的乘法重试。

### Task 8.1：refusal fallback + 按模型 quota cooldown

**Files:**

- Modify: `nanobot/providers/fallback_provider.py`
- Modify: `nanobot/providers/anthropic_provider.py`
- Create: `tests/providers/test_fallback_provider.py`

上游没有 fallback provider 的专属测试文件，现有覆盖分散在 `tests/agent/test_runner_fallback.py`。冷却逻辑要跟上游已有的 `tests/providers/test_provider_retry_after_hints.py` 对齐语义，别造出第二套 retry-after 解释。

行为：refusal 必须尝试下一个模型，但不写 quota cooldown；quota/rate-limit 只冷却具体 provider/model；同 provider 的其他模型可用；普通错误、stall、refusal 不污染 cooldown；使用 `time.monotonic()` 或注入时钟，测试禁止 sleep。

**refusal 信号要在 anthropic 侧生产。** 上游 `fallback_provider.py:53-58` 的错误分类里已经有 `"refusal"`，但 `anthropic_provider.py` 全文 grep `refusal` 零命中——上游那个分支现在是死代码。必须在 anthropic provider 里把 refusal 停止原因转成结构化 error，fallback 才收得到。只改 fallback 侧等于什么都没做。

**冷却参数写死这几条，别让实现者自己猜：** 默认冷却 600 秒；从响应解析出的 `retry_after` 钳制在 60–1800 秒之间；响应带 `retry_after` 即判定为限流，不依赖错误文案匹配；冷却 key 的粒度是 `provider + model`，不是 provider；所有候选模型都在冷却中时走兜底分支——取冷却剩余时间最短的那个直接尝试，而不是直接失败。

```bash
uv run pytest -q tests/providers/test_fallback_provider.py tests/agent/test_runner_fallback.py
git add nanobot/providers/fallback_provider.py tests/providers/test_fallback_provider.py tests/agent/test_runner_fallback.py
git commit -m "feat: 支持拒答回退与按模型限流冷却"
```

### Task 8.2：Phase 2/3 stall + Anthropic client reset

**Files:**

- Modify: `nanobot/agent/runner.py`
- Modify: `nanobot/providers/anthropic_provider.py`
- Modify: `tests/agent/test_runner_fallback.py`
- Modify: `tests/providers/test_anthropic_stream_idle.py`
- Create: `tests/providers/test_anthropic_client_reset.py`
- Modify: `tests/providers/test_anthropic_tool_result.py`

改写旧恢复协议测试时注意契约已经反向：本地那条「已吐字的 stall 不重试」断言与上游相反，上游 `fallback_provider.py:92-101 / 200-209 / 242-253` 允许在已有流式输出的情况下继续恢复。这些断言要反转，不是照搬。

保留上游 Phase 1 idle 检测，它在 `providers/anthropic_provider.py:723-792` 的 `resolve_stream_idle_timeout_s()`，stall 时抛 `stream stalled for more than N seconds`。Phase 2/3 上游没有——`agent/runner.py` grep `MAX_TIMEOUT_RETRIES` / `MAX_TOTAL_TIMEOUTS` / `timeout_retries` 全部零命中，这两阶段是本节要新写的东西。

本地这笔修的是真实事故：Phase 1 重试耗尽后 loop 静默 break，bot 直接停止工作，既不报错也不尝试恢复。

三阶段的精确语义：

- **Phase 1（上游已有）**：provider 层 idle 检测，单次请求内的 stall 判定。
- **Phase 2（新写）**：同模型、同上下文立即重试，上限 `_MAX_TIMEOUT_RETRIES = 2`。重试耗尽后**不 break**，而是把 stall 错误作为一条消息写入上下文，然后 `continue` 主循环，让模型看见「上次超时了」并自己决定下一步。这条是整个修复的核心，漏了等于没修。
- **Phase 3（新写）**：全局计数 `_MAX_TOTAL_TIMEOUTS = 4`。跨轮累计的 stall 达到上限后 runner 放弃，把失败向上抛给调用方。

计数器清零规则：任意一次正常响应同时清零 Phase 2 的连续计数与 Phase 3 的总计数；只有 stall/timeout 类错误计数，普通 API 错误不计。

Phase 3 不要写成「返回可由 fallback provider 接管的错误」——那不成立。`FallbackProvider` 在 provider 层（`fallback_provider.py:185-260`）工作，位置早于 runner，runner 走到 Phase 3 时 fallback 早已用完自己的全部机会，没有任何接管余地。Phase 3 的语义就是 runner 层放弃并上抛。

每次 Anthropic stall 关闭旧 client 并按原始 kwargs 重建。reset 失败不覆盖原始 stall。测试精确断言调用次数、新 client、消息序列合法性与 fallback 边界。

顺带在本任务处理 tool id 的长度边界：上游 `anthropic_provider.py:43` 是 `if not tid or _VALID_TOOL_ID.match(tid): return tid`，对合法字符集的 id 原样返回，全文没有长度检查。补一条 64 字符硬上限截断。非阻塞项，但顺手做掉比单独开一笔便宜。

```bash
uv run pytest -q \
  tests/agent/test_runner_fallback.py \
  tests/providers/test_anthropic_stream_idle.py \
  tests/providers/test_anthropic_client_reset.py \
  tests/providers/test_anthropic_long_request_fallback.py
git add nanobot/agent/runner.py nanobot/providers/anthropic_provider.py \
  tests/agent/test_runner_fallback.py \
  tests/providers/test_anthropic_stream_idle.py \
  tests/providers/test_anthropic_client_reset.py \
  tests/providers/test_anthropic_tool_result.py
git commit -m "fix: 增加 stall 分阶段恢复并重建 Anthropic client"
```

### Task 8.3：Opus 5、adaptive thinking、capability helper 与 SDK

**Files:**

- Modify: `nanobot/providers/anthropic_provider.py`
- Modify: `tests/providers/test_anthropic_thinking.py`
- Create: `tests/providers/test_anthropic_opus5.py`
- Modify: `pyproject.toml`
- Modify: actual lockfile generated by `uv lock`

上游已有 adaptive thinking 及 `opus-4-7`、`opus-4-8`、`sonnet-5`、`fable` 的 sampling 兼容。本任务只提炼 capability helper 并加入 Opus 5：不发不支持的 sampling 参数，默认 summarized adaptive thinking，支持完整 effort，显式 disable 优先，模型名匹配必须有边界；`fable` 合进统一 helper，不能留新特殊分支。

依赖改为 `anthropic>=0.120.0,<1.0.0`。

```bash
uv run pytest -q tests/providers/test_anthropic_thinking.py tests/providers/test_anthropic_opus5.py
uv lock
uv run python -c 'import anthropic; print(anthropic.__version__)'
git add nanobot/providers/anthropic_provider.py \
  tests/providers/test_anthropic_thinking.py \
  tests/providers/test_anthropic_opus5.py pyproject.toml uv.lock
git commit -m "feat: 增加 Anthropic Opus 5 模型能力"
```

### Task 8.4：会话重置时清理 model preset

spec 把这条归在 A12，但它跟 fallback、stall、cron 没有任何关系，纯粹是 session 语义。放在本节只为保持与 spec 编号对应，实现时按独立任务对待，不要等 8.1 到 8.3 完成。

**Files:**

- Modify: `nanobot/session/manager.py`
- Modify: `tests/command/test_model_command.py`
- Modify: `tests/agent/test_session_model_runtime.py`

补在 `Session.clear()`（`manager.py:289`），不要补在 `cmd_new`。该方法的 docstring 是 "Clear all messages and reset session to initial state"，里面已经 `pop("_last_summary", None)`，preset 残留属于它自己漏了一个字段。补在这里所有重置路径一致；补在命令层则每个未来的 clear 调用点都要记得重复一次。

写法与既有行并列：`self.metadata.pop(SESSION_MODEL_PRESET_METADATA_KEY, None)`，常量从 `nanobot/session/model_selection.py:8` 导入，同包无循环依赖。禁止使用本地旧字面量 `"model_preset"`，上游实际值是 `_nanobot_model_preset`。只清模型 override，`goal_state` 等其他 metadata 保留。

`loop.py:521` 有一处同名 pop，属于 preset 被删除后的降级恢复路径，保持原样。

```bash
uv run pytest -q tests/command/test_model_command.py \
  tests/agent/test_session_model_runtime.py tests/agent/test_runtime_refresh.py
git add nanobot/session/manager.py tests/command/test_model_command.py \
  tests/agent/test_session_model_runtime.py
git commit -m "fix: 会话重置时清除模型预设覆盖"
```

## 9. cron 双模式与 session 回收（A12.2）

这里保留两条语义：有 `payload.session_key` 的 job 原样走上游 bound session turn；没有 session key 的 job 每次创建独立 session，并支持 per-job model。不能让两个分支共享隐式状态。

### Task 9.1：payload model 与合法 unbound job

**Files:**

- Modify: `nanobot/cron/types.py`
- Modify: `nanobot/agent/tools/cron.py`
- Modify: `nanobot/cron/service.py`
- Modify: `tests/cron/test_cron_tool_schema_contract.py`
- Modify: `tests/cron/test_cron_service.py`

新增 `CronPayload.model: str | None = None`，不保留 `preset` alias。`None` 的存储语义保持「未显式指定」，执行时才解析为 `deep`。移除「unbound agent job 自动 disable」规则，但 malformed delivery payload 仍拒绝。

```bash
uv run pytest -q tests/cron/test_cron_tool_schema_contract.py tests/cron/test_cron_service.py
git add nanobot/cron/types.py nanobot/agent/tools/cron.py nanobot/cron/service.py \
  tests/cron/test_cron_tool_schema_contract.py tests/cron/test_cron_service.py
git commit -m "feat: 允许 cron job 指定模型并保持未绑定状态"
```

### Task 9.2：bound/unbound 执行分流

**Files:**

- Modify: `nanobot/cron/bound_runner.py`
- Modify: `nanobot/cli/commands.py`
- Create: `tests/cron/test_bound_runner.py`
- Modify: `tests/cli/test_commands.py`

建议在 `run_bound_cron_job()` 下抽 `_run_bound_cron_job()` 与 `_run_unbound_cron_job()`：

- bound：保留上游 `submit_cron_turn`、`CRON_TRIGGER_META`、`CRON_DEFER_UNTIL_IDLE_META` 和原 session runtime。
- unbound：key 使用 `cron:{job.id}:{run_id}`，同一 job 并发不碰撞；每次全新 session；`model is None → deep`，显式 model 覆盖；解析必须走 `RuntimeResolver/ProviderSnapshot`。
- run record 记录 session key、effective model、状态，不写 secret；解析失败必须把 running 收口成 failed。

测试调用次数、session key、effective model，不只看最后消息成功。

```bash
uv run pytest -q \
  tests/cron/test_cron_service.py \
  tests/cron/test_cron_tool_schema_contract.py \
  tests/cron/test_bound_runner.py \
  tests/cli/test_commands.py
git add nanobot/cron/bound_runner.py nanobot/cli/commands.py \
  tests/cron/test_bound_runner.py tests/cli/test_commands.py
git commit -m "feat: 支持绑定与隔离两种 cron 执行模式"
```

### Task 9.3：per-run cron session retention

**Files:**

- Modify: `nanobot/session/manager.py`
- Modify: `nanobot/cli/commands.py`
- Create: `tests/session/test_cron_retention.py`

固定策略：默认保留 30 天，每个 job 至少保留最近 3 次；只清理能严格解码为 `cron:{job_id}:{run_id}` 的 session；普通、bound、Dream session 永不进入；dry-run 返回候选 key/count/bytes；逐文件失败不阻断当前 cron 投递。

复用上游 `delete_session()`，必须同时清缓存和磁盘。

清理只留一个触发入口：gateway 启动时执行一次。不要在每次 cron run 之后再顺手 prune 一遍，也不要为它单开 CLI 子命令。三个入口意味着三处策略解释和三处测试，而这件事的实际时间尺度是天，启动时清一次足够。运维需要手工执行时，直接调那个函数即可，dry-run 作为参数存在。

```bash
uv run pytest -q tests/session/test_cron_retention.py tests/agent/test_session_delete.py
git add nanobot/session/manager.py nanobot/cli/commands.py \
  tests/session/test_cron_retention.py
git commit -m "feat: 回收隔离 cron 的历史会话"
```

## 10. 配置迁移与删除能力审计

新基座的 `Config` 使用 `extra_forbidden`。真实验证已经证明生产配置当前加载失败：首个错误是顶层 `tts`。不能把 schema 改成 `extra=ignore` 掩盖问题。

**Operational file（不进入代码 commit）：**

- `/root/workspace/nanobot_config/config.json`

部署前备份生产配置，删除：

- 顶层 `tts`
- `agents.defaults.contextPruning`
- `tools.commandRewrite`
- 任何 rtk / TraceHook 残留字段

先对备份副本操作并用新 worktree 代码加载；通过后再原子替换生产配置。

验收：

```bash
cd /root/git_code/nanobot/.worktrees/sync-2026-07
uv run python - <<'PY'
from pathlib import Path
from nanobot.config.loader import load_config
p = Path('/root/workspace/nanobot_config/config.json')
c = load_config(p)
print('LOAD_OK', type(c).__name__, c.agents.defaults.model)
PY

uv run pytest -q tests/config

grep -RInE 'ContextPruner|commandRewrite|TraceHook|timeout_seconds|edge-tts|TTSConfig' \
  nanobot tests pyproject.toml
```

最后一条 grep 只允许出现在明确的 migration/removal assertion 中；运行时代码零命中。

## 11. 分阶段回归与代码审查

每完成一个 commit，先跑任务级测试。每完成一个阶段，再跑聚合测试：

```bash
# provider/runtime
uv run pytest -q tests/providers tests/agent/test_subagent.py tests/test_tool_contextvars.py

# Discord/runtime/bootstrap
uv run pytest -q nanobot/channels/discord/tests tests/agent tests/command tests/tools

# cron/session
uv run pytest -q tests/cron tests/session tests/cli/test_commands.py
```

在全量测试前由独立 reviewer 审查：

- 是否复制了上游已有能力，尤其 Discord slash 是否重新实现了 builtin 已有的 dream/skill 逻辑。
- fallback 重试次数是否相乘。
- bound/unbound cron session 是否串线。
- OAuth refresh 是否存在共享 client 或凭据竞态。
- Discord command 闭包、名称冲突和可选依赖是否安全。
- bootstrap 是否覆盖了上游新增 prompt 段。
- 身份注入是否改动了 `nanobot/runtime_context.py` 这类通用机制文件。
- 所有 commit 是否只含本任务文件。

修复审查意见后再跑：

```bash
uv run pytest -q
```

## 12. 切换、部署、健康检查与回滚

### Task 12.1：切换前最终证据

```bash
cd /root/git_code/nanobot/.worktrees/sync-2026-07
git status --porcelain
git log --oneline --decorate upstream/main..HEAD
uv run pytest -q
```

工作区必须干净，每个 commit 只含本任务文件，全量测试全绿。

再跑一遍运行时依赖清单校验。这些文件不在代码里、不会被测试覆盖，但生产实际读取，缺了不报错只会让行为悄悄退化：

```bash
for f in docs/superpowers docs/plans docs/specs docs/202607/upstream-sync-2026-07; do
  [ -e "$f" ] && echo "OK   $f" || echo "MISS $f"
done
grep -n 'BOOTSTRAP_FILES' -A 6 nanobot/agent/context.py   # 必须四项，含 TOOLS.md
for f in SOUL.md USER.md AGENTS.md TOOLS.md; do
  [ -f /root/workspace/$f ] && echo "OK   workspace/$f" || echo "MISS workspace/$f"
done
```

任何一行 MISS 都不许往下走。

### Task 12.2：生产切换

1. 停止 gateway。
2. 确认 `backup/2026-07-27-pre-upstream → 9ca8c42d`。
3. 将生产 `main` 快进/重置到已审查的 sync branch；禁止在生产 checkout 手工解冲突。
4. 安装锁定依赖。
5. 用新代码加载真实 config。
6. 启动 gateway。

### Task 12.3：健康验证

```bash
systemctl --user restart nanobot-gateway
systemctl --user is-active nanobot-gateway
journalctl --user -u nanobot-gateway -n 200 --no-pager
```

人工验证：

- Discord 普通消息收发。
- 回复引用正文。
- 语音附件转写。
- 只 @ 其他 bot 不响应。
- `/model`、一个动态 `/skill`、`/dream-log`。
- 一个 bound cron 保持原 session。
- 一个 unbound cron 两次运行生成两个不同 session，并按指定 model/deep 执行。
- OAuth provider 完成最小请求；subagent 用不同 provider/model 成功运行。

### Task 12.4：回滚

若启动、真实配置加载或关键收发失败：停止服务，将 `main` 恢复到 `backup/2026-07-27-pre-upstream`，恢复 config 备份，重新安装旧锁文件依赖并启动服务。回滚后再次检查 `is-active` 和日志，不能只看进程存在。

## 13. 后续同步节奏

这次 70 天产生 1173 个上游 commit、43 个冲突文件，根因是 fork 能力长期压在重构路径上。以后不再等几个月大合：

- 每两周抓一次 `upstream/main`，只做三类扫描：上游是否吸收本地能力、是否改到 fork 热点、生产测试是否仍过。
- 每月完成一次小同步；若上游修改 provider runtime、Discord channel plugin、runner/fallback、cron/session 四个热点之一，当周同步。
- 每个纯本地能力维护一条行为测试。上游出现等价实现时，先删 fork 代码、保留测试验证，而不是继续养两套。

每次同步后记录一次纯本地补丁的数量，这个数字应该逐次下降。