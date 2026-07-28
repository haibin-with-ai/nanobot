# 阶段二规格：以 upstream/main 为新基座重放 fork 能力

## 1. 目标与边界

在隔离 worktree 中以 `upstream/main=3f808d0a` 为新基座，重放 fork 仍需保留的能力，产出一个可替换当前 `main` 的分支。

- 工作区：`/root/git_code/nanobot/.worktrees/sync-2026-07`，分支 `sync-upstream-2026-07`。
- 生产 checkout `/root/git_code/nanobot` 在切换前不得出现未完成 merge 或冲突标记。
- 重放按**能力**组织，不按 commit 回放；禁止 `git cherry-pick` 混合提交。
- 每个能力单独 commit，中文 commit message，只 stage 该能力涉及的文件。

## 2. 必须保留的能力（12 项）

```
#   能力                          上游落点                                   来源
A1  Anthropic Claude Code OAuth   providers/ 新增 provider + oauth_store      9edd4f90 等 8 笔
A2  subagent 独立 model/provider  ProviderSnapshot / LLMRuntime / spawn 参数   551fe46b, 409a3929, 182ea6b8
A3  read_file 关闭去重            tools 中 dedup 默认 force=True               45f75cb2
A4  gitstore 原生 git blame       line_ages 实现                              e0e86179
A5  Discord mention 防串线        channels/discord/runtime.py                  2ace8c8d
A6  Discord 语音转写              接上游 audio/transcription.py                2ace8c8d
A7  （已并入 A10）                agent/loop.py _request_context_for_turn      2ace8c8d
A8  Discord 回复引用正文修复      channels/discord/runtime.py                  128eb335
A9  Discord 动态 /skill + /dream  slash 注册 + manifest/locale                 5132903d, 5ceba799
A10 runtime 身份块 + 出站 metadata RuntimeContextProvider + AgentRunResult     85f47e11, 2ace8c8d
A11 bootstrap SOUL-first + anchor ContextBuilder + subagent + identity 模板    db88223a
A12 fallback 四项 + cron 架构     fallback_provider / anthropic / cron          见下
```

A12 拆细：

- refusal 触发 fallback：覆盖上游 `_NON_FALLBACK_ERROR_KINDS` 含 `"refusal"` 的策略。
- 三阶段 stall 超时的 Phase 2/3。
- stall 后重建 Anthropic client（`b45ee3df`）。
- 按模型 quota 冷却，含 `retry_after` 跨请求跳过（`c157b38d`）。
- cron：未绑定 job 走 per-run 独立 session；`CronPayload.model: str | None`，`None → deep`。
- cron per-run session 回收（保留窗口 + 清理入口）——本次新增，非旧 commit 回放。
- `/new` 清理 session model override（`c431a7df`）。
- Anthropic Opus 5 参数与 adaptive thinking 最终态（`caf407e1`+`c1c0aef0`+`9ca8c42d`、`d61aca5d`+`76c43718`），`anthropic>=0.120.0`。
- `1df48517` 的模型能力判定辅助函数，需融合上游新增的 `fable` 覆盖。
- Dream 三样增量：memory 行龄标注、`dream.md` 独有约束、对应测试。
- `.gitignore` 追加仍实际产生的本地生成物规则，保留上游规则。

## 3. 明确不重放

TTS 全线、spawn `timeout_seconds`、TraceHook 与 LLM 请求响应日志、本地 `ContextPruner`、rtk 命令改写全线、`_make_skills_loader` workspace 修复、Pydantic forward-ref 修复、本地 `/model` 实现、search.py 本地改动、以及所有已被上游吸收的 fallback/tool-id/并发上限修复。

配置侧同步删除：`Config.tts`、`TTSConfig`、`ContextPruningConfig`、`AgentDefaults.context_pruning`、`ToolsConfig.command_rewrite`。

## 4. 验收标准

1. `git status --porcelain` 在 worktree 干净，无冲突标记。
2. 全量 `pytest` 通过；A1–A12 每项至少一条针对性测试，且测试先于实现（TDD）。
3. 生产 `config.json` 能被新基座加载，已删字段不导致启动失败。
4. `grep` 确认无遗留：`tts`、`ContextPruner`、`commandRewrite`、`rtk`、`TraceHook`。
5. 切到 main 后重启 `nanobot-gateway`，健康检查通过，Discord 实际收发一轮验证。
6. 回滚路径：切换前对当前 `main` 打 `backup/2026-07-27-pre-upstream` 标签。

## 5. 风险

- **A12 是最大风险块**：fallback 与 cron 都踩在上游反复重构过的路径上，且策略与上游相反，未来每次同步都会再撞一次。需在代码注释里写明「与上游策略相反」的原因，降低下次同步误删的概率。
- **A11 影响全局行为**：bootstrap 顺序变化会改主 agent 与 subagent 的实际表现，测试只能验证注入内容，不能验证行为质量；切换后需人工观察一轮。
- **A2 与 A1 耦合**：subagent 独立模型依赖 OAuth provider 解析，需先做 A1。
- 上游已把渠道改为自包含插件包（`462a0dfb`），本地 `channels/discord.py`（1002 行单文件）对应上游 `channels/discord/` 包（`runtime.py` 839 行 + `manifest.py` + `validation.py` + `tests/` + `webui/`）。A5、A6、A8、A9 的钩子原先插在旧单文件的具体位置，落点需逐一重找。

补充事实（已验证，供重放时省去重复调研）：

- 上游 `_should_respond_in_group` 已含 @ 判定与 `_references_bot_message`，这两条不必重放；fork 独有的只是 `_mentions_other_bot_only` + `_resolve_bot_user_id`。
- 上游 `nanobot/audio/transcription.py` 已存在，且 feishu / matrix / telegram / websocket / wecom 五个 runtime 均已接入，唯独 Discord 未接。A6 应接上游能力，不要搬本地 `_transcribe_audio`。
- 上游 Discord slash 仅 `/model`、`/trigger`、`/help`；dream 系列命令仅在 `channels/telegram/runtime.py:436-439` 以 `BotCommand` 注册，Discord 侧没有。动态 `/skill` 上游完全没有。
