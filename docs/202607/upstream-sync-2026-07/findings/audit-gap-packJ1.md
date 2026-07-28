# Pack J1 审计：9 个 fallback / 流式稳定性 commit 对照 plan.md

审计对象：本地 71 commit 中的 J1 组 9 笔。基座 `upstream/main=3f808d0a`，worktree `/root/git_code/nanobot/.worktrees/sync-2026-07`。
判定口径：COVERED = plan 已写清且够实现者照做；DROPPED-OK = 上游实读代码有等价能力；GAP = 行为会丢或 plan 说不清。

---

## 1. 09fbdc4a — tool_use ID 截断到 64 字符（跨 provider fallback）

**判定：DROPPED-OK（带残留小口）**

上游 `nanobot/providers/anthropic_provider.py:35-47`：

```python
def _sanitize_tool_id(tid: str) -> str:
    if not tid or _VALID_TOOL_ID.match(tid):
        return tid
    safe_prefix = re.sub(r"[^a-zA-Z0-9_-]", "_", tid)[:48].strip("_") or "toolu"
    digest = hashlib.sha1(tid.encode()).hexdigest()[:8]
```

非法字符 id 被重写为 `≤48 + "_" + 8` ≈ 最长 57 字符，天然落在 64 以内，且带 sha1 防碰撞（本地纯截断反而会碰撞）。调用点 166/186/194/247/301/607 全部走这一支，空 id 由 `_gen_tool_id()` 兜底。上游 `tests/providers/test_anthropic_tool_result.py:110/132/148/194` 已覆盖清洗一致性、碰撞、重复 id 重映射。

**残留**：合法字符集但超长（>64）的 id 会**原样透传**，上游没有任何长度上限。实践风险低（OpenAI `call_*` 约 29 字符，本地 `_gen_tool_id` 也短），但本地这条硬上限确实消失了。→ 列入 GAP 表 P3。

## 2. 8b3dc7ce — 零内容 stream stall 自动重试

**判定：DROPPED-OK**

上游把这层从 provider 挪到了 base 的统一重试：`nanobot/providers/base.py:680-736` `chat_stream_with_retry` 维护 `has_streamed_content`，并以 `should_retry_guard=lambda: not has_streamed_content`（base.py:735）传给 `_run_with_retry`（base.py:875-906，退避 `_CHAT_RETRY_DELAYS = (1, 2, 4)`，base.py:201）。语义与本地一致：**零内容才重试，已吐字不重复重试**；已吐字的场景改由 `on_stream_recover`（base.py:715-717, 901-906）关旧段开新段。上游 anthropic stall 返回 `error_kind="timeout"`（anthropic_provider.py:790-796），落进 base 的可重试集。本地版本更弱。

## 3. 218be2cc — 三阶段 stall 恢复（重点）

**判定：GAP（P0，plan Task 8.2 描述与本地行为不是同一套状态机）**

本地行为（`git show 218be2cc -- nanobot/agent/runner.py`）：
- Phase 1：同请求立即重试，上限 `_MAX_TIMEOUT_RETRIES = 2`；
- Phase 2：重试用尽后，把 stall 错误**写成占位消息塞进上下文**，`continue` agent loop（bot 继续工作，而不是当场沉默）；进入 Phase 2 时 `timeout_retries` 清零；
- Phase 3：`total_timeouts` 累计到 `_MAX_TOTAL_TIMEOUTS = 4` 时放弃本轮，持久化占位符后 break；`total_timeouts` **全程不清零**。

plan.md:460 原句：「保留上游 Phase 1 idle 检测。Phase 2 同模型、同上下文重试一次；连续 stall 到阈值后 Phase 3 返回可由 fallback provider 接管的错误。只有 stall/timeout 计数，正常响应清零。」

四条缺口：
1. **阈值全无数字**：2 次立即重试、4 次总预算，一个都没写。实现者无从照做。
2. **Phase 2 语义写错**：plan 的「同模型、同上下文重试一次」实际是本地 Phase 1；本地 Phase 2 的核心是「错误纳入上下文并继续 agent loop」，这正是 J1 报告 pack-j1-fallback-stall.md:155 认定必须保住的线上行为，plan 里消失了。
3. **计数清零规则对不上**：plan 说「正常响应清零」，本地是「进入 Phase 2 时清 `timeout_retries`，`total_timeouts` 永不清零」。若按 plan 实现，长会话中每次正常响应都清零总预算，Phase 3 的「4 次放弃」实际永远触发不了。
4. **放弃后如何交给 fallback 讲不通**：plan 说 Phase 3「返回可由 fallback provider 接管的错误」。但 `FallbackProvider` 在 provider 层、runner 之下——runner 拿到 stall 时 fallback 早已把所有模型试完（fallback_provider.py:185-260）。runner 再「返回错误」没有任何东西会接管。本地 Phase 3 是**放弃并留占位符**，plan 需要改写这句。

补在哪：plan.md:460 整段重写，写死阈值常量名与数值、Phase 2 的「占位符入上下文 + continue」、两个计数器各自的清零条件、Phase 3 明确为「终止本轮、持久化占位符、不再回落 provider」。

## 4. d5fa553c — 跨 provider tool id 测试 + streamed stall 不重试回归测试

**判定：GAP（P2，plan 没说改写目标）**

本地这笔改了 `tests/providers/test_anthropic_tool_result.py`（id 清洗断言）和一个「已吐字的 stall 不再重试」的回归断言。两个断言在新基座下命运不同：

- id 清洗断言：上游 `tests/providers/test_anthropic_tool_result.py:110-220` 已有更强版本，删掉即可；
- **「streamed stall 不重试」断言必须反转**：上游 fallback_provider.py:92-101 的契约是「Streamed timeout errors are the recovery exception」，200-209 / 242-253 明确在已吐字后仍对 timeout 做 failover 并 `has_streamed[0] = False`。照本地旧断言重放会直接**测反上游行为**。

plan Task 8.2 的 Files 列表（plan.md:452-458）只有 `test_runner_fallback.py`、`test_anthropic_stream_idle.py`、新建 `test_anthropic_client_reset.py`，没提这两个文件、也没写「旧断言要反转」。CLASSIFICATION.md:103 写的「改写为新版恢复协议测试」在 plan 里没有落点。

补在哪：plan.md:452-458 的 Files 加 `tests/providers/test_anthropic_tool_result.py`（删本地重复断言），正文加一句「已吐字 stall 的旧断言反转为：仍走 failover 并在新 segment 续流」。

## 5. b45ee3df — stall 后重建 Anthropic client

**判定：COVERED**

plan.md:462：「每次 Anthropic stall 关闭旧 client 并按原始 kwargs 重建。reset 失败不覆盖原始 stall。测试精确断言调用次数、新 client、消息序列合法性与 fallback 边界。」Files 含 `nanobot/providers/anthropic_provider.py` 与新建 `tests/providers/test_anthropic_client_reset.py`（plan.md:455, 458）。上游确实缺这一层：worktree grep `_reset_client` 零命中，`except asyncio.TimeoutError`（anthropic_provider.py:790）只造错误响应、不动 client。落点唯一且明确，够实现者照做。

## 6. 070d66c5 — 流中途 stall 后仍切模型

**判定：DROPPED-OK**

上游 `nanobot/providers/fallback_provider.py:92-101` 文档契约 + `:200-209`：已 streamed 且 `error_kind == "timeout"` 时不再跳过 failover，调用 `on_stream_recover` 后把 `has_streamed[0]` 复位再切下一模型；`:242-253` 对 fallback 链上的模型做同样处理。比本地实现更完整（本地只解决主模型一跳）。

## 7. e72440f1 — refusal 触发模型故障转移

**判定：GAP（P1，plan Task 8.1 缺一半落点）**

plan.md:442 写了行为：「refusal 必须尝试下一个模型，但不写 quota cooldown」。但 Files（plan.md:437-438）只有 `nanobot/providers/fallback_provider.py` 和新建 `tests/providers/test_fallback_provider.py`。

上游两处堵死：
1. `fallback_provider.py:53-58` `_NON_FALLBACK_ERROR_KINDS = frozenset({"content_filter", "refusal", "context_length", "invalid_request"})` —— refusal 明确列为不回退；
2. `anthropic_provider.py` 全文 grep `refusal` 零命中，`_parse_response` 的 stop_reason 映射里没有 refusal 分支，也就是说**上游根本不会产出 `error_kind="refusal"`**，refusal 以非错误 finish_reason 流出。

本地 e72440f1 同时改了 anthropic_provider（把 refusal 结构化成 error）和 fallback_provider（从非回退集移除）。plan 只写了后半段：即使按 plan 把 "refusal" 从集合里删掉，也永远没有 refusal 错误进来，Task 8.1 的行为落空。

补在哪：plan.md:437 Files 增 `nanobot/providers/anthropic_provider.py`；442 正文补一句「Anthropic `stop_reason == "refusal"` 需转成 `finish_reason="error"` + `error_kind="refusal"`，并同步从 `_NON_FALLBACK_ERROR_KINDS`（fallback_provider.py:53）移除」。注意这条与上游策略方向相反，pack-j1 里标的 [2] 待拍板状态未在 plan 中体现。

## 8. ecceb97b — provider/模型选择固定

**判定：DROPPED-OK**

上游 `nanobot/utils/llm_runtime.py:14-27`：`@dataclass(frozen=True, slots=True) class LLMRuntime`，provider / model / generation / context_window 一次性冻结在一个值对象里，注释明写「Consumers must use these fields instead of consulting provider.generation after admission」。`AgentRunSpec.runtime`（runner.py:81）在整轮执行中不可变，runner 全部调用点走 `spec.runtime.provider`（runner.py:405, 867, 899, 905, 1098, 1147），loop.py 在 run 入口一次性注入（loop.py:712, 884, 953, 999, 1353, 1556）。本地的「运行中 provider 被换掉」问题被结构性消除。

## 9. c157b38d — quota / 限流按模型冷却（重点）

**判定：GAP（P1，方向对，参数与边界不足以照做）**

本地实现要点（`git show c157b38d -- nanobot/providers/fallback_provider.py`）：
- 冷却窗口：`retry_after` 优先，钳制在 `[60s, 1800s]`；无 hint 时默认 `600s`；
- `retry_after` 来源：`response.retry_after or response.error_retry_after_s`（两个字段上游 `LLMResponse` 都已存在，由 base 解析 Retry-After header，见上游 `tests/providers/test_provider_retry_after_hints.py`）；
- 判定 quota 错误三条并联：`error_kind ∈ {rate_limit, quota, insufficient_quota, resource_exhausted}`、**存在正 `retry_after` 即视为限流**、文本 token 匹配（`insufficient_quota` 等）；
- 冷却粒度：`dict[model_key -> monotonic deadline]`，key 是**模型名**；主模型与每个 fallback 模型在进入前各查一次，命中则跳过；
- 全部模型都在冷却时有专门分支，把「因冷却跳过」与「熔断器 open」区分开返回。

plan.md:442 只写到：「quota/rate-limit 只冷却具体 provider/model；同 provider 的其他模型可用；普通错误、stall、refusal 不污染 cooldown；使用 `time.monotonic()` 或注入时钟」。缺四条实现者必须知道的：默认 600s、钳制区间 60–1800s、`retry_after` 存在即判限流这条推断规则、以及**所有候选都在冷却时的兜底语义**（强行试一个？直接返回冷却错误？）。另外 plan 说 key 是 `provider/model`，本地是裸 model 名——这是有意收紧还是笔误，plan 未交代，同名模型跨 provider 会误判。

补在哪：plan.md:442 之后加一段「冷却参数与判定」，把上述四项写死；并显式声明 key 用 `(provider, model)` 且说明与本地实现的差异是有意为之。

---

## GAP 汇总表

| 优先级 | Commit | 丢失行为 | 本地位置 | 上游查证 | 建议补在 plan 哪节 |
|---|---|---|---|---|---|
| P0 | 218be2cc | 阈值(2/4)、Phase 2「错误入上下文+continue loop」、两个计数器清零规则、Phase 3 放弃语义（plan 说的「交给 fallback 接管」不成立） | `nanobot/agent/runner.py` `_MAX_TIMEOUT_RETRIES` / `_MAX_TOTAL_TIMEOUTS` | runner.py grep 三个符号零命中；fallback 在 provider 层 185-260，早于 runner 结束 | 重写 plan.md:460 整段 |
| P1 | e72440f1 | Anthropic refusal 未结构化为 error，fallback 永远收不到 refusal | 本地 commit 同时改 anthropic_provider + fallback_provider | `fallback_provider.py:53-58` 含 "refusal"；anthropic_provider grep refusal 零命中 | plan.md:437 Files 增 anthropic_provider.py；442 补转换规则 |
| P1 | c157b38d | 默认 600s、钳制 60–1800s、「有 retry_after 即判限流」、全冷却兜底分支、key 粒度差异 | 本地 `_QUOTA_COOLDOWN_*` / `_is_quota_error` / `_trip_quota_cooldown` | 上游 fallback_provider grep cooldown/quota 零命中；`retry_after` 字段已存在 | plan.md:442 后加「冷却参数与判定」段 |
| P2 | d5fa553c | 「已吐字 stall 不重试」旧断言需反转；tool id 测试文件未列入 | `tests/providers/test_anthropic_tool_result.py` 等 | `fallback_provider.py:92-101, 200-209, 242-253` 契约相反 | plan.md:452-458 Files + 正文一句 |
| P3 | 09fbdc4a | 合法字符集超长 id 无 64 字符硬上限 | 本地 sanitize 中的长度截断 | `anthropic_provider.py:43` 合法 id 原样返回，全文无长度检查 | 并入 Task 8.2 备注即可，非阻塞 |

COVERED：b45ee3df（plan.md:462）。
DROPPED-OK：8b3dc7ce（base.py:680-736/875-906）、070d66c5（fallback_provider.py:92-101/200-253）、ecceb97b（llm_runtime.py:14-27 frozen LLMRuntime）、09fbdc4a（带 P3 残留）。
