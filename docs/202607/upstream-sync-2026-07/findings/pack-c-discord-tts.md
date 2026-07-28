# Pack C：Discord 定制 + TTS

结论先说：四笔 commit 都是混合提交，不能整笔 cherry-pick。非 TTS 部分仍按下文逐项判定；**haibin 已于 2026-07-27 拍板：TTS 全部暂不保留，包括 Discord TTS 配置、`/tts`、`nanobot/tts/`、`edge-tts` 依赖及相关测试。**这条线本来就是没有消费者的死代码，直接砍掉。

---

### 2ace8c8d feat(discord): add mention filter, voice transcription, TTS, outbound metadata propagation (Spec2)

- 分类：[3] 纯本地（重放难度：**高**——一笔里四件事，三种不同难度）
- 本地做了什么：discord.py +70/-4（`_resolve_bot_user_id` / `_mentions_other_bot_only` / `_is_audio_attachment` / `_transcribe_audio` + `DiscordConfig.tts`）；`nanobot/tts/` 六个新文件 198 行；`nanobot/command/builtin.py` +38（`/tts` 开关，写 `session.metadata["_outbound_tts"]`）；`nanobot/agent/loop.py` +10（把 session metadata 里 `_outbound_*` 前缀的键透传到出站）；`pyproject.toml` +1（`edge-tts`）。
- 上游现状（逐项证据）：
  - **mention 过滤**：上游 `nanobot/channels/discord/runtime.py:739 _should_respond_in_group` 与本地改前版本逐字节相同（`message.mentions` / `raw_mentions` / `<@id>` / `_references_bot_message` 四条判定齐全）。上游**没有** `_mentions_other_bot_only`、也没有 `_resolve_bot_user_id`：`grep -n "_resolve_bot_user_id\|_mentions_other_bot" <upstream runtime.py>` → EXIT=1。
  - **语音转写**：上游有 `nanobot/audio/transcription.py`（`git ls-tree -r --name-only upstream/main -- nanobot/audio/` 有该文件），并在 `nanobot/channels/base.py` 提供了 `BaseChannel.transcribe_audio()`。`git grep -n "transcribe_audio(" upstream/main -- nanobot/channels/` 命中 base + feishu / telegram / matrix / slack 等，**唯独没有 discord**。也就是说：上游有基础设施、有别的 channel 的接线，**Discord 语音消息上游没接**。
  - **重要断裂**：`git show upstream/main:nanobot/channels/base.py | grep -n "transcription_provider"` → EXIT=1，上游已把每 channel 的 `self.transcription_provider / _api_key / _language` 属性删掉，改为 `nanobot/audio/transcription.py` 里的全局 `resolve_transcription_config()`。本地 `_transcribe_audio()` 正是靠这几个属性活着 → 直接搬过去会 AttributeError。
  - **TTS**：`git ls-tree -r --name-only upstream/main | grep -i tts` → exit 1（零命中）；`git grep -ni "text.to.speech\|edge-tts\|TTSConfig" upstream/main -- nanobot/` → 空。上游确认无 TTS。
  - **outbound metadata 传递**：`git grep -ln "_default_metadata" upstream/main` 只命中 `nanobot/agent/tools/message.py`，而上游那里已经不是本地那个 `ContextVar` 属性了——上游改成 `current_request_context()` / `RequestContext`（`nanobot/agent/loop.py` 内 `_request_context_for_turn` 组装 metadata）。本地在 loop.py 里 `message_tool._default_metadata.get()/set()` 的写法在上游**不存在对应对象**。
- 判定理由：四项能力上游都没有落到 Discord 上，功能不重叠，必须重放；但 mention 过滤是纯增量插入，转写和 metadata 传递要按上游新接口重写。
- 风险/注意：
  - `_transcribe_audio()`（约 30 行）应当**整块删掉**，改为在 `_download_attachments` 的音频分支里调 `await self.transcribe_audio(file_path)`——净减代码，且顺带白拿上游的 provider 收敛。
  - `_download_attachments` 除音频分支外与上游逐字节相同 → 7 行 clean insert。
  - loop.py 那 10 行要改成：在上游 `_request_context_for_turn` 里把 `ctx.session.metadata` 中 `_outbound_` 前缀键并进 metadata；第二个 hunk 落在 `ctx.outbound.metadata["_stop_reason"]` 旁边。逻辑不变，位置和 API 全变。
  - `edge-tts` 是本地独有依赖（`git show upstream/main:pyproject.toml | grep edge` 无命中），重放要重新加。
  - 测试位置变了：上游 Discord 测试在 `nanobot/channels/discord/tests/test_discord_channel.py`（包内），本地在 `tests/channels/test_discord_channel.py`。本笔的 160 行测试要迁址。

---

### 5132903d feat(discord): skill/model/tts slash commands with config toggle (Spec9)

- 分类：[2] 平行实现（`/model` 部分）+ [3] 纯本地（`/skill*` 动态注册、`/tts`）；重放难度：**中**
- 本地做了什么：discord.py +89。加 `DiscordConfig.slash_commands: bool = True` 开关；用 `SkillsLoader.list_skills()` 把每个 skill 动态注册成一个 slash command；注册 `/model`（`app_commands.Choice` 下拉，取自 `_load_model_choices()`）；注册 `/tts`。
- 上游现状：上游 `runtime.py:192 _register_app_commands` 已经存在，注册 `new / stop / restart / status / history / model / trigger / help`，并有统一的 `_forward_slash_command(interaction, command_text)` 扩展点（runtime.py:168 附近）——**这正是本地这批命令该挂的钩子，签名与本地一致**。
  - `/model`：上游 runtime.py:211 已注册 `@self.tree.command(name="model", ...)`，参数是自由字符串 `preset: str | None`；本地是 `app_commands.choices(...)` 下拉。**两边同名，不能共存**（discord.py `CommandTree` 重复注册会抛 `CommandAlreadyRegistered`）。
  - `/skill` 动态注册：`git grep -n "list_skills" upstream/main -- nanobot/channels/` 无命中；上游只有文本命令 `cmd_skill`（`nanobot/command/builtin.py`，且有 `nanobot/channels/discord/tests/`、`tests/command/test_skill_command.py`），**没有把 skill 铺成 slash command**。
  - `/tts`：上游 builtin 命令表里有 `cmd_model / cmd_skill / cmd_dream / cmd_dream_log / cmd_trigger / cmd_help`，无 `cmd_tts`。
  - `DiscordConfig`（上游 runtime.py:51）字段列表里没有 `slash_commands`。
- 判定理由：扩展点上游给好了，本地这 89 行的骨架（`@self.tree.command` + `_forward_slash_command`）在新架构里照抄即可；唯一真冲突是 `/model` 一个名字两套 UX。
- 风险/注意：
  - `/model` 必须 haibin 拍板：留上游自由文本（少代码、跟上游走），还是留本地 Choice 下拉（体验好，但每次上游动 `_register_app_commands` 都要重解冲突）。折中方案存在——保留上游的 handler 签名，只把 `choices` 装饰器叠上去。
  - 依赖已核实存在于上游：`SkillsLoader.list_skills()`、`_get_skill_description()`、schema 里的 `model_presets`。本地用了私有方法 `_get_skill_description`，属于跨层调用，重放时值得顺手改成公开路径。
  - 新增字段要同时进 `nanobot/channels/discord/manifest.py` 的 SETUP_SPEC 和 `webui/locales/*.json`，否则 WebUI 那边不认——这是老单文件时代不存在的额外成本。

---

### 128eb335 修复 Discord 回复引用内容丢失

- 分类：[3] 纯本地（重放难度：**低**）
- 本地做了什么：discord.py +25/-4。新增 `_extract_reply_context(message)`，从 `message.reference.resolved` 取被回复消息的正文与作者，拼成引用行；给 `_compose_inbound_content` 加第三个参数 `quoted_content`。
- 上游现状：上游 `runtime.py:712 _compose_inbound_content(content, attachment_markers)` **只有两个参数**，正文里不含任何引用内容；上游对 reply 的处理仅限两处——`_build_inbound_metadata` 里塞 `reply_to` 消息 ID，以及 `_references_bot_message` 用于 mention 判定。也就是说上游**知道**这是一条回复，但**不把被回复的正文喂给模型**。`grep -n "_extract_reply_context" <upstream runtime.py>` 无命中。
- 判定理由：上游无等价实现，且这是实打实的信息丢失 bug。
- 风险/注意：改动面只有两个函数、其中一个是纯新增静态方法，`_compose_inbound_content` 的第三参数带默认值，向后兼容。是本 pack 里最干净的一笔。上游同一函数在 merge-base 之后动过（签名未变，被移进包内 runtime.py），属于纯位移。

---

### 5ceba799 feat(discord): register /dream and /dream-log as native slash commands

- 分类：[3] 纯本地（重放难度：**低**）
- 本地做了什么：discord.py +15/-1，把 `/dream`、`/dream-log <sha>` 注册成原生 slash command，转发给 `_forward_slash_command`。
- 上游现状：上游 `nanobot/command/builtin.py` 有 `cmd_dream` / `cmd_dream_log`（文本命令层已有），但上游 `_register_app_commands` 的命令元组只有 `new / stop / restart / status / history` 加 `model / trigger / help` 三个独立 handler——**没有 dream**（见上文 runtime.py:193-240 全文引用）。
- 判定理由：能力层上游有，Discord 交互层上游没有，属于纯本地的 UI 增量。
- 风险/注意：15 行，直接追加到上游 `_register_app_commands` 尾部即可，无冲突面。唯一细节：本地把 `dream`/`dream-log` 放进了 `_BUILTIN_SLASH_NAMES` 去和 skill 动态注册去重，重放时要连这个集合一起搬，否则同名 skill 会撞车。

---

## 你问的四个问题，逐条回答

### 1. 上游 discord 包架构 & 本地定制各自的落点

上游包结构（`git ls-tree -r --name-only upstream/main -- nanobot/channels/discord/`）：

- `__init__.py` — 只有一行 docstring，不做 re-export。
- `manifest.py`（27 行）— channel 元数据 / SETUP_SPEC，供 WebUI 配置表单用。
- `runtime.py`（839 行）— 全部实现：`DiscordConfig`(51) → `DiscordBotClient`(71，含 `_register_app_commands`(192) / `_forward_slash_command`) → `DiscordChannel`（`_handle_discord_message`(551) / `_should_accept_inbound` / `_download_attachments`(685) / `_compose_inbound_content`(712) / `_should_respond_in_group`(739)）。
- `validation.py` — 配置校验。
- `webui/locales/*.json` — 配置项的多语言文案。
- `tests/test_discord_channel.py` — 测试进包。

本地六项定制的落点：

| 本地定制 | 落到上游哪里 | 形态 |
|---|---|---|
| @mention 过滤（只 @ 别的 bot 就闭嘴） | `runtime.py` `_handle_discord_message` 第 646 行后插一个 guard + 两个新方法 `_resolve_bot_user_id` / `_mentions_other_bot_only` | 纯新增，~20 行 |
| 语音转写 | `runtime.py` `_download_attachments` 音频分支，调 `BaseChannel.transcribe_audio()` | **重写**，本地 30 行压到 ~8 行 |
| TTS 播报 | `nanobot/tts/` 整包原样搬 + `DiscordConfig.tts` 字段（在 runtime.py，不在 config/schema.py） | 原样搬；但见问题 3 |
| outbound metadata 传递 | `nanobot/agent/loop.py` 的 `_request_context_for_turn`（不是 MessageTool 的 `_default_metadata`） | **重写**，~5 行 |
| slash commands | `runtime.py` `_register_app_commands` + `_forward_slash_command` 扩展点；配置字段进 `runtime.py` + `manifest.py` + `webui/locales/*.json` | 骨架照抄，`/model` 需拍板 |
| 回复引用 | `runtime.py` `_compose_inbound_content` 加参 + 新增 `_extract_reply_context` | 纯新增，~25 行 |

### 2. 上游是否原生支持（逐项检索证据）

| 能力 | 结论 | 证据 |
|---|---|---|
| slash command 注册（app_commands） | **上游已有（框架层）** | `runtime.py:192 _register_app_commands`，注册 new/stop/restart/status/history/model/trigger/help；有 `_forward_slash_command` 统一转发 |
| `/model` slash | **上游已有**（自由文本参数） | `runtime.py:211 @self.tree.command(name="model")` |
| `/skill` 铺成 slash / `/tts` / `/dream` slash | **上游没有** | `grep "tree.command" <upstream runtime.py>` 只有上述 8 个；`git grep list_skills upstream/main -- nanobot/channels/` 无命中 |
| 语音转写基础设施 | **上游已有** | `nanobot/audio/transcription.py`；`BaseChannel.transcribe_audio()` |
| Discord 走转写 | **上游没有** | `git grep -n "transcribe_audio(" upstream/main -- nanobot/channels/` 命中 base/feishu/telegram/matrix/slack 等，无 discord；`git grep -n "transcribe\|audio" upstream/main -- nanobot/channels/discord/` 无命中 |
| mention 过滤（基础） | **上游已有** | `runtime.py:739 _should_respond_in_group`，`group_policy == "mention"` 分支与本地改前逐字节相同 |
| mention 过滤（只 @ 他 bot 就闭嘴） | **上游没有** | `grep "_mentions_other_bot"` EXIT=1 |
| reply 上下文 | **半有**：只有 `reply_to` ID 和「是否回复本 bot」 | `_build_inbound_metadata` 的 `reply_to`；`_references_bot_message`。被回复正文**不进 prompt**：`_compose_inbound_content` 只吃 content + markers |

### 3. TTS 可搬性

`git ls-tree -r --name-only upstream/main | grep -i tts` → **exit 1，零命中**；`git grep -ni "text.to.speech\|edge-tts\|TTSConfig" upstream/main -- nanobot/` → 空。上游确认没有 TTS，不存在平行实现。

本地 `nanobot/tts/` 六文件共 198 行，外部依赖只有：`nanobot/config/paths.get_media_dir`（上游存在）、`nanobot/config/schema.TTSConfig`（本地新增，随包一起搬）、`httpx` / `loguru`（上游已有）、`edge_tts`（本地 pyproject 独有，需补）。**没有碰任何被上游重构过的接口 → 可原样搬，改动为零。**

但必须说破一件事：`git grep -n "TTSService(\|synthesize(" -- nanobot/`，production 代码里**从未实例化过 TTSService**——只有 `tests/tts/test_service.py` 用它。`nanobot/channels/discord.py` 里 `tts` 只出现在 `DiscordConfig.tts` 字段和 import 上，send 路径没有任何合成调用；`_outbound_tts` 这个 key 也只在 `nanobot/command/builtin.py:284-290` 被读写，没有消费者。**整条 TTS 链路目前是断的**：开关能开、能关、能查状态，就是不出声。

搬之前先决定：是补上 send 路径的接线（这才是真功能），还是干脆把这 198 行 + edge-tts 依赖 + 88 行测试一起砍掉。搬一个死开关到上游新架构，是给未来每次同步都留一笔无收益的税。

### 4. 工作量估算

**不是「改导入路径即可」，也不是「全部重写」，是「70% 贴回同名函数 + 30% 按新接口重写」。**

本地 Discord 侧四笔合计 discord.py `+199/-9`。按落点拆：

- **贴回即可（约 125 行）**：`_mentions_other_bot_only` + `_resolve_bot_user_id`（20）、`_extract_reply_context` + `_compose_inbound_content` 加参（25）、slash commands 骨架（80）。依据：上游 `_should_respond_in_group` / `_download_attachments` / `_compose_inbound_content` 三个宿主函数与本地改前**逐字节相同**，只是从 `discord.py` 位移到 `discord/runtime.py`；`_forward_slash_command` 这个扩展点上游已经存在且签名一致。
- **必须重写（约 45 行 → 压成 ~15 行）**：`_transcribe_audio`（上游删了 `self.transcription_provider` 属性，改用 `BaseChannel.transcribe_audio()`）；loop.py 的 outbound metadata 传递（上游把 `MessageTool._default_metadata` 换成了 `RequestContext` / `current_request_context()`）。这两处**是净简化**，改完代码更少。
- **额外新增成本（老单文件时代没有的）**：`slash_commands`、`tts` 两个配置字段要同步进 `manifest.py` 的 SETUP_SPEC 和 `webui/locales/*.json`；测试要从 `tests/channels/test_discord_channel.py` 迁到 `nanobot/channels/discord/tests/`（本地四笔共 289 行测试）。
- **唯一需要人拍板的冲突**：`/model` 同名两实现。

粗估：Discord 侧半天到一天，其中一半时间花在测试迁址和 WebUI manifest/locales 上，而不是逻辑。TTS 若决定接线另算，若决定砍掉则为负工作量。

---

## 小结

**按能力拆分重放，禁止整笔 cherry-pick。TTS 已拍板丢弃。**

候选顺序：先重放 128eb335（回复引用，最干净、修的是真 bug）→ 再 5ceba799（/dream slash，15 行无冲突）→ 再从 2ace8c8d 只提取非 TTS 部分（mention 过滤直接贴；语音转写改调 `transcribe_audio`；outbound metadata 改写进 `_request_context_for_turn`）→ 最后处理 5132903d 的非 TTS 部分，把 `/model` 冲突单独拍板。`/tts`、TTS 配置与模块全部不进入新基座。

重放非 TTS 能力时会碰到的上游文件：

- `nanobot/channels/discord/runtime.py`（主战场，四笔全落在这）
- `nanobot/channels/discord/manifest.py`（若保留 `slash_commands`，补 SETUP_SPEC）
- `nanobot/channels/discord/webui/locales/*.json`（若保留该配置，补文案）
- `nanobot/channels/discord/tests/test_discord_channel.py`（只迁入非 TTS 测试）
- `nanobot/agent/loop.py`（`_request_context_for_turn`，outbound metadata）
- 不再碰：`nanobot/tts/`、`tests/tts/`、`TTSConfig`、`edge-tts`、`cmd_tts`。
- 不需要碰：`nanobot/channels/base.py`、`nanobot/audio/transcription.py`、`nanobot/providers/transcription.py`——转写改成消费上游现成接口即可。

一句提醒：这个 pack 里真正的隐性成本不是 Discord 那 199 行，而是**上游把 channel 变成了带 manifest + locales + 包内测试的插件**。以后每加一个本地 Discord 配置项，都要同时改四个文件。如果 haibin 打算长期跟上游走，这次重放就该按插件规范老老实实做全套，而不是只把逻辑塞进 runtime.py 了事——否则下次同步 WebUI 会先炸给你看。
