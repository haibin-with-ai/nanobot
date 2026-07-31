# Bootstrap TOOLS 契约与规格单一来源修复

## 修法

`ContextBuilder.BOOTSTRAP_FILES` 从文件名字符串改为 `BootstrapFile(name, root)` 规格序列。文件名、来源与加载顺序现在都由这一张表定义，`_load_bootstrap_files()` 不再维护按文件名映射的第二张 `roots` 表。

顺序保持为 `SOUL.md`、`AGENTS.md`、`USER.md`、`TOOLS.md`。前三者分别从 agent、project、agent 工作区读取；`TOOLS.md` 固定从包内模板读取。因此，新工作区无需复制即可获得默认工具契约，旧工作区中的过期 `TOOLS.md` 也不能覆盖它。

`BootstrapFile` 实现 `__fspath__`，保留模板存在性测试将规格对象交给 `pathlib` 的既有用法。

包内 `TOOLS.md` 补入不可信外部内容规则。种子测试同时收紧：工作区 `TOOLS.md` 的独特内容不得进入上下文，内置契约必须进入。

Prime Directive 的尾部拼接逻辑未改。subagent 仍通过 `_load_bootstrap_files()` 继承 SOUL 与 TOOLS。

## TDD 记录

改动前运行：

`uv run --frozen pytest -q tests/agent/test_context_builder.py`

真实结果：`3 failed, 51 passed in 1.12s`。失败正是三条指定种子测试，其中顺序测试报 `AttributeError: 'str' object has no attribute 'name'`。

实现与测试收紧后再次运行同一命令，真实结果：`54 passed in 1.10s`。

## 变异验证

在 `/root/workspace/tmp/bootstrap-mutation` 副本中，把 `BootstrapFile("TOOLS.md", "bundled")` 故意改成 `BootstrapFile("TOOLS.md", "agent")`，再运行目标测试。

真实结果：`5 failed, 49 passed in 0.97s`。失败覆盖无工作区副本、空工作区、内置契约加载、工作区不可覆盖，以及规格来源断言，证明测试能抓住契约来源被破坏。

## 最终验证

`uv run --frozen pytest -q tests/agent`

真实结果：`1508 passed in 39.79s`。

`uv run --frozen ruff check nanobot/agent/context.py nanobot/templates/TOOLS.md tests/agent/test_context_builder.py`

真实结果：`All checks passed!`
