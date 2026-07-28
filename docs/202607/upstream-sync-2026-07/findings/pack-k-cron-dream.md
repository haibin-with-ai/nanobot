# Pack K — cron/session model 与 Dream 单阶段重构

判定基线：本地 `HEAD = 9ca8c42d`，`upstream/main = 3f808d0a`，merge-base `ba38f908`。全部结论来自只读 git 命令，命令与输出内嵌在各条目里。

---

## 先决事实：d1a94dae 已在上游，且上游此后又走了 29 步

```
$ git merge-base --is-ancestor d1a94dae upstream/main && echo YES
YES
$ git log --no-merges --format='%h %ad %s' --date=short d1a94dae..upstream/main \
    -- nanobot/agent/memory.py nanobot/templates/agent/dream.md
7fd28c9f 2026-07-26 fix(memory): preserve unprocessed dream history
4e2640f2 2026-07-27 fix(memory): keep failed Dream batches retryable
15e42059 2026-07-23 fix(memory): progress past completed no-op batches
745757cc 2026-07-25 fix(memory): skip non-dict history.jsonl lines when reading
c1fd76ad 2026-07-15 refactor(prompts): share workspace override handling
791c7fd5 2026-07-13 fix(memory): prune encoded Dream sessions
f0c989ba 2026-07-05 fix(dream): ground memory audit records in the real git diff (#4673)
c9c69e43 2026-07-03 fix(memory): cap workspace Dream prompt overrides
f38fd7d5 2026-06-30 feat(memory): add workspace Dream prompt override
6c880a66 2026-06-24 fix: advance dream cursor when Dream is disabled ... (#4242)
15f218e9 2026-06-10 fix: enforce exact Dream memory file writes
...（共 29 笔，此处截取与 Dream 语义直接相关者）
```

也就是说：本地 55b46a2f 声称的「移植 upstream d1a94dae」，移植的是一个上游 2026-06 初的快照，上游在那之后对同一批文件又打了 29 笔补丁——**移植版本身已经过期**。这决定了 Pack K 里 Dream 三笔的整体走向。

---

### d6e49fdf fix(cron): 每次定时任务用独立 session，避免会话无限增长

- 分类：**[2] 平行实现**（若最终选上游，本地这笔直接丢；若坚持本地语义，重放难度**高**）
- 本地做了什么：`nanobot/cli/commands.py` 单文件改动，把 cron 执行的 session key 从 `job.id` 改成 `f"cron:{job.id}:{int(time.time()*1000)}"`，每次触发开一个全新 session，避免一个 job 的会话无限追加（注释里明确提到 thinking-block signature 中毒复现）。
- 上游现状：上游已把 cron 执行整体重构成 `nanobot/cron/bound_runner.py::run_bound_cron_job`，语义**正好相反**——
  ```
  $ git show upstream/main:nanobot/cron/bound_runner.py | sed -n '68,72p'
      session_key = job.payload.session_key
      if not session_key:
          raise ValueError(f"cron job {job.id} is missing payload.session_key")
  ```
  上游把 cron 回合当作**原会话内的一次普通 turn** 投递（`agent.submit_cron_turn(InboundMessage(..., session_key_override=session_key))`，附 `CRON_TRIGGER_META` / `CRON_DEFER_UNTIL_IDLE_META`），未绑定 session 的 job 直接抛错拒绝执行。`nanobot/cli/commands.py` 里的旧 `process_direct` 路径已被 `run_bound_cron_job(job, agent=agent, cron=cron)` 取代。
- 判定理由：两边解的是同一个问题的两端。本地要「隔离」，上游要「归属」——cron 结果回到用户原来的对话里，靠上游的 auto-compact / retention 控制体积，而不是靠开新会话。功能重叠、语义不等价，必须 haibin 拍板。
- 风险/注意：本地方案有一个已经发生的副作用，磁盘上可查：
  ```
  $ ls /root/workspace/sessions | grep -c '^cron_'
  177
  $ du -sh /root/workspace/sessions
  19M
  $ grep -rn "prune" nanobot/cli/commands.py | head    # 无输出
  ```
  177 个 per-run cron session 文件，**没有任何回收路径**——单会话不再膨胀，代价是文件数无界增长。上游的绑定方案里，cron 产物会进入用户会话历史（想要「安静的定时任务」的人会不适应）。选哪个是产品判断，不是技术判断。

---

### 55b46a2f Dream 单阶段重构：移植 upstream d1a94dae

- 分类：**[1] 上游已吸收**（且上游更新）
- 本地做了什么：删两阶段 `Dream` 类与 `dream_phase1/2.md`，新增单阶段 `dream.md`；`MemoryStore.build_dream_prompt()` 承担截断/切片；新增 `AgentLoop.run_dream_once()` 统一 `/dream` 与 cron 入口，内部走 `process_direct`。
- 上游现状：d1a94dae 已是 `upstream/main` 祖先（上方 `--is-ancestor` 证据），并在其后收了 29 笔演进。本地移植版**缺**至少这些上游能力：
  - `build_dream_tools()`——上游 Dream 仍跑受限工具集（`git show upstream/main:nanobot/agent/memory.py | grep -n build_dream_tools` → 635 行；本地 `git grep -c build_dream_tools HEAD` → 无匹配，本地 Dream 跑全量工具）。
  - `DreamRunProgress` / `dream_run_completed(resp, had_tool_errors=...)`——上游用工具报错门控 cursor 推进（4e2640f2「keep failed Dream batches retryable」）。
  - workspace prompt override（f38fd7d5 + c9c69e43 上限）。
  - `prune_dream_sessions` 的 base64url 解码版（791c7fd5）。上游 session 文件名已改成编码形式，本地 `session/manager.py:306` 仍是 `key.replace(":", "_")`。
  - 上游 cron dream 分支还有 `ephemeral=True`、`record_response_token_usage(source="dream")`、`compact_history()`、`try/finally` 提交（`git show upstream/main:nanobot/cli/commands.py | sed -n '1806,1874p'`）——本地 `run_dream_once` 全无。
- 判定理由：本地是上游旧快照的重写，上游是同一设计的当前版且严格更全。重放这笔等于把上游往回退 29 笔。
- 风险/注意：本地确有上游没有的两处增量，丢弃前要单独摘出来（见「小结」）：
  ```
  $ git grep -n "line_age\|Target Cursor" HEAD -- nanobot/agent/memory.py    # 有
  $ git grep -n "line_age\|Target Cursor" upstream/main -- nanobot/agent/memory.py   # 无输出
  ```
  即 memory 行龄标注（`_annotate_memory_line_ages` + `stale_threshold_days`）与 prompt 里的 `## Target Cursor` 段，上游侧确认无此实现。

---

### e7545114 Dream 单阶段重构：重写测试

- 分类：**[1] 上游已吸收**
- 本地做了什么：跟随 55b46a2f 重写 `tests/agent/test_dream.py` 等。
- 上游现状：上游保留自己的 Dream 测试套件，且包含本地已删掉的路径：
  ```
  $ git ls-tree -r --name-only upstream/main tests | grep -i dream
  tests/agent/test_dream.py
  tests/agent/test_dream_tools.py
  ```
  `test_dream_tools.py` 对应上游仍存在的 `build_dream_tools()`——本地无此文件也无此函数。
- 判定理由：测试是实现的影子。实现走上游，测试也走上游。
- 风险/注意：若采纳「小结」里保留行龄标注的建议，需要从本地测试里摘出对应用例补进上游套件，其余整体丢弃。

---

### 03b44175 Dream 单阶段重构：spec 与 plan 文档

- 分类：**[3] 纯本地**（重放难度：低）
- 本地做了什么：仅 `docs/` 下的 spec 与 plan 文档，无代码。
- 上游现状：上游无对应文档（该目录为 fork 私有产物目录）。
- 判定理由：文档不冲突，也不影响运行。
- 风险/注意：文档描述的是**本地移植版**的设计，与上游当前 Dream 已不符。留着可以，但要在文首标注「描述的是 2026-07 fork 快照，非上游当前实现」，否则将来会误导。

---

### c431a7df 修复 new 命令保留模型覆盖

- 分类：**[3] 纯本地**（重放难度：中——上游改过同文件且改了 key 名）
- 本地做了什么：`nanobot/command/builtin.py` 加一行 `session.metadata.pop("model_preset", None)`，让 `/new` 把会话级模型覆盖也清掉；配套 20 行测试断言 `goal_state` 不被误删。
- 上游现状：上游 `/new` 处理器**仍未清理该字段**：
  ```
  $ git show upstream/main:nanobot/command/builtin.py | sed -n '283,330p'   # cmd_new 全文，无 metadata.pop
  $ git grep -n "SESSION_MODEL_PRESET_METADATA_KEY" upstream/main -- nanobot/
  ```
  上游把 key 换成了常量 `SESSION_MODEL_PRESET_METADATA_KEY = "_nanobot_model_preset"`（本地是裸字符串 `"model_preset"`），但 `/new` 路径确实没 pop 它。
- 判定理由：上游无等价修复，bug 仍在，必须重放。
- 风险/注意：重放时**必须换成上游常量名**，直接搬 `"model_preset"` 字面量会静默失效（pop 一个不存在的 key，测试若也用旧字面量还会绿）。这正是「patch 调用点、别搬字面量」的典型坑。

---

### 0d7d9439 feat(dream): ground memory audit commit messages in real git diff

- 分类：**[1] 上游已吸收**
- 本地做了什么：自述即「Port upstream f0c989b」——引入 `GitStore.summarize_working_tree` 与 `MemoryStore.dream_content_diff`，commit message 改由真实 working-tree diff 生成而非 LLM 自述。
- 上游现状：源头 commit 在上游：
  ```
  $ git merge-base --is-ancestor f0c989b upstream/main && echo YES
  YES     # f0c989ba fix(dream): ground memory audit records in the real git diff (#4673)
  ```
  函数级比对：`build_dream_commit_message` 两边**完全相同**（`diff <(...) <(...)` 无输出）；`dream_content_diff` 仅 docstring 差一句措辞；`summarize_working_tree` 上游已继续演进（错误抛出、CRLF/bytes 解码处理），本地版落后。
- 判定理由：这是显式的上游回移，源头已在上游且上游版更新。
- 风险/注意：本地 commit message 里写「keeping the fork's unconditional cursor advance」——这条 fork 语义与上游 4e2640f2（失败批次可重试）、15e42059（空操作批次照常推进）不一致。采纳上游即接受上游的 cursor 语义，这一点要让 haibin 明确知情，别当成无损替换。

---

### 0928d8d9 fix: resolve dream model override provider

- 分类：**[1] 上游已吸收**（且本地这笔已被自己后来的 commit 删除）
- 本地做了什么：`agent.dream.model = dream_cfg.model_override` 把 preset 名当模型名直接赋值，会把 `"codex"` 发去默认 Anthropic 端点报 `not_found_error`；这笔改成先 `build_provider_snapshot(config, preset_name=...)` 再 `set_provider(...)`。
- 上游现状：上游用运行时解析，结构上就不可能犯这个错：
  ```
  $ git show upstream/main:nanobot/agent/loop.py | sed -n '223,227p'
      def dream_runtime(self) -> LLMRuntime | None:
          if not self.dream_model_preset:
              return None
          return self.runtime_resolver.resolve_preset(self.dream_model_preset)
  ```
  `dream_model_preset` 由 `defaults.dream.model_override` 注入（loop.py:485），`resolve_preset` 返回完整 provider+model runtime。
- 判定理由：上游架构层面已解，且**本地这段代码已经不存在**——55b46a2f 删掉了 `Dream` 类，`grep "agent.dream" nanobot/cli/commands.py` 无匹配。这是一笔历史上被自己覆盖掉的补丁。
- 风险/注意：无。丢弃，不需要任何回归验证。

---

### 67bd27c2 fix: 统一 cron 任务模型字段

- 分类：**[2] 平行实现**（若选上游 cron 架构则整笔失去载体；若坚持本地，重放难度**高**）
- 本地做了什么：把 cron 的 `payload.preset` 字段改名为 `model`（tool schema / service / types / commands 五处同步），并把 `CRON_DEFAULT_PRESET` 从 `"fast"` 提到 `"deep"`，理由是弱模型撑不住多步编排。
- 上游现状：上游 `CronPayload` **根本没有 model/preset 字段**：
  ```
  $ git show upstream/main:nanobot/cron/types.py | sed -n '/class CronPayload/,/^class /p'
      kind / message / deliver / channel / to / channel_meta
      session_key / origin_channel / origin_chat_id / origin_metadata
  $ git grep -n "preset\|model" upstream/main -- nanobot/agent/tools/cron.py   # 无输出
  $ git log --oneline upstream/main -S"preset" -- nanobot/cron/types.py        # 无输出
  ```
  上游模型选择完全跟随绑定会话的 runtime（bound_runner 走 `submit_cron_turn`，用该 session 自己的 preset），不提供 per-job 模型。
- 判定理由：这笔是本地「per-job 模型」特性的一部分，上游没有这个概念。它不是修 bug，是 fork 的产品能力。
- 风险/注意：与 d6e49fdf 是同一个决策的两半——per-run 独立 session 才需要 per-job model（没有会话可继承）。上游绑定会话方案下，`payload.model` 无处安放。**两笔必须一起拍板，不能拆开**。另外注意本地默认从 `fast` 提到 `deep`，若走上游路线，这层成本/质量取舍会被静默取消，cron 将跟随用户当时会话的模型（可能是 flash）。

---

## 小结

**建议：整包以「丢弃 + 三处摘取」为主，但 cron 两笔必须先由 haibin 拍板架构方向。**

三条清晰的线：

**一、Dream 三笔（55b46a2f / e7545114 / 0d7d9439）与 0928d8d9 —— 全丢，改用上游。** 证据链完整：d1a94dae 与 f0c989b 都已是 `upstream/main` 祖先，上游之后又打了 29 笔（受限工具集、`had_tool_errors` 门控、workspace override、编码 session 清理、token 计量、compact）。本地是旧快照的重写，重放等于开倒车。0928d8d9 更彻底——它修的代码已被本地自己删了。

丢之前只摘三样东西：
1. **memory 行龄标注**（`_annotate_memory_line_ages` + `stale_threshold_days` + prompt `## Target Cursor`）——上游检索确认无此实现，是真正的本地增量，值得作为 patch 重新贴到上游 `build_dream_prompt` 上。
2. **本地 dream.md 的三段独有内容**（短会话噪声防护、Completion contract、截断感知编辑说明）——共 7 行，逐行贴回上游模板即可。
3. 对应测试用例。

**二、`/new` 保留模型覆盖（c431a7df）—— 必须重放，一行。** 上游 `cmd_new` 确认未清理该 metadata。唯一的坑：key 名要用上游常量 `SESSION_MODEL_PRESET_METADATA_KEY`（`"_nanobot_model_preset"`），别搬本地字面量 `"model_preset"`，否则 pop 空 key 静默失效。

**三、cron 两笔（d6e49fdf + 67bd27c2）—— 捆绑决策，不能拆。** 这是 Pack K 唯一需要 haibin 亲自拍板的地方：

- 选上游：cron 回到原会话（`bound_runner.run_bound_cron_job`），两笔全丢。代价是 cron 产物混入用户对话历史，per-job 模型能力消失，`CRON_DEFAULT_PRESET="deep"` 的成本取舍失效。
- 选本地：两笔都要重放到上游的 `bound_runner.py` 结构上——上游未绑定 job 直接抛 `ValueError`，本地的「无状态 per-run session」在上游是被明确拒绝的形态，等于要改上游的核心契约。难度高，且每次上游同步都会再撞一次。
- 无论选哪边，本地现存的 **177 个未回收的 `cron_*.jsonl`**（`/root/workspace/sessions`，共 19M）是个独立待办：本地 per-run 方案从未实现清理逻辑，`prune` 在 `cli/commands.py` 中无任何调用点。

**重放时会碰到的上游文件：** `nanobot/command/builtin.py`（`/new` 一行，必须重放）；若摘取 Dream 增量，还会碰 `nanobot/agent/memory.py`（`build_dream_prompt`）与 `nanobot/templates/agent/dream.md`；若 cron 决策选本地，则涉及 `nanobot/cron/bound_runner.py`、`nanobot/cron/types.py`、`nanobot/agent/tools/cron.py`、`nanobot/cli/commands.py` 四个文件，且是与上游设计正面对撞的改动。

**未验证项：** 上游 `submit_cron_turn` 内部的会话增长控制（retention / auto-compact 具体阈值）未深入核实，因此「上游绑定会话是否真的不会重现本地遇到的 thinking-block 中毒」这一点无法下结论——若 haibin 倾向选上游，这是必须先补的一次验证。
