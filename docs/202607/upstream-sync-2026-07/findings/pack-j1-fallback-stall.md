# Pack J1：fallback 与流式稳定性 — 归属判定

范围：10 个本地 commit（fallback 切换策略 / 流式 stall 恢复 / tool_use_id 清洗）。
基座 merge-base `ba38f908`，上游 `upstream/main` = `3f808d0a`。

## 上游侧基本盘（先摆事实，后面逐条引用）

`git show upstream/main:nanobot/providers/fallback_provider.py`（378 行）与本地版（380 行）同源但已分叉。

上游配置面也已核实：`git show upstream/main:nanobot/config/schema.py` 的 `AgentDefaults` 定义 `fallback_models: list[str | ModelPreset] = Field(default_factory=list, alias="fallbackModels")`；`fallbackModels` 可填 preset 名或内联 `ModelPreset`。`factory.py::build_provider` 先经 `resolve_model_presets` 把它们解析成完整 preset，再构造 `FallbackProvider(primary, fallback_presets=..., provider_factory=...)`。因此本地改动不能再按旧的「纯模型名 list」结构整块重放。上游的 primary 熔断按 **provider 实例** 计数，quota 冷却若补回必须按 `fallback.model` 过滤，不可退回旧字段。  
证据锚点：`schema.py:98`、`schema.py:111-112`、`factory.py:146-188`（均指 `upstream/main`）。

上游在 `ba38f908..upstream/main` 区间对该文件有 8 笔提交：

```
96eb965a feat(webui): show the actual fallback model (#5017)
15de6be0 fix(providers): fall back on authentication errors
2099cb00 fix(providers): fail over across provider failure domains
c2c47f7a fix(fallback): treat empty API choices as fallbackable error
8ecc2d69 fix: log primary model error before fallback
c00371c7 docs: clarify streamed timeout fallback behavior
bc4bb508 fix: continue recovered streams in a new segment
2c5a4e07 fix(providers): allow retry and fallback on stream stalled timeout
```

逐项对账：

| 能力 | 上游 | 证据 |
|---|---|---|
| refusal 触发切换 | **没有，且方向相反** | `fallback_provider.py:28-33` 的 `_NON_FALLBACK_ERROR_KINDS` 显式含 `"refusal"`；`_should_fallback` 在 `:356-363` 命中即 `return False`。上游 `anthropic_provider.py` 也没有 `stop_reason == "refusal"` 的结构化分支；其 `stop_reason` 只统一进入 `LLMResponse(finish_reason=stop_reason or "stop")`（约 `:788-795`） |
| 按模型 quota 冷却 | **没有** | `git grep -ni cooldown upstream/main -- nanobot/ tests/` 只命中 primary 熔断常量/逻辑：`_PRIMARY_FAILURE_THRESHOLD=3`、`_PRIMARY_COOLDOWN_S=60`、`_primary_tripped_at`（`fallback_provider.py:14-15,142-152`）；无 per-model 冷却表、无 quota cooldown、无基于 `retry_after` 的跨请求跳过 |
| stall 失败后由 fallback 切模型 | **已有** | `fallback_provider.py:201-226` 对 primary 已吐内容的 timeout 清 `has_streamed` 后继续 failover；`:242-256` 对 fallback stall 开新 stream segment 并试下一个。来源 commit `2c5a4e07` / `bc4bb508` |
| stall 后同模型重试 | **已有（base 层）** | `base.py::_run_with_retry` 在 `:865-920` 以 `error_kind == "timeout"` 认定 transient；有内容则 `on_stream_recover()`，没回调则关 delta；随后按 `_CHAT_RETRY_DELAYS` 重试。不是本地 `anthropic_provider` 内部重试实现 |
| 跨 provider tool_use_id 清洗 | **已有且更完整** | `anthropic_provider.py:33-47`：合法 `[a-zA-Z0-9_-]+` 原样保留；非法 id 改成 48 字符前缀 + 8 位 SHA1；调用覆盖 tool_use / tool_result 的非流式与流式路径，详见下文 |

---

### e72440f1 fix(fallback): refusal 触发模型故障转移
- 分类：**[2] 平行实现（方向冲突）**（重放难度：中）
- 本地做了什么：`anthropic_provider` 在 `stop_reason == "refusal"` 时返回 `finish_reason="error", error_kind="refusal", error_should_retry=True`；`fallback_provider` 把 `"refusal"` 从 `_NON_FALLBACK_ERROR_KINDS` 挪进 `_FALLBACK_ERROR_KINDS`。
- 上游现状：上游 `fallback_provider.py` 的 `_NON_FALLBACK_ERROR_KINDS = frozenset({"content_filter", "refusal", "context_length", "invalid_request"})` —— refusal 明确不切换。上游 Anthropic `_parse_response` 把 `stop_reason="refusal"` 保留为 `finish_reason="refusal"`，却不设 `error_kind`，所以 `FallbackProvider` 的主入口（仅 `finish_reason == "error"` 才判 fallback）实际会直接当成功返回；这比「refusal 属于不切集合」更早终止。也就是说本地改动与上游当前语义**正好相反**，不是「上游还没做」而是「上游做了相反的决定」。
- 判定理由：功能重叠但语义对立，必须 haibin 拍板。本地 commit message 和测试明确针对「Opus 4.6 偶发 refusal 后可切到 fallback」；上游把 refusal 放入不切集合的选择来自最初引入 fallback candidate 的 `5efd6791 feat(runner): support fallback candidates`，但该 commit message 只谈配置解析，**未给 refusal 不切的理由**。证据缺口不能拿猜测填。
- 风险/注意：重放时只是一个 token 在两个 frozenset 之间移动，冲突面极小，但要连带保留 anthropic 侧那 15 行 refusal 分支，否则 `error_kind="refusal"` 根本不会产生。若你把真实生产 availability 放在第一位，这笔应重放；它是明确的产品策略分叉，不该伪装成纯技术去重。

### c157b38d fallback: quota/限流错误按模型冷却，默认10分钟
- 分类：**[3] 纯本地**（重放难度：中）
- 本地做了什么：新增 `_QUOTA_COOLDOWN_DEFAULT_S = 600`（min 60 / max 1800）、`_QUOTA_ERROR_TOKENS`、实例字段 `self._quota_cooldowns: dict[str, float]`，命中 quota/rate-limit 后按模型名静默一段时间，支持从响应字段 `error_retry_after`（本地属性名）读取 retry-after，并钳制到 60–1800 秒。
- 上游现状：**没有**。上游 `fallback_provider.py` 全文（378 行）无 per-model `cooldown` 字典、无 `_QUOTA_*` 常量；唯一 cooldown 是 primary 级别的 3 次失败 / 60 秒熔断。上游把 quota 相关词放在 `_FALLBACK_ERROR_TOKENS` 里（`insufficient_quota` / `quota_exceeded` / `out of credits`），只用于「这次要不要切」，不做跨请求记忆。注意上游 base retry 有 `_extract_retry_after_from_response` 用来安排**当前调用链**的 sleep，但它不让下一个用户请求跳过该模型，不等价于本地冷却。
- 判定理由：上游无等价物，且这是线上真实痛点（一个模型欠费后每个请求都要先撞一次）。必须重放。
- 风险/注意：上游 `_should_fallback` 已被 `15de6be0`（auth 错误也切）和 `2099cb00`（跨 provider 故障域）重写过，本地的冷却逻辑要接到新版 `_should_fallback` / `_try_with_fallback` 上，不能整块覆盖。

### 070d66c5 修复流式中途卡死无法自动切换模型的问题
- 分类：**[1] 上游已吸收（上游实现更好）**（重放难度：—）
- 本地做了什么：调整判定顺序——先判 `_should_fallback` 再判 `has_streamed`，让「已吐出部分内容 + 可切换错误」也继续 failover；并把 `error_should_retry is False` 从「一票否决」改成「fallbackable kind 仍然切」。
- 上游现状：上游用**更彻底**的方案解决同一问题：`supports_stream_recover_callback = True`，`chat_stream` 接收 `on_stream_recover` 回调；primary 流 stall（`error_kind == "timeout"`）且已有内容时，日志 `"Primary model '{}' stream stalled after content was emitted; attempting failover anyway"`，随后 `has_streamed[0] = False` 并 `await on_stream_recover()` 开一个新的 stream segment 继续 failover；fallback 之间 stall 也同样处理。对应上游 commit `2c5a4e07 fix(providers): allow retry and fallback on stream stalled timeout` 与 `bc4bb508 fix: continue recovered streams in a new segment`。
- 判定理由：本地只是「硬切、丢掉已输出的部分内容」，上游是「开新 segment 续上」，语义更干净且带回调协议。直接采用上游。
- 风险/注意：上游方案要求下游（runner / channel）实现 `on_stream_recover`，切上游后要确认本地 channel 侧接得住；这是本 pack 里最需要联调的一处。

---

## tool_use_id 清洗

上游 `anthropic_provider.py` 已有完整实现（第 33–47 行）：

```python
_VALID_TOOL_ID = re.compile(r"^[a-zA-Z0-9_-]+$")

def _sanitize_tool_id(tid: str) -> str:
    if not tid or _VALID_TOOL_ID.match(tid):
        return tid
    safe_prefix = re.sub(r"[^a-zA-Z0-9_-]", "_", tid)[:48].strip("_") or "toolu"
    digest = hashlib.sha1(tid.encode()).hexdigest()[:8]
    return f"{safe_prefix}_{digest}"
```

来源：`4d7c2074 fix(anthropic): sanitize tool_use/tool_result IDs to API pattern` + `0e986155 fix: keep duplicate id repair in Anthropic provider`。调用点覆盖 6 处（166 / 186 / 194 / 247 / 301 行等），比本地 2 处更全。

### 09fbdc4a fix: sanitize tool_use IDs to 64 chars for cross-provider fallback
- 分类：**[1] 上游已吸收**（重放难度：—）
- 本地做了什么：新增 `_sanitize_tool_id`，仅做 `tool_id[:64]` 截断，用在 `tool_result.tool_use_id` 与 `tool_use.id` 两处。
- 上游现状：**已有且更强**。上游先用 `_VALID_TOOL_ID` 判合法（合法则原样返回，零改写），非法才 `48 字符前缀 + sha1(8)` → 结果恒 ≤ 57 字符，天然满足 64 上限，且冲突概率远低于裸截断（裸截断会把两个长 id 压成同一个）。
- 判定理由：目标一致、上游实现严格更优，直接采用上游。

### 3419e4d8 fix: 保留 tool id 中的下划线，修复早报 cron 的 thinking block 报错
- 分类：**[1] 上游已吸收（同一个坑，上游独立踩到并修对）**（重放难度：—）
- 本地做了什么：把 `_TOOL_ID_RE` 从 `[^a-zA-Z0-9-]` 改成 `[^a-zA-Z0-9_-]`，保住下划线。本地注释写明了真实事故：extended thinking 开启时 thinking block 的 signature 承诺了精确的 tool_use id，把原生 `toolu_01...` 改写成 `toolu-01...` 会让 signature 失效，回放时报 `thinking blocks ... cannot be modified` —— 这是早报 cron 的线上故障。
- 上游现状：上游 `_VALID_TOOL_ID = r"^[a-zA-Z0-9_-]+$"` 同样保留下划线，且更进一步：合法 id **完全不进改写路径**，原生 Anthropic id 恒等回传，signature 不可能被破坏。
- 判定理由：上游语义已覆盖本地修复。唯一值得搬的是**注释里的事故知识**（为什么不能动下划线），建议在切上游后把这段中文成因注释补进 `_sanitize_tool_id` 的 docstring，否则以后有人再「优化」正则会重踩。
- 风险/注意：这一笔属于「本地踩到的线上 bug」，但结论是上游已经修好——不需要重放代码，需要重放**回归测试**（见 d5fa553c）。

---

## 流式 stall 恢复

上游 `anthropic_provider.chat_stream`（约 723–795 行）的形态：`idle_timeout_s = resolve_stream_idle_timeout_s()`，用 `asyncio.wait_for(stream.__anext__(), timeout=idle_timeout_s)` 逐 chunk 卡表（注释明确说要跟踪**任何** SSE chunk 而非只跟 text_stream，否则 extended thinking 会假 stall），超时后 `except asyncio.TimeoutError: return LLMResponse(..., finish_reason="error", error_kind="timeout")`。

关键差异：**上游 anthropic provider 层只负责侦测并报告 stall，不负责 client 恢复**。重试/切换在上层——`base.py::_run_with_retry`（约 860–989 行）拿 `error_kind == "timeout"` 判定 transient；有已输出内容且有回调时先 `on_stream_recover()` 开新 segment，无回调则关掉 delta，随后才按 `_CHAT_RETRY_DELAYS` 重试。`fallback_provider.py` 则把 timeout stall 放行给下一个模型。`runner.py` 另有 `NANOBOT_LLM_TIMEOUT_S`（默认 300s）外层墙钟 + 流式请求 `max(300, timeout*2)` 的放宽。

检索结果：
- `git grep -c "_reset_client" upstream/main -- nanobot/providers/anthropic_provider.py` → **0**，上游没有 client 重建。
- `git grep "_MAX_TOTAL_TIMEOUTS\|_MAX_TIMEOUT_RETRIES" upstream/main -- nanobot/` → **空**，上游没有 runner 层的分阶段超时预算。
- 上游 anthropic 里没有 `_StreamStall` 这类携带 `had_content` 的内部异常。
- 上游 anthropic 也没有 `read_timeout` 字样；idle stall 由 `asyncio.wait_for(__anext__)` 自己卡表。它与 httpx read timeout 是两回事。

这回答了核实重点 2：**上游已有 stall 检测、同模型重试、跨模型 failover 与流 segment 重建；没有 Anthropic client/httpx 连接池重建，也没有本地 runner 的 3-phase 总超时预算。**

### 8b3dc7ce feat: auto-retry on stream stall with zero content
- 分类：**[1] 上游已吸收（平行实现后上游更完整）**（重放难度：—）
- 本地做了什么：引入 `_StreamStall(idle_timeout_s, had_content)` 内部异常，从 `_do_stream` 传到 `chat_stream`；零内容 stall → 同模型本地重试一次，再 stall 则 `error_should_retry=True` 交给 fallback；已吐内容的 stall → 不重试、`error_should_retry=False`（避免重复内容）。commit message 记录了事故日期 2026-05-20/21。
- 上游现状：上游用**同一目标、不同分层**——`had_content` 的角色由 `base.py::_run_with_retry` 的 `should_retry_guard` 承担；无内容 timeout 直接按 transient 同模型 retry；有内容 timeout 先 `on_stream_recover()` 开新 segment 再 retry；retry 仍失败后，`fallback_provider` 再切模型。provider 层不做本地 retry loop。
- 判定理由：上游已覆盖「零内容 stall 自动重试」，还补了有内容续流和跨模型恢复。若再重放本地 provider 层 retry，会与 base 层重试叠乘。**弃用本地 retry 代码，采上游。**
- 风险/注意：b45ee3df 当时挂在 `_StreamStall` handler 上，但 client 重建这个副作用仍是上游缺口；必须把它换落点重放，不能因 8b3dc7ce 被吸收而一起丢掉。

### b45ee3df fix(anthropic): 流 stall 后重建 client，避免复用死连接的超时螺旋
- 分类：**[3] 纯本地 —— 线上 bug，上游没遇到没修**（重放难度：中）
- 本地做了什么：新增 `_reset_client()`（复用既有 `_build_client`，与 OAuth 刷新同模式），在 `_StreamStall` 处理入口丢弃整个 httpx 连接池。commit message 给了完整因果链：stall 的物理成因是 httpx 连接池里一条半死的 keep-alive socket，重试复用 `self._client` 会大概率再抓到同一条中毒连接 → 再 stall 90s；叠加 runner 的 4 次超时重试就是**~6 分钟原地撞墙后放弃**，用户观感是「到这一步就不 work 了」。
- 上游现状：**完全没有**。上游 `anthropic_provider.py` 全文无 `_reset_client`，stall 后所有重试路径（base 层 retry、fallback 层 failover 到同一 provider 的另一个模型）都复用同一个 `self._client` 实例。
- 判定理由：**必须重放，且是本 pack 优先级最高的一笔**。这是纯粹的物理层缺陷修复，与上游的分层重构正交——不管重试逻辑放在 provider 还是 base，只要重试还走同一个连接池，这个 bug 就在。**上游为何未修、是否未遇到，没有证据；只能确认当前代码缺少连接池重建。**
- 风险/注意：重放时不能依赖 `_StreamStall`（若采纳上游分层，该异常会消失）。正确的落点是在 `chat_stream` 的 `except asyncio.TimeoutError` 分支里，返回 timeout 响应**之前**调一次 `self._reset_client()`——这样无论上层用哪种恢复策略，下一次请求拿到的都是干净连接。回归测试 `test_zero_content_stall_rebuilds_client_before_retry` 也要跟着改写断言。

### 218be2cc 3-phase timeout recovery for stream stalls
- 分类：**[3] 纯本地 runner 策略（但只部分建议重放）**（重放难度：高）
- 本地做了什么：`runner.py` 加 `_MAX_TIMEOUT_RETRIES=2` / `_MAX_TOTAL_TIMEOUTS=4` 两个计数器，三阶段——① 立即重试 ② 重试耗尽但总预算未尽时，把 `_append_model_error_placeholder(messages)` 塞进上下文继续 agent loop ③ 总预算耗尽才 break。commit message 点明修的是「Phase 1 耗尽后静默 break、bot 直接不工作」。
- 上游现状：**没有 3-phase 策略**（grep 两个常量均为空）。上游确有通用 `_append_model_error_placeholder`（`runner.py:668`）且 error 时会持久化 placeholder，但随后 `break`；只有当中途 injection/long-goal 要求继续时才再进 loop。上游的 provider base 会做同请求 transient retry，fallback 会换模型，却没有「全失败后接受错误进上下文并无条件再开一轮」的 Phase 2。
- 判定理由：不能把它误标成「上游已吸收」。**Phase 2 的用户可见语义仍是纯本地**；只是 Phase 1 已被上游 base retry 覆盖，原样搬会形成 runner × base 的重试乘法。
- 风险/注意：建议重放 Phase 2/3 的**总预算与继续 agent loop**，删除本地 Phase 1 同请求 retry；或者先做故障注入实测，证明「上游 base retry + fallback + b45ee3df client reset」已经达到可接受成功率，再决定弃用。没有实测前整笔丢掉，就是第三次把线上事故当过时代码清掉。

### d5fa553c fix cross-provider tool_use_id sanitization tests + streamed stall no-retry regression test
- 分类：**[3] 纯本地（测试资产）**（重放难度：低）
- 本地做了什么：两件事——① 更新 `test_anthropic_tool_result.py` 里 tool_use_id 清洗的断言；② 新增 `tests/providers/test_fallback_streamed_no_retry.py`（159 行），断言「已流出内容 + primary stall 时 `error_should_retry=False`」，防止把同一个注定失败的请求重试 4 次浪费 ~7 分钟。
- 上游现状：上游无同名测试文件。但注意——上游 `bc4bb508` 已经把这个场景的**期望行为改掉了**：现在是开新 segment 继续 failover，而不是 `error_should_retry=False` 放弃。
- 判定理由：① 的断言随上游 `_sanitize_tool_id` 实现变化，直接按上游行为重写即可；② 的断言与上游语义冲突，**不能原样重放**，但测试描述的成本场景（7 分钟白等）是真金白银的线上教训，应改写成「stall 后总耗时上界」的回归测试保留下来。
- 风险/注意：这是唯一能守住上面几笔行为的安全网，别在合并里顺手删掉。

---

## 归属汇总

| commit | 分类 | 动作 |
|---|---|---|
| 09fbdc4a tool_use id 64 字符 | [1] 上游已吸收 | 弃用，采上游 |
| 3419e4d8 保留下划线 | [1] 上游已吸收 | 弃用代码，**搬注释** |
| 070d66c5 stall 后仍切模型 | [1] 上游已吸收（更优） | 弃用，采上游 `on_stream_recover` |
| e72440f1 refusal 触发切换 | [2] 方向冲突 | **需 haibin 拍板** |
| 8b3dc7ce 零内容 stall 重试 | [1] 上游已吸收（更完整） | 弃用 provider 层重试，采上游 base retry |
| 218be2cc 三阶段超时恢复 | [3] 纯本地 runner 策略 | **Phase 2/3 建议重放；Phase 1 不搬** |
| c157b38d quota 按模型冷却 | [3] 纯本地 | **必须重放** |
| b45ee3df stall 后重建 client | [3] 纯本地·线上 bug | **必须重放，最高优先级** |
| d5fa553c 回归测试 | [3] 测试资产 | 改写断言后保留 |
| 09eefb | 不存在 | `git cat-file -t 09eefb` → `fatal: Not a valid object name 09eefb`；`git log --all` / `git rev-list --all` 均无此前缀，按指令忽略 |

## 必须重放：本地线上事故而上游未修

1. **`b45ee3df`：Anthropic stall 后重建 client。** 这是确定项。上游只有 stream segment 重建，没有 `self._client` / httpx 连接池重建；重试继续踩半死 keep-alive socket 的物理故障仍在。落点改到上游 `chat_stream` 的 `except asyncio.TimeoutError`，不要复活整套 `_StreamStall`。
2. **`218be2cc` 的 Phase 2/3：全链路 timeout 后继续 agent loop，并设总预算。** 上游 provider retry/fallback 改善了前半段，但全失败后 runner 仍持久化 placeholder 后 `break`，没有本地的无条件再开一轮。该事故表现正是「bot 直接不工作」。不要原样搬 Phase 1，以免重试叠乘；Phase 2/3 是必须保住的行为，除非故障注入实测证明可删除。
3. **`c157b38d`：quota/限流按模型跨请求冷却。** 严格说是线上运维缺口而非单点 bug；上游只会本次 fallback，下一请求还会再撞同一坏模型。生产上必须重放。

`3419e4d8` 也是本地真实线上事故（早报 cron 的 thinking signature 被 tool id 改写），但上游已独立修好：合法含下划线 id 原样返回。因此只搬事故注释与回归意图，不搬代码。`070d66c5` 同理：本地确实踩到「中途 stall 不切模型」，上游 `2c5a4e07` / `bc4bb508` 已覆盖且实现更好。

## 建议 replay 顺序（按依赖与风险）

1. 先采用上游 `fallback_provider` / `base` 的 stream recovery 协议及 `fallbackModels` preset 结构。
2. 在上游 anthropic timeout 捕获点重放 `b45ee3df` 的 client reset，并补「下一次 call 使用新 client」测试。
3. 在新版 `FallbackProvider` 上重写 `c157b38d`：按 `fallback.model` 冷却，不能覆盖上游 auth/failure-domain/observer 逻辑。
4. 重放 `e72440f1`（若 availability 优先的本地策略继续成立），连同 Anthropic refusal 结构化返回测试。
5. 只移植 `218be2cc` Phase 2/3 的 runner 语义；删掉 Phase 1。同一故障最多由 base retry 一层负责。
6. 把 `d5fa553c` 改写为新版协议测试：id 合法恒等、stall 开新 segment、client reset、总恢复时长/尝试次数有上界。

## 证据缺口

- 已读 upstream commit `2c5a4e07` / `bc4bb508` 正文与 diff；它们分别引入「stall 可 retry/fallback」和「续流开新 segment」。
- 已追到 refusal 不切换的引入 commit `5efd6791`；commit message 未解释该选择，**上游作者意图未抓到**。这里只能确认代码事实，不能替作者编理由。
- 未做故障注入实测，尚不能量化上游 base retry + fallback + client reset 后 `218be2cc` Phase 2 的剩余收益；因此报告采取保守合并：保 Phase 2/3，去 Phase 1。


