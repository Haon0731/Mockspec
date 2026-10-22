# 单案例依赖约束落地验证

参考提交：`54a75f04b2b163ebe4fed3a487ad494ed7a377d3`。
本轮只验证已审核 `flask_308` 的一行：`has_app_context=False, code=403, args=(), kwargs={}`。

**结果：存在一份合法的人工辅助 Mock 见证，使这一行原测试通过；只改变 fallback
响应为正常返回后，同一行在原异常预期处失败。** 这验证了选定行为可以落到具体替换点，
并影响真实测试观察。不是 Scenario Agent 自动推导结果，也不是无指导生成基线。

## 目标、方案、替换点与观察

| 测试目标 / 可见依据 | 选定依赖行为 | 实际替换点 | 执行观察 |
| --- | --- | --- | --- |
| `test:26–32` 固定参数行；`mut:282` 判断上下文查询结果 | 查询正常返回 None | 使用原 `mocker` fixture 替换 `flask.helpers._cv_app` 模块绑定，配置其 `get` 方法 | 两次均记录 `get(None)` 一次，返回 None |
| `imports:12` 将 Werkzeug abort 导入为 `_wz_abort`；`mut:285` 调用 fallback | 接收 `403`，无额外位置/关键字参数 | 使用 `mocker.patch` 替换 `flask.helpers._wz_abort` | 两次均记录 `(403,)` 和 `{}`，各一次 |
| `test:102–103` 要求 Exception；`test:126` 要求消息包含 `Werkzeug abort 403` | 人工选择 `RuntimeError("Werkzeug abort 403")` | 上述 fallback Mock 的 `side_effect` | 见证 `1 passed, 4 deselected`；异常 traceback 包含真实 MUT 第 285 行，局部 `ctx=None, code=403, args=(), kwargs={}` |
| 原异常预期不变 | 诊断版只改为 `return_value=None` | 相同绑定、相同输入、相同其余配置 | `1 failed, 4 deselected`，原预期报 `DID NOT RAISE <class 'Exception'>`；setup/teardown 均通过 |

选择整项 `_cv_app` 模块绑定，而不是尝试修改原对象的 `get` 方法；替换点来自 MUT 的
名字查找及可见 imports。没有根据参数标签或 slot 名称恢复隐藏配置。
实际 patch 由 `pytest_mock.plugin.MockerFixture` 完成；原 fixture 生命周期未替换。

异常具体类型和精确字符串是**人工选择**，不是测试唯一要求。原断言只要求 Exception
及消息子串；其他满足要求的响应可能也有效，本轮未执行它们。

## 最小见证与唯一诊断差异

所有新增配置均放在 `mock_stub_001`，其余 Mock 与 verify 区域保持 `pass`：

```python
mock_query = mocker.patch("flask.helpers._cv_app")
mock_query.get.return_value = None
mock_fallback = mocker.patch(
    "flask.helpers._wz_abort", side_effect=RuntimeError(f"Werkzeug abort {code}")
)
```

诊断版仅将最后一次 patch 的响应参数改为 `return_value=None`。
这里展示的换行仅为排版，实际候选、注入后的测试和精确差异均已保留：

- [见证配置](../../experiments/dependency-witness/results/witness/mock_stub.txt)
- [诊断配置](../../experiments/dependency-witness/results/diagnostic/mock_stub.txt)
- [精确差异](../../experiments/dependency-witness/results/candidate.diff)
- [成功执行事实](../../experiments/dependency-witness/results/witness/runtime/execution.json)
- [失败执行事实](../../experiments/dependency-witness/results/diagnostic/runtime/execution.json)
- [最小行为约束记录](../../experiments/dependency-witness/results/behavior.json)

此诊断错误是人工构造的对照，不能作为真实生成 Agent 产生错误的证据。

## 运行与证据边界

审核 Adapter/SourceStore 重新加载原输入，原参数化数据、普通断言和 MUT 均未更改。
复用冻结快照中已核验的 `apply_regions.py`，仅在新工作区调用，宿主独立核对注入结果
与逐个允许区域替换的预期文本完全一致。

旧 `run_target_test.py` 固定选择整个测试函数，会执行五行，因此本轮不直接运行它。
薄运行脚本沿用 pytest 和原依赖 bundle，由 collection hook 从已有 callspec 中匹配
完整参数行，只运行一个 node，另外四个显式 deselect。scenario 字符串只用于确认
行身份，不用于推断行为。没有修改参数化或另写替代测试。

运行边界：固定 Docker 镜像、禁网络、只读容器根、冻结 repo 与依赖只读挂载、注入后的
单个测试只读覆盖。观察器和审核切片也只读；仅 `/outputs` 与临时目录可写。
没有复制数据集、重新计算依赖树哈希、读取参考 Mock 或历史答案。

实际运行环境：CPython **3.10.12**、pytest **9.0.2**、pytest-mock **3.15.1**。
使用 runtime lock 指定的冻结 bundle `7757e0c16550b4362d55d6cdd87c4430c2c300ce9a3ac0b6d5ae2c5bccb989ec`，
以及本机已有镜像 `sha256:04a57de0d4ccb65b7f73a5880277b19c65ca23c5b677d89d3a4673706f55866b`。
记录 ABI、镜像和实际版本，不声称重新核验了完整历史环境的所有文件。

运行时 `flask.helpers.__file__` 为 `/target/src/src/flask/helpers.py`。宿主观察器把审核
MUT 切片编译为同版本 code object，与加载函数的字节码、常量、名称、参数、闭包及标志
比较；同时确认原测试导入的 abort 正是该函数，执行前后函数身份不变。
未使用 `inspect.getsource` 或把其他运行时库源码返回给分析端。

观察器只读取 Mock 调用记录和已抛异常的 traceback，不包裹 MUT、不截获其异常。
traceback 证明真实 MUT 的 fallback **调用点**执行；被替换的 Werkzeug 实现没有执行。
失败版的调用记录也在 fixture 恢复 Mock 前保留，pytest 原失败状态不被转换成通过。
宿主把“诊断失败符合预测”和“测试通过”分成不同字段。

已有局部 solver 结果仅辅助说明 None 对当前条件的真假选择；它没有证明可替换性、
fallback 异常、消息断言或测试执行。见证脚本不调用 solver；离线回归单独重跑既有 solver 测试，不扩展其模型。

## 复现

在具有现有 Docker 镜像和审核 snapshot/runtime bundle 的主机运行：

```bash
PYTHONPATH=src .venv/bin/python experiments/dependency-witness/run_witness.py \
  --output out/dependency-witness/flask308-reproduction
PYTHONPATH=src .venv/bin/python -B -m pytest -q -p no:cacheprovider \
  experiments/dependency-witness/tests
```

输出目录必须不存在。脚本仅执行见证和诊断各一次，记录真实进程返回码及 pytest
setup/call/teardown。环境或来源不匹配则保留失败，不自动换库、改断言或扩大案例。
Docker socket 需要执行权限；本次访问通过宿主授权完成。

本次完整工作区：`out/dependency-witness/flask308-attempt1/`，未清理。
`results/` 是其中指定文件的可审阅副本。首次执行脚本已保存为工作区的
`executed_runner.py`，其 SHA 与原 summary 一致。执行后只强化了宿主离线验证：
要求异常帧精确匹配 fallback 行和当前实参；对原始执行事实重新验证通过，没有增加
目标运行次数，也没有重写原 summary 的执行脚本摘要。
离线回归：项目与新边界测试 **201 passed**，既有研究测试 **89 passed**。
排除含归档对照的 `tests/test_real_data.py`，本轮未执行该文件。
文件身份与测试命令见 [verification.json](verification.json)。

## 后续生成对照准备

[A](../../experiments/dependency-witness/generation-inputs/A/task.json) 是原始审核 UnifiedTask。
[B](../../experiments/dependency-witness/generation-inputs/B/task.json) 的 task 字节与 A 一致，
另附[人工场景指导](../../experiments/dependency-witness/generation-inputs/B/guidance.json)。
指导在执行之前整理，不含 Mock 实现代码、patch 指令或本轮执行结果。

[对照协议](../../experiments/dependency-witness/generation-inputs/protocol.json) 要求未来分别
启动独立新上下文，只暴露 A 或 B 的白名单文件，不能挂载整个项目或读取人工见证、
分析、历史候选及答案。目录分开本身不构成安全隔离；本轮只准备材料，未运行生成器。
没有付费请求，没有重用旧预算。人工指导有用也不能证明 Scenario Agent 自动推导有效。

## 停止结论

这一行的依赖行为方案已通过合法 Mock 落地，并由成功/失败对照证实影响原测试观察。
四个其他参数行、替代场景、真实库语义、自动生成效果和 Scenario Agent 效果均未验证。
不接回生产 Agent，不升级 Schema，不扩展分析器或生成框架；本轮在此停止。
