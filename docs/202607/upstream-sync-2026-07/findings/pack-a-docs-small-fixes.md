# Pack A：纯文档/同步元数据 + 剩余小修

- 分歧基点 merge-base：`ba38f908`
- 上游 HEAD：`3f808d0a`（2026-07-27），领先 base **1173** 笔
- 本 pack 覆盖 16 笔本地 commit：14 笔 docs + 2 笔小修
- 结论速览：docs 14 笔 = **[3] 纯本地，建议归档保留、不带入新基座**；`0928d8d9` = **[1] 上游已吸收**（上游做得更彻底）；`67bd27c2` = **[3] 纯本地，重放难度高**（承载结构被上游重构掉）

---

## 一、docs 14 笔：docs/superpowers/ 私有档案

### 事实

全部 14 笔只碰 `docs/superpowers/`，零代码文件（`git show --name-only` 过滤后 non-superpowers-files 全为 0）：

| commit | 日期 | 规模 | 文件 |
|---|---|---|---|
| 2c2d6d46 | 2026-05-18 | +216 | plans/2026-05-18-upstream-sync.md |
| a7111b91 | 2026-05-18 | +676 | plans/…pack1-anthropic-oauth-provider-routing.md |
| 0661cfb4 | 2026-05-18 | +1095 | plans/…pack2-discord-transcription-tts.md |
| 5a762af1 | 2026-05-18 | +976 | plans/…pack3-runtime-session-metadata.md |
| 5138dc94 | 2026-05-18 | +903 | plans/…pack4-command-rewrite-hook.md |
| 6845ba80 | 2026-05-18 | +646 | plans/…pack5-subagent-trace-logging.md |
| cdc3fa71 | 2026-05-18 | +584 | plans/…pack6-memory-consolidation-pruning.md |
| dd36ca38 | 2026-05-18 | +569 | plans/…pack7-tools-workspace-behavior.md |
| 049c185b | 2026-05-18 | +683 | plans/…pack8-bootstrap-docs.md |
| 540fe8b2 | 2026-05-18 | +2401 | specs/spec1–spec4 |
| c230d290 | 2026-05-18 | +2356 | specs/spec5–spec8 |
| c1eb8c3d | 2026-05-18 | +112 −24 | specs 跨文档对齐（ForkConfig / _request_model / AgentRunResult / sender_name） |
| a8f2dce0 | 2026-05-18 | +419 −398 | 8 份 spec 的 Linus-mode review 修订 |
| 47f5ce97 | 2026-05-18 | +114 −44 | 标记「上游已实现」+ grep fallback 改系统 grep |

上游侧证据：`git ls-tree -r --name-only upstream/main docs/ | grep -c superpowers` → **0**。上游 `docs/` 有 53 个文件，没有 superpowers 这一支，也没有等价的 plan/spec 目录。本地 `docs/superpowers/` 当前恰好只有这 17 个文件，全部出自这 14 笔——删掉这批就等于删掉整个目录。

### 分类

**[3] 纯本地（重放难度：低，上游根本没碰过这些路径）**，但**不建议重放进新基座**，理由：

1. 这是 2026-05-18 那次 fork 重放的**过程档案**，不是产品文档。上游是别人的仓库，不会吸收你的 pack 计划和 spec；带进去只会在每次同步时产生噪音。
2. 内容已经过时。47f5ce97 的「upstream-already-implemented」标注是对 **2026-05-18 的上游状态**做的判断，而今天上游已经在 base 之上走了 1173 笔——这批标注现在全部不可信，会主动误导下一次同步。
3. 里面有从未落地的设计。抽查 spec7 的 "grep fallback 从 Python 改系统 grep"：本地 `nanobot/agent/tools/search.py`（416 行）里 grep 没有 `subprocess` / `create_subprocess` / `which("grep")` 任何一处；上游 `search.py`（591 行）同样没有 ripgrep/系统 grep 路径。也就是说这条改动只存在于 spec，代码从没跟上——文档与实现已经脱钩。

### 处置建议

**归档保留、不带入新基座。** 具体做法：在切基座前把 `docs/superpowers/` 整个目录打成一个 tar/单独 archive 分支（或移到 `docs/archive/2026-05-18-fork-replay/`），新基座上不再维护。它唯一还剩的价值是**历史交叉引用**——本次 2026-07 同步里 pack B（OAuth）、pack C（Discord TTS）、pack F/G/H（subagent/tools）分析的那批本地代码，其原始设计意图就写在 spec1 / spec2 / spec5 / spec7 里。要重放时可以回去看「当初为什么这么设计」，但**不要把里面的实现状态当事实**，一律回到代码去验。

---

## 二、0928d8d9 `fix: resolve dream model override provider`

### 本地做法

在 `nanobot/cli/commands.py` 的 dream 装配处，把 `dream.model_override` 当成 preset 名去查 `model_presets`，解析出 provider 并切换；解析不到就 warn 并退回默认 provider（try/except + 警告的软兜底）。

### 上游现状

上游已经把这条路径**做进了配置与运行时骨架**，比本地这笔更彻底：

- `nanobot/config/schema.py`：`dream.model_override` 在**配置加载期**就按 preset 名校验，不认识的名字直接报错，而不是运行时 warn。
- `nanobot/agent/loop.py`：`AgentLoop` 接收 `dream_model_preset`，提供 `dream_runtime()`。
- `nanobot/agent/model_runtime.py`：`resolve_preset()` 统一把 preset 名解析成 provider + model。
- `nanobot/utils/llm_runtime.py`：`LLMRuntime` 是 frozen dataclass，字段含 `provider: LLMProvider`、`model: str`、`model_preset: str | None`——provider 和 model 被一起冻结进同一个运行时对象，不再靠调用方各自去 provider 上取。
- `nanobot/cli/commands.py`：dream 装配直接用 `agent.dream_runtime()`。

也就是说本地这笔要解决的病根（"model_override 只换了 model 没换 provider"）在上游被**结构性消除**了：provider 不可能和 model 分家，因为它们在同一个 `LLMRuntime` 里。

### 分类

**[1] 上游已吸收**（且实现更好）。直接丢弃本地这笔，改用上游。

**唯一行为差异需要 haibin 知情**：本地是「preset 不认识 → warn + 退回默认」，上游是「preset 不认识 → 配置加载失败」。如果你现在的 `config.json` 里 `dream.model_override` 写的是一个**模型名而不是 preset 名**（本地软兜底会让它悄悄跑起来），切上游后会在启动期直接炸。切基座前查一遍这个字段。

---

## 三、67bd27c2 `fix: 统一 cron 任务模型字段`

### 本地做法

把 aa21c8ce（2026-06-09，`feat(agent): /model 改为 per-session 生效 + cron 模型钉 fast`）引入的 `CronPayload.preset` 字段统一改名为 `model`，并顺带把 cron 默认 preset 从 `fast` 改成 `deep`。改动面：

- `nanobot/cron/types.py`：`preset` → `model`（per-job model preset name；None → cron 默认）
- `nanobot/cron/service.py`：`add()` 参数改名 + **jobs.json 序列化/反序列化新增 `"model"` 键**
- `nanobot/agent/tools/cron.py`：工具 schema 参数 `preset` → `model`，list 输出新增 `Model:` 行
- `nanobot/cli/commands.py`：`CRON_DEFAULT_PRESET = "fast"` → `"deep"`，`_run_gateway` 里 `eff_preset = job.payload.model or CRON_DEFAULT_PRESET`
- `tests/cron/test_cron_service.py` +70

### 上游现状

上游**完全没有这个特性**，而且承载它的结构已经被拆掉：

- `git grep -E "preset|model" upstream/main -- nanobot/cron/ nanobot/agent/tools/cron.py` → **零命中**。上游 `CronPayload` 字段是 kind / message / deliver / channel / to / channel_meta / session_key / origin_channel / origin_chat_id / origin_metadata，没有任何模型字段。
- 上游 cron 自 base 以来有 **25+ 笔重构**，关键几笔：`80524e9e refactor: bind cron jobs to origin sessions`、`af8192dc refactor: move bound cron execution out of gateway`、`83355548 refactor: migrate legacy cron payloads to bound sessions`、`b24b5f19 fix(cron): always require bound automation sessions`。
- 执行点已经从本地那个 `_run_gateway`（`process_direct(..., preset=...)`）搬到了 `nanobot/cron/bound_runner.py` 的 `submit_cron_turn()`——本地这笔改的那段代码在上游**已不存在**。
- 上游有自己的 per-session preset 机制：`nanobot/session/model_selection.py` 的 `SESSION_MODEL_PRESET_METADATA_KEY = "_nanobot_model_preset"`，`/model` 走 `loop.set_session_model_preset(ctx.key, name)`。cron job 绑定到 origin session，于是 cron 跑的时候**继承那个 session 持久化的 preset**。

### 分类

**[3] 纯本地（重放难度：高——上游重构了承载结构）**

要注意，本地这套设计的**动机在上游被部分满足、部分反转**：

- 「cron 不要跟着临时 `/model` 漂」——上游的 session preset 是持久化的，不再是 transient，所以「漂」这个病本身弱化了。
- 但反过来：上游 cron 绑定 origin session，意味着你在某个 session 里 `/model fast` 之后，**该 session 名下所有 cron job 都跟着变 fast**。本地想要的「per-job 钉死 + 全局 cron 默认 deep」在上游拿不到。

### 重放路径（若决定保留）

1. `CronPayload` 加回 `model: str | None`。
2. `CronService` 的 add / 序列化 / 反序列化补 `"model"` 键——注意上游 jobs.json 加载已经做了 dual-case 键与 null 容错（afed32b0 / b81c0558 等），新字段要跟上同样的读法。
3. 落点从 `cli/commands.py::_run_gateway` **改到 `nanobot/cron/bound_runner.py::submit_cron_turn`**：把 `job.payload.model or CRON_DEFAULT_PRESET` 解析成 `LLMRuntime`（走 `resolve_preset`），以 `runtime=` 传进这一轮，覆盖 session 自身的 preset。
4. `CRON_DEFAULT_PRESET` 这个常量放在 `cli/commands.py` 已经不合适，应挪进 cron 模块或配置。
5. 工具 schema 的 `model` 参数与 list 输出可原样重放（上游 `tools/cron.py` 这块没有冲突字段）。

### 关键依赖

**67bd27c2 只是 aa21c8ce 的改名 + 默认值调整，不能单独重放。** 真正的特性引入在 aa21c8ce（per-session `/model` + cron 钉 preset）。而 aa21c8ce 的前半部分（`/model` 改 per-session 生效）上游已有等价实现（`session/model_selection.py` + `command/builtin.py`），属 [1]；后半部分（cron 模型钉）才是 [3]。**重放时应把 aa21c8ce 与 67bd27c2 压成一笔「cron per-job model pin」的新提交，直接以 `model` 为字段名写在上游的 bound_runner 结构上，不要重演改名史。**

---

## 四、与其他 pack 的重叠

| 项 | 重叠情况 |
|---|---|
| docs 14 笔 | **无代码重叠**（零代码文件）。但内容上与 pack B（spec1 OAuth）、pack C（spec2 Discord TTS）、pack F/G/H（spec5 subagent trace、spec7 tools workspace）是同一批特性的**设计文档**。三份 findings 的结论应以代码为准；spec 只能当动机参考。 |
| 0928d8d9 | 触及 `nanobot/cli/commands.py` dream 装配段。若有 pack 负责 dream/memory consolidation（对应 spec6），需确认它不要重复认领这笔——本笔已判 [1]，直接丢弃即可，不需要任何 pack 重放。 |
| **与 Pack K 直接重叠** | `findings/pack-k-cron-dream.md` 已独立分析了 **0928d8d9 与 67bd27c2 这两笔**（该文件在我写入时才出现）。两份结论方向一致：0928d8d9 = 丢弃用上游；cron 那笔需 haibin 拍板捆绑重放。**一处分歧需要核**：Pack K 把 67bd27c2 与 `d6e49fdf`（2026-06-05，cron 独立 session）捆绑；我的证据是 `git log -S"preset: str \| None = None  # per-job" ba38f908..main -- nanobot/cron/types.py` → 只命中 `aa21c8ce` 与 `67bd27c2`，即**引入 `preset` 字段的是 aa21c8ce**，d6e49fdf 是另一个问题（session 增长）。合并结论时以 `-S` 输出为准，三笔（d6e49fdf / aa21c8ce / 67bd27c2）可能都要一起考虑。**建议 Pack A 不认领这两笔，交由 Pack K 收口，本节仅作交叉验证。** |
| 67bd27c2 | 触及 `cron/types.py`、`cron/service.py`、`agent/tools/cron.py`、`cli/commands.py`。**与 aa21c8ce 强依赖**；aa21c8ce 若被归到「agent/model 相关 pack」，则 67bd27c2 必须与它合并处理，不能留在 Pack A 单独重放。目前 base..main 范围内只有这两笔碰 cron，`cli/commands.py` 则是多 pack 共享的高冲突文件（上游同段已被 af8192dc 移走），冲突解决要统一由 cron 那一笔负责。 |

---

## 五、给 haibin 的一句话

Pack A 里真正需要你拍板的只有一件事：**cron per-job model pin 要不要在新基座上重建**。上游把 cron 绑进 origin session，等于把「这个任务用什么模型」的控制权交给了那个 session 的 `/model` 状态——你原来的设计是反的，你要的是任务自己钉死模型、不受人类临时切换影响。这两种语义不可调和，重放就是逆着上游的结构走，以后每次同步都要再打一次架。其余 15 笔要么归档（14 笔文档）、要么直接扔（dream 那笔上游做得更干净）。
