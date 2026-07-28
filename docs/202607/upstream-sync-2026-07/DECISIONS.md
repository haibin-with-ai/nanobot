# 2026-07-27 haibin 拍板记录

## 已拍板

1. **spawn timeout 暂不保留。**
   - 对应：`057b23ad` 中的 spawn `timeout_seconds` 与 `409a3929` 中只为该参数调整的测试部分。
   - 同步动作：不重放；采用上游当前 spawn 行为。

2. **TraceHook 暂不保留。**
   - 对应：`551fe46b` 中 `TraceHook` / hook context model / LLM 请求响应日志部分。
   - 同步动作：不重放这部分。
   - 注意：`551fe46b` 还混有「subagent 独立 model/provider」能力，须单独判定，不能因 TraceHook 被砍而整笔丢弃。

3. **TTS 全部暂不保留，包括 Discord 相关 TTS。**
   - 对应：`2ace8c8d` 中 `nanobot/tts/`、Discord TTS 配置与播报路径；`5132903d` 中 `/tts` slash command 及相关开关。
   - 同步动作：不重放 TTS 模块、TTS 配置、TTS 命令、TTS 测试。
   - 注意：`2ace8c8d` 还混有 mention 过滤、语音转写、outbound metadata；`5132903d` 还混有 `/skill`、`/model`，须拆分判定。

4. **ContextPruner 直接采用上游方案。**
   - 对应：`ced62a8c`。
   - 同步动作：整笔不重放，不创建本地 `nanobot/agent/pruner.py` / `context_pruning` 配置；采用上游 `nanobot/agent/context_governance.py` 的 `compact_inflight_overflow()` 与现有 autocompact 体系。

5. **read_file 继续关闭去重。**
   - 对应：`45f75cb2`。
   - 同步动作：保留本地语义——重复读取仍返回完整内容，不返回 `[File unchanged...]` 桩。
   - 实现约束：不要回放旧 diff；在上游新实现上做最小改动。优先保留上游完整 dedup / 哈希 / `force` 代码，只把默认行为改成返回全文（预计为将 `force` 默认值改为 `True`，实现时以测试确认实际语义）。

6. **rtk 命令改写暂不保留。**
   - 对应：`e3c39d8b`、`492c9b9f`、`7046af9c`，以及 `182893f2` 中 rewrite hook 的小修。
   - 同步动作：不重放 `CommandRewriteHook`、`commandRewrite` 配置、rtk 子进程管理与相关测试；采用上游当前 `exec` 行为。

7. **剩余五组全部保留并实现（2026-07-27 拍板）。**

   1. **subagent 独立 model/provider**：保留。`551fe46b` 的该能力 + `409a3929` 的 model 参数测试 + `182ea6b8` 的 OAuth 依赖项一并按上游 `ProviderSnapshot` / `LLMRuntime` 重写。
   2. **Discord 非 TTS 能力**：全部保留。@mention 过滤与防串线、语音转写（改调上游 `transcribe_audio`）、outbound metadata、回复引用正文修复、动态 `/skill`、`/dream` 与 `/dream-log`。`/model` 采用上游 per-session 实现，不重放本地版本。
   3. **runtime / bootstrap**：全部保留。通用 channel/chat_id/sender_name/current-time 身份块（改注册为上游 `RuntimeContextProvider`）、SOUL-first 加载顺序与 soul anchor、subagent 注入 SOUL.md + TOOLS.md、Discord 禁用 pipe table 的 format hint、`AgentRunResult` 的 `elapsed_ms` / `llm_elapsed_ms` 耗时统计。
   4. **fallback 策略**：全部保留。refusal 触发 fallback（与上游 `_NON_FALLBACK_ERROR_KINDS` 含 `"refusal"` 的策略相反，需显式覆盖）、三阶段 stall 超时的 Phase 2/3、stall 后重建 Anthropic client、按模型的 quota 冷却。
   5. **cron 架构**：保留 fork 语义——每次触发独立 session + per-job `model` 字段（默认 `deep`）。

   关于 cron 的补充说明：这一项与上游是正面对撞，不是叠加。上游 `bound_runner.run_bound_cron_job` 对缺少 `payload.session_key` 的 job 直接抛 `ValueError`，把 cron 当作原会话内的一次普通 turn；fork 要的是无状态隔离会话。因此重放时必须在上游 `bound_runner` 上显式支持「未绑定 job → per-run session」这条分支，而不是删掉上游绑定路径。两种模式并存：显式绑定 session 的 job 走上游语义，未绑定的走 fork 的 per-run 隔离。

   **附带强制项**：fork 的 per-run 方案至今没有回收逻辑。实测 `/root/workspace/sessions` 有 177 个 `cron_*.jsonl`、合计 6.4M，最早 06-30、最新 07-27，即近一个月内单调累积、无清理。本次重放必须同时实现 per-run cron session 的回收（保留窗口 + 清理入口），否则等于把一个已知泄漏原样搬到新基座。

   per-job 模型字段的最终形态以本地 `nanobot/cron/types.py:32` 为准：`model: str | None = None`，`None` 落到 cron 默认 `deep`。不要重演 `preset → model` 的改名史。

## 当前仍待核实（不阻塞重放）

- 上游 `submit_cron_turn` 的会话增长控制阈值（retention / auto-compact）未核实；因为已选 fork per-run 语义，此项降级为参考信息。
- 旧配置中 `channels.discord.tts`、`agents.defaults.contextPruning` 等已砍字段在上游 schema 下是否被 extra-ignore，需在重放时用生产 `config.json` 实际加载验证。
