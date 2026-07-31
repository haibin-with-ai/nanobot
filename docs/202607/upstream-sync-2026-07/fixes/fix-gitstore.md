# 修复 git blame porcelain 解析与超时

## 改动

`nanobot/utils/gitstore.py` 的 blame 记录头改为完整正则匹配：40 位十六进制 oid，后接三个十进制字段。普通 commit summary 不再可能被当成记录头。

解析过程中若 committer-time 缺少记录头、时间字段不是整数，或正文行找不到对应时间，统一抛出 `GitStoreError`。错误包含 porcelain 行号及最多 160 字符的原始行上下文，并保留原始异常链。

`git blame --porcelain` 增加 30 秒超时。该值给本项目文档记录的 12k 行、120 commits、0.23 秒实测留出约 130 倍余量，同时保证调用线程不会无限阻塞。捕获 `subprocess.TimeoutExpired` 后抛出 `GitStoreError`，消息包含文件路径和超时秒数。

## 测试

真实临时 Git 仓库集成测试覆盖：长 summary、非 ASCII summary、多行 commit message。超时测试验证传给 subprocess 的值确为 30 秒，并验证异常消息包含路径和超时值。

首次红灯：

`uv run --frozen pytest -q tests/utils/test_gitstore.py`

结果：4 failed, 29 passed。三种 commit message 均触发旧解析器裸 `ValueError: invalid literal for int()`；超时测试显示 `subprocess.run` 未收到 timeout。

最终测试：

`uv run --frozen pytest -q tests/utils`

结果：275 passed in 6.38s。

## 变异验证

解析器变异：将严格记录头正则故意放宽为任意 40 字符开头。

`uv run --frozen pytest -q tests/utils/test_gitstore.py -k commit_metadata`

结果：1 failed, 2 passed, 30 deselected。长 summary 被误认成头，最终抛出 `GitStoreError: Invalid git blame porcelain at line 13`。随后恢复严格正则。

超时变异：故意删除 blame subprocess 的 timeout 参数，并让测试断言实际调用参数。

`uv run --frozen pytest -q tests/utils/test_gitstore.py -k blame_timeout`

结果：1 failed, 32 deselected，`KeyError: 'timeout'`。随后恢复 30 秒 timeout。
