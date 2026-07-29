# 只读复审：Discord 渠道 / 命令层 / bootstrap prompt / 身份注入

审查范围：`git diff 3f808d0a...HEAD`（36 个提交，HEAD = dcb926b4，分支 sync-upstream-2026-07，worktree `/root/git_code/nanobot/.worktrees/sync-2026-07`）中与 Discord 渠道、命令层、bootstrap、runtime 身份注入、耗时统计相关的部分。全程只读，未做任何写操作与 git 变更。

## 结论

没有阻断项。两条重要问题：Discord slash 命令没有按既定方案从 `BUILTIN_COMMAND_SPECS` 派生，导致 6 个内建命令在 Discord 上不存在、带参命令退化成无参；SOUL 锚点读的工作区和 bootstrap 读 SOUL.md 的工作区不是同一个，项目态运行时锚点会消失或串味。其余五个重点项（闭包捕获、命令冲突、可选依赖、身份注入落点、ContextVar 计时、TTS 清除）核对通过，证据见下。

---

## 重要

### 1. Discord slash 命令仍是手写清单，未从 BUILTIN_COMMAND_SPECS 派生

证据：`nanobot/channels/discord/runtime.py:210-228`

```python
def _register_app_commands(self) -> None:
    commands = (
        ("new", "Stop current task and start a new conversation", "/new"),
        ...
        ("dream", "Consolidate recent conversations into memory", "/dream"),
        ("dream-log", "Show recent Dream consolidation activity", "/dream-log"),
    )
```

对照 `nanobot/command/builtin.py:119-178` 的 `BUILTIN_COMMAND_SPECS`（共 15 条，每条自带 `description` 与 `accepts_args`）。计划 Task 5.4 要求「从 BUILTIN_COMMAND_SPECS 派生 + 按 accepts_args 分流」，实现改成把 dream / dream-log 手工塞进硬编码元组。后果有三层：

- 缺席：`dream-restore`、`dream-prompt`、`evaluator-prompt`、`skill`、`goal`、`pairing` 六条内建命令在 Discord 上没有 slash 入口（它们仍能靠打字触发，但 UI 里查不到）。
- 退化：`history`、`dream-log` 在 specs 里 `accepts_args=True`，这里注册成纯无参 `_forwarder`，用户无法通过 slash 传参。
- 双份维护：描述文案在 builtin.py 和 discord runtime 各写一遍，上游改动会静默漂移。

顺带同源：技能命令 `runtime.py:333` 固定转发 `/{original_name}`，同样不带参数，`khb-ask`、`ljg-fetch` 这类要参数的技能只能空转。

最小修复方向：遍历 `BUILTIN_COMMAND_SPECS`，`accepts_args=False` 走现有 `_forwarder`，`accepts_args=True` 生成带 `args: str | None = None` 的 callback 拼 `f"/{name} {args}"`；`model` / `trigger` 若想保留 `describe` 文案，用一张覆盖表而非另起分支。技能命令同理加可选 `args`。

### 2. SOUL 锚点与 bootstrap 的 SOUL.md 取自不同工作区

证据：`nanobot/agent/context.py:82`（`root = workspace or self.workspace`）→ `:124`（`anchor = self._build_soul_anchor(root)`）→ `:129`（`soul_path = (workspace or self.workspace) / "SOUL.md"`），而 bootstrap 那边 `:184-193` 明确写死 `"SOUL.md": self.workspace`。

`RunSpec.workspace` 存在项目态取值（`nanobot/agent/runner.py` 按 spec 传入 `build_messages(workspace=...)`），一旦项目工作区 ≠ agent 工作区：头部注入的是 agent 的 SOUL.md，尾部锚点却去项目目录找 SOUL.md，通常不存在（锚点静默消失），存在时则锚了另一份人格。这正是「锚点」设计要防的那种静默失效。

最小修复方向：`_build_soul_anchor()` 不接收 workspace，或调用处改成 `self._build_soul_anchor(self.workspace)`，与 bootstrap 的取值表保持单一真源。

---

## 建议

### 3. manager 里出现 Discord 特判

证据：`nanobot/channels/manager.py:154-157`

```python
if cls.name == "discord":
    kwargs["workspace"] = self.config.workspace_path
    kwargs["disabled_skills"] = set(...)
```

同文件已有 `websocket` 特判，属既有风格，不算新债；但通道要用工作区是通用需求，长期应走 BaseChannel 统一构造参数而不是按名字发糖。类型没问题：`workspace_path` 返回 `Path`（config/schema），`SkillsLoader` 拿到的是 Path。

### 4. TOOLS.md 未纳入 `_SKIPPABLE_DEFAULTS`，与子代理行为不一致

证据：`nanobot/agent/context.py:56-57`（`BOOTSTRAP_FILES` 加了 TOOLS.md，`_SKIPPABLE_DEFAULTS` 仍只有 AGENTS/USER）对比 `nanobot/agent/subagent.py:535-538`（模板原样即跳过）。主 agent 会把没改过的 bundled TOOLS.md 全文塞进系统提示，子代理不会。要么统一跳过，要么统一注入。

### 5. 每轮耗时在多次 run 的回合里是「最后一次覆盖」

证据：`nanobot/agent/loop.py:1064` `if (stats := _turn_run_stats.get()) is not None: stats.update(...)`。一个回合内若发生多次 `runner.run()`（续跑、pending 合并），统计不是累加而是最后写入者胜出，展示的耗时会偏小。若语义就是「最后一次 run」，值得在注释里写死。

### 6. `elapsed_ms` 把 after_run 钩子算进去了

证据：`nanobot/agent/runner.py:380` 的赋值发生在 `after_run` 钩子之后。数值口径是「整轮墙钟含收尾钩子」，与 `llm_elapsed_ms` 的纯模型等待并列时容易被误读为模型外开销全是自身逻辑。

### 7. 时区串格式

证据：`nanobot/agent/identity_context.py` 输出形如 `UTC+0800`，非 `UTC+08:00`。无害，仅提一句。

---

## 已核对无问题

**闭包捕获（重点 2）**：`runtime.py:277-286` 用 `_forwarder(command_text)` 工厂返回 handler，逐次绑定，无循环变量晚绑定；注释也点明了动机 —— 不用默认参数捕获，避免默认值泄进 Discord 的命令 schema（旧写法 `_command_text: str = command_text` 会被 discord.py 解析成命令参数）。技能命令 `:332` 复用同一工厂。

**命令名冲突（重点 2）**：`runtime.py:317-319` 的 reserved 集合 = 已注册 tree 命令名 ∪ `_builtin_command_names()`；后者（`:45-52`）现场构造 CommandRouter 跑 `register_builtin_commands` 再取 `router.command_names()`（`nanobot/command/router.py`，本次 +6 行的通用小 helper，返回去掉前导 `/` 的名字），因此技能不可能盖掉任何路由器命令，包括未做成 slash 的那六条。循环内 `:342` 还会 `reserved.add(command_name)`，`a_b` 与 `a-b` 归一化撞名也被挡住。`:319` 的 `slots = 100 - len(tree.get_commands())` 在循环外算一次，保证总数不越 Discord 全局上限。名字合法性 `:288-296` 走 `_SKILL_COMMAND_CHARS` 白名单 + 32 字符上限，非法直接跳过。

**可选依赖（重点 2）**：`runtime.py:32-35` 的 `import discord / app_commands` 全在 `if DISCORD_AVAILABLE:` 内，`DiscordBotClient` 类体本身也在 `if DISCORD_AVAILABLE:`（`:86`）下；`SkillsLoader` / `BUILTIN_SKILLS_DIR` 是 nanobot 内部模块，无条件导入无风险。技能加载整段包在 `try/except Exception` 里（`:304-311`），加载失败只 warning 不影响客户端启动。

**bootstrap 段落完整性与顺序（重点 3）**：`git diff -- nanobot/agent/context.py` 全量只有 35+/8-，改动仅限 `BOOTSTRAP_FILES` 顺序（`SOUL → AGENTS → USER → TOOLS`）、`_load_bootstrap_files` 的取值表、以及尾部锚点函数。上游的 `tool_contract.md`、`# Memory`、`# Active Skills`、`skills_section.md`、`# Recent History`、`[Archived Context Summary]` 六段（`context.py:89-120`）逐段保留、顺序未动，没有漏段。锚点追加在全部段落之后（`:122-126`），只截取 `## Prime Directive` 到下一个二级标题，缺失时返回空串，不会硬塞。

**子代理继承（重点 3）**：`subagent.py:519-560` 保留上游 `agent/subagent_system.md` 模板渲染不动，之后追加 `SOUL.md` + `TOOLS.md`（顺序与主 agent 一致），profile 文件从 `self.workspace`（agent 工作区）读，未被项目工作区污染，且与模板同内容时跳过。

**身份注入落点（重点 4）**：`git diff --name-only` 中没有 `nanobot/runtime_context.py`，通用机制文件零改动；新增 `nanobot/agent/identity_context.py` 只消费其公开 API；注册发生在 `nanobot/agent/loop.py` 的 `from_config` 装配处，通过 `register_runtime_context_provider()` 挂入。Discord 侧只负责喂元数据（`runtime.py:269-282` 的 `_build_inbound_metadata` 填 `sender_name` / `channel_name`），身份链路里没有渠道特判。

**ContextVar 计时（重点 5）**：`runner.py:127` `_llm_timing: ContextVar[...]`，`:348` 在 `run()` 内 `set`，`:383-384` 在 `finally` 里 `reset(token)`，不挂共享实例，并发回合互不串扰（注释亦点明动机）。递归重复计时由 `:811-822` 的 depth 守卫处理：只有最外层 `outermost` 累加 `llm_ms`，内层重试不重复计。嵌套 `run()`（同任务内）会 set 新字典并在退出时 reset，外层统计不受污染。`loop.py:169/1682/1708` 同样是 ContextVar + finally reset。

**TTS 彻底清除（重点 6）**：全仓 `grep -riE 'edge[-_]?tts|TTSConfig|/tts'` 在 `nanobot/`、`tests/` 的 py/md/json/toml 中零命中；仅剩历史文档（superpowers 时期的旧 plan 文本）提到过，无代码、无配置、无命令。

**测试**：只跑不改，全绿。

```
uv run --frozen pytest -q nanobot/channels/discord/tests -k 'slash or skill or dream or command'
→ 26 passed, 77 deselected

uv run --frozen pytest -q tests/agent/test_runner_timing.py tests/agent/test_runner_injections.py \
    tests/agent/test_context_builder.py tests/agent/test_subagent.py \
    nanobot/channels/discord/tests tests/command
→ all passed
```

覆盖缺口（如实标注）：现有 Discord 测试覆盖了闭包捕获与技能命令注册，但**未抓到**任何断言 slash 命令集合与 `BUILTIN_COMMAND_SPECS` 一致的用例 —— 问题 1 之所以能活下来，正因为没有这道断言；修复时应同步补一条「slash 名称集合 ⊇ specs 名称集合」的测试。
