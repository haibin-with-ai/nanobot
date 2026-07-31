# Cron / Session / CLI / Config 代码审查

基线：`upstream/main`

## Critical

### `nanobot/cron/bound_runner.py:120, 139, 170-225`：同一个 `CronPayload.model` 在两条执行路径上含义不同

非绑定任务会把 `payload.model` 解析为 session preset，再调用 `process_direct`；绑定任务直接走 `submit_cron_turn`，完全不读取 `payload.model`。这不是实现细节差异，而是公开字段的双语义：同一任务只要补上 session binding，指定模型就会悄悄失效。现有测试只覆盖非绑定路径，因此恰好把这个漏洞藏住了。

最小修复方向：模型选择只能有一个入口。要么在进入 bound/unbound 分流前统一解析并设置本次 turn 的模型，要么明确删除绑定任务上的 job-level model 能力并在创建、更新时拒绝该组合。不能继续接受字段后静默忽略。

### `nanobot/cron/bound_runner.py:79-97`、`nanobot/cron/service.py:111-139`：半绑定态被当成合法降级，任务会写进错误会话

`session_key` 已存在但缺 `origin_channel` 或 `origin_chat_id` 时，代码只打一条 warning，然后改走一次性会话。调用者表达的是“回到这个 session”，实际结果却写入 `cron:{job}:{run}`。这类错最坏的地方不是失败，而是成功地做错事。`_normalize_agent_turn_job` 只处理带 legacy delivery 字段的记录，无法消灭 session-key-only 的半绑定态，于是特殊分支会永久存在。

最小修复方向：在存储边界建立并验证一个原子 binding 值，完整绑定才可执行 bound path；显式出现残缺 binding 时禁用或报错，不得降级为 unbound。迁移逻辑负责一次性修复旧数据，runner 不再猜调用者意图。

## Important

### `nanobot/session/manager.py:947-1029`：`prune_cron_run_sessions` 与 `maybe_prune_cron_run_sessions` 复制了整套删除算法

两段代码分别扫描、分组、按时间和数量保留、删除文件、驱逐 cache；差别只有节流判断和日志。规则已经出现实质漂移：显式版本返回 `deleted`，节流版本不返回；两边日志与异常面也不同。以后改 key 规则、保留策略或 cache 清理时，漏改一份只是时间问题。这是典型重复代码加隐式耦合。

最小修复方向：保留一个无节流、返回删除数的 `_prune_cron_run_sessions(now_ms)`；`prune` 直接调用，`maybe_prune` 只负责节流门。删除算法只能有一份。

### `nanobot/session/manager.py:944-1029`、`nanobot/cron/bound_runner.py:116-119`：节流状态只活在进程内，且被藏在 SessionManager 实例字段里

`_last_cron_run_prune_ms` 是运行时临时状态，重启即丢失；每个 SessionManager 实例又各自计时。注释声称“每天最多一次”，代码保证的只是“同一实例存活期间每天最多一次”。测试通过直接篡改私有字段验证节流，反而固化了这个不对外可见的时钟状态。

最小修复方向：先决定真正契约。若只需进程级降载，改名和注释说清楚，并注入时钟便于测试；若确实要求跨重启每天一次，把 last-prune 写入一个明确的维护状态文件。不要用对象私有字段冒充持久化调度状态。

### `nanobot/cli/commands.py:1604-2300`：`_run_gateway` 已经是上帝函数，新增 OAuth 又继续向同一巨型文件堆职责

`commands.py` 已达 3059 行，远超 800 行红线；`_run_gateway` 横跨配置解析、provider 装配、session、cron、heartbeat、HTTP health server、浏览器拉起、任务生命周期和 shutdown，内部还嵌套多层 async 函数。新增 `claude_code_login` 也把 OAuth、配置回写、provider 默认值和 CLI 呈现塞进同一文件。单个改动必须理解整个进程装配面，测试只能靠大面积 monkeypatch。

最小修复方向：不做大重写。先沿现有 seam 提取两个独立模块：`gateway_runtime.py` 接管 runtime 装配和生命周期，`provider_auth.py` 接管 Claude OAuth 与配置更新；Typer command 只解析参数、调用服务、打印结果。每个新函数控制在 20 行附近，嵌套不超过 3 层。

### `nanobot/config/schema.py:243-266`、`nanobot/cli/commands.py:2747-2790`：Claude 凭据规则被分散到 schema validator 和命令流程

refresh-token alias 白名单写死在 schema；CLI 又自行决定 token 保存位置、默认 provider、默认 model、endpoint 和 OAuth 开关。一个“Claude Code 登录”事实被同步到多个点，新增 alias 或改变凭据优先级时很容易出现 schema 拒绝 CLI 刚写出的值，或 CLI 写出 resolver 不认的组合。

最小修复方向：建立单一的 Claude credential 解析/归一化函数，schema validator、CLI 登录和 runtime resolver 共用；CLI 只提交归一化结果。默认 model/provider 的策略也应由一个配置服务原子更新，不要散落字段赋值。

### `tests/cron/test_bound_runner.py:24-100, 156-205`、`tests/session/test_cron_retention.py:19-77`：测试替身重复且行为模型已经漂移

`FakeSessions`、`FakeAgent`、`FakeCron` 在同一文件内重复定义，session 测试又有另一套 runner/agent 替身。它们只实现当前断言需要的方法，没有模拟真实的 preset 持久化、session save、绑定消息入队和 prune cache 驱逐之间的关系。结果是“model 在 unbound fake 上被记录”能通过，却没有任何测试发现 bound path 完全忽略 model。

最小修复方向：抽一个最小共享 harness，基于真实 `SessionManager` 加薄 agent stub；对 bound/unbound 使用同一组契约测试，至少覆盖 model、session key、run record、失败状态和 prune 后文件/cache 一致性。替身只替换外部 I/O，不重写被测语义。

## Minor

### `nanobot/cron/service.py:142-344`：序列化、迁移、存储恢复仍挤在 service 中，文件已逼近红线

`service.py` 764 行，尚未越过 800 行，但 `_save_store` 手写全部字段，`CronPayload.from_store_dict` 又维护另一份字段映射；这次新增 `model` 已要求多点同步。继续加字段很快会再次漏存或漏迁移。

最小修复方向：把 store codec 提成独立模块，由 `to_store_dict/from_store_dict` 成对维护；service 只调 codec，不再手写 payload/state 镜像。

### `nanobot/session/manager.py:420-439`：一次性 cron session key 把 job id 编码了两遍

`cron:{job_id}:{run_id}` 中的 `run_id` 又以 job id 开头，解析器只能靠反向引用识别这种偶然形状。注释是在解释复杂性，不是在消除复杂性。它还把清理策略绑定到 `_new_run_id` 的内部格式。

最小修复方向：定义单一稳定格式，例如 `cron-run:{job_id}:{started_ms}:{nonce}`，由一个 formatter/parser 对共同维护；run record id 与 session key 不必复用同一字符串结构。

## 判定

**当前双语义不可持续。** bound/unbound 已经在模型选择、会话归属、执行入口和清理生命周期上分叉，半绑定态又靠 warning 兜底。继续补特殊分支，只会把 `CronPayload` 变成一个字段相同、契约不同的联合类型，却没有类型标签。要保留两种任务，就把它们建模成两个明确 variant，共享调度和审计，分别拥有完整且可验证的执行契约；否则下一次同步还会在“看似兼容”的分支里制造静默错误。
