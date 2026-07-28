# Pack D/E/I 覆盖审计（9 commit）

审计对象：`85f47e11 4acbefc6 b2972889 e3c39d8b 492c9b9f 7046af9c db88223a 78dc871d 182893f2`
基座：`upstream/main = 3f808d0a`，worktree `/root/git_code/nanobot/.worktrees/sync-2026-07`
计划：`docs/202607/upstream-sync-2026-07/plan.md`
方式：逐 commit `git show` 拆行为，逐条对 plan 找落点，判 DROPPED-OK 时在 worktree 实读代码取证。

结论先说：**3 个 GAP，全部集中在 85f47e11 的 session 持久化和 db88223a 的 TOOLS.md**；另有 3 处 plan 与原 commit 的有意偏离，需要拍板而不是补漏。

---

## 1. 85f47e11 — runtime identity / timing / session metadata（Spec3）

对照 plan 第 6 节。拆出 6 条行为：

| # | 行为 | 判定 |
|---|---|---|
| 1 | `ContextBuilder` runtime block 增加 `Channel Name` / `Sender Name` | COVERED |
| 2 | Discord `_build_inbound_metadata()` 注入 `channel_name` + `sender_name` | COVERED |
| 3 | `AgentRunResult` 增加 `elapsed_ms` / `llm_elapsed_ms`，`_request_model()` 返回单次耗时 | COVERED |
| 4 | `_run_agent_loop()` 返回 `AgentRunResult`（method B 重构） | COVERED（隐含） |
| 5 | `AssistantTurnMetrics` → `_save_turn()` 把 model/usage/elapsed_ms/llm_elapsed_ms 写进 assistant 消息 | **GAP-1** |
| 6 | `_persist_user_message_early()` 把 sender_id + sender_name 写进 user 消息 | **GAP-2** |

**行为 1/2 — COVERED。** plan Task 6.1 原句：「身份块包含 current time、channel、chat ID、sender ID/name、channel name；缺字段时省略而非编造」「Discord ingress 负责把真实 sender/channel 字段放进 `InboundMessage.metadata`」。上游确实没有：worktree `grep -rn "sender_name\|channel_name\|display_name" nanobot/channels/discord/*.py nanobot/agent/context.py nanobot/agent/loop.py` 只命中 `manifest.py:22 display_name="Discord"` 和 `runtime.py:354 display_name = "Discord"`，都是渠道名常量，与本 commit 无关。

**行为 3 — COVERED。** plan Task 6.2 原句：「给 `AgentRunResult`（`runner.py:105`）增加 `elapsed_ms`、`llm_elapsed_ms` 两个带默认值的字段……总耗时覆盖 run 全周期，LLM 耗时只累加 `_request_model()`」。上游 `grep -rn "elapsed_ms" nanobot/` 无命中，确需重放。

**行为 4 — COVERED（隐含）。** 这是本地为串 metrics 做的返回值重构，plan 6.2 要求「不改构造签名顺序」，上游 `_run_agent_loop` 结构已不同，重放时按上游形态实现即可，不构成独立能力缺口。

### GAP-1：assistant 消息的 metrics 持久化没有落点

- **丢失行为**：本地把 `model` / `usage` / `latency_ms` / `elapsed_ms` / `llm_elapsed_ms` 五个字段写进 `session.messages[last_assistant_idx]`，让 session 落盘后可离线复盘每一轮用了哪个模型、烧了多少 token、卡在 LLM 还是 tool。
- **本地位置**：`nanobot/agent/loop.py` 的 `AssistantTurnMetrics` dataclass + `_save_turn()`（diff hunk `@@ -1479,8 +1513,21 @@`）。
- **上游查证**：worktree `nanobot/agent/loop.py:1845-1852`

  ```python
              last_assistant_idx = len(session.messages) - 1
      ...
      if turn_latency_ms is not None and last_assistant_idx is not None:
          session.messages[last_assistant_idx]["latency_ms"] = int(turn_latency_ms)
  ```

  只有 `latency_ms`，没有 model / usage / elapsed_ms / llm_elapsed_ms。
- **plan 查证**：全文 `grep -n "elapsed_ms\|AssistantTurnMetrics\|session.messages"` 只命中第 369 行，即 Task 6.2 那句 `AgentRunResult` 字段描述。耗时只进了内存返回值，没进会话历史。
- **建议**：补在第 6 节，作为 Task 6.3 或 Task 6.2 的第二条行为——「`_save_turn()` 在上游 `latency_ms` 之外，追加 model/usage/elapsed_ms/llm_elapsed_ms 到 assistant 消息，字段缺失时不写」，测试点：字段可选写入、旧 session 不含新字段仍可加载。

### GAP-2：user 消息的 sender 身份持久化没有落点

- **丢失行为**：`_persist_user_message_early()` 把 `sender_id` + `sender_name` 落进 user 消息条目。身份块（行为 1）只影响本轮 prompt，落盘字段才让历史消息知道是谁说的——群聊多人场景下这是两件事。
- **本地位置**：`nanobot/agent/loop.py` `_persist_user_message_early()`。
- **上游查证**：worktree `nanobot/agent/loop.py` 的 `_persist_user_message_early()` 只写 role/content 与通用 extra，`grep -rn "sender_id" nanobot/agent/loop.py` 无命中。
- **plan 查证**：Task 6.1 的行为句止于「放进 `InboundMessage.metadata`」和「传给出站 message tool」，没有一句提到写入 session 历史。
- **建议**：并入 Task 6.1 行为清单末尾，与身份块共用同一份 metadata 提取逻辑，避免 Discord 特判。

---

## 2. 4acbefc6 — config forward-ref 与 schema 迁移

分类文档写「forward-ref 用上游，仅按真实生产配置保必要迁移」。这里把「必要迁移」查实了。

**forward-ref 修复 — DROPPED-OK。** 上游已有等价实现，且比本地更彻底：

- `nanobot/config/loader.py:45` 在加载路径上先解析引用；
- `nanobot/config/schema.py:427-430` `Config.__init__` 里做惰性 rebuild 兜底。

本地那版 try/except ImportError 收窄补丁在这套结构下无残留价值。

**schema 迁移 — COVERED，且已实测足够。** 「必要迁移」在生产配置 `/root/workspace/nanobot_config/config.json` 上的实际含义只有一条：**顶层 `tts` 块存在，必须删**（生产配置确有 `tts.enabled=true`）。另一条本地迁移逻辑「无 `modelPresets` 时剥离 `fallbackModels`」对生产是空转——生产同时有 `modelPresets` 和 `fallbackModels`，不触发。

实测取证（只读复制到 tmp，未改生产配置）：

```
# 原样加载
ERR: 1 validation error for Config
tts  Extra inputs are not permitted [type=extra_forbidden]

# 按 plan 第 10 节删掉 tts / agents.defaults.contextPruning / tools.commandRewrite 后
LOAD_OK anthropic/claude-opus-4-5
```

plan 第 10 节列的三处删除即为充分集合，无第四处隐藏 `extra_forbidden`。这条不用改 plan。

---

## 3. b2972889 — `_make_skills_loader` workspace 修复

**DROPPED-OK。** 修的是本地 Discord slash-command 注册路径里 SkillsLoader 用错 workspace，而**上游整条路径不存在**：worktree `grep -rn "SkillsLoader" nanobot/channels/` 无任何命中，`nanobot/channels/discord/` 下也没有技能注册代码。被修的函数在新基座上没有宿主，无从重放。与已砍清单一致。

---

## 4. e3c39d8b / 492c9b9f / 7046af9c — rtk 命令改写全线

三个 commit 都只服务 `CommandRewriteHook`：

- `e3c39d8b`：新增 `nanobot/agent/hooks/rewrite.py`、`ToolsConfig.command_rewrite`、`AgentLoop.from_config()` 条件挂钩、`SubagentManager` 的 `extra_hooks`；
- `492c9b9f`：超时后 kill rtk 子进程；
- `7046af9c`：管道命令跳过改写（`ls | grep` 被改写成 `rtk ls | grep` 导致 8 天日记误报的那个 bug）。

**DROPPED-OK，全部。** 上游 `grep -rn "rtk" nanobot/ --include=*.py` 零命中，`nanobot/agent/hooks/` 不存在 rewrite 模块。

需要留意的是**钩子基础设施不用补**：`CompositeHook`、`AgentLoop(..., hooks=)` 上游已有（worktree `nanobot/agent/*.py` 命中），所以 e3c39d8b 里唯一非 rtk 的增量（subagent 的 `extra_hooks` 组合）也随 rtk 一起失去用途——上游 `nanobot/agent/subagent.py` 无 `hooks` 参数，砍掉不留悬空。

副作用备案（不是 GAP，是既定砍除的后果）：同步后生产环境 exec 的 `ls` 不再被改写为 `rtk ls`，输出不再带文件大小列。plan 第 10 节删 `tools.commandRewrite` 与之一致。

---

## 5. db88223a — SOUL-first / soul anchor / subagent bootstrap / Discord table hint（Spec8）

对照 plan 第 7 节 Task 7.1。拆出 4 条行为，**全部上游没有**，plan 也全部提到了，但有 1 条被写漏、2 条被有意改写。

上游取证（worktree）：

- `nanobot/agent/context.py:57` — `BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md"]`（AGENTS 在前，**无 TOOLS.md**）
- `nanobot/agent/context.py` 无 `_load_soul_anchor` / `# Remember` / `load_and_format_bootstrap`
- `nanobot/templates/agent/identity.md:17` — `... No tables — use plain lists.`（旧措辞，无 pipe table 硬禁令）
- `nanobot/templates/agent/subagent_system.md` 1-21 行 — 无 `{{ bootstrap }}` 插槽；`nanobot/agent/subagent.py:519-535` `_build_subagent_prompt()` 只传 workspace / agent_workspace / history_log / skills_summary

| # | 行为 | 判定 |
|---|---|---|
| 1 | bootstrap 顺序 SOUL 优先 | COVERED（顺序有偏离，见下） |
| 2 | 主 agent bootstrap 含 `TOOLS.md` | **GAP-3** |
| 3 | prompt 尾部 soul anchor | COVERED（内容有偏离，见下） |
| 4 | subagent 注入 `SOUL.md` + `TOOLS.md` | COVERED |
| 5 | Discord 明确禁止 Markdown pipe table | COVERED |

行为 4 plan 原句：「subagent 注入 workspace 的 `SOUL.md` 与 `TOOLS.md`，同时保留上游 project/agent workspace、untrusted-content 和 skills summary 结构」——比本地 commit 更严谨，因为上游模板已经多了 `agent_workspace` / `history_log`，直接覆盖会丢东西。
行为 5 plan 原句：「Discord 格式提示明确禁止 Markdown pipe table；不整体覆盖上游 identity 模板」。

### GAP-3：主 agent 的 TOOLS.md 掉了

- **丢失行为**：`BOOTSTRAP_FILES` 本地是四个文件 `SOUL.md → USER.md → AGENTS.md → TOOLS.md`，plan Task 7.1 第一条只写「bootstrap 顺序固定为 `SOUL.md → AGENTS.md → USER.md`」，**TOOLS.md 不在列**。上游 `context.py:57` 同样没有。两边都没有，就是真丢。
- **本地位置**：`nanobot/agent/context.py` `ContextBuilder.BOOTSTRAP_FILES`（commit diff 首个 hunk）。
- **生产影响实证**：`/root/workspace/TOOLS.md` 存在（2101 字节，7 月 15 日更新），当前主 agent 系统提示词里确实带着这份工具约束（临时文件只放 `~/workspace/tmp/`、grep/glob 优先于 shell、交付走 write skill 等）。按 plan 现在的写法同步完，这份约束只剩 subagent 拿得到，主 agent 拿不到——这正是最需要它的一侧。
- **建议**：改 plan 第 7 节 Task 7.1 第一条行为句，把加载列表写全并显式包含 `TOOLS.md`，测试项「测试顺序、缺文件降级」里补一条「TOOLS.md 存在时进入主 prompt，缺失时静默降级」。

### 两处有意偏离，需要拍板（不计 GAP）

1. **顺序**。本地是 `SOUL → USER → AGENTS`，plan 写 `SOUL → AGENTS → USER`。commit message 明写理由是 primacy effect，把 USER 提到 AGENTS 前面是原意的一部分。plan 换了顺序但没给理由，执行时会静默按 plan 走。
2. **soul anchor 内容**。本地 `_load_soul_anchor()` 是把整份 `SOUL.md` 原文追加为 `# Remember` 块（首尾双次曝光，primacy + recency）；plan 写「system prompt 尾部追加**精简** soul anchor；不能重复整个 SOUL.md」。这是把原设计的核心机制改小了一号——生产 SOUL.md 4217 字节，重复一次的成本可控，效果是否要保留由你定。若确认精简，plan 需要定义「精简」的判据（取哪几节？截断多少字符？），否则实施时无法验收。

---

## 6. 78dc871d — .gitignore

**COVERED，且 plan 说得够具体。** plan Task 1.2 要求保留上游 `.gitignore` 全文、仅追加本地 5 条。实测两边差异恰好如此：

```
$ diff -u <(git show upstream/main:.gitignore) .gitignore | tail
-exp/
-.playwright-mcp/
-bridge/node_modules/
-webui/.verify-*
+data-gym-cache/
+graphify-out/
+pytest-of-root/
+tmp*.jpg
+tmp*.png
```

上游独有的 4 条（`exp/`、`.playwright-mcp/`、`bridge/node_modules/`、`webui/.verify-*`）是本地分叉后上游新加的，按 plan「保留上游全文」会自然带上；本地独有的正是要追加的 5 条，无第六条遗漏。这条不用改。

---

## 7. 182893f2 — 「6 fixes」混合 commit

rewrite.py（rtk）、runner.py、search.py 三条属已砍清单，不复议。剩下三条单独判：

### 7.1 discord.py 的 `any(tuple)` 短路修复 — DROPPED-OK

本地 bug 是把两个判断塞进 `any((a, b))` 导致提前求值/短路语义错。上游已经是拆开的顺序判断，worktree `nanobot/channels/discord/runtime.py:744-761` 区段为独立 `if ... return` 结构，不存在被修的那行表达式（`grep -rn "mentions_bot" nanobot/channels/discord/*.py` 无命中，函数名与实现均已重写）。无宿主可修。

### 7.2 anthropic_provider.py 的 `_ensure_valid_token` 单一异常处理 — COVERED

上游**根本没有 OAuth 刷新**：`grep -n "refresh\|auth_token\|oauth\|OAuth" nanobot/providers/anthropic_provider.py` 无命中，`nanobot/providers/oauth_store.py` 文件不存在。所以这条不是「上游已有」，而是「整个子系统要按 plan Task 2.1/2.2 重建」——而生产配置 `providers.anthropic_claude_code` 正在用它，不能不建。

关键是这条修复的**语义**有没有进 plan：有。Task 2.2 原句「刷新失败保留真实认证错误」，正是 182893f2 要的「不要吞异常包成通用错误、单一异常路径向上抛」。判 COVERED。

### 7.3 oauth_store.py 的 import 位置 — 无独立行为

把 `import time` 从函数内提到模块级，纯风格。`oauth_store.py` 在新基座上是按 Task 2.1 全新写的文件，没有可重放的行为增量。不计 GAP，也不必写进 plan。

---

## GAP 汇总

| ID | commit | 丢失行为 | 本地位置 | 建议补入 |
|---|---|---|---|---|
| GAP-1 | 85f47e11 | assistant 消息持久化 model/usage/elapsed_ms/llm_elapsed_ms（上游 `loop.py:1851` 只写 `latency_ms`） | `nanobot/agent/loop.py` `AssistantTurnMetrics` + `_save_turn()` | 第 6 节，新增 Task 6.3 或并入 Task 6.2 |
| GAP-2 | 85f47e11 | user 消息持久化 `sender_id` + `sender_name` | `nanobot/agent/loop.py` `_persist_user_message_early()` | 第 6 节 Task 6.1 行为清单 |
| GAP-3 | db88223a | 主 agent bootstrap 丢掉 `TOOLS.md`（上游 `context.py:57` 三文件，plan 7.1 也只写三个；生产 `/root/workspace/TOOLS.md` 现役） | `nanobot/agent/context.py` `BOOTSTRAP_FILES` | 第 7 节 Task 7.1 第一条行为 + 对应测试项 |

### 待拍板的有意偏离（非遗漏）

1. plan 7.1 的 bootstrap 顺序 `SOUL → AGENTS → USER`，与原 commit 的 `SOUL → USER → AGENTS` 不同，plan 未给理由。
2. plan 7.1 要求 soul anchor「精简、不重复整个 SOUL.md」，原实现是全文双曝光；若维持精简，需在 plan 里给出可验收的精简判据。
3. rtk 全线砍除后，生产 exec 的 `ls` 输出不再带文件大小列——已知的行为回退，与第 10 节删 `tools.commandRewrite` 一致。

### 本轮判定为 DROPPED-OK 且已实读取证的项

- 4acbefc6 forward-ref：`config/loader.py:45` + `config/schema.py:427-430`
- b2972889：`nanobot/channels/` 无 `SkillsLoader`
- e3c39d8b / 492c9b9f / 7046af9c：`nanobot/` 无 `rtk`，`agent/hooks/rewrite.py` 不存在
- 182893f2 discord 分支：`channels/discord/runtime.py:744-761` 已是拆开的顺序判断
