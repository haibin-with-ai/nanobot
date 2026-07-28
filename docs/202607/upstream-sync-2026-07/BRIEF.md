# 2026-07-27 nanobot 上游同步 — 阶段一归属判定 BRIEF

## 仓库事实（已核实）

- 仓库：`/root/git_code/nanobot`（这是生产 checkout，**禁止修改任何源码**）
- remote：`origin` = git@github.com:haibin-with-ai/nanobot，`upstream` = git@github.com:HKUDS/nanobot
- merge-base：`ba38f908`（2026-05-18 那次同步定的基座）
- `upstream/main` = `3f808d0a`，领先 1173 commits
- 本地 `main` = `9ca8c42d`，领先 71 commits
- 直接 merge 会产生 43 个冲突文件

## 已知的结构性变化（上游侧）

- **channels 插件化**：`nanobot/channels/discord.py`（单文件）→ `nanobot/channels/discord/` 包
  （`manifest.py` / `runtime.py` / `validation.py` / `webui/locales/*.json` / `tests/`）。
  本地对 discord.py 的 202 行改动落在一个上游已不存在的文件上。
- 上游新增了 `nanobot/audio/transcription.py`、`nanobot/providers/xai_oauth.py`、
  `nanobot/agent/hooks/file_edit_activity.py`，schema 里有了 `fallback_models`。
- 上游**没有**：TTS 模块、ContextPruner、Anthropic Claude Code OAuth。
- `nanobot/templates/agent/dream.md` 两边都有，本地版与上游版只差 6 行。

## 任务：三分类

对分给你的每个 commit，判定为：

- **[1] 上游已吸收** — 上游已有等价或更好的实现，本地这笔可直接丢弃、改用上游。
- **[2] 平行实现** — 两边各做了一份，功能重叠但不等价，需要 haibin 拍板留谁。
- **[3] 纯本地** — 上游没有，必须重放。再标注重放难度：低（上游未动相关文件）/ 中（上游改过同文件）/ 高（上游重构了承载结构）。

## 硬规则

1. **只读**。不得修改 `nanobot/`、`tests/`、`pyproject.toml` 等任何源码；不得 checkout、reset、merge、rebase 生产分支。
   只允许 `git show/diff/log/grep/cat-file/ls-tree` 这类读操作，以及在自己的 findings 文件里写字。
2. **证据优先**。每条判定必须给出证据：上游文件路径、上游函数/类名、上游 commit hash、或明确的「upstream 侧无此实现」的检索命令与结果。
   查不到就写「未验证」，禁止推测填空。
3. 比对方法建议：
   - 看本地这笔改了什么：`git show <hash> --stat` / `git show <hash>`
   - 看上游对同一文件做了什么：`git log --oneline ba38f908..upstream/main -- <path>`
   - 看上游当前实现：`git show upstream/main:<path>`
   - 全仓检索上游：`git grep -n "<符号>" upstream/main -- nanobot/`
4. 输出写到 `/root/git_code/nanobot/docs/202607/upstream-sync-2026-07/findings/<你的文件名>.md`。

## 输出格式

每个 commit 一段：

```
### <hash> <标题>
- 分类：[1|2|3]（重放难度：低/中/高）
- 本地做了什么：一两句
- 上游现状：证据（路径 / 符号 / commit / 检索结果）
- 判定理由：一两句
- 风险/注意：可选
```

结尾加一节 `## 小结`，给出该 pack 的整体建议（整包丢弃 / 整包重放 / 逐条挑），以及重放时预计要碰哪些上游文件。
