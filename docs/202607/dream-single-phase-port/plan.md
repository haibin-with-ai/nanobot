# Plan: Dream 单阶段机制移植

## 0. 工作区与边界

主仓 `/root/git_code/nanobot` 当前有 cron 相关未提交改动。本任务在独立 worktree `/root/workspace/tmp/nanobot-dream-port` 和分支 `port-dream-single-phase` 中完成，避免污染用户现有改动。

Upstream 基线：

- PR: https://github.com/HKUDS/nanobot/pull/3990
- Commit: `d1a94dae8aa29cabf6808f4abc84c5b269e29883`

## 1. 当前本地机制

本地 Dream 路径：

- `nanobot/agent/loop.py` 初始化 `self.dream = Dream(...)`
- `/dream` 调 `loop.dream.run()`
- CLI cron job 调 `agent.dream.run()`
- `Dream.run()` 读取 `.dream_cursor` 后的 `history.jsonl`，执行两阶段模板：
  - `templates/agent/dream_phase1.md`
  - `templates/agent/dream_phase2.md`
- Dream 自己创建 `AgentRunner`，自己给工具 registry，最后 Python 侧推进 cursor 并 git auto-commit。

本地增强必须保留：

- `GitStore` 追踪 `SOUL.md` / `USER.md` / `memory/MEMORY.md` / `memory/.dream_cursor`
- `/dream-log` / `/dream-restore`
- `DreamConfig.model_override` 解析 preset 的本地修复语义，迁移后如无 Dream 类，也要让 direct call 使用该 preset 或明确保留默认 agent preset。

## 2. 目标机制

引入 `dream.md` 单阶段 prompt。Dream 不再是独立处理器，而是一个普通 agent 任务：

1. 读取 `.dream_cursor` 之后最多 `max_batch_size` 条 history。
2. 构造一条 direct message，内含：
   - 当前时间
   - 待处理 entries
   - `SOUL.md` / `USER.md` / `MEMORY.md` 当前内容
   - 操作规则
   - 成功后写 `.dream_cursor = last_cursor`
3. 调用本地 `AgentLoop.process_direct(...)`，使用独立 dream session 和 `model_preset`，不使用 upstream 残留的 `system=True`。
4. 由普通 agent 工具链读写文件和 cursor。
5. `run_dream_once()` 负责 cursor 验证、GitStore auto-commit 和 session 清理。

## 3. 实现步骤

### Step 0.5: upstream 文件映射

先列 upstream commit 实际改动，并逐文件判定：

```bash
git show --name-status d1a94dae8aa29cabf6808f4abc84c5b269e29883
```

必须在实现记录中维护 mapping：

- upstream 文件
- 本地对应文件
- 操作：移植 / 手工改写 / 跳过
- 跳过原因

初步核心 mapping：

- `nanobot/templates/agent/dream.md`：移植并本地化。
- `nanobot/templates/agent/dream_phase1.md` / `dream_phase2.md`：删除或停止引用。
- `nanobot/agent/memory.py`：移植 prompt 构造、dream tool/cursor/completion helper，保留本地 GitStore 和 line-age 能力。
- `nanobot/agent/loop.py`：移除 `self.dream` 调用链，新增 Dream runtime config 与 `run_dream_once()`。
- `nanobot/command/builtin.py`：`/dream` 调 `loop.run_dream_once()`。
- `nanobot/cli/commands.py`：cron Dream branch 调 `agent.run_dream_once()`。
- tests：按本地接口重写，不整文件覆盖。

### Step 1: 引入 prompt 构造函数

在 `nanobot/agent/memory.py` 中新增轻量函数/方法，优先放在 `MemoryStore` 上，复用 upstream 语义：

```python
def build_dream_prompt(self, *, max_batch_size: int, annotate_line_ages: bool = True) -> tuple[str, int] | None:
    ...
```

逻辑：

- `last_cursor = store.get_last_dream_cursor()`
- `entries = store.read_unprocessed_history(since_cursor=last_cursor)`
- `batch = entries[:max_batch_size]`
- 空 batch 返回 `None`
- `target_cursor = batch[-1]["cursor"]`
- 渲染 `agent/dream.md`
- 返回 `(prompt, target_cursor)`

不得修改 `read_unprocessed_history` 签名；本地接口没有 `limit` 参数。

prompt 构造必须继承旧 Dream 防爆 caps：

- `MEMORY.md` 预览最多约 32k chars
- `SOUL.md` / `USER.md` 预览最多约 16k chars
- 每条 history content 预览最多约 4k chars
- prompt 明确说明预览可能截断，必要时用文件工具读取完整文件

`annotate_line_ages` 二选一，本次选择保留：把旧 `_annotate_with_ages` 逻辑迁入 prompt 构造路径，继续受 `DreamConfig.annotate_line_ages` 控制。

### Step 2: 添加 `templates/agent/dream.md`

从 upstream commit 移植，但要本地化：

- 保留对 `memory/.dream_cursor` 的明确写入要求。
- 明确只有完成三类记忆文件维护后才写 cursor。
- 加入粒度约束入口：不要从单次短会话中制造过细永久记忆；倾向合并为主题级 facts。这里只做轻量约束，不展开 haibin 专属策略。
- 输出中必须包含完成标记，供 `MemoryStore.dream_run_completed(resp)` 判断。

### Step 3: 在 `AgentLoop` 增加统一 `run_dream_once()`

新增统一 helper，供 `/dream` 和 cron 共用：

```python
async def run_dream_once(self) -> DreamRunResult:
    ...
```

流程固定：

1. 调 `self.context.memory.build_dream_prompt(...)`。
2. 无 prompt：返回 `did_work=False`。
3. 有 prompt：调用本地签名，不使用不存在的 `system=True`：

```python
await self.process_direct(
    prompt,
    session_key=MemoryStore.dream_session_key(),
    channel="system",
    chat_id="dream",
    model_preset=self.dream_model_preset,
)
```

本地 `process_direct` 真实签名只有 `content/session_key/channel/chat_id/media/on_progress/on_stream/on_stream_end/model_preset`，没有 `system`、`ephemeral`、`tools` 参数。Dream 可用工具应通过普通 tool registry 暴露；如 upstream 需要专用 dream tools，必须先确认本地是否已有等价机制，再设计，不可把不存在的参数硬塞进去。

4. 调 `MemoryStore.dream_run_completed(resp)` 判断完成标记。
5. 读取 `store.get_last_dream_cursor()`，确认等于 `target_cursor`。
6. 只有完成标记与 cursor 都正确，才报告 completed。
7. 无论成功失败，调用 `store.git.auto_commit(MemoryStore.build_dream_commit_message(...))`，但不得制造空 commit。
8. 调 `store.compact_history()` 与 `MemoryStore.prune_dream_sessions(...)`。

### Step 4: Dream config 迁移到 `AgentLoop`

删除旧 `self.dream` 依赖前，必须把配置搬到明确字段：

- `self.dream_max_batch_size`
- `self.dream_model_preset`
- `self.dream_annotate_line_ages`

`AgentLoop.from_config()` 从 `config.agents.defaults.dream` 注入这些字段。

`DreamConfig.model_override` 本次明确按 preset alias 处理，并传给 `process_direct(model_preset=...)`。不支持随意裸 `provider/model` 字符串，除非复用现有 preset 解析路径并有测试。

### Step 5: 改 `/dream`

修改 `nanobot/command/builtin.py::cmd_dream`：

- 后台调 `loop.run_dream_once()`。
- 无 prompt：发送 `Dream: nothing to process.`。
- 成功且 cursor 验证通过：发送 `Dream completed in Xs.`，带 commit sha（如有）。
- 有响应但未完成或 cursor 未推进：发送 `Dream did not complete... memory cursor was not advanced.`。

### Step 6: 改 cron Dream job

修改 `nanobot/cli/commands.py` 中 job handler：

- 从 `await agent.dream.run()` 改为 `await agent.run_dream_once()`。
- 保留 `dream_cfg.build_schedule(...)`。
- cron 不直接处理 GitStore/cursor；统一依赖 `run_dream_once()`。

### Step 7: 清理旧 Dream 调用链

删除旧 Dream 前处理全部引用：

- `AgentLoop.__init__`: `self.dream = Dream(...)`
- `AgentLoop._apply_provider_snapshot`: `self.dream.set_provider(...)`
- `builtin.py::cmd_dream`: `loop.dream.run()`
- `cli/commands.py`: `agent.dream.run()`
- tests 中的 `agent.dream` mock/断言

最终代码不保留 `self.dream` 作为配置容器；如果旧 `Dream` 类仍存在但无调用点，要么删除，要么明确只留兼容测试并计划后续删除。优先删除。

### Step 8: 测试

测试优先顺序：

1. `tests/agent/test_dream.py`
   - `build_dream_prompt` 无 entries 返回 None。
   - 有 entries 时 prompt 包含目标 cursor、entries 内容、三类文件、`.dream_cursor`。
   - history 与文件预览按 caps 截断。
   - `annotate_line_ages=True` 时 MEMORY 预览包含 age hints。
2. `AgentLoop.run_dream_once()` 测试：
   - 无 entries 不调用 `process_direct`。
   - 有 entries 调用一次 `process_direct`，传入 `session_key`、`model_preset`、dream tools。
   - 响应完成但 cursor 未推进时不报告 completed。
   - cursor 正确推进后 auto_commit 被调用。
3. command 测试：
   - `/dream` 无待处理 history 返回 nothing。
   - `/dream` 有 history 后台触发 `run_dream_once()`。
   - `/dream-log` 在 Dream auto_commit 后仍能看到记录。
4. CLI cron 测试：
   - dream job handler 触发 `run_dream_once()`，不是 `agent.dream.run()`。

运行前先证明导入的是当前 worktree：

```bash
cd /root/workspace/tmp/nanobot-dream-port
PYTHONPATH=/root/workspace/tmp/nanobot-dream-port \
/root/git_code/nanobot/.venv/bin/python -c "import nanobot, pathlib; print(pathlib.Path(nanobot.__file__).resolve())"
```

测试命令：

```bash
cd /root/workspace/tmp/nanobot-dream-port
PYTHONPATH=/root/workspace/tmp/nanobot-dream-port \
/root/git_code/nanobot/.venv/bin/python -m pytest \
  tests/agent/test_dream.py \
  tests/agent/test_dream_session.py \
  tests/cli/test_commands.py
```

## 4. 风险与回滚

风险：

- direct Dream prompt 允许模型自己写 `.dream_cursor`，如果工具被限制写 cursor，会卡住。
- 删除旧 Dream 类可能影响本地 `/dream-log` 对 GitStore 提交格式的假设。
- `model_override` 从 Dream 类迁移到 direct path 容易漏掉。

回滚：

- revert 本分支提交即可。
- 因为使用独立 worktree，不影响主仓 cron 未提交改动。

## 5. 验证出口

完成前必须给出：

- `git diff --stat`
- 关键 diff 摘要
- pytest 输出
- subagent plan review 摘要
- subagent code review 摘要
