# Pack J2：新模型适配 + 运行中模型切换

范围：9 个本地 commit（d61aca5d / 76c43718 / 1df48517 / caf407e1 / c1c0aef0 / 9ca8c42d / ecceb97b / f76609cb / aa21c8ce）

状态：分析中（逐条落盘）

## 检索基线（上游 anthropic_provider）

上游 `nanobot/providers/anthropic_provider.py` L546-580（`git show upstream/main:nanobot/providers/anthropic_provider.py | sed -n '535,600p'`）：

```python
thinking_enabled = bool(reasoning_effort) and reasoning_effort.lower() != "none"
_model_lower = model_name.lower()
omit_temperature = any(m in _model_lower for m in ("opus-4-7", "opus-4-8", "sonnet-5", "fable"))
...
if reasoning_effort == "adaptive":
    kwargs["thinking"] = {"type": "adaptive"}
    if not omit_temperature: kwargs["temperature"] = 1.0
elif thinking_enabled:
    budget_map = {"low": 1024, "medium": 4096, "high": max(8192, max_tokens)}
    budget = budget_map.get(reasoning_effort.lower(), 4096)
    kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
    kwargs["max_tokens"] = max(max_tokens, budget + 4096)
```

关键检索结果：

| 检索命令 | 结果 |
|---|---|
| `git grep -n -i "opus-5\|opus5" upstream/main -- nanobot/` | **零命中**。上游完全没有 Opus 5 |
| `git grep -n -i "sonnet-5" upstream/main -- nanobot/` | 只有 anthropic_provider.py L548/L552 两处：仅出现在 `omit_temperature` 的字符串元组里 |
| `git grep -n "output_config\|xhigh\|summarized" upstream/main -- nanobot/` | 无 Anthropic 相关命中（xhigh 只在 `openai_compat_provider.py:818` 的 DashScope 分支） |
| `git show upstream/main:nanobot/config/schema.py` L121 | 默认模型 `anthropic/claude-opus-4-5` |
| `git show upstream/main:pyproject.toml \| grep anthropic` | `anthropic>=0.45.0,<1.0.0`（未升级） |

一句话：**上游把 `enabled`+`budget_tokens` 这条老路留着当默认，只有显式 `reasoning_effort == "adaptive"` 才走 adaptive；上游知道 sonnet-5 但只知道「它不吃 temperature」，Opus 5 在上游不存在。**

上游对该文件的改动（`git log --oneline ba38f908..upstream/main -- nanobot/providers/anthropic_provider.py`，16 笔），跟本 pack 相关的只有两笔：
- `29d71868 fix(providers): widen omit_temperature to cover opus-4-8 and fable`
- `00cc0da5 fix(providers): omit temperature for sonnet 5`

其余 14 笔全是 tool_id 消毒 / 内容块兜底 / 流超时，跟 thinking 参数无关。

---

## 一、Anthropic 参数适配（6 笔）

### d61aca5d fix: thinking type 'enabled' → 'adaptive' (deprecated by Anthropic API)
- 分类：**[3] 纯本地**（重放难度：中——上游改过同文件，但改的是别的行）
- 本地做了什么：把 `thinking_enabled` 分支里的 `{"type": "enabled", "budget_tokens": budget}` 改成 `{"type": "adaptive", ...}`。
- 上游现状：`upstream/main:nanobot/providers/anthropic_provider.py:574` 仍是 `kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}`，上面 L572-573 还留着 `budget_map`。上游只在 `reasoning_effort == "adaptive"` 这个字面值时才走 adaptive（L564）。
- 判定理由：上游未吸收，老参数路径原样健在。
- 风险/注意：这笔和 76c43718 是同一处代码的两步走，重放时应当作一笔合并处理，不要分开 cherry-pick。

### 76c43718 fix: adaptive thinking has no budget_tokens — remove extra param
- 分类：**[3] 纯本地**（重放难度：中）
- 本地做了什么：删掉 `budget_map` / `budget` / `max_tokens = max(max_tokens, budget + 4096)`，`thinking` 只留 `{"type": "adaptive"}`。
- 上游现状：同上，L572-575 四行 budget 逻辑一行没少。
- 判定理由：上游未吸收。
- 风险/注意：这是行为差异的**真实分歧点**——上游默认把 low/medium/high 翻译成 budget_tokens 并抬高 max_tokens，本地默认扔掉 budget 交给模型自适应。合并时上游那段 `elif thinking_enabled: budget_map...` 会跟本地整块冲突，必须整块取本地。

### 1df48517 适配 Claude Sonnet 5 参数
- 分类：**[2] 平行实现**（重放难度：中）
- 本地做了什么：抽出 `_MODELS_WITHOUT_SAMPLING_PARAMS = ("claude-opus-4-7","claude-opus-4-8","claude-sonnet-5")` + `_model_rejects_sampling_params()`，替换掉写死的 `"opus-4-7" in model_name`。
- 上游现状：上游用 `00cc0da5 fix(providers): omit temperature for sonnet 5` 和 `29d71868 ... opus-4-8 and fable` 做了同一件事，但形式是内联的 `any(m in _model_lower for m in ("opus-4-7","opus-4-8","sonnet-5","fable"))`（L548-553）——**没有抽函数，多覆盖一个 `fable`**。
- 判定理由：功能重叠、覆盖面上游还多一个 fable，但本地抽出的辅助函数是后面 caf407e1 的地基。
- 风险/注意：重放时不要简单二选一。**建议：保留本地的函数抽取形式，把上游的 `"fable"` 补进 `_MODELS_WITHOUT_SAMPLING_PARAMS`**，否则丢掉上游已修的 fable 400。注意本地 caf407e1 把匹配语义从 `in` 收紧成了「精确等于或 `name-` 前缀」，`fable` 这种模型名要确认能被前缀规则命中（`fable` 若实际模型 id 形如 `claude-fable-...` 则匹配不上，需要单独核对）。

### caf407e1 适配 Claude Opus 5
- 分类：**[3] 纯本地**（重放难度：中）
- 本地做了什么：新增 `_EFFORT_MODELS` / `_DEFAULT_THINKING_ON_MODELS` / `_THINKING_SUMMARIZATION_MODELS` / `_ADAPTIVE_EFFORT_LEVELS`（含 `xhigh`/`max`）与 `_matches_model()` 精确前缀匹配；给 Opus 5 加 `thinking.display = "summarized"` 与 `output_config = {"effort": ...}`；`reasoning_effort == "none"` 时对默认开思考的模型显式下 `{"type":"disabled"}`。
- 上游现状：`git grep -i "opus-5\|opus5" upstream/main -- nanobot/` **零命中**；`git grep "output_config\|summarized\|xhigh" upstream/main -- nanobot/` 无 Anthropic 命中。上游 `nanobot/config/schema.py:121` 默认模型还停在 `anthropic/claude-opus-4-5`。
- 判定理由：上游根本不知道 Opus 5 的存在，`output_config.effort` 这个新 API 面上游也没有。
- 风险/注意：**必须重放**。这是本 pack 的核心价值。

### c1c0aef0 补全 Opus 5 默认摘要思考
- 分类：**[3] 纯本地**（重放难度：低——只改一行，且依附 caf407e1）
- 本地做了什么：`elif thinking_enabled:` → `elif thinking_enabled or _matches_model(model_name, _DEFAULT_THINKING_ON_MODELS):`，让 Opus 5 在没显式要 thinking 时也默认开 adaptive+summarized。
- 上游现状：同 caf407e1，上游无此概念。
- 判定理由：caf407e1 的补丁，应与之合并重放。

### 9ca8c42d 补全 Opus 5 兼容边界
- 分类：**[3] 纯本地**（重放难度：低，但含一处需要拍板的 pyproject 改动）
- 本地做了什么：三件事——(a) `reasoning_effort` 加 `"disabled"` 别名一并视为关思考；(b) 把 `none` 分支重写成 `thinking_disabled and _matches_model(...)`；(c) **`pyproject.toml` 把 `anthropic>=0.45.0` 抬到 `>=0.120.0`**。
- 上游现状：`git show upstream/main:pyproject.toml | grep anthropic` → `"anthropic>=0.45.0,<1.0.0"`，上游没升。上游 L546 仍是 `reasoning_effort.lower() != "none"` 单一别名。
- 判定理由：上游未吸收。
- 风险/注意：**pyproject 的 anthropic 版本抬升是全仓影响面**，`output_config` / `thinking.display` 需要新 SDK 才认。合并时 pyproject.toml 大概率与上游其他依赖改动冲突，这一行要单独确认保留。

---

## 二、一次 run 内 provider/model 绑定（2 笔）

### ecceb97b fix(agent): 模型切换不再打断进行中的轮次（provider/model 错配）
- 分类：**[1] 上游已吸收，而且已结构性取代**（不应重放）
- 本地做了什么：给 `AgentRunSpec` 加可选 `provider`，`loop.py` 构造 spec 时把 `provider=self.provider` 与既有 `model` 一起钉死；runner 的请求、流、finalization retry、token 估算都改走 `_provider_for(spec)`。回归测试模拟 mid-run 替换 `runner.provider`，验证旧 run 仍走 pinned provider。
- 上游证据：
  1. `git show upstream/main:nanobot/utils/llm_runtime.py`：新建 `@dataclass(frozen=True, slots=True) class LLMRuntime`，一个不可变值里同时装 `provider`、`model`、复制后的 `GenerationSettings`、`context_window_tokens`、preset/signature。
  2. `git show upstream/main:nanobot/providers/factory.py` L14-21：`@dataclass(frozen=True) class ProviderSnapshot` 同时装 `provider`、`model`、generation、context 与 signature。
  3. `git show upstream/main:nanobot/agent/runner.py | sed -n '80,145p'`：`AgentRunSpec.runtime: LLMRuntime` 是**必填**，`AgentRunner.__init__()` 已没有 provider 参数。
  4. `git show upstream/main:nanobot/agent/runner.py | grep -n "self.provider\|spec.runtime"`：`self.provider` **0 次**；请求路径统一用 `spec.runtime.provider`，模型统一用 `spec.runtime.model`，generation 也用 `spec.runtime.generation`（例如 L787/825/867/899/905/1098/1147-1148）。
  5. `git show upstream/main:nanobot/agent/loop.py` L996-1001：在 build/admission 后将同一个 `runtime` 作为整体塞进 `AgentRunSpec(runtime=runtime)`；L1580-1592 只在 `ctx.runtime is None` 时按 session resolve 一次，然后写回 `ctx.runtime`。
  6. `git show upstream/main:tests/agent/test_runner_runtime_identity.py`：`test_active_run_keeps_provider_captured_at_admission` 在第一次工具迭代中把“当前选择”切到第二 provider 并篡改第一 provider 的 generation，最终断言两次请求都只走第一 provider、temperature 两次都保持 0.2。
- 判定理由：上游不是在本地补丁上再补一层，而是把 runner 的可变 provider 字段整个删了。provider/model/generation 成为一颗不可拆的 frozen runtime，**ecceb97b 的 provider/model 错配 bug 在上游已被结构性消除**。
- 重放风险：硬重放会退回“两套身份来源”（`spec.runtime` 加可选 `spec.provider`），反而重新制造 special case。不要重放。

### f76609cb docs(agent): 注释 provider 必须 per-run 钉死的并发根因
- 分类：**[1] 上游已吸收/实现已自证**（不应重放）
- 本地做了什么：只扩写 `AgentRunSpec.provider` 的注释，解释一个 AgentLoop 跨 session 并发、全局 `/model` 改 shared provider，因而 provider 与已 snapshot 的 model 会错配。
- 上游现状：该字段已经不存在；`LLMRuntime` docstring 明写 `One captured provider/model configuration used for an entire execution`，runner 测试直接覆盖 active-run identity。原注释里的“`/model` 是 GLOBAL”也已被上游后续 `c22efb5f feat(agent): make model presets session-scoped (#4866)` 改写，不再成立。
- 判定理由：注释绑定的是已删除的局部修法和已变化的全局命令语义，重放没有落点，也会传递过时事实。

**这一组的结论很硬：ecceb97b/f76609cb 都不要重放。上游已把「provider 是 runner 状态、model 是 spec 状态」这种危险的左右脚，合成了同一个不可变 runtime。**

上游吸收链的可核查 commit：
- `bd94fefd refactor(agent): introduce immutable model runtime resolver`
- `cb03d2c7 refactor(agent): snapshot generation without provider mutation`
- `b4f06980 refactor(agent): make runner consume required runtime`
- `c22efb5f feat(agent): make model presets session-scoped (#4866)`

检索：`git log upstream/main --oneline -- nanobot/utils/llm_runtime.py nanobot/agent/runner.py | head -25`。

---

## 三、/model 与 cron

### aa21c8ce feat(agent): /model 改为 per-session 生效 + cron 模型钉 fast
- 分类：**混合提交：前半 [1] 上游已吸收，后半 [2]/[3] 取决于 cron 架构拍板**（禁止整笔重放）
- 本地做了什么：
  1. 将 `/model` 从修改 `AgentLoop` 的共享 provider/model 改成把 preset 写入 session metadata，并按 session resolve provider snapshot。
  2. 给 `CronPayload` 增加 `preset`，定时任务固定用 `fast`，避免继承全局模型。
- 上游现状：
  - **per-session `/model` 已结构性吸收。** 上游 `nanobot/session/model_selection.py` 定义 `SESSION_MODEL_PRESET_METADATA_KEY = "_nanobot_model_preset"`；`c22efb5f feat(agent): make model presets session-scoped (#4866)` 把选择写入 session metadata；`AgentLoop` 在 turn admission 时解析成不可变 `LLMRuntime`。这比本地 `_session_snapshot_cache` 更完整，直接采用上游。
  - **per-job cron model 上游没有。** 上游 `CronPayload` 只有 `session_key/origin_*` 等绑定信息，无 `model/preset`；`bound_runner.run_bound_cron_job` 把 cron turn 投回绑定 session，因此模型自然跟随该 session 的当前 runtime。这个差异已在 Pack K 的 `d6e49fdf + 67bd27c2` 中完整展开。
- 判定理由：一笔 commit 横跨两个产品决策。`/model` 前半必须丢弃改用上游；cron 后半不能单独复活，必须与「cron 独立 session vs 绑定原会话」一起拍板。
- 风险/注意：若选上游 cron，整笔剩余部分也丢；若选本地 cron 隔离，应该把 `aa21c8ce + 67bd27c2` 压成上游新结构上的一笔 `per-job model`，直接使用最终字段名 `model`，不要重演 `preset → model` 的改名史。

## 小结

- **直接采用上游**：`ecceb97b`、`f76609cb`、`aa21c8ce` 的 per-session `/model` 部分。
- **必须按当前 API 重放**：`caf407e1 + c1c0aef0 + 9ca8c42d` 的 Opus 5 兼容最终态；`d61aca5d + 76c43718` 的 adaptive thinking 最终态。
- **融合上游后重放**：`1df48517` 的辅助函数形式要补回上游新增的 `fable` 覆盖。
- **待 cron 架构拍板**：`aa21c8ce` 的 per-job preset 部分，与 `d6e49fdf + 67bd27c2` 捆绑处理。

