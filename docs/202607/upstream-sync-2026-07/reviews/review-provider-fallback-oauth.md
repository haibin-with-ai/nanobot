# 独立审查：provider / fallback / OAuth / subagent 模型选择

- 审查范围：`git diff 3f808d0a...HEAD`（worktree `/root/git_code/nanobot/.worktrees/sync-2026-07`，分支 `sync-upstream-2026-07`）
- 落在审查范围内的实际改动（`git diff --stat`）：

```
nanobot/agent/runner.py                 |  33 ++++-
nanobot/agent/subagent.py               |  27 +++-
nanobot/agent/tools/spawn.py            |  34 ++++-
nanobot/providers/anthropic_provider.py |  75 ++++++++++-
nanobot/providers/factory.py            |  14 +-
nanobot/providers/oauth_store.py        | 225 +++++++++++++++++++++++++++++++++
nanobot/providers/registry.py           |  16 +++
```

- 只读审查，未修改任何文件、未做 git 写操作。跑过的验证：`uv run --frozen pytest -q tests/providers` → **886 passed in 11.70s**；`uv run --frozen pytest -q tests/providers/test_oauth_store.py tests/providers/test_anthropic_token_refresh.py tests/providers/test_anthropic_claude_code.py tests/agent/tools/test_spawn_model_selection.py tests/agent/test_subagent.py` → **75 passed**。

## 先说最要紧的一条前提事实

`nanobot/providers/fallback_provider.py` 在本次 diff 里**一行都没动**（不在 `git diff --stat` 输出中），runner 里也**没有 stall 三阶段状态机**（全仓 `grep -rn "MAX_TIMEOUT_RETRIES\|MAX_TOTAL_TIMEOUTS\|timeout_retries\|_reset_client\|quota_cooldown" nanobot/ tests/` 零命中）。也就是说 plan.md 的 Task 8.1（refusal 触发 fallback + quota 冷却 + client reset）和 Task 8.2（stall 三阶段重试）**尚未回放**，`git log --oneline 3f808d0a..HEAD` 里也没有对应提交。

所以第 1、2、3 条的结论要分成两段读：**当前代码有没有 bug**（多数是"没有，因为功能不存在"），以及**回放这两个 Task 时会踩什么坑**。

---

## 1. stall 重试 × fallback 模型链的相乘放大

**结论：当前分支不存在"stall 三阶段 × 模型链"的相乘，因为 stall 状态机不存在。但 upstream 自带的通用重试与 fallback 链已经在相乘，最坏一次 `_request_model` 就是 13 次上游请求（2 个 fallback 时）。回放 8.2 时若把重试写进 runner，这个数字会再乘一次。**

代码路径证据：

- `nanobot/agent/runner.py:944-952`：runner 调 `provider.chat_stream_with_retry(...)` / `chat_with_retry(...)`，外面只包一层 `asyncio.wait_for(coro, timeout=outer_timeout_s)`，runner 自身不做模型选择，也不做请求级重试。**既定纪律这一条在当前代码里是守住的。**
- `nanobot/providers/base.py:875-900, 930-985`：`_run_with_retry` 是重试的唯一来源。standard 模式 `_CHAT_RETRY_DELAYS = (1, 2, 4)`，`attempt` 递增到 `> 3` 才 break → **最多 4 次外层尝试**；另有非瞬时错误的"剥图重试"分支再多打一次完整链路。
- `nanobot/providers/fallback_provider.py:181-300`：每次外层尝试进入 `FallbackProvider.chat_stream`，顺序打 primary + 全部 fallback 模型，**子 provider 调的是 `chat_stream`/`chat` 而不是 `*_with_retry`**，子层不再叠重试。这一层设计是对的。
- 熔断在 `fallback_provider.py:140-180`：`_primary_failures` 累到阈值后 primary 被跳过，能削掉一部分。

最坏情况算术（N = fallback 模型数，取 N=2）：

| 阶段 | 上游请求数 |
|---|---|
| 外层尝试 1–3（primary + 2 fallback） | 3 × 3 = 9 |
| 外层尝试 4（primary 已熔断，跳过） | 2 |
| 非瞬时错误的剥图重试（primary 已熔断） | 2 |
| **单次 `_request_model` 合计** | **13** |

再往上叠 runner 层：同一轮迭代里还有 malformed tool call 的二次 `_request_model`、`_request_no_tools` 兜底（`runner.py` 主循环）、以及空回复重试（`_MAX_EMPTY_RETRIES`），一轮迭代最坏 ~39 次；`max_tool_iterations` 默认 200（`nanobot/config/schema.py:130`），理论上限在千级，实际靠 `wait_for` 和墙钟时间兜住。

严重级别：**建议**（当前代码），但回放 8.2 时是**阻断级前置条件**——stall 重试必须只改 stall 状态（是否继续等、是否放弃本次流），不得在 runner 里换模型或重发请求；否则上表要再乘 3。

最小修复方向：回放 8.2 时把三阶段计数器放在 `FallbackProvider` 或 `base._run_with_retry` 一侧，runner 只消费 `finish_reason`；并给 `_run_with_retry` 加一个"整链路总请求数"硬上限（例如 `attempts × (1+N) ≤ 12`），避免 N 变大时线性爆炸。

### 附带发现：外层超时把 fallback 一起掐掉

`runner.py:944-952` 的 `outer_timeout_s` 包住的是**整条重试+fallback 链**，不是单次请求。非流式路径（`chat_with_retry`）的 `outer_timeout_s` 就等于单次请求的 `timeout_s`（默认 300s），意味着只要 primary 自己慢到接近超时，`wait_for` 会在 fallback 还没轮到时就把整条链 cancel 掉，用户看到的是 `timed out after 300s`，而不是 fallback 的结果。

- 严重级别：**重要**（影响非流式路径：cron、subagent 等）。
- 最小修复：外层预算应为 `单请求超时 × 预期链长`，或把超时下沉到 provider 单次请求层，runner 只保留一个远大于链路预算的兜底值。

## 2. 重试耗尽后是否 break 出主循环

**已核对，无问题（不会静默停摆），但需要在回放 8.2 时守住。**

证据 `nanobot/agent/runner.py:677-697`：

```
if response.finish_reason == "error":
    ... final_content = clean or spec.error_message or _DEFAULT_ERROR_MESSAGE
    stop_reason = "error"; error = final_content
    self._append_model_error_placeholder(messages)
    ... _try_drain_injections(... phase="after LLM error") → should_continue 则 continue
    break
```

- `_DEFAULT_ERROR_MESSAGE`（`runner.py:54`）保证 `final_content` 非空，用户一定收得到错误消息，**不是历史上的静默停摆**。
- `_append_model_error_placeholder`（`runner.py:1508-1511`）把占位 assistant 消息写回 `messages`，历史结构保持合法，符合"错误写回上下文"的要求。
- 这里的 `break` 结束的是本轮 turn（带错误回执），不是无声退出；且注入队列非空时会 `continue` 回主循环。

严重级别：无。回放 8.2 时的红线：stall 耗尽必须走这条同一路径（构造 `finish_reason="error"` 的 response 交回主循环），**不能在 `_request_model` 内部直接 return/raise 出主循环**。

## 3. refusal 是否触发 fallback、client reset、quota 冷却

**结论：fork 要求的 refusal 触发切换 —— 没有实现，当前仍是上游语义（refusal 不切换）。这是本次同步的功能缺口，不是新引入的 bug。**

证据：

- `nanobot/providers/fallback_provider.py:50-60`：`_NON_FALLBACK_ERROR_KINDS` 中仍含 `"refusal"`（第 55 行）。全仓 provider 层 `grep -rn "refusal" nanobot/providers/*.py` 只有这一处加 `base.py:178` 的注释，没有任何 fork 侧覆盖。
- quota 冷却：无专用实现（`grep quota_cooldown` 零命中）。现有的只有通用熔断 `_PRIMARY_FAILURE_THRESHOLD` / `_primary_tripped_at`（`fallback_provider.py:140-180`）。
- client reset：无实现（`grep _reset_client` 零命中）。

严重级别：**重要**（功能缺口，plan Task 8.1 未回放）。最小修复方向：在 fork 侧从 `_NON_FALLBACK_ERROR_KINDS` 移除 `"refusal"` 并补一条针对 refusal 的判定（注意区分"模型拒答"与"内容安全网关拒绝"，后者切模型也没用，会白烧一整条链）。

**状态泄漏部分：已核对，当前实现无泄漏。** 证据：

- `fallback_provider.py:181-300`：`chat_stream(**kwargs)` 每次调用都是新 dict，链内对 `kwargs["model"]`、`on_content_delta` 的改写不会跨外层重试残留；`model` 还有 `finally` 复原。
- `fallback_provider.py:140-180`：熔断半开探测返回 True 时不清零 `_primary_failures`，失败时刷新 `_primary_tripped_at`，成功时清零，是标准半开语义，**没有计数器卡死**。
- 唯一需要记账的是：这些计数器挂在 `FallbackProvider` 实例上，而实例经 `_resolved_presets` 缓存长期共享（见第 5 条）。

## 4. Anthropic OAuth 刷新：并发与凭据写坏

这一块是本次 diff 里真正的新代码（`oauth_store.py` 全新 225 行 + `anthropic_provider.py` 的刷新钩子），也是问题最集中的地方。

### 4.1 【阻断】刷新响应缺 `refresh_token` 时会把凭据不可逆写坏

- `nanobot/providers/oauth_store.py:222`：`refresh_token=data.get("refresh_token", "")`
- `nanobot/providers/oauth_store.py:224`：`account_id` 同样直接取响应值，缺失即 `None`
- `nanobot/providers/oauth_store.py:195`：`self.save(refreshed)` 无条件覆盖本地凭据

一旦上游某次刷新响应不带 `refresh_token`（OAuth 规范允许不轮换），本地 refresh token 被写成空串。之后 `oauth_store.py:180-181` 的 `if not creds.refresh_token: return creds` 会让刷新永久失效，access token 过期后进入 401 死循环，且原 refresh token 已被覆盖、无法恢复，只能让用户重新走一遍授权。

最小修复：`refreshed.refresh_token = data.get("refresh_token") or latest.refresh_token`，`account_id` 同理 fallback 到旧值；`save()` 前加断言"新凭据必须至少和旧的一样完整"。

### 4.2 【重要】环境变量迁移后被固化，token 过期无自愈路径

- `oauth_store.py:63-65`：`_from_env` 构造出 `expires_at=0`、`refresh_token=""` 的凭据
- `oauth_store.py:157`：`_migrate` 立刻 `save()` 落盘
- `oauth_store.py:140-142`：此后 `load()` 永远命中 storage，环境变量再更新也读不到
- `oauth_store.py:45-47`：`expires_at <= 0` 使 `fresh_for` 恒为 True

结果：`CLAUDE_CODE_OAUTH_TOKEN` 注入的 token 过期后，本地文件里那份"永远新鲜、无 refresh token"的凭据会一直被用，401 之后 `get_token(force_refresh=True)` 因无 refresh token 原样返回旧 token，provider 拿同一个 token 重试一次再失败。用户改环境变量也不生效，必须手工删文件。

最小修复：env 来源的凭据不落盘（只做进程内缓存）；或 `load()` 时若 storage 记录缺 refresh_token 而 env 存在，则以 env 为准。

### 4.3 【重要】刷新是同步阻塞调用，跑在 async 请求路径上

- `anthropic_provider.py:704-719`：`_refresh_credentials` 是 `def`（非 async），从 `async def chat` 的 401 分支同步调用
- `oauth_store.py:206`：内部用同步 `httpx.Client` 发刷新请求
- `oauth_store.py:187-196`：外面套同步 `FileLock`（超时 30s）
- `nanobot/providers/factory.py`：provider 构造期也直接 `OAuthCredentialStore().get_token()`，同样在事件循环里

刷新期间整个事件循环被冻结，最坏 30s（HTTP）+ 30s（抢锁），所有会话、心跳、cron 一起停摆。

并发正确性本身**已核对，无问题**：跨进程有 FileLock + 双重检查（`oauth_store.py:187-196`）；同进程内因为 `_refresh_credentials` 全程同步、无 `await`，同一事件循环内不可能交错，不存在自死锁；跨线程场景下失败方最多等到 lock timeout，被 `anthropic_provider.py:704` 的 `except` 吞掉后降级为"不刷新"，不会写坏文件。**问题只在阻塞，不在竞态。**

最小修复：`await asyncio.to_thread(self._store.get_token, force_refresh=True)`，factory 侧的构造期取 token 同理挪出热路径或改懒加载。

### 4.4 【重要】`chat_stream` 没有接刷新逻辑

`anthropic_provider.py` 只在 `chat()` 的异常分支里做 `_is_auth_error → _refresh_credentials → 重试`；`chat_stream()`（同文件 773 行起）的异常分支没有这条路径。而 runner 主路径走的正是 `chat_stream_with_retry`（`runner.py:944-952`）。

后果：流式主路径上 token 过期只会返回 401，`FallbackProvider` 把 401 判为可切换（`fallback_provider.py` 的状态码判定含 401/403），于是**静默降级到备用模型**，用户以为 Claude Code 订阅在用，其实早就切走了。

测试侧佐证：`tests/providers/test_anthropic_token_refresh.py` 的 11 个用例全部只覆盖 `chat`，无一个 `chat_stream`。

最小修复：把 401 刷新重试抽成共用 helper，`chat` 与 `chat_stream` 都走一遍；补一条 stream 场景的回归测试。

### 4.5 【建议】刷新后重建 client 用了未归一化的 base_url

- `anthropic_provider.py:84`：`__init__` 用 `self._normalize_base_url(api_base)` 建 client
- `anthropic_provider.py:721-729`：`_rebuild_client` 直接用 `self.api_base`（`base.py` 里存的是原始值）

配置了带 `/v1` 后缀的 `api_base` 时，刷新前后 client 的 base_url 不一致（刷新后可能变成 `/v1/v1/messages`）。这个 bug 只在"OAuth + 自定义 api_base"组合下暴露，隐蔽度高。最小修复：`_rebuild_client` 复用 `self._normalize_base_url(self.api_base)`。

### 4.6 【建议】两处小瑕疵

- `anthropic_provider.py:712` 用 `logger.warning("...: %s", exc)`：loguru 是 `{}` 占位，`%s` 不会被替换，日志里会留下字面量 `%s`，排障时看不到异常内容。
- `oauth_store.py:218` 存 `expires_at` 时已减了 5 分钟 margin，`oauth_store.py:45-47` 判新鲜度时又减一次 → 实际提前约 10 分钟刷新。不致命，但字段名叫 `expires_at` 却不是真过期时间，后续维护容易误读。
- 底层 `oauth_cli_kit` 的 `_save_token_file` 是 `write_text` 后 `chmod`（非原子写、有短暂 0644 窗口）。三方库行为，仅记录。

## 5. subagent 独立 model/provider 是否污染主线程

**结论：主线程默认 runtime 不会被污染，已核对；唯一的共享是 provider 实例（含熔断状态），属于设计选择而非 bug，但要知情。**

证据：

- `nanobot/agent/tools/spawn.py`（diff 新增 `_resolve_runtime`）：只调 `self._resolver.resolve_preset(model)`，未指定 preset 时原样沿用父 runtime；resolver 缺席时忽略 `model` 而不报错。
- `nanobot/agent/model_runtime.py:116-130`：`resolve_preset` **不写 `self._runtime`**，只写 `_resolved_presets` 缓存；改默认的是 `select_preset`（第 132 行起），subagent 路径完全不碰它。**主线程默认模型不会被 subagent 改掉。**
- `nanobot/utils/llm_runtime.py:15, 60-80`：`LLMRuntime` 是 frozen dataclass，`with_generation_overrides` 走 `replace()` 返回新对象；`subagent.py:231-257` 的 temperature override 因此不会回写父 runtime。
- 并发安全：`resolve_preset` 全程同步无 `await`，多个 subagent 并发也不会交错破坏缓存字典。

需要知情的两点（**建议**级）：

1. `_resolved_presets` 按 preset 名缓存，返回的是**同一个 provider 实例**。若 subagent 与主线程用同一 preset，subagent 打崩 primary 触发的熔断计数（`fallback_provider.py:140-180`）会直接作用到主线程的下一次请求。这是有意的全局熔断还是意外耦合，值得在 DECISIONS.md 里显式写一句。
2. `model_runtime.py:126` 用 `provider=self._runtime.provider` 作为快照基底，而 `self._runtime` 会被主线程 `select_preset` 改变、缓存又只按名字键。于是"某个 preset 第一次被谁解析"决定了它继承哪个基底 provider，**解析顺序影响结果**。当前没暴露问题（`refresh()` 会 `clear()` 缓存，见 `model_runtime.py:187`），但是个隐性顺序依赖。最小修复：缓存 key 带上基底 provider 的 signature。

---

## 汇总表

| # | 项 | 结论 | 级别 | 证据 |
|---|---|---|---|---|
| 1 | stall × fallback 相乘 | stall 状态机未实现；upstream 重试 × 链 = 单次最坏 13 次请求 | 建议（回放时升为阻断前置） | runner.py:944-952 / base.py:875-985 / fallback_provider.py:181-300 |
| 1b | 外层超时掐掉 fallback | 非流式路径外层预算等于单请求超时 | 重要 | runner.py:944-952 |
| 2 | 重试耗尽后 break | 已核对，无问题，不会静默停摆 | — | runner.py:677-697, 54, 1508-1511 |
| 3 | refusal 触发 fallback | 未实现，仍是上游语义 | 重要（功能缺口） | fallback_provider.py:50-60 |
| 3b | client reset / quota 冷却 | 未实现；现有熔断无状态泄漏 | 重要（缺口）/ 无问题（泄漏） | grep 零命中 / fallback_provider.py:140-180 |
| 4.1 | refresh_token 被写空 | 凭据不可逆损坏 | **阻断** | oauth_store.py:195, 222, 224 |
| 4.2 | env 凭据固化 | token 过期后无自愈 | 重要 | oauth_store.py:45-47, 63-65, 140-142, 157 |
| 4.3 | 同步刷新阻塞事件循环 | 最坏冻结 60s；竞态本身无问题 | 重要 | anthropic_provider.py:704-719 / oauth_store.py:187-196, 206 |
| 4.4 | chat_stream 无刷新 | 主路径 401 静默降级 | 重要 | anthropic_provider.py:773+ vs 764；测试全无 stream 用例 |
| 4.5 | 重建 client 未归一化 URL | OAuth + 自定义 api_base 组合下路径出错 | 建议 | anthropic_provider.py:84 vs 721-729 |
| 4.6 | 日志 `%s` / margin 双减 | 排障噪音、语义混淆 | 建议 | anthropic_provider.py:712 / oauth_store.py:45-47, 218 |
| 5 | subagent 污染主线程 | 已核对，默认 runtime 不被污染 | — | model_runtime.py:116-130 / llm_runtime.py:15,60-80 / spawn.py |
| 5b | provider 实例（熔断态）共享 | 设计耦合，需知情 | 建议 | model_runtime.py:120-130 / fallback_provider.py:140-180 |

**建议的处理顺序：4.1（凭据写坏，不修会让用户重授权）→ 4.4（主路径静默降级，最伤感知）→ 4.3 → 4.2 → 3（回放 Task 8.1）→ 1b → 其余。**
