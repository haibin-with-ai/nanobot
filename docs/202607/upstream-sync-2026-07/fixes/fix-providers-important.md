# providers 包 Important 修复（I1–I8）

对应 `reviews/coding-review-providers.md` 的全部 Important 条目。分四笔提交落在
`sync-upstream-2026-07`：`4e2330a9`、`bec4b464`、`9072c878`、`c004856b`、`94250e3e`。

## I1 四张模型能力表合成一张

`anthropic_provider.py` 顶部原有 `_MODELS_WITHOUT_SAMPLING_PARAMS`、`_EFFORT_MODELS`、
`_DEFAULT_THINKING_ON_MODELS`、`_THINKING_SUMMARIZATION_MODELS` 四条并列元组，`_build_kwargs`
对同一个 model 连查四次。现在是一个 frozen dataclass `_ModelCaps` 加一张 `_MODEL_CAPS` 字典和
查表函数 `_caps_for`，加新模型只改一行。

被四张表巧合掩盖的雷同时拆掉：原来「thinking 被显式关掉且该模型默认开 thinking」那条分支
不写 temperature，今天不炸只因为默认开 thinking 的模型恰好也在「不接受采样参数」名单里。
现在带不带 temperature 只由 `caps.omit_sampling` 一个字段决定，与 thinking 表脱钩。

测试 `tests/providers/test_anthropic_model_caps.py`（7 例）。红：`4 failed, 1 passed, 2 errors`，
`AttributeError: module ... has no attribute '_caps_for'`。绿：`7 passed in 1.37s`。
变异（把温度判定改回 `not caps.omit_sampling and not caps.thinking_default`）：
`2 failed, 5 passed`，两处均为 `KeyError: 'temperature'`。

## I2 OAuth 与 API key 收敛成凭据策略对象

`product_mode == "claude_code"` 原本在三处复述：客户端 kwargs 的字段名、system 首块身份注入、
凭据刷新。现在 `_ApiKeyCredential` 与 `_OAuthCredential` 各自给出 `key_field`、`decorate_system`、
`refresh`，构造时一次性查 `_CREDENTIALS` 表。provider 上的 `_inject_identity` 删除，
`_refresh_credentials` 从 21 行缩到 7 行，三处 if 全消。

测试 `tests/providers/test_anthropic_credential_strategy.py`（5 例），
既有 `test_anthropic_claude_code.py` 的 7 处调用点改为走 `_credential.decorate_system`。
红：`2 failed, 3 passed`，`AttributeError: 'AnthropicProvider' object has no attribute '_credential'`。
绿：`5 passed in 1.47s`。变异（把默认凭据换成 OAuth，即 API key 模式也注入身份）：
`2 failed, 3 passed`，失败于 `assert 'You are Claude Code...' == 'You are Evie.'`。

## I3 降级判定改成有序规则表

`_should_fallback` 原是 45 行顺序敏感 if 链，顺序即语义却零文档。现在拆成 `_ErrorFacts`
（统一小写的信号快照）加 `_FALLBACK_RULES` 有序规则表，每条带 `why_here` 说明它为什么排在那个位置，
分四段：能指认「换一家有救」的强信号、明确「换谁都一样」的信号、HTTP 状态码、兜底正文子串。
`_should_fallback` 本体缩成一次遍历。

行为锁定测试 `tests/providers/test_should_fallback_rules.py`（17 例）先在重构前跑绿
（`17 passed in 0.07s`），重构后再跑绿，证明分类结果一行未变。
变异（把结构化鉴权 token 规则挪到两条「非切换」规则之后）：`1 failed, 16 passed`，
失败用例是「鉴权 token 压过 invalid_request 类型」。

## I4 fallback 不再原地改调用方 kwargs

复查发现该项已由前一轮 `17b79ae9` 修掉：`_MISSING` 哨兵与 finally 还原全部消失，
现在是 `attempt_kwargs = {**kwargs, ...}`。本轮无改动。

## I5 FallbackProvider docstring 说清状态生命周期

原 docstring 声称 "the wrapper itself is stateless between turns"，与 `_primary_failures`、
`_primary_tripped_at`、`_quota_cooldowns` 三个跨请求字段矛盾。改写为：候选选择是请求级的，
但断路器与冷却状态跨轮存活且只在进程内，网关重启即遗忘；这些字段是无锁的裸属性赋值，
依赖单事件循环隐式串行，不得跨线程或跨事件循环共享实例。

## I6 冷却身份主备统一命名

主 provider 的冷却 key 过去取实例 `name` 或类名，备用 preset 取配置里的 `provider` 字段，
同一个端点被记成两个 key。结果是主模型限流进冷却后，指向同一端点的备用 preset 仍会被当成
「另一个模型」再打一次。现在 `FallbackProvider` 接 `primary_name` 与 `provider_name_resolver`
两个参数，主备走同一条解析路径；factory 传入 `config.get_provider_name(...)`，
把 `"auto"` 先解析成真实后端名再入 key。

测试 `tests/providers/test_fallback_cooldown_identity.py`（2 例）。红：
`TypeError: FallbackProvider.__init__() got an unexpected keyword argument 'primary_name'`。
绿：`2 passed in 0.48s`。变异（把主 key 改回实例名/类名）：`1 failed, 1 passed`，
日志里能直接看见 `Primary model skipped: quota cooldown; trying fallback 'claude-opus-5'`,
也就是刚冷却掉的同一个模型立刻被再打一次。

## I7 过期且无法刷新的 OAuth 凭据不再静默返回

`oauth_store.get_token()` 在凭据过期又没有 refresh_token 时静默返回陈旧凭据，调用方拿到一个
注定 401 的 token。现在判据改为 `creds.fresh_for(0)`：真过期且无法刷新时返回 None，
并 warning 出凭据文件路径与「re-login required」。

选返回 None 而非抛异常的依据是两个真实调用点：`factory.py:49` 紧跟 `return creds.access_token if creds else None`，
已有 None 分支；`anthropic_provider.py` 的 `_refresh_credentials` 外面本来就是 try/except 加
`if not creds or not creds.access_token: return False`。抛异常会把 provider 构造炸掉，
把「401 时报错」升级成「启动时崩」。

`_from_env` 那条 `expires_at=0` 的环境变量凭据路径没有被误伤，并由两个用例钉住。
测试 `tests/providers/test_oauth_expired_no_refresh.py`（5 例）。红：`2 failed, 3 passed`。
绿：`5 passed`。变异（改回静默返回陈旧凭据）：`2 failed, 3 passed`。

## I8 backend 分派链换成 registry 侧构造器表

`_make_provider_core` 的六段 `elif backend == ...` 手工分派链删除，构造行为并回 `registry.py`：
每个 backend 一个 `_build_*` 函数，一张 backend → builder 表，`ProviderSpec` 查表即可。
数据与行为回到同一个文件，加新 provider 只改一处。

测试 `tests/providers/test_provider_builder_table.py`（193 行）走真实构造路径断言对象类型与参数。
变异（删掉 `"anthropic": _build_anthropic` 映射）：`3 failed, 12 passed`。

## 收口

`uv run --frozen pytest -q tests/providers` → `983 passed in 11.63s`；
`uv run --frozen ruff check nanobot/providers` → `All checks passed!`。

## Deferred

- `commands.py` 与 discord `runtime.py` 的大型拆分本轮不做。
- `_run_agent_loop` 的 5 元组返回值改 dataclass（agent 包 I4）本轮不做，已记在
  `fix-agent-stall.md`。
