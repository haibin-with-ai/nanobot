# 包 A：providers 层代码审查（sync-upstream-2026-07）

审查基准：`skills/coding/SKILL.md`（好品味／极简／七类坏味道）
范围：`git diff upstream/main...HEAD -- nanobot/providers/`（anthropic_provider.py、oauth_store.py、fallback_provider.py、factory.py、registry.py）+ tests/providers/
验证：`uv run --frozen pytest -q tests/providers/` → **931 passed in 11.82s**（在 worktree `/root/git_code/nanobot/.worktrees/sync-2026-07` 实跑）

---

## 结论先行

功能是对的，测试是真的（opus-5 的 thinking／tool_choice 约束、client reset、fallback 熔断都有针对性用例）。但这批代码的**结构没有跟上功能的复杂度**：新写的 `_stream_once` 75 行嵌套 8 层，`_try_with_fallback` 158 行嵌套 6 层，两处都把"正常路径"和"重试／恢复路径"各抄了一遍。模型能力用四张平行元组表达，鉴权双路靠一个魔法字符串 `product_mode` 在三处分叉。

一个真 bug：**无回调的流式路径把整段生成塞进一个 90 秒超时**，而触发这条路径的恰恰是最长的生成。

四个纯新增文件里 `oauth_store.py` 和 `registry.py` 是这批代码中最干净的部分，registry 那 16 行声明式配置是正面样板。

---

## Critical

### C1. 无回调流式：整段生成共用一个 idle timeout，长生成必被误杀（真 bug）

`nanobot/providers/anthropic_provider.py:948-960`

`_stream_once` 里当 `on_content_delta` 与 `on_thinking_delta` 都为 None 时，跳过逐 chunk 循环，直接：

```python
final = await asyncio.wait_for(stream.get_final_message(), timeout=idle_timeout)
```

`get_final_message()` 会消费整条流。于是 `idle_timeout`（默认 `DEFAULT_STREAM_IDLE_TIMEOUT_S = 90.0`，见 `base.py:21`）从"两个 chunk 之间的空闲上限"悄悄变成了"整次生成的墙钟上限"。有回调时这行是免费的（流已被循环耗尽，立即返回），无回调时它就是唯一的超时闸门。

更糟的是谁会走无回调路径：`anthropic_provider.py:800-806`，`chat()` 撞上 `_is_streaming_required_error`（max_tokens/thinking budget 太大、API 强制要求流式）后回退调用 `chat_stream`，不传任何 callback。也就是说，**最可能超过 90 秒的那类请求，正好落在这条把 90 秒当总时限的分支上**，然后被当成 "stream stalled" 抛 TimeoutError + `_reset_client()`。

最小修复：取消这个特殊分支，永远走 chunk 循环，回调可空。

```python
async for chunk in stream:                      # 循环本身就是 idle 计时器
    await self._pump_chunk(chunk, callbacks)    # 回调为 None 时直接丢弃
final = await stream.get_final_message()        # 此时是纯本地聚合，不需要 wait_for
```

顺带消灭一个 if。

### C2. `_stream_once` 75 行 / 嵌套 8 层，且 stall 处理逻辑抄了两份

`nanobot/providers/anthropic_provider.py:886-960`（函数体）、`chat_stream` 内 `865-880` 与 `894-909`

用 AST 量过：`_stream_once` len=75、最大嵌套 depth=8。`async with` → `if` → `while` → `if chunk.type ==` → `elif` → `if delta.type ==` 一路叠下去。这是本次 diff 新写的代码（hunk 覆盖 L821-959），不是历史包袱，直接违反 skill 里"函数 20 行、嵌套不过三层"。

嵌套的核心来源是 chunk 分派：`content_block_delta` / `text_delta` / `thinking_delta` / `content_block_start` 这几种类型用 if-elif 串成一根链。这是典型的"该用数据结构却用了控制流"——分派表能把三层削成一层：

```python
_CHUNK_HANDLERS = {
    ("content_block_delta", "text_delta"): _emit_text,
    ("content_block_delta", "thinking_delta"): _emit_thinking,
    ...
}
handler = _CHUNK_HANDLERS.get((chunk.type, getattr(chunk.delta, "type", None)))
if handler:
    await handler(self, chunk, cb)
```

同一函数附近还有第二笔：`chat_stream` 里"捕获 TimeoutError → 判断 emitted → `_reset_client()` → 拼错误响应"这 12 行，在首次调用和鉴权重试后各写了一遍（865-880 / 894-909）。冗余坏味道，改一处忘另一处只是时间问题。

最小修复：把两次尝试变成循环。

```python
for attempt in range(2):                       # 0=首发, 1=刷新凭据后重试
    try:
        return await self._stream_once(kwargs, cb, emitted)
    except TimeoutError:
        return self._stalled_response(emitted)  # 抽出去，一处
    except Exception as e:
        if attempt or emitted[0] or not self._is_auth_error(e) or not await self._refresh_credentials():
            raise
```

另：`emitted` 用 `list[bool]` 当可变盒子跨函数传，是"函数没法返回两个值"时的补丁。把这段状态收进一个小的 `_StreamPump` 对象（持有 emitted + callbacks），盒子和参数一起消失。

### C3. `_try_with_fallback` 158 行 / 嵌套 6 层，主路径与备用路径逻辑重复

`nanobot/providers/fallback_provider.py:247-404`

AST 实测 len=158、depth=6。它同时在做六件事：熔断判定、冷却过滤、主调用、stall 恢复、逐个 fallback 尝试、错误响应合成。

其中"发起调用 → 捕获 stall/timeout → 通知 `on_stream_recover` → 决定是否继续"这套逻辑，主 provider（273-291）和 fallback provider（321-337 附近）各实现一遍，细节还不完全对称。

最小修复：抽出"一次尝试"作为唯一执行单元，主与备共用同一条路径：

```python
async def _attempt(self, provider, model_kwargs, ctx) -> AttemptResult: ...

for candidate in self._candidates(kwargs):     # 主 + 未冷却的 fallback，一个序列
    result = await self._attempt(...)
    if result.ok:
        return result.response
```

主 provider 与 fallback preset 之所以现在没法统一，是因为它们的"身份"表达方式不同（见 I6）。把候选统一成 `(key, provider, overrides)` 三元组，函数长度自然掉到能读的量级。

### C4. OAuth 凭据获取在事件循环里做阻塞 IO（文件锁 + 同步 httpx）

`nanobot/providers/factory.py`（`_anthropic_credential` → `OAuthCredentialStore().get_token()`）；调用点 `fallback_provider.py:352` 的 `self._provider_factory(preset)`

`get_token()` 内部可能：抢一把最长 30 秒的 `filelock.FileLock`，然后用**同步 httpx** 走一次 token refresh。这两件事都是纯阻塞的。

`anthropic_provider._refresh_credentials()` 已经很清醒地用 `asyncio.to_thread` 把这件事挪出事件循环——说明作者知道这是阻塞的。但 factory 这条路没有同等待遇，而 `_try_with_fallback` 会**在请求处理过程中**同步调用 `provider_factory(preset)`。只要有一个 fallback preset 指向 `anthropic_claude_code`，一次 fallback 就能把整个事件循环（所有并发会话、所有 Discord 心跳）冻住最长几十秒。

同一个危险操作，一处包了 `to_thread`，一处裸调——这种不对称本身就是"脆弱"的定义。

最小修复：给 `provider_factory` 一个异步入口，或在 factory 内把凭据读取延迟到 provider 首次实际用（懒加载），让阻塞点始终落在已经被 `to_thread` 包住的 `_refresh_credentials` 上。

---

## Important

### I1. 四张平行的模型元组 = 数据泥团

`nanobot/providers/anthropic_provider.py:36-47`

`_MODELS_WITHOUT_SAMPLING_PARAMS`、`_EFFORT_MODELS`、`_DEFAULT_THINKING_ON_MODELS`、`_THINKING_SUMMARIZATION_MODELS` 是四条并列元组，今天内容高度重合（都围着 opus-5 转），`_build_kwargs` 里对同一个 model 连查四次 `_matches_model`。加一个新模型要记得改四个地方，忘一个就是静默的错配置。

这是"能力"这个概念被拆散在四个容器里。最小修复是一张表：

```python
@dataclass(frozen=True)
class _ModelCaps:
    omit_sampling: bool = False
    effort: bool = False
    thinking_default: bool = False
    thinking_summarize: bool = False

_MODEL_CAPS = {"claude-opus-5": _ModelCaps(True, True, True, True), ...}
caps = _caps_for(model)     # 一次查找，之后全是字段访问
```

顺带说一个被这四张表巧合掩盖的雷：`_build_kwargs` 中 `thinking_disabled and thinking_on_by_default` 那条分支**不设置 temperature**。今天不炸，只因为"默认开 thinking 的模型"恰好也在"不接受采样参数"名单里。这两张表一旦分叉，temperature 会无声消失。合表之后这种耦合会显式暴露成一个字段组合。

### I2. 鉴权双路靠魔法字符串 `product_mode` 在三处分叉

`nanobot/providers/anthropic_provider.py:101-114`（`_client_kwargs`）、`534-548`（`_inject_identity` 调用点）、`754-775`（`_refresh_credentials`）

OAuth 与 API-key 的差异被压缩成一个 `product_mode: str = ""`，取值靠 `== "claude_code"` 字符串比较，散落在客户端构造、system prompt 注入、凭据刷新三处。factory 那边则是 `"claude_code" if creds else ""`。

问题不是"字符串不够类型安全"，而是**同一个决策被复述了三次**。读代码的人要把三处拼起来才知道 OAuth 模式到底和 API key 差在哪。第四处差异（比如 OAuth 专属的 beta header 或不同的错误处理）出现时，还会有第四个 `if`。

最小修复：一个凭据策略对象，把差异收在一处。

```python
class _Credential(Protocol):
    def client_kwargs(self) -> dict: ...
    def decorate_system(self, blocks: list) -> list: ...
    async def refresh(self) -> bool: ...

# ApiKeyCredential.decorate_system 是 identity 函数；refresh 返回 False
```

三个 `if` 一起消失，这才是"消除特殊分支"。

### I3. `_should_fallback` 的规则顺序就是全部语义，却没有一行说明

`nanobot/providers/fallback_provider.py:419-465`

45 行、十余条顺序敏感的 if。鉴权 token 检查排在 `_NON_FALLBACK_ERROR_KINDS` 之前（大概是为了不让 `invalid_request` 吞掉 `invalid_api_key`），status 检查排在正文子串匹配之前（大概是为了不让 `"empty"` 命中 400 的 "content blocks must be non-empty"）。这些"大概"全是我逆推出来的，代码里没写。

顺序即语义、语义无文档 = 晦涩 + 脆弱。任何人插一条新规则都可能在不知情的情况下改变既有分类。

最小修复：改成显式的有序规则表，每条带一句 why：

```python
_RULES = (
    ("auth errors never fall back: same key on another model also fails", _is_auth),
    ("400-class errors are the caller's bug, retrying elsewhere only burns quota", _is_client_error),
    ...
)
```

或者退一步，至少在函数顶部用 5 行注释写清"为什么是这个顺序"。

### I4. 原地改 kwargs 再 finally 还原

`nanobot/providers/fallback_provider.py:362-380`

为了给 fallback 换模型，代码先把 `model / max_tokens / temperature / reasoning_effort` 四个键的原值抄进 `original_values`（用哨兵 `_MISSING` 区分"不存在"和"值为 None"），改掉，调用，再在 `finally` 里逐个还原。

19 行代码只为回答一个问题："调用方的 dict 别被我改脏。" 而正确答案是不要去改：

```python
attempt_kwargs = {**kwargs, "model": m, "max_tokens": ..., "temperature": ...}
if preset.reasoning_effort is None:
    attempt_kwargs.pop("reasoning_effort", None)
```

哨兵、备份、finally 一起消失。skill 里"不必要的复杂性"说的就是这个。

### I5. FallbackProvider docstring 与代码直接矛盾

`nanobot/providers/fallback_provider.py:104`（"Failover is request-scoped (the wrapper itself is stateless between turns)"）vs `126-129`（`_primary_failures` / `_primary_tripped_at` / `_quota_cooldowns`）

这批改动给 wrapper 加了跨请求的熔断计数和配额冷却字典，docstring 里"stateless between turns"一句没改。写错的注释比没有注释危险：下一个人会据此认为可以随便共享或重建实例。

最小修复：改一句话，并说明状态的生命周期与线程安全假设（这些 dict 有没有锁保护？现在是靠单事件循环隐式串行——这一点也值得写下来）。

### I6. 冷却 key 不规范：主 provider 和 preset 用两套命名

`nanobot/providers/fallback_provider.py:156-162` 附近的 `_primary_key` / preset key 构造

主 provider 的 key 用 `getattr(self._primary, "name", None)` 兜底到类名（`LLMProvider` 上并没有 `name` 属性，实际落到 `"AnthropicProvider"`），fallback preset 的 key 用 `preset.provider`（`"anthropic"`）。于是同一个 provider+model 组合，走主路径和走 preset 路径拿到的是**两个不同的冷却 key**：主上刚被 429 冷却，配置里指向同一模型的 fallback 照样会被试一遍。

这也是 C3 里主/备无法统一成一个候选序列的根因。最小修复是给候选定义一个规范身份：`(spec_name, model)`，主 provider 构造时就把 `spec_name` 记下来，不要靠 `getattr` 猜。

### I7. 没有 refresh_token 且已过期时静默返回陈旧凭据

`nanobot/providers/oauth_store.py:180-181`

`get_token()` 在无法刷新时直接返回过期凭据。调用方拿到一个注定 401 的 token，用户看到的是一句 "authentication error"，而真实原因是"你的订阅登录早就过期了，且本地没有 refresh_token"。诊断信息在这一行被丢掉了。

最小修复：这种情况返回 None 或抛一个带明确文案的异常，让上层能说出"请重新执行登录"。

### I8. `_make_provider_core` 的 backend if/elif 链又长了一节

`nanobot/providers/factory.py`（`_make_provider_core`，六段 backend 分派）

历史问题，但这次改动往里又塞了一段。`ProviderSpec` 已经是声明式的了，唯独构造方式还留在函数里用 `elif backend == "..."` 手工分派——数据和行为被拆到两个文件。

最小修复：让 registry 直接带构造器（`builder: Callable[[ProviderSpec, ProviderConfig], LLMProvider]`），`_make_provider_core` 缩成一次查表 + 一次调用。加新 provider 从"改两个文件"变成"加一条记录"。

---

## Minor

- **oauth_store.py:222 + 各 `fresh_for` 调用点**：`_parse_refresh_response` 存盘时已经从 `expires_at` 里减掉了 `_REFRESH_MARGIN_MS`，调用方又用 `fresh_for(min_ttl_ms=_REFRESH_MARGIN_MS)` 再减一次，实际提前 10 分钟刷新。不致命，但 `expires_at` 这个字段名此刻在说谎。建议存原值，margin 只在判定处减。
- **oauth_store.py:145-165**：`needs_refresh()` / `get_token()` 里的 `load()` 可能触发 `_migrate()` 写盘。谓词带写副作用，测试和排障时容易被绊。
- **oauth_store.py（`_from_env`）**：环境变量来的 token 是 `expires_at=0` 且无 refresh_token，`fresh_for` 恒为真，迁移进本地 storage 后就再也不回头读 env。用户轮换 `CLAUDE_CODE_OAUTH_TOKEN` 不生效，且这枚 token 永不过期、永不刷新。docstring 提了迁移语义，没提这两个后果。
- **anthropic_provider.py:516-520**：`_convert_tool_choice` 在 thinking 打开时把调用方的 `tool_choice` 一律改写成 `{"type": "auto"}`。这条约束是真的（Anthropic 对 thinking + `any` 返 400），但**理由只写在 `tests/providers/test_anthropic_opus5.py:43` 的中文 docstring 里**，产品代码零注释。opus-5 默认开 thinking 之后，这条改写从偶发变成常态，值得在实现处留一行 why。
- **anthropic_provider.py:121-129**：`_reset_client()` 不 close 旧 client（注释解释了取舍：正在跑的流还挂在上面）。但反复 stall 会累积 httpx 客户端与连接，没有上限也没有指标。至少加一条 warning 计数。
- **anthropic_provider.py:748-752**：`_is_auth_error` 把 403 也算作"凭据过期"。Anthropic 的 403 更多是权限/地区问题，刷新 token 救不了，只是白白多一次网络往返。
- **fallback_provider.py:63-88**：`_FALLBACK_ERROR_TOKENS` 里有 `"empty"`、`"balance"`、`"connection"` 这类极泛子串，直接对错误正文做匹配。今天不误伤，全靠 I3 里那个未写明的规则顺序兜住。合并到规则表时应该收紧成前缀或结构化字段。
- **anthropic_provider.py 全文 962 行**，超过 skill 的 800 行线。按职责切一刀是自然的：请求参数构造（`_build_kwargs` 一族）、流式泵、凭据/鉴权，三块彼此几乎不共享状态。
- **registry.py:356 vs 367**：`anthropic_claude_code` 的 `keywords=("claude-code", "claude_code")` 实际上是死配置——真实模型 id 里不会出现这两个词，且排在前面的 `anthropic` spec 已经用 `"claude"` 关键词吃掉了所有 `claude-*`（`config/schema.py:536` 按 PROVIDERS 顺序匹配）。订阅模式只能靠显式前缀 `anthropic_claude_code/...` 选中，registry 里没有一行说明这件事。

---

## 正面记录（不是凑数，是明确要保留的）

- `registry.py` 新增的 16 行是这批代码里最干净的部分：纯声明、注释写的是 why（"Headers alone are not enough"）、指向了对应实现。所有 provider 差异都该长这样。
- `oauth_store.py` 整体结构合理：跨进程文件锁 + 原子写 + `0600` 权限 + 双检查刷新，`_migrate` 的来源优先级也写清了。上面几条都是打磨，不是返工。
- `_refresh_credentials` 用 `asyncio.to_thread` 隔离阻塞刷新，是对的（问题在于 factory 那条路没照做，见 C4）。
- 测试是真测试：`test_anthropic_opus5` 覆盖了 thinking + tool_choice 的 400 约束，`test_anthropic_client_reset` 覆盖了 stall 后重置，fallback 熔断/冷却也有用例。931 个用例全绿。

---

## 总评

这批 providers 代码离"能让人一眼读懂并放心改"，差的不是正确性，是**把知识写成数据的自觉**。

模型能力是四张要人肉同步的元组，鉴权模式是一个在三处被复述的字符串，错误分类是一串顺序即语义却没人解释顺序的 if，流式和 fallback 都把"再试一次"写成了复制粘贴而不是循环。每一处单看都能读懂，代价是读者必须把散落三四处的片段在脑子里重新拼成一个概念——而这正是改坏它的方式：你改了三处里的两处，测试还是绿的。

C1 那个 90 秒超时最能说明问题：它不是打字错误，是"无回调"这个特殊分支被当成优化引进来时，顺手把 idle timeout 的语义偷换掉了。**特殊分支从来不是省事，是把一笔债转移给下一个读代码的人。**
