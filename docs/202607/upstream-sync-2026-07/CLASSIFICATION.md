# nanobot 上游同步：71 commits 三分类总表

基线：`ba38f908..HEAD` 共 71 个本地非 merge commit；上游 `upstream/main=3f808d0a`。详细证据见 `findings/` 八份报告。表中「丢弃」指不把本地 commit 重放到新基座，不等于删除上游已有能力。

## 分类口径

- **[1] 上游已吸收**：采用上游当前实现，本地 commit 丢弃。
- **[2] 平行实现**：上游有重叠设计或相反策略，需按能力拆分/拍板。
- **[3] 纯本地**：上游无等价实现；不代表一律保留，仍按产品价值决定重放或砍掉。
- **混合**：一个 commit 同时含多类内容，禁止直接 cherry-pick。

## Pack A：2026-05 同步文档（14 commits）

```
commit    分类   动作
2c2d6d46  [3]   归档保留；不纳入运行时代码
47f5ce97  [3]   归档保留
a8f2dce0  [3]   归档保留
c1eb8c3d  [3]   归档保留
c230d290  [3]   归档保留
540fe8b2  [3]   归档保留
dd36ca38  [3]   归档保留
cdc3fa71  [3]   归档保留
6845ba80  [3]   归档保留
5138dc94  [3]   归档保留
5a762af1  [3]   归档保留
0661cfb4  [3]   归档保留
a7111b91  [3]   归档保留
049c185b  [3]   归档保留
```

证据：均只改 `docs/superpowers/`，上游无对应私有决策文档。内容描述 2026-05 快照，保留时应加「历史归档、非当前实现」标记。

## Pack B：Anthropic Claude Code OAuth（8 commits）

```
commit    分类       动作
9edd4f90  [3]        保留；按上游 provider API 重写
06eaa4c4  [3]        折叠进 OAuth 最终态
248d459b  [3]        折叠；静态 headers 改走 ProviderSpec.default_extra_headers
d9a16a0a  [3]        折叠；保留 Claude CLI 凭据迁移
948c2163  [3]        折叠；保留本地 flat credentials 迁移
6e035f09  [2]/[3]   Anthropic 401 刷新保留；fallback auth 分类采用上游
9e251310  [3]        保留 provider/model 前缀在解析边界剥离
182ea6b8  [3]        依赖 subagent 独立模型；若该特性砍掉，本笔随之丢弃
```

证据：上游全仓无 `anthropic_claude_code`；OAuth 只有 codex/copilot/xai。上游 `15de6be0` 已实现 auth error fallback；`ProviderSnapshot` 已取代手工 provider swap，但 spawn 尚未暴露独立 model。

## Pack C：Discord + TTS（4 commits）

```
commit    分类       动作
2ace8c8d  混合[3]   TTS 已拍板砍；mention/转写/outbound metadata 待拍板
5132903d  [2]/[3]   /tts 已砍；/model 用上游；动态 /skill 待拍板
128eb335  [3]        建议保留：回复引用正文丢失修复
5ceba799  [3]        建议保留：Discord /dream、/dream-log 原生命令
```

已拍板：**所有 TTS 都不进入新基座**，包括 `nanobot/tts/`、Discord TTS、`/tts`、TTS config、`edge-tts`、相关测试。

## Pack D/E/I：runtime、rtk hook、bootstrap（9 commits）

```
commit    分类       动作
85f47e11  [2]        身份 runtime metadata 待拍板；耗时统计建议砍
4acbefc6  [2]        forward-ref 用上游；仅按真实生产配置保必要迁移
b2972889  [1]        丢弃；旧 call site 已被上游删除
e3c39d8b  [3]        已拍板砍；不重放 CommandRewriteHook
492c9b9f  [3]        已拍板砍；不重放 rtk 子进程清理
7046af9c  [3]        已拍板砍；不重放 pipe/redirect 规则
db88223a  [2]        table hint 建议保；SOUL-first/subagent bootstrap 待拍板
78dc871d  [3]        不重放 commit；只追加仍实际需要的 ignore 规则
182893f2  混合       禁止 cherry-pick；各小修随所属 pack 处理
```

证据：上游有 `RuntimeContextProvider`，但无通用 channel/chat/sender identity；hook 签名已从返回 params 改成 context 原地修改；上游 bootstrap 仍是 AGENTS→SOUL→USER，subagent 未注入 SOUL/TOOLS。

## Pack F/G/H：subagent、上下文治理、工具（10 commits）

```
commit    分类       动作
551fe46b  [2]        TraceHook/LLM 日志已拍板砍；独立 model/provider 待拍板
409a3929  [3]        spawn timeout 测试已拍板砍；model 参数测试随独立模型
cb1dadfa  [1]        丢弃；上游 runtime 结构消除了该失败模式
58cd1135  [1]        丢弃；用上游 ProviderSnapshot/LLMRuntime
c41b8717  [1]        丢弃；上游 cfabc29f 已修同一并发上限 bug
ced62a8c  [2]        已拍板丢弃；采用上游 context_governance
057b23ad  混合       ripgrep 部分已回滚；spawn timeout 已拍板砍；其余测试择取
862cf645  [1]        丢弃；search.py 整取上游
45f75cb2  [2]        已拍板保留关闭去重；在上游实现上默认 force=True
e0e86179  [3]        保留；line_ages 用原生 git blame
```

## Pack J1：fallback 与流式稳定性（10 commits）

```
commit    分类       动作
09fbdc4a  [1]        丢弃；采用上游 hash-safe tool id 清洗
3419e4d8  [1]        丢代码，保事故注释/回归意图
8b3dc7ce  [1]        丢弃；上游 base retry + segment recover 更完整
218be2cc  [3]        Phase 1 砍；Phase 2/3 是否保留待拍板/故障注入
 d5fa553c [3]        改写为新版恢复协议测试
b45ee3df  [3]        建议必须保：stall 后重建 Anthropic client
070d66c5  [1]        丢弃；上游已支持流中 stall 后 failover
e72440f1  [2]        refusal 是否触发 fallback，策略待拍板
ecceb97b  [1]        丢弃；上游 immutable LLMRuntime 结构性覆盖
c157b38d  [3]        建议必须保：quota 按模型跨请求冷却
```

## Pack J2：Anthropic 新模型 + 模型切换（9 commits）

```
commit    分类       动作
d61aca5d  [3]        保留 adaptive thinking 最终态
76c43718  [3]        与上一笔折叠，移除 budget_tokens
1df48517  [2]        融合：保留抽象，同时补上游 fable 覆盖
caf407e1  [3]        必须保：Opus 5 API 参数
c1c0aef0  [3]        与 Opus 5 最终态折叠
9ca8c42d  [3]        与 Opus 5 折叠；保 anthropic>=0.120.0
ecceb97b  [1]        与 J1 重叠记录；只计一次 commit
f76609cb  [1]        丢弃；上游 runtime 与测试已自证
aa21c8ce  混合       per-session /model 用上游；per-job cron model 随 cron 拍板
```

注：`ecceb97b` 在 J1/J2 报告里交叉分析，71 总数只计一次。

## Pack K：cron + Dream（8 commits，2 笔与其他 pack 交叉）

```
commit    分类       动作
d6e49fdf  [2]        cron 独立 session vs 上游绑定 session，待拍板
55b46a2f  [1]        丢弃；upstream d1a94dae 后又演进 29 笔
e7545114  [1]        丢弃；测试采用上游
03b44175  [3]        历史文档归档，不作代码依据
c431a7df  [3]        必须保：/new 清理 session model override
0d7d9439  [1]        丢弃；上游 f0c989ba 已吸收且更新
0928d8d9  [1]        丢弃；上游 runtime resolver 已结构性覆盖
67bd27c2  [2]        与 d6e49fdf 捆绑；per-job model 是否保留待拍板
```

Dream 主体采用上游，只摘本地三样增量：memory 行龄标注、`dream.md` 7 行独有约束、对应测试。

## 已拍板项（2026-07-27）

1. spawn timeout：砍。
2. TraceHook / LLM req-resp 日志：砍。
3. TTS（含 Discord）：全砍。
4. ContextPruner：砍，采用上游 context governance。
5. read_file：保留「默认关闭去重、总返回全文」，在上游代码上最小修改。
6. rtk command rewrite：砍；不重放 hook、配置、子进程逻辑与测试。

7. 剩余五组全部保留并实现：subagent 独立模型/provider、Discord 全部非 TTS 能力、runtime/bootstrap（含耗时统计）、fallback 全部策略、cron per-run session + per-job model。

阶段一到此结束：71/71 有分类与上游侧证据，全部条目已拍板。详细拍板文本与实现约束见 `DECISIONS.md`，重放规格见 `PHASE2-SPEC.md`。

## 阶段二入口

新基座隔离 worktree 已建立，生产 checkout 不参与 merge：

```
/root/git_code/nanobot/.worktrees/sync-2026-07   分支 sync-upstream-2026-07   基于 upstream/main=3f808d0a
```
