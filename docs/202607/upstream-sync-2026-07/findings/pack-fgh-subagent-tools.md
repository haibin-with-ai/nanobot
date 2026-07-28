# Pack F/G/H — subagent 体系 + ContextPruner + 工具层

基座 `ba38f908`，上游 `upstream/main` = `3f808d0a`。以下所有证据均为只读 `git show/log/grep`，未改动任何源码。

## 先说三条全局事实（后面每条判定都挂在这上面）

**一、上游把 subagent 的模型来源整个重构了。** 新增 `nanobot/utils/llm_runtime.py`（`LLMRuntime` / `ProviderSnapshot` / `runtime_from_provider_snapshot`），`AgentRunSpec` 不再有 `model` / `reasoning_effort` / `max_tokens` 字段，改为持有一个必填的 `runtime: LLMRuntime`；subagent 通过 `current_request_context().runtime` 拿到调用方的 runtime（`af85c356 refactor(agent): capture subagent runtime before spawn`、`b4f06980 refactor(agent): make runner consume required runtime`）。本地 Spec5 那套"在 subagent 里手工 resolve model alias、手工 swap provider"的代码，**承载结构已经不存在**。

**二、上游 spawn 工具的参数面和本地是错开的。** 上游 `nanobot/agent/tools/spawn.py` 的 schema 是 `task / label / temperature / wait`（`7a6cc657 feat(spawn): allow per-subagent sampling temperature`、`3a400e02 feat(agent): support inline subagent consultation`）；本地是 `task / label / model / timeout_seconds`。两边零交集，合并结果只能是并集。

**三、上游把上下文治理独立成模块了。** `nanobot/agent/context_governance.py`（542 行，含 `compact_inflight_overflow` / `MICROCOMPACT_KEEP_RECENT` / `COMPACTABLE_TOOLS` / 工具结果落盘 offload）+ `nanobot/agent/autocompact.py`。本地没有 `context_governance.py`，本地 runner 里还是老的内联 `_microcompact`，`ContextPruner` 是挂在它前面的第二层。

---

## Pack F — subagent

### 551fe46b feat(subagent): independent model/provider, TraceHook, LLM req/resp logging (Spec5)
- 分类：**[2] 平行实现**（重放难度：**高**）
- 本地做了什么：10 文件 414 行。subagent 支持独立 model/provider（config 里 `SubagentDefaults`）、新增 `nanobot/agent/hooks/trace.py`（`TraceHook`）、`AgentHookContext` 加 `model` 字段、runner 里加 LLM 请求/响应日志。
- 上游现状：
  - 独立 model/provider：上游有基础设施但**没暴露给 spawn**。`git show upstream/main:nanobot/utils/llm_runtime.py` 有 `ProviderSnapshot(provider, model, preset)` 和 `runtime_from_provider_snapshot()`；但 `upstream:nanobot/agent/tools/spawn.py` 的 `execute()` 签名是 `(task, label, temperature, wait)`，第 88 行直接用 `request_ctx.runtime` —— subagent 跑的是**调用方的 runtime**，无法换模型。
  - `SubagentDefaults`：`git grep -ni "subagent" upstream/main -- nanobot/config/schema.py` 只有一行 `max_concurrent_subagents: int = Field(default=1, ge=1)`（schema.py:131），无模型默认值配置。
  - TraceHook：`git ls-tree upstream/main -- nanobot/agent/hooks/` 只有 `__init__.py` / `file_edit_activity.py` / `rewrite.py`，**无 trace.py**；`upstream:nanobot/agent/hook.py` 的 `AgentHookContext` 无 `model` 字段。
  - LLM req/resp logging：上游 `runner.py` 无对应日志点（检索 `LLM request` / `LLM response` 无命中）。
- 判定理由：功能上游确实没有，但本地实现方式（在 subagent 内解析 alias、往 `AgentRunSpec(model=..., reasoning_effort=...)` 塞字段）在上游已被 `LLMRuntime` 取代，diff 无法直接应用。要重放的是**意图**不是补丁：用 `runtime_from_provider_snapshot()` 造一个新 runtime 传给 spawn，代码量比本地版少。
- 风险/注意：`AgentHookContext` 加字段会碰上游 hook 协议（上游 `fe7d9435 fix(agent): preserve runtime compatibility contracts` 刚加固过兼容契约）；TraceHook 本身是独立文件，可原样落地，但要按上游 `hooks/__init__.py` 的注册方式接。`git grep TraceHook -- nanobot/ tests/` 显示本地也只有 hooks 目录内自引用，未被 loop 强绑定，迁移成本低。

### 409a3929 fix(tests): update spawn mock signatures for model/timeout_seconds kwargs
- 分类：**[3] 纯本地**（重放难度：**低**）
- 本地做了什么：`tests/test_tool_contextvars.py` 加 3 行，让 mock 接受 `model` / `timeout_seconds`。
- 上游现状：上游 spawn 无这两个参数，所以上游 mock 自然没有。
- 判定理由：它是 551fe46b + 057b23ad 的尾巴，跟着主功能走。主功能重放则改，主功能丢弃则丢弃。

### cb1dadfa fix: catch KeyError from model alias resolver in subagent spawn
- 分类：**[1] 上游已吸收**（严格说是"上游已消除该失败模式"）
- 本地做了什么：1 行，`except (ValueError, KeyError)` 包住 model alias 解析。
- 上游现状：上游 subagent 不解析 alias —— 模型来自 `request_ctx.runtime`，解析发生在更上游、在进入 subagent 之前。检索 `git grep -n "resolve_model_alias" upstream/main -- nanobot/agent/subagent.py` 无命中。
- 判定理由：这是本地"在 subagent 里 resolve alias"这个设计的补丁。设计不重放，补丁没有落脚点。若重放独立 model 特性，改用 `ProviderSnapshot` 后也不会有裸 KeyError。

### 58cd1135 fix(subagent): swap provider+model for cross-provider presets
- 分类：**[1] 上游已吸收**（设计层面被更好的方案取代）
- 本地做了什么：subagent.py +22 行，为跨 provider 的 preset 手工交换 provider+model，loop 里注入 `preset_snapshot_loader`。
- 上游现状：上游把这件事做成了一等公民 —— `upstream:nanobot/utils/llm_runtime.py` 的 `ProviderSnapshot` 就是 (provider, model, preset) 三元组，`runtime_from_provider_snapshot()` 负责整体切换；`upstream:nanobot/agent/loop.py` 也已有 `preset_snapshot_loader`。
- 判定理由：本地是在没有 runtime 抽象的年代打的手工补丁，上游的抽象覆盖了它且更干净。丢弃本地补丁，重放独立 model 特性时直接用上游 API。
- 风险/注意：丢弃的前提是**特性本身**（551fe46b）在上游 API 上重做。若 551fe46b 也丢，那跨 provider 能力一起没有。

### c41b8717 修复: subagent 并发上限 maxConcurrentSubagents 配置不生效
- 分类：**[1] 上游已吸收**（残留一行可选增量）
- 本地做了什么：loop 把 config 的 `max_concurrent_subagents` 传给 `SubagentManager`，并在配置热更新时同步 `self.subagents.max_concurrent_subagents`。
- 上游现状：`cfabc29f fix(agent): propagate maxConcurrentSubagents config to SubagentManager`（上游 2026-05-24，本地这笔 6 月才打）已修同一个 bug；`upstream:nanobot/agent/tools/spawn.py:79-86` 的超限拒绝文案与本地逐字相同。
- 判定理由：同一个 bug 被两边独立修掉，上游修得更早。整笔丢弃。
- 风险/注意：唯一没被覆盖的是**热更新同步**那一行（`git grep -n "_sync_subagent_runtime_limits" upstream/main -- nanobot/` 无命中；上游把 limit 直接构造进 `SubagentManager`，不存 loop 上）。如果在意"改配置不重启也生效"，这一行需要单独重放，成本约 1-3 行。

---

## Pack G — ContextPruner

### ced62a8c feat(pruner): add ContextPruner for transient tool result trimming (Spec6)
- 分类：**[2] 平行实现**（重放难度：**高**）
- 本地做了什么：新增 `nanobot/agent/pruner.py`（99 行），把老的 tool result 换成占位符，保留最近 N 个 assistant 轮次；schema 加 `context_pruning`（`SoftTrimConfig`，`enabled` 默认 **False**）；runner 在 `_microcompact` 之前调一次。
- 上游现状：上游有**功能上更强的等价物**，且是两层：
  - `upstream:nanobot/agent/context_governance.py` —— `compact_inflight_overflow()` + `MICROCOMPACT_KEEP_RECENT` + `COMPACTABLE_TOOLS` + `INFLIGHT_COMPACT_TARGET_RATIO`，做的正是"把旧 tool result 压成占位符、保留最近若干条"，并额外带：按 token 预算触发（不是每轮无条件跑）、记录已压缩 id 保证幂等、超大结果落盘 offload。
  - `upstream:nanobot/agent/autocompact.py` —— 整段历史摘要式压缩。
  - 关系：**autocompact 与本地 pruner 是正交的**（一个是到阈值后摘要整段历史，一个是逐条裁剪工具结果）；真正与本地 pruner **重叠甚至覆盖**的是 `context_governance.compact_inflight_overflow`，两者同层、同目标、同手法。
- 判定理由：本地 pruner 解决的问题上游已解决且更细。加上本地默认 `enabled=False`（schema 默认值），实际收益接近零。建议丢弃本地 pruner，接上游 `context_governance`；若真需要"更激进的裁剪"，改上游的 `MICROCOMPACT_KEEP_RECENT` / `INFLIGHT_COMPACT_TARGET_RATIO` 常数即可，不需要另立一个模块。
- 风险/注意：本地 runner 现在是"pruner → 内联 `_microcompact`"两段；换上游后内联 `_microcompact` 也会被 `context_governance` 取代，runner 那块要整段接上游版本，不能只删 pruner 调用。两个测试文件（`tests/agent/test_context_pruner.py` 164 行、`tests/config/test_context_pruning_config.py` 72 行）随之作废。

---

## Pack H — 工具层

### 057b23ad feat(tools): ripgrep-first grep, spawn timeout, workspace/message/shell tests (Spec7)
- 分类：**拆开看** —— grep 部分 **已被本地自己回滚**（见 862cf645，等同 [1]）；spawn timeout 部分 **[3] 纯本地**（重放难度：**高**）；测试部分 [3]（难度低-中）。
- 本地做了什么：search.py +326 行做 ripgrep 优先的 grep 后端抽象；subagent/spawn 加 `timeout_seconds`（`asyncio.wait_for(run_coro, timeout=timeout_seconds)`，本地 subagent.py:294-295）；补 config path / exec security / message 抑制的测试。
- 上游现状：
  - grep：`upstream:nanobot/agent/tools/search.py` 仍是**纯 Python 正则**（`re.compile` at line 438，全文无 `subprocess` / `ripgrep` / `rg` 命中），并新增了 `FindFilesTool`。
  - spawn timeout：上游无。上游只有 `llm_wall_timeout_for_session`（单次 LLM 调用的墙钟超时），**没有整个 subagent 任务的截止时间**；`upstream:spawn.py` 的 execute 签名里无 `timeout_seconds`。
- 判定理由：ripgrep 那半已被自己否掉，别再捡。timeout 这半是上游确实缺的实用能力，值得重放，但要落在被重构过的 spawn/subagent 上（上游该文件 70 天内 17 笔提交），属于"重写而非 cherry-pick"。
- 风险/注意：重放 timeout 时注意上游新增的 inline 模式（`wait=true`，`7e15c4c4 fix(agent): track inline subagent lifecycle`）—— 阻塞式 spawn 加超时的语义要单独想清楚，本地版没这个场景。

### 862cf645 refactor: roll back search.py to upstream, remove rg/grep backend abstraction
- 分类：**[1] 上游已吸收 / 直接丢弃**
- 本地做了什么：删掉 057b23ad 的 326 行 rg 抽象和 207 行测试。
- 上游现状 / 核实结果：`git diff --stat ba38f908:nanobot/agent/tools/search.py main:nanobot/agent/tools/search.py` **输出为空** —— 本地 search.py 与 fork 基座**逐字节一致**，无任何本地自创内容。另 `tests/tools/test_grep_backends.py` 已不存在于本地。
- 判定理由：回滚彻底成功。它使本地与**当时的**上游一致，但不等于与当前上游一致 —— 上游此后又有 5 笔提交（`480ca28a` / `44ef697a` / `3a420136` / `84935609 refactor(tools): use structured tool error results` / `b189a376`），diff 显示上游多 187 行、本地多的 12 行全是旧版本残影。
- 结论：057b23ad + 862cf645 这一对在 search.py 上净效果为零，**两笔一起丢弃，search.py 整文件取上游**。

### 45f75cb2 disable read_file dedup — always return full content
- 分类：**[2] 平行实现**（重放难度：**低**）
- 本地做了什么：删掉 read_file 的"文件未变则返回 `[File unchanged...]` 桩"逻辑，只保留 `record_read` 记账，永远返回全文。
- 上游现状：**dedup 仍在**，但上游给了逃生口 —— `upstream:nanobot/agent/tools/filesystem.py:237-238` 新增 `force=BooleanSchema("Bypass same-file read deduplication and return content again.")`，:282 `force: bool = False`，:328-359 是完整的 dedup 判定链（mtime 变化 → `can_dedup = False` → 读全文；mtime 未变但内容变 → 同样读全文）。
- 判定理由：同一个痛点两种解法。上游选"默认 dedup + 可显式绕过 + 内容哈希兜底"，本地选"直接砍掉"。上游方案信息更全（连"mtime 未变但内容变"都处理了），本地方案对模型更友好（不需要它记得传 `force`）。这条**需要 haibin 拍板**。
- 风险/注意：若采上游，要接受 agent 偶尔拿到 `[File unchanged]` 桩；SOUL.md 里"Read before you write / 编辑前重读原文件"这条习惯会经常撞上它 —— 这正是本地当初砍掉 dedup 的动机。折中方案：保留上游代码，把 `force` 默认值改 True，一行改动，冲突面最小。

### e0e86179 fix(gitstore): line_ages 改用原生 git blame 替换 dulwich annotate
- 分类：**[3] 纯本地**（重放难度：**低**）
- 本地做了什么：`line_ages()` 改用 `subprocess ["git","blame","--porcelain",...]` + `_parse_blame_committer_times()` 解析，替换 dulwich。
- 上游现状：**仍是 dulwich**。`git show upstream/main:nanobot/utils/gitstore.py` 第 294 行 `annotated = porcelain.annotate(str(self._workspace), file_path)`，第 49 行 `_compute_line_ages(annotated)` 仍消费 annotate 的 `((commit, tree_entry), line_bytes)` 结构。
- 判定理由：上游没有等价改动，必须重放。且 `git log -L :line_ages:nanobot/utils/gitstore.py ba38f908..upstream/main` **无输出** —— 上游 70 天里 8 笔 gitstore 提交没有一笔碰过 `line_ages`，补丁可近乎直接应用。
- 风险/注意：上游有 `dfc3919b fix: stop masking runtime failures`，重放时确认本地的 `except → logger.exception` 兜底不与上游"不掩盖失败"的方向冲突。

---

## 小结

**逐条挑，且天平明显偏向上游。** 十笔里只有两笔是无争议的净收益。

**直接丢弃（4 笔）**：`c41b8717`（上游 `cfabc29f` 先修，仅热更新一行可选补）、`cb1dadfa` + `58cd1135`（都是本地手工 alias/provider 方案的补丁，该方案已被上游 `LLMRuntime`/`ProviderSnapshot` 取代）、`862cf645` + `057b23ad` 的 search 部分（净效果为零，search.py 整文件取上游）。

**必须重放（1 笔，成本最低）**：`e0e86179` gitstore git blame —— 上游没碰过 `line_ages`，几乎白拿。

**haibin 已拍板（2026-07-27）**：`551fe46b` 中 TraceHook / LLM 请求响应日志暂不重放；`057b23ad` 中 spawn `timeout_seconds` 暂不重放，`409a3929` 中只服务于 timeout 参数的测试调整随之丢弃。`551fe46b` 中的「subagent 独立 model/provider」是另一项能力，仍须单独判定，不能整笔丢弃。

**haibin 已拍板（2026-07-27）**：`45f75cb2` 保留「read_file 默认关闭去重、总是返回全文」的本地语义；实现时不回放旧 diff，而是在上游新实现上做最小改动，优先保留上游哈希与 `force` 机制、把默认行为改为全文返回并用测试确认。`ced62a8c` 整笔丢弃，直接采用上游 `context_governance.compact_inflight_overflow` + autocompact 方案，不再保留本地 `ContextPruner`。

**重放时会碰的上游文件**：`nanobot/agent/subagent.py`、`nanobot/agent/tools/spawn.py`（上游 70 天 17 笔提交，冲突最重）、`nanobot/agent/runner.py`（`AgentRunSpec` 已改为 runtime 驱动）、`nanobot/agent/loop.py`、`nanobot/agent/hook.py` + `nanobot/agent/hooks/__init__.py`、`nanobot/config/schema.py`、`nanobot/utils/gitstore.py`（最干净）、`nanobot/utils/llm_runtime.py`（只读、作为新 API 使用）。`nanobot/agent/tools/search.py` 与 `nanobot/agent/pruner.py` 不需要碰 —— 前者整取上游，后者整删。
