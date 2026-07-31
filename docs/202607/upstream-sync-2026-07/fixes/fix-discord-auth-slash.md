# fix-discord-auth-slash

范围：`nanobot/channels/discord/runtime.py`、`nanobot/channels/discord/tests/test_discord_auth_gate.py`（新增）、
`nanobot/channels/discord/tests/test_discord_channel.py`（补断言）。

审查来源：`reviews/coding-review-discord.md` 的 I2、I3、I4。

## I2 未授权消息先取数后鉴权（已修）

`_handle_discord_message` 过去只用 `_should_accept_inbound` 挡住来自服务器频道的陌生人，
私聊则一路放行到 `_download_attachments` 与 `_build_quoted_context`，再加 typing 与表情回执，
最后才由 `BaseChannel._handle_message` 发一个配对码。也就是说任何陌生人给机器人发一条带附件的
私信，都能让它先下载文件、再拉引用消息、再点两次表情，全部发生在鉴权之前。

修法：在 `_should_accept_inbound` 之后补一道 `is_allowed` 闸门，未授权者直接以原始文本走
`_handle_message`，配对码照发，附件下载、引用抓取、typing 与表情一概不做。一个分支，无嵌套。

红灯（改实现之前）：

```
uv run --frozen pytest -q nanobot/channels/discord/tests/test_discord_auth_gate.py::test_unauthorized_dm_is_rejected_before_payload_loading
E  AssertionError: Expected mock to not have been awaited. Awaited 1 times.
   Failed to add read receipt reaction: 'types.SimpleNamespace' object has no attribute 'add_reaction'
   Generated pairing code WA9V-QUWX for 42@discord
1 failed in 0.94s
```

绿灯：

```
uv run --frozen pytest -q nanobot/channels/discord/tests/test_discord_auth_gate.py::test_unauthorized_dm_is_rejected_before_payload_loading
1 passed in 0.94s
```

变异验证（把闸门条件改成 `if False:` 后复跑，恢复后再跑）：

```
1 failed, 1 passed in 0.83s
FAILED test_unauthorized_dm_is_rejected_before_payload_loading
```

## I3 被拒 slash 命令仍回 `Command accepted.`（当前代码已不存在，附证据）

按报告描述去找落点，结果对不上：`Command accepted.` 这个字符串在 `nanobot/` 整个包里搜不到，
只出现在新补的断言里。所有 slash 入口（`_forwarder`、`_arg_forwarder`、内置命令、技能命令）都汇到
`_forward_slash_command`，它在未授权时回 `You are not allowed to use this bot.` 并直接 return，
既不 publish 也不继续。

因此本轮不改实现，只把行为钉死为回归测试：`test_discord_channel.py` 的
`test_slash_rejection_never_sends_acceptance_reply` 断言拒绝路径的回复恰好只有那一条拒绝语，
`test_discord_auth_gate.py` 的 `test_rejected_slash_answers_denial_and_never_publishes`
另外断言 `bus.publish_inbound` 未被 await。两条均绿。

原始报告里那条 `outcome == "denied"` 的断言被我删掉了：为了测试给 `_forward_slash_command`
发明一个返回值协议，是让测试反过来定义接口，不是验证行为。

## I4 slash 拒绝分支缺真实断言（已修）

见上一节的两条测试，都不是只验命令树形状，而是真的走 `command.callback(interaction)`
并检查回复内容与 bus 调用。

## 验证

```
uv run --frozen pytest -q nanobot/channels/discord/tests
110 passed, 1 warning in 1.18s

uv run --frozen ruff check nanobot/channels/discord/runtime.py nanobot/channels/discord/tests/test_discord_auth_gate.py
All checks passed!
```

## deferred

- M1 `on_message()` 125 行入站总控按职责拆分（鉴权、防串线、引用正文、附件转写）：本轮只加了一道
  前置闸门，没做整体拆分。拆分会大面积移动这个文件，与正在进行的生产切换评估叠加风险太高，
  留到切换之后单独做。
- M2 测试文件重复 setup 抽公共 fixture、以及 `test_discord_channel.py` 的整体拆分：同上，本轮不做。
