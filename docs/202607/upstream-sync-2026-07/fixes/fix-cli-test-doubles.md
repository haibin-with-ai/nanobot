# CLI 测试替身与原子写失败契约修复

## 修法

Important 4：新增 `tests/cli/fakes.py`，集中提供 `SessionManagerStub`。`test_commands.py` 中网关启动测试不再各自重复实现 `prune_cron_run_sessions`、`flush_all`，需要附加行为的替身统一继承共享替身，原有测试自己的状态记录和断言保留。共享替身在导入时核对真实 `SessionManager` 的必要方法，接口漂移只需改一处并会立即暴露。

Minor 1：`tests/utils/test_helpers.py` 新增 rename/replace 失败测试。测试钉住契约：`Path.replace` 的 `OSError` 不被吞掉；已有目标文件保持原内容；临时文件由 helper 的 `finally` 清理，目录只剩原目标。

## TDD 与真实输出

Minor 1 红测试实际没有变红：首次运行 `uv run --frozen pytest -q tests/utils/test_helpers.py` 输出 `9 passed in 0.36s`。这说明实现原本已满足 rename 失败契约，缺的是回归测试。

Important 4 重构前运行 `uv run --frozen pytest -q tests/cli tests/utils`：`488 passed in 8.43s`。

重构中间验证曾暴露共享替身自检写错接口，输出为 `3 errors`；核对真实实现后移除不存在的 `maybe_prune_cron_run_sessions` 自检。随后又暴露三个子类未调用基类初始化，输出为 `3 failed, 485 passed in 11.89s`；共享默认方法改为兼容测试子类自有初始化，不削弱断言。

最终运行 `uv run --frozen pytest -q tests/cli tests/utils`：`488 passed in 8.65s`。通过数与重构前一致。

改动文件 Ruff：`uv run --frozen ruff check tests/cli/test_commands.py tests/cli/fakes.py tests/utils/test_helpers.py`，输出 `All checks passed!`。

## Deferred

无。补测未发现实现缺陷，未修改任何实现代码。
