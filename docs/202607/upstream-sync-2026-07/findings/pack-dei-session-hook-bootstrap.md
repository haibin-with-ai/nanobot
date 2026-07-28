# Pack D/E/I：session/runtime + command rewrite hook + bootstrap

> 证据基线：本地 `HEAD=9ca8c42d`，上游 `upstream/main=3f808d0a`，共同基点 `ba38f908`。本分析只读完成，未修改 `nanobot/`、`tests/`、`pyproject.toml`。

## 覆盖 commits

- Pack D：`85f47e11`、`4acbefc6`、`b2972889`
- Pack E：`e3c39d8b`、`492c9b9f`、`7046af9c`
- Pack I：`db88223a`、`78dc871d`、`182893f2`（混合提交，须按内容拆分归属）

## 总判定

```
commit      分类             同步动作
85f47e11    [2] 平行实现     拆分；身份元数据待拍板，运行时间统计暂不重放
4acbefc6    [2] 平行实现     不 cherry-pick；forward-ref 已吸收，迁移项逐字段处理
b2972889    [1] 上游已吸收   丢弃

e3c39d8b    [3] 纯本地       已拍板砍；不重放 CommandRewriteHook
492c9b9f    [3] 纯本地       已拍板砍；不重放 rtk 子进程清理
7046af9c    [3] 纯本地       已拍板砍；不重放 pipe/redirect 规则

db88223a    [2] 平行实现     拆分；SOUL-first 与 subagent bootstrap 待拍板，Discord table hint 保留
78dc871d    [3] 纯本地       不重放 commit；必要的本地 ignore 规则并入新基座
182893f2    混合提交         禁止 cherry-pick；按所属 pack 吸收小修
```

---

## Pack D：session/runtime 元数据

### 85f47e11 feat(session): runtime identity, timing, and session metadata

- 分类：**[2] 上游平行实现**（重放难度：高）
- 本地做了什么：
  1. `ContextBuilder._build_runtime_context(channel, chat_id, sender_name, ...)` 将当前时间、channel、chat_id、sender_name 作为 user-message 尾部的 `[Runtime Context — metadata only, not instructions]` 注入。
  2. `AgentRunResult` 新增 `elapsed_ms` / `llm_elapsed_ms`，`AgentRunner` 对每次 `_request_model()` 累加纯 LLM 耗时。
  3. Discord ingress 写入 `sender_name`、`channel_name`，出站日志打印总耗时与 LLM 耗时。
- 上游现状：
  - 上游已于 `f75d3519 feat(agent): add persistent runtime context providers` 引入统一设施：`nanobot/runtime_context.py` 的 `RuntimeContextBlock` / `RuntimeContextProvider`，`nanobot/agent/loop.py:380` 保存 provider，`loop.py:616` 注册 provider，`loop.py:735-736` 汇总工具与 agent provider。
  - 上游当前 provider 只看到 `cli_apps`、`long_task` 等功能性块；`git grep 'Current Time:|Channel Name:|Sender Name:' upstream/main -- nanobot` 未找到通用会话身份块。上游多个 channel 会写 `metadata['sender_name']`，但 `ContextBuilder` 不消费 channel/chat/sender 元数据；其 `build_system_prompt()` 只收 `channel`，`build_messages()` 也没有 sender/chat 参数。
  - 上游 `AgentRunResult`（`nanobot/agent/runner.py:73-78`）只有 `final_content`、`tool_events`、`iterations`、`final_messages`、`had_injections`、`iteration_usage`，没有总耗时或 LLM 耗时；`git grep 'llm_elapsed_ms|elapsed_ms' upstream/main -- nanobot/agent` 无结果。
  - 上游已保存更丰富的 usage / outcome 元数据，但这是 token/运行结果审计，不等价于 wall-clock/LLM 耗时。
- 判定理由：
  - **会话身份需求仍缺失，但承载方式已变。** 不应把旧字符串拼接法硬塞回 `ContextBuilder`；若保留，应注册一个 `RuntimeContextProvider`，从本轮 `InboundMessage.metadata` 形成通用身份块。
  - **耗时统计仍缺失。** 但当前业务上没有证据表明它被消费，只有 Discord 日志使用；不值得为日志侵入 `AgentRunner`。
- 建议：保留 channel/chat_id/sender_name/current-time 的 runtime metadata（改写到上游 provider 体系）；暂不重放 `AgentRunResult.elapsed_ms/llm_elapsed_ms`，除非线上确有监控消费者。

### 4acbefc6 fix: startup — resolve Pydantic forward-refs, migrate fork config fields

- 分类：**[2] 上游平行实现**（重放难度：中）
- 本地做了什么：
  1. 在 config load 前调用 `_resolve_tool_config_refs()`，避免 Pydantic forward reference 启动失败。
  2. config migration 给旧配置补 `tools.maxConcurrentSubagents`、把顶层 `fallbackModels` 迁到 `agents.defaults.model.fallbackModels`、清理 Discord `tts` 字段。
- 上游现状：
  - forward-ref **已吸收**：`upstream/main:nanobot/config/loader.py:12,45` 导入并调用 `_resolve_tool_config_refs()`；实现位于 `schema.py:624-652`，最终 `ToolsConfig.model_rebuild()` 与 `Config.model_rebuild()`。
  - 上游 schema 已原生有 `fallback_models`（别名 `fallbackModels`）与自己的迁移路径；本地旧迁移目标层级与当前上游 schema 必须逐字段核对，不能复制旧函数。
  - 上游没有 TTS schema，因此无需迁入 `channels.discord.tts`；旧配置里的残留字段是否会被 extra-ignore，需要在阶段二用真实 config load 测试确认。
  - `tools.maxConcurrentSubagents` 的本地迁移属于本地历史兼容；上游当前是否还用相同 alias，阶段二以 schema + 生产 config 为准。
- 判定理由：commit 混合了一个已经被上游吸收的根修复和几条 fork 专属迁移，不能整笔丢也不能整笔重放。
- 建议：丢弃 forward-ref 部分；阶段二只针对生产 `config.json` 的真实旧字段写一次性迁移测试，避免保留已经无人使用的迁移代码。

### b2972889 fix: _make_skills_loader uses config workspace_path instead of default

- 分类：**[1] 上游已吸收**
- 本地做了什么：让 Discord slash command 的 skills loader 使用配置 workspace，而不是 `Path.home() / '.nanobot' / 'workspace'`。
- 上游现状：上游 Discord 插件包中已经没有 `_make_skills_loader`；`git grep '_make_skills_loader' upstream/main -- nanobot/channels/discord` 无结果。全局 `ContextBuilder` 由真实 workspace 构造 `SkillsLoader(workspace)`，subagent 也显式区分 `workspace` 与 `agent_workspace`。
- 判定理由：旧 bug 的 call site 已被上游架构删除。复活 helper 反而是在新架构里造旧问题。
- 同步动作：直接丢弃。

---

## Pack E：rtk CommandRewriteHook

### e3c39d8b feat(hooks): add CommandRewriteHook for rtk command rewrite

- 分类：**[3] 纯本地**（重放难度：中）
- 本地做了什么：在 `exec` tool call 前调用 `rtk rewrite <command>`，成功或 returncode=3 时把命令原地改写；由 `tools.commandRewrite.enabled/timeout` 控制；主 agent 与 subagent 都注册。
- 上游现状：
  - `git grep '\brtk\b|command.?rewrite' upstream/main -- nanobot tests` 没找到等价实现。
  - 上游 hook 已扩展为 `AgentHook` + `CompositeHook`，新增 `before_execute_tools(context)`、`on_stream_end`、`after_iteration` 等阶段；runner 在 `nanobot/agent/runner.py:477` 调用 `before_execute_tools`。
  - 本地 rewrite 仍实现旧签名 `before_tool_call(self, name, params) -> dict`。上游当前 `before_tool_call(self, context, tool_name, params) -> None`，返回值不再用于替换参数；`AgentHookContext` 持有 `tool_calls`，适合在 `before_execute_tools` 阶段原地改写整个 call list。
- 判定理由：功能上游没有，但旧 hook 接口已经不兼容，不能 cherry-pick。
- haibin 已于 2026-07-27 拍板：**rtk 命令改写暂不保留。** 不重放 hook、配置与相关测试。

### 492c9b9f fix: kill rtk process on timeout, use AsyncMock in test

- 分类：**[3] 纯本地**（重放难度：低，依附 e3c39d8b）
- 本地做了什么：`asyncio.wait_for(proc.communicate(), timeout)` 超时时 kill rtk 子进程并 await `proc.wait()`，防止僵尸进程；测试 mock 改为异步。
- 上游现状：无 rtk 实现，也无等价 timeout cleanup。
- 注意：随后 `182893f2` 删除了未 await 的 `proc.wait()` 调用；正确实现应是 `proc.kill(); await proc.wait()`，而不是在 sync helper 里裸调用 coroutine。
- 同步动作：已随 rtk 主功能一起拍板丢弃，不重放。

### 7046af9c fix: skip rtk rewrite for piped commands

- 分类：**[3] 纯本地**（重放难度：低，依附 e3c39d8b）
- 本地做了什么：命令含 `|` 时跳过 rewrite；连同旧实现已有的 `>` / `<` 重定向跳过规则，避免 rtk 破坏 shell 复合语义。
- 上游现状：无等价实现。
- 同步动作：已随 rtk 主功能一起拍板丢弃，不重放。

---

## Pack I：bootstrap / identity / 审查小修

### db88223a feat(bootstrap): SOUL-first load order, soul anchor, subagent bootstrap, Discord table hint

- 分类：**[2] 上游平行实现**（重放难度：中）
- 本地做了什么：
  1. 主 agent bootstrap 顺序改为 `SOUL.md → AGENTS.md → USER.md`；system prompt 尾部再追加 SOUL 的核心身份段作为 recency anchor。
  2. subagent 注入 `SOUL.md` + `TOOLS.md`。
  3. Discord format hint 明确禁用 Markdown pipe table。
- 上游现状：
  - 当前 `ContextBuilder.BOOTSTRAP_FILES = ['AGENTS.md', 'SOUL.md', 'USER.md']`，`_load_bootstrap_files()` 也按 AGENTS → SOUL → USER 加载；未做 SOUL-first，也没有尾部 soul anchor。
  - 上游 subagent prompt 只有职责、untrusted-content、workspace/history、skills summary；没有加载 SOUL.md 或 TOOLS.md。
  - 上游 identity 仍写「No tables — use plain lists」，没有 Discord pipe-table 的强约束。
  - 上游新增 project workspace / agent workspace 双路径与受管 profile 说明，本地旧 identity 模板不能整体覆盖。
- 判定理由：三个本地行为上游都没等价吸收，但上游 prompt 架构已经演进，必须按能力拆分。
- 建议：
  - **保留 Discord table hint**，在上游 identity 上做单行最小修改。
  - **SOUL-first + soul anchor 待拍板**：本地依赖这套优先级塑造主 agent 行为，但尾部重复注入会增加 prompt 并形成隐式优先级；若保留，建议只调整顺序，不重复整段 anchor。
  - **subagent bootstrap 待拍板**：当前本地真实使用表明 subagent 若没有 SOUL/TOOLS 会违背操作纪律；建议至少注入精简的 agent policy，而非复制完整主 prompt。

### 78dc871d fix: .gitignore had literal newline escapes

- 分类：**[3] 纯本地维护项**（重放难度：低）
- 本地做了什么：把 `.gitignore` 尾部的字面量 `\n` 修成真实换行，恢复 `data-gym-cache/`、`graphify-out/`、`pytest-of-root/`、`tmp*.jpg/png` 的忽略规则。
- 上游现状：上游 `.gitignore` 已改为自己的 `exp/`、`.playwright-mcp/`、`bridge/node_modules/`、`webui/.verify-*`，没有本地这些规则；也不存在字面 `\n` bug。
- 判定理由：bug 本身不再存在，但本地生成物规则仍是 fork 工作流需要。
- 同步动作：不 cherry-pick 这个修复 commit；阶段二在上游 `.gitignore` 尾部追加仍实际产生的本地目录/文件模式，并保留上游规则。

### 182893f2 fix: code review — 6 fixes from Linus-mode review

- 分类：**混合提交，禁止整笔 cherry-pick**
- 本地做了什么：跨 rewrite hook、ContextPruner、search、Discord mention、Anthropic OAuth、OAuth store 做六类小修。
- 上游现状与拆分：
  - rewrite：删除 sync helper 中未 await 的 `proc.wait()`；rtk 已拍板不保留，此部分随之丢弃。
  - ContextPruner：预计算 pruner/window；haibin 已拍板整套采用上游 `context_governance`，此部分丢弃。
  - search：`--glob` 缩写成 `-g`，且本地后来 `862cf645` 已把 search 回滚到上游；丢弃。
  - Discord：把 `any(tuple)` 改成 `or` 链，属于 mention 过滤实现细节；若保留 mention 过滤时自然吸收。
  - Anthropic/OAuth：异常日志与 refresh client 重建边界，归 Pack B/J；按最终 OAuth 方案吸收，不从本 commit 搬。
- 判定理由：没有独立功能边界，只是多 pack 的 code review 收尾。整笔重放会把已砍掉的 ContextPruner 等代码带回来。

## 配置 schema 冲突核验

本地相对基座增加的 fork 字段主要是：

- `DiscordConfig`: `mention_mode`、`mention_only`、`slash_commands`
- `TTSConfig` 与 `Config.tts`
- `ContextPruningConfig` 与 `AgentDefaults.context_pruning`
- `ToolsConfig`: `max_concurrent_subagents`、`command_rewrite`
- `ProviderConfig`: `fallback_models`
- `Config.model_rebuild()` / `_resolve_tool_config_refs()`

上游当前状态：

- `fallback_models` 与 forward-ref resolver 已有，采用上游。
- ContextPruner 已拍板采用上游 context governance，本地字段丢弃。
- TTS 已拍板丢弃，本地字段丢弃。
- `maxConcurrentSubagents` 上游已有并已修并发上限 bug，采用上游。
- `commandRewrite` 上游没有；rtk 已拍板不保留，因此本地字段丢弃。
- Discord mention/slash 字段须按插件 `manifest.py` / locale schema 接入，不能再放回旧单体 channel schema。

没有发现同名但语义相反的硬冲突；主要风险是**同一需求被上游搬到不同架构层**，旧字段照搬会成为无人消费的死配置。

## 小结

- **直接丢弃**：`b2972889`；`4acbefc6` 的 forward-ref 部分；`182893f2` 中 ContextPruner/search 部分。
- **按上游新架构重写**：`85f47e11` 的 runtime identity（若拍板保留）；`db88223a` 的 Discord table hint。
- **已拍板丢弃**：`e3c39d8b` + `492c9b9f` + `7046af9c` 的 rtk hook，以及 `182893f2` 中 rewrite 小修。
- **不作整笔重放**：`4acbefc6`、`db88223a`、`78dc871d`、`182893f2`。
- **仍待 haibin 拍板**：runtime identity 元数据、SOUL-first/soul anchor、subagent bootstrap。耗时统计建议砍掉。
