# Discord 与 bootstrap 改动审查

结论：没有发现可直接造成越权或数据损坏的 Critical 问题，但有 4 个 Important。最硬的 bug 在 bootstrap：同一份默认 `TOOLS.md` 会因工作区创建时间不同，分别变成“完全不进 prompt”与“拿旧版本进 prompt”。Discord 的主要问题则是把鉴权放在网络取数之后，并让 slash 命令在拒绝访问时谎报成功。

## Critical

无。

## Important

### [I1] 默认 `TOOLS.md` 的新增规则对新工作区无效，对旧工作区却会注入过期版本

**位置：** `nanobot/agent/context.py:185-210`，`nanobot/templates/TOOLS.md:1-28`

**问题本质：** `load_bootstrap_files()` 把 `TOOLS.md` 放进 `_SKIPPABLE_DEFAULTS`，只要工作区文件与当前 bundled template 相同，就整份跳过。于是本次模板新增的工具规则在新工作区中根本不会进入 system prompt。更糟的是，升级前已复制到工作区的旧默认 `TOOLS.md` 不再等于新的 bundled template，会被误判为用户定制并注入 prompt。行为由工作区创建时间决定，这是典型的隐式版本状态，不是配置。

这相当于用“是否等于今天的出厂值”判断一台机器有没有被用户改装。昨天出厂且从未动过的机器，今天会被判成改装机。

**最小修复方向：** 不要用当前模板内容承担版本迁移。若默认 `TOOLS.md` 本来就不应进 prompt，就不要把行为规则放进该模板；把规则放到真正加载的 system prompt 片段。若它应当加载，则移出 `_SKIPPABLE_DEFAULTS`，并给模板文件引入显式版本或生成标记，迁移时只升级可证明未被用户修改的旧默认文件。

### [I2] 未授权消息先触发 Discord API 取数，鉴权顺序反了

**位置：** `nanobot/channels/discord/runtime.py:360-392`

**问题本质：** `on_message()` 在调用 `_handle_message()` 做 allowlist / pairing 校验之前，先执行 `_build_reply_context()`；后者在缓存未命中时会调用 `fetch_message()`。任何能让 bot 看见消息、但不在 allowlist 的用户，都可以通过回复消息持续制造额外 Discord API 请求。授权失败只阻止后续附件下载和入站发布，阻止不了前面的网络开销。

**最小修复方向：** 在任何引用消息查询、附件读取和内容扩展之前算出 `authorization_id` 并完成授权判断。更干净的做法是让 `BaseChannel` 暴露一个无副作用的授权判断入口，Discord 入站只在通过后构建 reply context；DM pairing 作为拒绝分支单独发送。

### [I3] 被拒绝的 slash 命令仍回复 “Command accepted.”

**位置：** `nanobot/channels/discord/runtime.py:346-359`，`nanobot/channels/discord/runtime.py:382-399`

**问题本质：** `_forward_slash_command()` 在 defer 后调用 `_handle_message()`，但 `_handle_message()` 对未授权请求只发送 pairing 或记日志，然后返回 `None`。调用方无法区分“已发布入站消息”和“已拒绝”，因此无论结果如何都发送 `Command accepted.`。在 guild 中，未授权用户甚至只会得到这句成功提示，实际命令静默丢弃。

**最小修复方向：** 让入站处理返回明确结果，例如 `accepted / pairing_required / denied`，slash 层按结果回复；不要靠 `None` 同时表达成功和拒绝。若不想改基类接口，至少在 defer 前按与 `_handle_message()` 完全相同的授权主体做一次判断，并让拒绝路径直接结束。

### [I4] 新测试只验证命令树形状，恰好漏掉 slash 拒绝分支的真实 bug

**位置：** `nanobot/channels/discord/tests/test_discord_channel.py:2030-2081`

**问题本质：** `TestBuiltinSlashCoverage` 验证了命令名、参数数量和字符串拼接，却把 `_forward_slash_command` 整体替换成 `AsyncMock`。这证明了菜单长什么样，没有证明一次真实 interaction 如何经过 defer、鉴权、bus publish 和 followup。结果是 [I3] 这种用户可见错误在测试全绿时照样存在。测试替身切在被测行为的正中央，测到的是 mock 调用，不是 slash 分支。

**最小修复方向：** 保留一组命令表契约测试，再增加最薄的端到端分支测试：使用真实 `_forward_slash_command()`，只替换 Discord response/followup 与 bus；覆盖允许、guild 拒绝、DM pairing、publish 异常四条路径，并断言发布次数和最终用户回复。

## Minor

### [M1] `on_message()` 已膨胀为 125 行入站总控，嵌套和职责都越线

**位置：** `nanobot/channels/discord/runtime.py:343-467`

**问题本质：** 一个函数同时做 bot/self 过滤、slash 文本拦截、mention 规则、thread 判定、reply 拉取、附件落盘、身份 metadata、session key 和 bus 发布。它远超 20 行，分支嵌套达到并超过 3 层；更关键的是，鉴权顺序 bug 正是这些职责揉在一起后的产物。`runtime.py` 也已到 1079 行，超过 800 行边界。

**最小修复方向：** 不要做抽象框架。只切三刀：`_classify_inbound()` 负责过滤与 room/session，`_load_authorized_payload()` 负责鉴权后的 reply/附件，`_publish_inbound()` 负责 metadata 与发布；`on_message()` 留成顺序清楚的编排函数。

### [M2] 测试文件继续堆叠局部假对象与重复 setup，已失去可维护边界

**位置：** `nanobot/channels/discord/tests/test_discord_channel.py:1430-2081`

**问题本质：** 文件已达 2081 行。新增区域反复创建 `DiscordChannel`、`DiscordBotClient`、`SimpleNamespace` interaction/message，并在多个测试里重新拼 response/followup 行为。假对象没有统一契约，少一个 `guild`、`channel_id` 或 response 状态就可能让测试走进现实中不存在的分支。重复 setup 不是几行代码难看，而是每个测试都在私自定义一版 Discord。

**最小修复方向：** 把新增能力测试拆到独立文件，例如 `test_discord_slash.py`、`test_discord_inbound.py`；提供一个最小但字段完整的 interaction/message factory 和共享 channel/client fixture。不要再扩展万能 `SimpleNamespace`，只保留每类 Discord 对象的一份测试替身。

## 结构判断

`nanobot/channels/manager.py:146-185` 的 dispatcher 生命周期调整和 `nanobot/webui/ws_http.py:656-662` 的启动顺序改动，本次未看到独立的真实 bug。它们把 outbound dispatcher 放到 channel start 之前、stop 之后，顺序有明确资源依赖，算得上可解释。

`nanobot/agent/context.py:68-172` 的 system prompt 构建仍靠 append 顺序表达优先级，已经出现 runtime metadata、identity、bootstrap、memory、skills 的隐式协议。本次最小修复不必重构整条链，但后续再插一个 prompt 区块前，应先把顺序定义成具名阶段；继续靠“哪一行先 append”维持语义，只会再造一个 `TOOLS.md` 式版本虫。
