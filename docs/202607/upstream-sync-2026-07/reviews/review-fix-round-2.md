# Fix Round 2 复审：sync-upstream-2026-07

审查范围：`c31d776d~1..HEAD`（8 个提交），worktree `/root/git_code/nanobot/.worktrees/sync-2026-07`。只读审查，未改动任何源码。

## 结论

这批修复方向都对，但有三处是**新引入的退化**，不是老问题：stall 状态机的最终放弃路径把"优雅降级"换成了未捕获异常（整轮上下文不落盘）；FallbackProvider 在"备用全冷却"时丢掉主模型的真实错误和 `error_kind`，反过来把刚做的 stall 判定打瞎；墙钟预算 ×attempts 是静态放大，99% 只用主模型的调用也照样把用户等待从 10 分钟拉到 40 分钟。

cron model 字段往返、OAuth refresh_token 保留、SOUL anchor 取 workspace、Discord 命令派生这四块实现干净，除边角外没有实质问题。

测试有两处是"重复实现"而非验证：删掉 runner 的计数器清零两行，`tests/agent` 全套 1499 条依然全绿；fallback 的 refusal 用例全走非流式 `chat()`，把流式路径上"该功能其实不生效"整个盖住了。

全量测试实跑：`5712 passed, 15 skipped in 122.98s`。

---

## Critical

### C1. `ModelStallError` 无生产捕获点，最终放弃路径丢整轮上下文

- **位置**：`nanobot/agent/runner.py:686-693`（raise）→ 唯一落点 `nanobot/agent/loop.py:1274`（通用 `except Exception`）
- **问题**：`grep -rn "ModelStallError" --include=*.py` 全仓只有三处——定义、抛出、以及 `tests/agent/test_runner_stall_recovery.py:110` 的 `pytest.raises`。**生产代码里没有任何人接它。**

  改动前（`git show 2bc328a9`）：`error_kind == "timeout"` 的响应直接落到 `runner.py:707` 的 `finish_reason == "error"` 分支——给用户 `spec.error_message`、往 messages 写 placeholder、正常返回、正常持久化。

  改动后：第 4 次 stall 抛异常穿出 `runner.run` → `loop.py:1274` `logger.exception` + `delivery.fail()`。而 checkpoint 物化（`loop.py:1257-1262` 的 `_restore_runtime_checkpoint` + `sessions.save`）**只写在 `CancelledError` 分支里**，通用异常分支没有。

  净效果与提交信息 "重试耗尽写回上下文而非静默停摆" 相反：`_STALL_NOTICE` 确实进了 messages，但这一轮的 messages 根本不落盘，本轮已完成的工具结果一并丢失，用户拿到的是通用失败文案而不是"模型连续无响应"。
- **复现**：让 provider 连续 4 次返回 `finish_reason="error", error_kind="timeout"`，跑完整 `AgentLoop` 而不是裸 runner。现有测试只裸跑 runner 并断言抛异常，正好绕开了这个后果。
- **最小修复**：二选一。
  1. 在 `loop.py` 的 `_run_turn` 里显式 `except ModelStallError`，复用 CancelledError 分支同款的 checkpoint 物化 + `sessions.save`，再用 `spec.error_message` 回话；
  2. 更简单：runner 内不抛，把兜底转成 `LLMResponse(finish_reason="error", error_kind="stall")` 交给已有的 `runner.py:707` 分支——`_STALL_NOTICE` 已经在 messages 里，落盘和回话都是现成的。

---

## Important

### I1. `_reset_client()` 关的是共享 client，并发下会打断别人的在途流

- **位置**：`nanobot/providers/anthropic_provider.py:121-136`（`await client.close()` 在 133 行），调用点 `anthropic_provider.py:851`
- **问题**：provider 实例由 `AgentLoop` 级 preset 缓存复用（`nanobot/agent/model_runtime.py` 的 `_resolved_presets`，`factory.py:132` 只在缓存未命中时构造），gateway 并发服务多个会话时，多个 turn 共用同一个 `AnthropicProvider` 和同一个 `AsyncAnthropic._client`。任何一个请求 idle 超时，`_reset_client()` 就把共享的 httpx client 关掉，其他正在 stream 的请求当场炸。

  更糟的是二级效应：这些被误伤的请求通常**已经吐过字**，`fallback_provider.py:256` 的 `has_streamed and error_kind != "timeout"` 短路会拒绝换模型，错误直接怼到用户脸上。

  而 `_refresh_credentials` 走的 `_rebuild_client`（`anthropic_provider.py:779`）反过来**不 close**，每次 token 刷新泄漏一个连接池。两处语义正好互为镜像的错。
- **复现**：两个会话并发跑 Anthropic 流式请求，其中一个触发 `NANOBOT_STREAM_IDLE_TIMEOUT_S`。
- **最小修复**：统一为"只换引用、不主动 close"（旧 client 由 GC 与 httpx 自身超时回收），或引入在途请求计数、归零后再关。别让一条请求的自愈动作有权处置共享资源。

### I2. 备用全冷却时，主模型的真实错误和 `error_kind` 被丢掉

- **位置**：`nanobot/providers/fallback_provider.py:293`（`last_response = None`）、`:384-387`（合成兜底响应）
- **问题**：主模型失败后没有 `last_response = response`。若所有 fallback 都被 `cooling` 跳过，循环空转到底，返回的是合成的 "circuit open and no fallbacks available"，**不带 `error_kind`**。

  这条路径可达：`_cooling_down` 只保证"全都在冷却时留最闲的一个"，而最闲的那个可能就是 primary，此时全部 fallback 键都在 `cooling` 集合里。429 风暴之后就是这个形态。（`_resolve_provider` 对所有 preset 都返回 None 的配置错误同理。）

  后果是双杀：一，欠费/限流的准确文案被替换成一句语焉不详的熔断提示；二，`error_kind` 丢失导致 `runner._is_stall` 判不出 timeout，**这一轮新做的 stall 状态机在"全链路超时"这个它最该生效的场景里直接失效**。
- **最小修复**：`_should_fallback` 判定通过后立刻 `last_response = response`。一行。

### I3. 墙钟预算 ×attempts 是静态放大，常见路径也被拖长

- **位置**：`nanobot/agent/runner.py:977-979`
- **问题**：`outer = max(300, timeout_s*2) * attempts`，`attempts = 1 + len(fallback_presets)`（`fallback_provider.py:152`）。默认 `NANOBOT_LLM_TIMEOUT_S=300`（`runner.py:869`）、2~3 个 fallback → 单次 LLM 调用的墙钟从 600s 变成 1800~2400s。

  这条预算里还套着两层重试：`base.py` 的 `_run_with_retry`（standard 模式 4 次，backoff 1/2/4s）× 每个候选模型各自的 idle 超时。外层再叠 stall 状态机允许的一轮 4 次 stall。乘出来单个用户 turn 的最坏等待是小时级。

  关键缺陷是 `attempts` 静态：绝大多数调用只用主模型就成功，但预算照样按满链放宽。主模型挂死时，用户从等 10 分钟拿到超时提示，变成等 40 分钟。为了兜住"慢主模型掐掉备用模型"这个少数场景，代价压在了多数场景上。
- **最小修复**：把预算改成 deadline 语义而不是 per-call timeout——runner 记一个绝对截止时间（单模型预算 + 换模型的小余量），FallbackProvider 换模型时传剩余时间给下一个候选。这样链路总时长不随候选数线性放大，"慢主模型吃掉备用模型配额"也同样被解决。

### I4. 拒答换模型在流式路径上基本不生效，副作用却全收了

- **位置**：`fallback_provider.py:24`（refusal 进 `_SWITCHABLE_ERROR_KINDS`）、`:256`（has_streamed 短路）、`:283`（`_primary_failures += 1`）、`anthropic_provider.py:729`（refusal → `finish_reason="error"`）
- **问题**：三条独立的伤。

  1. **功能基本不生效**：Anthropic 的 refusal 通常是先吐一段文本再以 `stop_reason=refusal` 收尾，`on_content_delta` 已经把 `has_streamed[0]` 置 True（`fallback_provider.py:221`），`:256` 的短路直接 break，不换模型。所以"拒答触发换模型"只在非流式 `chat()` 上成立——而 nanobot 主路径是流式。测试恰好全是 `chat()`，看不见这一点。
  2. **历史被污染**：refusal 变成 `finish_reason="error"` 后走 `runner.py:707` 的错误分支，`_append_model_error_placeholder` 往 messages 写占位符，**真正的拒答原文不进历史**。用户在流里看到了拒答，模型下一轮却不知道自己拒过，上下文与用户所见不一致。
  3. **计错账**：`_primary_failures += 1` 把内容级拒答算进主模型健康度，攒够 3 次开 60s 熔断，把所有用户降级到备用模型。测试 `test_refusal_does_not_cool_the_model_down` 只验证了 1 次拒答后 primary 仍被调用，名字承诺的事情实现并没做到。
- **最小修复**：refusal 不计入 `_primary_failures`（这是内容判断，不是端点健康）；`:256` 的短路对 refusal 放行（拒答重来不算重复输出，但这条要先拍产品口径）；refusal 别复用 `finish_reason="error"`，或者至少让 runner 对 `error_kind == "refusal"` 保留原文入历史而不是写占位符。

### I5. 有 session_key 却判不出 bound 的 cron job，投递被静默丢弃

- **位置**：`nanobot/cron/session_turns.py:71`（`is_bound_cron_job`）、`nanobot/cron/bound_runner.py:85-98`
- **问题**：判据要求 `session_key` + `origin_channel` + `origin_chat_id` 且没有 legacy 字段，任一不满足就走 unbound 分支。而 unbound 只把结果写进 run 记录，**没有任何投递**（对比 bound 分支的显式 `_deliver`）。

  改动前，缺 origin 会在 `session_delivery.py:13` 抛 `ValueError`，run 记为 error，日志和 run 列表都能看见。改动后：run 记 ok，任务确实跑了，用户永远收不到。失败模式从"响亮"变成"静默"。

  可能性评估要诚实：新建 job 都带 origin（`nanobot/agent/tools/cron.py:216` 一路灌下去），`_normalize_agent_turn_job` 也会迁移 legacy deliver 字段。残留只可能来自老 store、手工编辑、或迁移漏网。低频，但代价是用户信任的定时提醒不响。
- **最小修复**：unbound 分支入口加一句 `if job.payload.session_key: logger.warning(...)`；或者更硬——`session_key` 存在但判不出 bound，直接按错误处理，别降级成无投递运行。

---

## Minor

- **M1** `anthropic_provider.py:771`：`logger.warning("...: %s", exc)`。loguru 用 `{}` 格式化。实测 `logger.warning('x: %s', ValueError('boom'))` 输出 `x: %s`，异常信息整个丢掉——恰好在最需要诊断的 token 刷新失败处。改 `{}`。
- **M2** `anthropic_provider.py:~660`：adaptive 分支里 `_convert_tool_choice(tool_choice, thinking_enabled)` 传的 `thinking_enabled` 此刻是 `False`（opus-5 是"默认开 thinking"，不由 effort 触发），但 thinking 实际开着。一旦有人传 `tool_choice="required"`，就会同时发出 `thinking` + `tool_choice: any`，Anthropic 直接 400。全仓 grep 现在没有调用方传 required（只有 `memory.py:1052` 传 None），是埋雷不是着火。改成传 `thinking_enabled or <该模型强制 thinking>`。
- **M3** `anthropic_provider.py:862-873`：401 自愈重试没有"是否已吐字"的护栏。注释断言 401 只在建连阶段发生，但 `_is_auth_error` 是按 status 401/403 判的——代理层在流中途返 403 时会重跑整个 stream，用户看到重复输出。修法：`_stream_once` 带一个 emitted 标志，只有 `emitted == False` 才重试。另外重试分支的超时不再调 `_reset_client()`，半死连接会留着。
- **M4** `nanobot/cli/commands.py:1701`：`prune_cron_run_sessions()` 只在 gateway 启动时扫一次，常驻数月不重启就永远不回收。另外 `delete_session` 内部已吞 `OSError` 并返回 bool，prune 里的 `try/except OSError` 是死代码，且删除失败也会计进 `deleted`，数字虚报。接进已有的周期维护任务，并按返回值计数。
- **M5** `oauth_store.py`：这次保住了 `refresh_token`，同一段里 `account_id` 仍会被 None 覆盖——同类 bug，只是目前只影响展示。顺手对齐。
- **M6** `fallback_provider.py:183-192`：`_note_quota_cooldown` 对任何带 `retry_after` 的错误都上冷却，而 `base.py` 的 `_extract_retry_after` 是**从错误文本里正则抠** "try again in N seconds"。一句 5xx overloaded 文案就能把某个模型冷却 ≥60s。加前置条件：`status == 429 or error_kind in {rate_limit, quota}`。
- **M7** `fallback_provider.py:199`：`_primary_available` 直接用 `time.monotonic()`，绕过注入的 `self._clock`，熔断半开路径因此不可测——现有测试也确实没覆盖。
- **M8** `nanobot/channels/discord/runtime.py:~300-330`：builtin 派生既无名字/描述校验也无 `try/except`，而下面的 skill 命令注册是包在 `try/except` 里的。今天 16 条 builtin 全合法（实测 `^[a-z0-9_-]{1,32}$` + 描述长度全部通过，13 条派生 + 3 条手写 = 16，留给 skill 84 个槽），但以后有人加一条 `/mcp list` 或中文名，`app_commands` 会在注册期抛异常，整棵命令树注册失败。加正则过滤 + 单条 try/except。
- **M9** `runner.py` 常量文案语言不一致：`_STALL_NOTICE` 中文，`_PERSISTED_MODEL_ERROR_PLACEHOLDER` 等英文。

---

## 没有问题的部分（明说，不凑数）

- **cron model 字段往返**（`cron/service.py:312` 附近 + `cron/types.py:64`）：写入键与 `from_store_dict` 读取键一一对应，全仓只有这一处 payload 序列化（`grep -rn '"deliver"'` 两处，一写一读），无第二个漏字段的序列化器。
- **per-run session key 与回收正则**：run_id = `{job.id}:{ts}:{uuid4hex8}`，session key = `cron:{job.id}:{run_id}`，job.id = `str(uuid4())[:8]`（纯 hex），落在 `[0-9A-Za-z_-]+` 字符集内；带反向引用的解析正则与真实 key 形状一致，':' 不在字符集里所以贪婪匹配不会串位。保留策略 `keep_per_job=3 且 30 天`是"与"关系，日更任务稳态约 30 个/job，不会失控。已删除 job 的最后 3 个 session 会永久保留，量有界，可接受。
- **OAuth refresh_token 保留**：`get_token(force_refresh=True)` 在拿锁后重读文件，发现别人已刷新且仍新鲜就直接复用（`oauth_store.py:~190`），跨进程和进程内的惊群都挡住了。这块写得扎实。
- **`_client_kwargs` / `_matches_model`**：`max_retries=0` 交给上层统一重试，边界匹配用 `rsplit("/")` 剥 vendor 前缀后再前缀比对，date-suffix 变体能命中，不会误伤第三方兼容端点（它们不会叫 claude-opus-5-*）。
- **SOUL anchor 取 workspace**：与 `agent/context.py:189` 已有的 `SOUL.md → self.workspace` 映射一致，锚点和正文同源，没有分叉。
- **Discord 带参命令与数量上限**：`accepts_args` 派生成可选参数 + `describe` 提示，未填参时转发裸命令由 router 出 usage；`reserved` 用的是 `router.command_names()`（不带斜杠），与 skill 命令名同形，去重有效；100 上限的槽位计算正确。

---

## 测试质量

**变异测试实证**：把 `runner.py:705-706` 的 `consecutive_stalls = 0` / `total_stalls = 0` 两行删掉（在 `/root/workspace/tmp/mutcheck` 的副本上做，未动 worktree），`tests/agent` 全套 **1499 passed**，全绿。

原因是 `test_a_good_answer_clears_the_counters` 用**两次独立的 `run()`** 来验证"清零"，而这两个计数器是 run 内的局部变量——两次 run 本来就各自从 0 开始，断言与实现无关。真正会用到 reset 的路径是 error/final 之后 injection 续跑（`runner.py:727` / `:745`），零覆盖。

**覆盖盲区**：`tests/providers/test_fallback_provider.py` 的 refusal 用例全部走 `chat()`（`has_streamed=None`），流式路径的 `has_streamed` 短路完全没测——I4 那条"功能在真实路径上不生效"能藏住，就是因为测试只测了不用的那条路。

**补测建议**（按性价比排序）：

1. 流式 refusal：`on_content_delta` 先吐字再返回 refusal，断言"是否换模型"符合产品口径——这条能同时锁住 I4 的三个面。
2. 全 fallback 冷却：断言返回的响应保留主模型的 `error_kind` 与文案（锁 I2）。
3. 跑完整 `AgentLoop` 的 4 次 stall：断言 session 落盘且用户拿到 stall 文案，而不是断言抛异常（锁 C1）。
4. 并发两条流 + 其一 idle 超时，断言另一条不受影响（锁 I1）。

---

## 验证记录

```
$ uv run --frozen pytest -q
5712 passed, 15 skipped, 1 warning in 122.98s

$ uv run --frozen pytest -q tests/agent/test_runner_stall_recovery.py \
      tests/providers/test_fallback_provider.py tests/cron/test_cron_model_field.py
28 passed in 0.68s

$ uv run --frozen pytest -q tests/providers/test_anthropic_client_reset.py \
      tests/providers/test_anthropic_token_refresh.py tests/providers/test_anthropic_opus5.py \
      tests/cron/ tests/session
238 passed in 5.87s

# 变异测试（副本，非 worktree）：删除 runner.py:705-706 计数器清零
$ cd /root/workspace/tmp/mutcheck && uv run --frozen pytest -q tests/agent
1499 passed in 48.70s        # 变异存活

# loguru 格式化实证
$ python3 -c "logger.warning('token refresh failed: %s', ValueError('boom'))"
token refresh failed: %s     # 异常信息丢失

# Discord builtin 命名合法性实证
16 条 spec，name 全部匹配 ^[a-z0-9_-]{1,32}$，description 长度全部在 1..100，invalid: 0
```
