# plan.md 遗漏审计总表（71 commit 逐条对账）

审计时间：2026-07-28
基座：`upstream/main = 3f808d0a`　本地：`ba38f908..HEAD` 共 71 commit
判定口径：COVERED（plan 有落点）/ DROPPED-OK（上游已有等价实现或已拍板砍掉）/ GAP（三者皆非）

覆盖情况：71/71 已核。分包审计明细见同目录 `audit-gap-packBC.md`、`audit-gap-packDEI.md`、`audit-gap-packFGH.md`、`audit-gap-packJ1.md`；Pack A（14 笔文档）与 Pack J2/K（15 笔）由主线程直接核对，证据记于本文件。

结论：**15 个 GAP**，其中 5 个会导致重放后系统不可用而非仅退化。

---

## 一、阻断级（重放后直接坏）

### GAP-1　`config/schema.py` 缺 `anthropic_claude_code` 字段 → 配置加载抛 ValueError

上游 `nanobot/config/schema.py:286` 的 `convert_extra_providers` 会对落进 `model_extra` 的 key 调 `find_by_name`，一旦命中 registry 已注册的 spec 就 `raise ValueError`。plan §2 的 Files 清单与 `git add` 列表均无 `config/schema.py`（全文命中 2 处，均在其他语境）。

registry 注册 spec 与 schema 加字段必须在同一笔提交内完成，否则用户配置里写 `anthropicClaudeCode` 会在启动阶段直接失败。

补入：plan Task 2.1 Files 增 `nanobot/config/schema.py`，正文说明字段与 `AliasChoices`。

### GAP-2　Claude Code 身份是 system block，不是 HTTP header

本地 `nanobot/providers/anthropic_provider.py:23` 定义常量并在 `product_mode == "claude_code"` 时 insert 到 system 数组首位。上游全仓 grep `official CLI for Claude` 零命中。

plan Task 2.2 只写了「静态身份头放进 `ProviderSpec.default_extra_headers`」。该链路（`registry.py:56` → `factory.py:33-38`）只能注 HTTP 头，碰不到请求体。按现文重放，OAuth 握手通过但请求会被拒。

补入：plan Task 2.2 增一条 system block 注入要求，明确它与 `default_extra_headers` 是两条独立链路。

### GAP-3　`TOOLS.md` 掉出 bootstrap

上游 `nanobot/agent/context.py:57` 为 `["AGENTS.md", "SOUL.md", "USER.md"]`；本地为四项，多 `TOOLS.md`。生产 `/root/workspace/TOOLS.md` 现役（2101 字节），承载临时文件规则、llm-note 交付走 write 技能等约定。

plan Task 7.1 只列三个文件。按现文重放，该文件从下次重启起不再进入上下文。

补入：plan Task 7.1 的 bootstrap 文件清单补 `TOOLS.md`。

### GAP-4　stall 三阶段恢复协议写错且不完整（218be2cc）

plan.md:460 那段的问题有四处：未写阈值 `_MAX_TIMEOUT_RETRIES=2` / `_MAX_TOTAL_TIMEOUTS=4`；未写 Phase 2 的核心语义（把 stall 错误写入上下文后 continue loop，而非静默 break）；未写两个计数器的清零规则；Phase 3 描述为「交给 fallback 接管」不成立——fallback 在 provider 层 185-260 行，早于 runner 结束，runner 放弃时 fallback 已无机会介入。

上游 `nanobot/agent/runner.py` grep `MAX_TIMEOUT_RETRIES` / `MAX_TOTAL_TIMEOUTS` / `timeout_retries` 全部零命中，即 Phase 2/3 必须新写。上游 Phase 1 idle 检测在 `providers/anthropic_provider.py:723-792`（`resolve_stream_idle_timeout_s`）。

本地这笔修的是真实事故：Phase 1 耗尽后 loop 静默中断，bot 直接停止工作且无恢复尝试。

补入：重写 plan.md:460 整段。

### GAP-5　Anthropic refusal 未结构化为 error，fallback 永远收不到（e72440f1）

上游 `fallback_provider.py:53-58` 的错误分类里含 `"refusal"`，但 `anthropic_provider.py` grep `refusal` 零命中——上游 anthropic 侧不产生这个信号，fallback 侧的分支是死代码。

本地那笔同时改了 anthropic_provider 与 fallback_provider 两侧。plan.md:437 的 Files 只列了 fallback 侧。

补入：plan.md:437 Files 增 `nanobot/providers/anthropic_provider.py`，442 行补 refusal → 结构化 error 的转换规则。

---

## 二、行为退化级

### GAP-6　quota 冷却缺关键参数与判定逻辑（c157b38d）

缺：默认 600s、钳制区间 60–1800s、「响应带 `retry_after` 即判限流」这条判定、全部 provider 冷却时的兜底分支、冷却 key 的粒度。上游 `fallback_provider.py` grep `cooldown` / `quota` 零命中（`retry_after` 字段本身已存在）。

现文对实现者不足以照做。补在 plan.md:442 之后。

### GAP-7　spawn 传未知 preset 会崩（cb1dadfa）

上游 `model_presets.py:76-82` 对未知 preset 名 `raise KeyError`，`model_runtime.py:116` 直接透传，`spawn.py` 全文无 try/except。本地 `subagent.py:247` 降级为裸字符串不崩。

补入：plan Task 3.2 在 spawn 边界补捕获，Task 3.1 加断言「未知 preset 返回 `ToolResult.error` 而非抛异常」。

### GAP-8　assistant 消息落库缺 model / usage / elapsed_ms / llm_elapsed_ms（85f47e11）

上游 `loop.py:1851` 只写 `latency_ms`。plan 第 6 节只说了给 `AgentRunResult` 加字段，没给落库出口——字段加了但不持久化。

### GAP-9　user 消息落库缺 sender_id / sender_name（85f47e11）

同上，plan 第 6 节无落点。

### GAP-10　本地历史文档在新基座上全部丢失（Pack A 14 笔 + 03b44175）

CLASSIFICATION 把这批判为「归档保留」，plan 全文对 `docs/` 零动作。实际规模：

```
docs/superpowers   本地 17 文件 / 上游 0
docs/plans         本地  1 文件 / 上游 0
docs/specs         本地  1 文件 / 上游 0
docs/202607        本地  2 文件 / 上游 0（已提交部分）
```

另有本次同步自己产出的 22 个文件（spec、plan、CLASSIFICATION、findings 全套）尚未提交，处于未跟踪状态。新基座是从 `upstream/main` 拉的干净分支，其 `docs/` 有 53 个上游文件且不含以上任何目录。

补入：plan 第 1 节增一条归档任务，第 12 节切换步骤前置校验这些路径存在。

---

## 三、测试与边界

### GAP-11　d5fa553c 旧断言需反转，且 tool id 测试文件未列入

「已吐字 stall 不重试」的旧断言与上游契约相反（`fallback_provider.py:92-101, 200-209, 242-253`）。plan.md:452-458 的 Files 未列 `tests/providers/test_anthropic_tool_result.py`。

### GAP-12　message send_callback 失败路径无测试（057b23ad）

实现存在于 `message.py`（`_sent_in_turn` 赋值在 await 之后），但上游 `tests/tools/test_message_tool*.py` 无 `side_effect` 注入用例。建议 plan 第 4 节新增 Task 4.3，仅补测试。

### GAP-13　tool id 无 64 字符硬上限（09fbdc4a 残留）

上游 `anthropic_provider.py:43` 对合法 id 原样返回，全文无长度检查。合法字符集但超长的 id 不会被截断。非阻塞。

### GAP-14　OAuth token 注入路径一句未写

plan 全文 grep `auth_token` 零命中。本地 `anthropic_provider.py:130-131` 用 `AsyncAnthropic(auth_token=...)` 而非 `api_key`；上游 anthropic_provider grep `auth_token|auth_mode|oauth` 零命中。整节接线主干靠标题隐含，最易被当成「上游已有」跳过。

### GAP-15　凭据迁移少两条来源

本地 `oauth_store.py:76-80` 是三级优先级：`CLAUDE_CODE_OAUTH_TOKEN` 环境变量 → `~/.claude/` → legacy `~/.claude.json`。plan 只写了第 2 条。

---

## 四、有意偏离但未写理由（需拍板）

- bootstrap 顺序：plan Task 7.1 写 `SOUL → AGENTS → USER`，原实现为 `SOUL → USER → AGENTS`。
- soul anchor：plan 要求「精简、不重复整个 SOUL.md」，原实现为全文双曝光，但未给「精简到什么程度算过关」的判据。

---

## 五、复核为「不是遗漏」的高风险项

以下几条形似遗漏，实测已被覆盖，重放时不必再花时间：

- `_sanitize_tool_id` 保留下划线（3419e4d8）：上游 `anthropic_provider.py:43` 为 `if not tid or _VALID_TOOL_ID.match(tid): return tid`，合法 id 原样返回，那个每天 08:00 触发的 thinking 签名失效根因不复现。
- cron `model is None → deep`（aa21c8ce / 67bd27c2）：与本地 `cron/types.py:32` 注释一字不差，plan Task 9.1/9.2 覆盖 `CronPayload.model`、`cron:{job.id}:{run_id}` session key、per-job model 全部三条。
- `.gitignore` 五条（78dc871d）：plan Task 1.2 逐字列全 `data-gym-cache/`、`graphify-out/`、`pytest-of-root/`、`tmp*.jpg`、`tmp*.png`。
- provider/model 前缀剥离（9e251310 / 182ea6b8）：上游 `factory.py:24-30` 的 `_resolve_model_preset` 无 `/` 拆分，`schema.py:495` 的 `_match_provider` 只用前缀选路由不改写 model 串，必须重放；plan.md:112 已有落点。
- adaptive thinking（d61aca5d / 76c43718）：上游 `anthropic_provider.py:564-574` 已有 `adaptive` 分支且比本地完整（保留 `enabled` + budget 作为其他档位）。
- anthropic SDK 版本（9ca8c42d）：plan.md:489 明确写了 `anthropic>=0.120.0,<1.0.0`，:497 的 `git add` 含 `pyproject.toml uv.lock`。
- Dream diff-grounded commit message（0d7d9439）：上游 `gitstore.py:327` 有 `summarize_working_tree`、`:432` 有 `_head_tree`、`memory.py:625/737` 有 `dream_content_diff` 与 `build_dream_commit_message`，完全吸收。
- Dream model_override 换 provider（0928d8d9）：上游改走 preset 架构（`loop.py:485` 传 `dream_model_preset`，`model_presets.py:46` 与 `model_runtime.py:243` 落到 `build_provider_snapshot`），本地那个「写 codex 却打到 Anthropic 端点」的 bug 在上游结构里不存在。
- Dream 单阶段重构（55b46a2f / e7545114）：本就是移植上游 `d1a94dae`。
- 057b23ad 携带的三个测试文件：`tests/config/test_config_paths.py`、`tests/tools/test_exec_security.py`、`tests/tools/test_message_tool_suppress.py` 在上游均存在，仅 message 那条的 `side_effect` 用例缺失（见 GAP-12）。
