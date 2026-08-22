---
tapd_url: "⚠️待确认：未提供 TAPD 需求链接"
req_type: "技术需求"
status: "draft"
spec_version: "1"
---

# Subagent 模型选择 fail-fast

> **做什么**：让 `spawn(model=...)` 对未知裸模型名 fail-fast，仅接受已配置 preset/唯一 alias 或显式 `<provider>/<model>`。
> **为什么**：阻止幻觉模型名借默认 provider 发错请求，并产生错误的供应商归因。
> **影响范围**：subagent 模型选择契约、错误反馈与回归测试。
> **灰度方式**：默认直接生效，不设兼容开关；合法 preset 和默认 subagent 行为不变。
> **主要风险**：依赖旧 raw-model 透传的调用会被拒绝，必须迁移为 preset 或显式 provider/model。

## 1. Intent / Why

当前 subagent 将未知 `model` 依次尝试为 preset、模型别名，最后降级成 raw model 字符串，并沿用默认 subagent provider。于是 `model="minimax"` 可能实际请求 Anthropic，错误又被上层误报成“Minimax 凭证失效”。

本需求要把错误挡在外部 API 调用之前，让模型选择从“猜路由”变成显式契约：调用者写错模型时，应拿到本地、确定、可修复的错误，而不是收到另一个 provider 的认证或模型不存在错误。

成功标准：

- 未注册的裸模型名不会触发任何 provider 请求，也不会创建后台 subagent 任务。
- 合法 preset 仍按 preset 绑定的 provider、model 和推理参数运行。
- 合法显式 `<provider>/<model>` 只路由到该 provider，并将不含 provider 前缀的 model 传给 API。
- 错误消息足以让调用者改正，不泄露密钥或凭证状态。

## 2. Scope + Non-goals

### In scope

- `spawn` 工具的 `model` 选择契约与错误反馈。
- model preset、provider 注册状态和 provider 配置状态三者之间的判定规则。
- 错误提示、可观测行为和回归验收。
- `spawn` 参数说明必须阻止主代理把任意品牌名当作可用模型。

### Non-goals

- 不新增、删除或自动生成 model preset。
- 不配置 MiniMax、Gemini、DeepSeek 等 provider，也不探测远端模型目录。
- 不验证某个 provider 是否真的支持指定 model；这是 provider API 的职责。
- 不把 OAuth token 的实时有效性当作“provider 已配置”的前置检查。
- 不改变主会话 `/model`、全局默认模型、fallbackModels 或 provider failover 语义。
- 不把合法值硬编码进 JSON Schema enum；运行期配置变更仍是权威来源。

## 3. Testable Acceptance

- [ ] `model` 省略或为空时，行为与当前一致：使用默认 subagent provider/model。
- [ ] `model` 等于已配置 preset 名，或能按现有规则唯一解析为 preset alias 时，使用该 preset 的 provider、model、reasoning effort、temperature 与 max tokens；多匹配 alias 仍返回 ambiguous 错误。
- [ ] `model` 为既非 preset、也非可唯一解析 alias，且无 `/` 的字符串时，`spawn` 同步返回 `unknown subagent model` 类错误；provider 的 `chat` / `chat_stream` 调用次数为 0，任务表不新增记录。
- [ ] 未知裸模型错误列出当前可用 preset，并明确提示显式格式 `<provider>/<model>`；输出不包含 API key、OAuth token 或凭证内容。
- [ ] `model="<provider>/<model>"` 仅当 provider 位于 provider 注册表且本地已配置时通过；API 收到的 model 是去掉首段后的完整余串。
- [ ] provider 名支持现有规范化规则：大小写不敏感，`-` 与 `_` 等价；传给 API 的 model 原样保留。
- [ ] `<provider>/`、`/<model>`、未知 provider、未配置 provider 均在本地拒绝；不发起 provider 请求，不创建任务。
- [ ] 显式 provider 的 provider 构建失败时返回该失败，不得退回默认 subagent provider。
- [ ] preset 名优先于 slash 语法：若配置中存在同名 preset，则按 preset 解析，避免破坏已有 preset。
- [ ] 不再出现 `Unknown model alias ... using raw string` 路径；相关旧测试改为断言 fail-fast。
- [ ] `spawn` 工具描述说明：优先使用可用 preset；绕过 preset 时必须提供 `<provider>/<model>`，不能只给裸 model ID。
- [ ] 单元测试覆盖默认值、合法 preset、未知裸值、合法显式 provider、未知/未配置 provider、空 provider/model、含多段 `/` 的 model，以及“错误时零外部调用”。

## 4. Key Decisions

### KD-1：裸字符串只表示 preset 或其唯一 alias

`spawn(model="fast")` 中的裸字符串被定义为 preset 名，或按现有规则能唯一解析到 preset 的 alias；它不是任意远端 model ID 或品牌名。这样 `minimax`、`opus` 一类无匹配值无法借默认 provider 蒙混过关，同时保留已有 preset alias 的便利。

被拒方案：无匹配时把输入继续当 exact/raw model。它把调用者意图变成猜测，正是本次错路由的来源。

### KD-2：显式 raw model 必须携带 provider

需要绕过 preset 时，调用者必须写 `<provider>/<model>`，例如 `openrouter/anthropic/claude-sonnet-4`。只在第一个 `/` 处分割，因此 model 部分允许继续包含 `/`。

显式 provider 由 provider 注册表解析，并必须有本地可用配置。OAuth、local、direct provider 按现有 provider 工厂的“已配置”语义处理；普通 provider 需要有效的本地配置。该检查只证明“可尝试调用”，不承诺凭证尚未过期或远端 model 存在。

### KD-3：校验发生在任务创建之前

模型选择校验必须先于后台任务创建与任务登记。失败作为 `spawn` 工具结果同步返回，避免出现一个注定失败的后台任务，也让主代理能当场改正参数。

### KD-4：错误展示合法契约，不伪造供应商诊断

错误至少包含无效输入、可用 preset 列表和 `<provider>/<model>` 用法。对未知或未配置 provider 分别说明，但不得把当前 provider 的认证状态归因给输入字符串代表的品牌。

### KD-5：不保留兼容 raw-string 开关

不提供 legacy 开关、shim 或静默兼容期。旧行为会向错误 endpoint 发真实请求，兼容它等于保留 bug。需要使用裸 model ID 的调用方应新增 preset，或显式写 provider。

## 5. Interface / Contract

### `spawn` 输入

```text
model: string | null
```

解析顺序：

1. `null` / `""`：使用默认 subagent 配置。
2. 与 `modelPresets` 的 key 精确匹配：加载该 preset snapshot。
3. 无 `/` 且按现有 preset alias 规则唯一匹配：加载匹配的 preset snapshot；多匹配返回 ambiguous 错误。
4. 含 `/`：按第一个 `/` 解析为 `<provider>/<model>`，规范化并校验 provider，然后构建绑定该 provider 的 snapshot。
5. 其余：返回未知 subagent model 错误。

### 错误结果

工具失败继续遵循现有 tool result 字符串通道，不引入新的传输协议。消息语义如下：

```text
Unknown subagent model '<value>'. Use a configured preset: <sorted presets>,
or an explicit configured provider/model such as '<provider>/<model>'.
```

显式 provider 错误应区分：

```text
Unknown provider '<provider>'.
Provider '<provider>' is not configured.
Invalid explicit model '<value>'; expected '<provider>/<model>'.
```

### 兼容性

- 默认 subagent 调用、合法 preset 与可唯一解析的 preset alias：兼容。
- 已存在且名称含 `/` 的 preset：因 preset 优先，兼容。
- 依赖无匹配裸 model 自动落到当前 provider 的调用：有意 breaking；迁移为已注册 preset 或显式 `<provider>/<model>`。
- 配置文件格式、会话数据和 task status schema：不变。
