# 包 B 代码审查：agent 核心与工具（Linus 风格）

审查对象：worktree `/root/git_code/nanobot/.worktrees/sync-2026-07`，`git diff upstream/main...HEAD -- nanobot/agent/`。
标准：`skills/coding/SKILL.md`（好品味 / 极简 / 七类坏味道）。全程只读，未改动任何源码或测试。
已跑：`uv run --frozen pytest -q tests/agent/test_runner_stall_recovery.py tests/agent/test_runner_timing.py tests/agent/tools/test_spawn_model_selection.py tests/agent/test_loop_save_turn.py tests/agent/test_subagent.py` → `101 passed in 3.22s`。

## 结论先行

这批改动的功能意图都成立，但状态表达全线偷懒：**stall 恢复没有一个"状态"，只有两个可证明冗余的计数器加一个跨分支布尔**；**耗时统计为了不改一个返回元组，架了两条 ContextVar 隐式通道**；**最危险的一处是墙钟预算与 stall 重试相乘，没有整轮封顶**——单轮最坏可以占着 session 锁跑到 80 分钟，恰恰是 `runner.py:874` 注释里声称要防的那件事。

identity_context.py 这层不是多余抽象，它就是既有 `RuntimeContextProvider` 协议的一个 62 行实现，判定通过。
spawn/subagent 的模型传递链路也不是数据泥团：`model` 字符串在工具边界一次性解析成 `LLMRuntime` 对象后整体下传，这是对的。

Critical 1 条，Important 7 条，Minor 8 条。

---

## Critical

### C1. 墙钟预算 × stall 重试 = 无上限的单轮阻塞

`nanobot/agent/runner.py:874-905`（超时计算）、`runner.py:986-996`（outer_timeout_s）、`runner.py:692-709`（stall 重试）

单次请求的外层墙钟：默认 `NANOBOT_LLM_TIMEOUT_S=300`，流式路径取 `max(300, 2*timeout_s) = 600`，再乘 `model_attempt_budget`（clamp 到 2）→ **一次 `_request_model` 最长 1200 秒**。而 stall 分支把超时响应判成"可重试"，`_MAX_TOTAL_STALLS=4` 意味着最多 4 次这样的请求串行发生：**最坏 ~80 分钟**，期间 session 锁一直被占。

`runner.py:874` 的注释原文是"给整条重试链一个有限的墙钟上界，避免 per-session 锁饥饿"。stall 重试层在它上面又乘了 4，把这条保证抹掉了，而且两层各自都不知道对方存在——这是典型的"局部正确、组合失效"。

顺带：每次 stall 重试还吃掉一个 `spec.max_iterations` 名额，一次超时风暴能把工具调用预算烧掉 4 轮。

最小修复方向：把预算变成 **run 级 deadline** 而不是 per-request 超时，让两层共用同一个数。

```python
# run() 里
deadline = perf_counter() + spec.wall_budget_s      # 单一真相
# _request_model_timed 里
outer = min(outer_timeout_s, max(1.0, deadline - perf_counter()))
# stall 分支里
if perf_counter() >= deadline: -> 直接进 give-up 路径，不再看 total_stalls
```

---

## Important

### I1. 两个 stall 计数器，其中一个可证明是冗余的

`runner.py:420-421`（初始化）、`462-465`（成功响应时双双归零）、`693`、`699-702`

给定常量 `_MAX_STALL_RETRIES=2`、`_MAX_TOTAL_STALLS=4`（`runner.py:62-63`），实际序列是：stall#1 cons=1；#2 cons=2；#3 cons=3>2 → 归零并插 notice；#4 total>=4 → 放弃。**`consecutive_stalls` 的归零点（701）永远只会被走到一次，之后立刻就 give-up 了**。也就是说整个 `consecutive_stalls` 等价于 `total_stalls == 3` 这一个判断。

更糟的是命名骗人：它叫 "consecutive"，但和 `total_stalls` 在同一处（462-465）清零，两者语义没有任何差别。归零点散在三处（421 / 465 / 701），谁将来把常量调成 `_MAX_TOTAL_STALLS=9`，行为会静默变成"每 3 次插一次 notice"——没人会预期到。

最小修复：一个计数器，判断写成显式表达式；或把三阶段做成一个函数返回枚举。

```python
def _stall_verdict(self, stalls, iteration, spec) -> Literal["retry", "notice_retry", "give_up"]:
    if stalls >= _MAX_TOTAL_STALLS or iteration + 1 >= spec.max_iterations:
        return "give_up"
    return "notice_retry" if stalls == _MAX_STALL_RETRIES + 1 else "retry"
```

### I2. `stall_gave_up` 是跨分支布尔，把状态机撕成两半

`runner.py:691`（`stall_gave_up = False`）、`696`、`711`（20 行外的另一个 `if` 里消费）

第三阶段（放弃）的判定在 692-697，但生效点在 711 的错误分支里。中间隔着 `finish_reason == "error"` 的判断，读代码的人必须记住这个 flag 才能拼出控制流。这是"用变量传递控制流"，不是状态。

最小修复：判定放弃时直接改写 response 让它走统一错误出口，flag 消失。

```python
if verdict == "give_up":
    response.content = _STALL_GIVE_UP_MESSAGE   # 后面的错误分支原样处理
```

### I3. Phase 2 的唯一动作可能静默失效，日志却说"已重试"

`runner.py:1553-1563`（`_append_stall_notice`）、`runner.py:702-706`

`_append_stall_notice` 在"最后一条是 assistant 且无 tool_calls"时直接 `return`，什么都不做。而 stall 之前刚好可能经历过 length-recovery / empty-retry 路径留下 assistant 消息——**恰恰是模型已经在犯病的场景，提示注入被静默丢掉**，紧接着 702 的 `logger.warning` 照样宣称插了 notice。排障时会被这条日志骗。

最小修复：`_append_stall_notice` 返回 bool，日志按真实结果打；或无法追加 assistant 后缀时改追加一条 user 角色提示。

### I4. `_turn_run_stats` ContextVar：为了不改一个返回元组，架了条隐式全局通道

`nanobot/agent/loop.py:169`（定义）、`1064-1069`（写）、`1682`/`1708-1709`（set/reset）

`_run_agent_loop` 只有一个生产调用方（`loop.py:1684`，在 `_run_turn` 里）。ContextVar 搬运的四个值中：

- `model` —— `runtime` 就是调用方在 1682 传进去的 `ctx.runtime` 同一个对象，调用方本来就有，纯粹重复；
- `usage` / `elapsed_ms` / `llm_elapsed_ms` —— 全在 `_run_agent_loop` 内部的 `result` 上，只是被 5 元组返回值截掉了。

也就是说：**为了回避"把 5 元组改成 dataclass"这一步（因为测试大量依赖它），引入了跨 await 的隐式全局状态**。测试倒逼架构，晦涩性坐实。而且它是静默降级的——哪天有人把 `_run_agent_loop` 放进一个在 `.set()` 之前创建的 task 里，`stats` 永远是空，`run_stats` 变成 None，没有任何报错，只是审计数据悄悄没了。

最小修复：返回一个 `TurnOutcome` dataclass（把现有 5 元组一并收进去），删掉 ContextVar 和 `AgentContext.run_stats` 的中转字段，测试跟着改。这是唯一治本的方向；ContextVar 是给"跨越无法修改的调用栈"准备的，这里调用栈就在隔壁。

### I5. filesystem 去重状态：三处 `entry.can_dedup = False` 全是死代码

`nanobot/agent/tools/filesystem.py:334`（取 entry）、`348`、`359`、`363-366`；配合 `nanobot/agent/tools/file_state.py` 的 `record_read`

`record_read` 是**新建 `ReadState` 对象替换 dict 槽位**，不是就地修改。所以 363 调完 `record_read` 之后，365-366 对 334 拿到的旧 `entry` 写 `can_dedup = False`——写在一个已经不在字典里的对象上，**完全没有效果**。348、359 两处同样在 `record_read` 之前/周围改旧对象，同样被覆盖。

后果是注释声称的语义反了：一次 `force=True` 的读之后，下一次 `force=False` 的读**仍然可以命中去重**，而代码本意是禁止。

叠加第二个问题：`force` 默认值已翻成 `True`（`filesystem.py:286`），去重实际上默认关闭，但每次读依然走 `record_read` → 每次读都对整文件算一遍 sha256。一个默认不用的功能，收着 100% 的成本。

另外 `force` 这个名字本身就是坏品味：默认为 `True` 的 "force" 不是开关是噪音，语义应当是 `dedup: bool = False`。

最小修复：状态变更交给 `FileStates` 自己，调用方不碰内部对象。

```python
# file_state.py
def record_read(self, path, mtime, digest, *, dedupable: bool = True) -> None: ...
# filesystem.py
self._file_states.record_read(fp, mtime, digest, dedupable=not force)
```

### I6. cron 的 model 只在半夜执行时才校验

`nanobot/agent/tools/cron.py:158-202`（`_add_job`）、`nanobot/cron/bound_runner.py:120`

`spawn` 工具在参数校验阶段就 resolve preset，认不出来立刻回"unknown preset, available: ..."（`tools/spawn.py`）。cron 走的是同一个 preset 注册表，却把 `model` 当裸字符串存进 payload，直到 `bound_runner` 触发时才 resolve —— 拼错一个 preset 名，`cron add` 返回成功，任务在凌晨三点抛异常。同一份数据、两套校验时机，这是僵化，也是最典型的"半年后才发现"型 bug。

`CronTool.create(ctx)` 手上就有 `ctx.runtime_resolver`，修复只是在 `_add_job` 里复用 spawn 那段 resolve + 错误文案。

### I7. `BOOTSTRAP_FILES` 与 `roots` 是两张必须手工同步的表

`nanobot/agent/context.py:58`（`BOOTSTRAP_FILES` 类属性，可被子类覆写）、`context.py:193-200`（`roots[filename]`）

文件名清单和"每个文件从哪个根目录读"分成两个结构，靠下标 `roots[filename]` 关联。任何人往 `BOOTSTRAP_FILES` 加一项而忘了同步 `roots`，就是运行时 `KeyError`——而且是类属性，子类覆写这条路完全没有防护。数据泥团 + 脆弱性。

最小修复：合成一张表。

```python
BOOTSTRAP_FILES = (("SOUL.md", "agent"), ("AGENTS.md", "agent"), ("USER.md", "agent"), ("TOOLS.md", "workspace"))
```

（若要保留向后兼容的取值路径，`roots.get(filename, self.workspace)` 也比 KeyError 强，但那是打补丁不是治本。）

---

## Minor

### M1. `on_finally` 的重复调用分支

`runner.py:393-399`：`if context.exception is None: await hook.on_finally(context) else: try: await hook.on_finally(context) except ...`。同一个调用写两遍，区别只是要不要吞异常。合成一条：正常路径让异常抛出去、异常路径吞掉——直接写成一次调用加条件性的 `except`，或干脆统一吞掉并记日志。消掉这个特殊分支只需 4 行变 3 行。

### M2. `finally` 覆盖了 `else` 刚设好的 `context.messages`

`runner.py:375` 设 `deepcopy(result.messages)`，`runner.py:392` 在 finally 里又设 `deepcopy(messages)`。目前 `AgentRunResult(messages=messages)`（`runner.py:816-818`）是同一个 list 对象，所以碰巧等价；哪天 `_run_core` 改成返回过滤后的副本，这里会静默丢弃。而且 `after_run` 钩子看到的是 375 的值、`on_finally` 看到的是 392 的值，两个钩子看到不同世界。

### M3. 耗时统计的 ContextVar + depth 计数器，可以整体删掉

`runner.py:134`（`_llm_timing`）、`354-355`、`388`、`391`、`848-861`

`_request_model` 只有两个调用点：`runner.py:459`（唯一外层）和 `runner.py:1040`（malformed 重试的自递归）。`depth` 这个字段存在的全部理由就是防 1040 那次递归重复计时——而 1040 直接调 `_request_model_timed` 就没这回事了。去掉 depth 之后，`timing` 只剩一个累加器，而 `run()` 里第 354 行本来就持有这个 dict，把它作为显式参数传给 `_run_core` 即可，ContextVar 也不需要。

顺带：`_request_model(self, *args, **kwargs)` 的透传把这个热点函数的类型签名整个丢了（`runner.py:848-861`），IDE 和类型检查在这里失明。

另外 `timing: dict[str, float] = {"llm_ms": 0.0, "depth": 0.0}` 把"输出累加器"和"重入守卫"塞进同一个 dict，两个毫无关系的东西共用一个数据结构——小号数据泥团。

### M4. `on_stream_end(resuming=...)` 五个调用点三套策略，没人说得清规则

`runner.py:485` / `599` / `609` / `635` / `683`：注入续跑传 `True`，length-recovery 传 `True`，empty-retry 传 `False`，stall 走的是 683 的 `resuming=should_continue`（stall 时为 `False`）然后 `continue` 开新一轮。也就是**同样是"这一轮还没结束、马上要再发一次请求"，四条路径给了两种答案**，且没有任何注释解释差异。对流式渠道而言这决定了卡片是被收尾还是保持打开，用户侧观感直接不同。

至少应把"stall 重试"和"length recovery"归为同类。彻底的做法是在 683 之前先算出 verdict：`resuming = should_continue or will_retry`。

### M5. `latency_ms` 与 `run_stats` 两套写法做同一件事

`loop.py:1894-1901`：相邻两个 `if last_assistant_idx is not None`，前者单独写 `latency_ms`，后者把 `run_stats` 的键无差别 splat 进同一条消息。`latency_ms` 本来就该是 `run_stats` 里的一个键。而且 splat 没有键白名单，将来某个统计项叫 `content` 或 `role` 就会直接覆盖消息本体（目前靠 `session/manager.py:252` 的持久化白名单兜住不外泄给 provider，但会话内存里的 dict 已经被改了）。

### M6. MEMORY.md 的年龄标注可能被 Dream 写回，形成滚雪球

`nanobot/agent/memory.py:635`（拼 `  ← {n}d`）、`memory.py:660-663`

标注混进了送给模型的 MEMORY.md **正文**，而 MEMORY.md 正是 Dream 会整篇改写回盘的文件。模板 `templates/agent/dream.md:90` 解释了标注含义，但没有一句"改写时必须去掉标注"，代码里也**没有任何剥离 `← Nd` 的逻辑**（全仓仅 memory.py:635 一处生成，无对称的清洗）。一旦模型原样带回，下一轮就是 `  ← 30d  ← 45d`。

另外 `len(lines) != len(ages)` 这道守卫只挡得住行数变化：工作区就地改了一行内容而不改行数时，blame 年龄会张冠李戴，守卫完全看不见。注释里"unstaged edit would shift every age"的判断偏乐观。

### M7. spawn 里的防御性冗余

`nanobot/agent/tools/spawn.py`：`_UnknownPresetError` 一处 raise、八行外一处 catch，用异常做本地控制流；`getattr(ctx, "runtime_resolver", None)` 对一个**已经在 `agent/context.py:82` 声明过、且默认值就是 None** 的 dataclass 字段再做一层 getattr 防御。`SpawnTool` 未声明 `_scopes`，只在 core 作用域加载，subagent 那条没设 resolver 的 `ToolContext` 根本碰不到它——防的是不存在的情况。直接 `ctx.runtime_resolver` 即可。

### M8. 函数/文件超标位置（按 SKILL.md 的 20 行 / 3 层 / 800 行）

| 位置 | 规模 | 拆分方向 |
|---|---|---|
| `runner.py:404` `_run_core` | 423 行，最深 4 层（552 行处） | 每个"异常响应类别"（empty / length / stall / error）抽成 `_handle_*(response, state) -> Verdict`，主循环只剩 dispatch |
| `runner.py:863` `_request_model_timed` | 194 行，4 层 | 拆 `_build_request_kwargs`（已有）/ `_compute_timeouts`（872-905 + 986-996 合并）/ `_invoke_with_budget` |
| `loop.py:838` `_run_agent_loop` | 254 行，3 层 | 与 I4 一起做：先抽出 result 组装，返回 dataclass |
| `loop.py:252` `__init__` | 200 行 | 按子系统分组成若干 `_setup_*` |
| `tools/filesystem.py:844` `execute` | 143 行，**6 层** | 最深那处是编辑匹配逻辑，先把 6 层降到 3 层 |
| `tools/filesystem.py:280` `execute` | 138 行，4 层 | 去重那段（334-366）整体下沉到 `FileStates`，见 I5 |
| `tools/cron.py:158` `_add_job` | 65 行，5 层 | 参数校验前置成独立函数 |
| 文件超 800 行 | `loop.py` 2089 / `memory.py` 1253 / `filesystem.py` 1115 / `runner.py` 1594 | 本次同步不是拆分时机，但 `loop.py` 已经是包 B 里所有耦合的汇点，下一次动它之前应先分文件 |

### M9. 小刺（不构成问题，记录备查）

- `runner.py:999-1011`：`except asyncio.TimeoutError` 里再判 `if outer_timeout_s is None` —— `None` 时压根没套 `wait_for`，这条分支只在 provider 自己抛 TimeoutError 时成立，却给出"stream stalled"文案，非流式路径也会拿到这句话。合成一条消息即可。
- `identity_context.py:49-50`：先写 `fields = [...]` 字面量再紧跟一行 `.append()`，没有条件也没有理由，合进字面量。
- `identity_context.py`：`sender_id` 直接进系统提示词，是有意为之还是顺手带的，值得确认一次（本次不作为问题记）。

---

## 判定：identity_context.py 是否是多余抽象

**不是。** 它是 62 行、一个函数、返回既有 `RuntimeContextProvider` 的实现，在 `loop.py:514` 通过既有注册机制挂上去，没有新增协议、没有新增生命周期、没有第二条并行路径。它就是"并入已有 context provider 机制"的正确形态。这一条没有问题。

---

## 总评：半年后最可能咬人的一处

**C1 —— 墙钟预算与 stall 重试相乘。**

其他条目要么是读代码时膈应人（I1/I2 的计数器和 flag），要么是改起来两行的错位（I5/I6），都会在下一次有人动这块代码时暴露。只有 C1 是**平时完全看不见**的：它需要一个真正抽风的 provider 才会现形，而那一天它给的症状是"bot 卡死不回话"，日志里只有四条 stall warning，没有任何一行写着"我打算在这里等 80 分钟"。到时候排障的人会去查网络、查 provider、查锁，最后才想起来 `runner.py:874` 那条注释里承诺过的"有限墙钟上界"早就在 692 行被人乘了四倍。

**给运维/取舍的一句话**：这次同步如果只允许改一处，改成 run 级 deadline，别改计数器。
