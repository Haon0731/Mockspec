# Python 后端能力门预登记（Issue #24）

## 目的与边界

本探针只判断成熟 Python 分析后端能否为后续 Flask、状态和未知效果分析提供最低机制。
输入为一个冻结 Flask 案例和一个机制探针；不执行目标测试、loader 或被分析代码，不读取
隐藏答案，不修改冻结 snapshot，不运行 165 例。结果是研究证据，不进入生产 Scenario Agent。

## 候选与选择

- 首选 CodeQL Python bundle 2.27.4。选择原因是官方 Python 控制流、局部／全局数据流、
  属性读写节点和异常流 API，可用约 111 MB 的语言专用 bundle 做有界验证。
- Joern 4.0.679 保留为备选。它提供 Python CPG 和 JSON 数据流切片，采用 Apache-2.0；
  本轮不下载约 1.84 GB 的发行包。其当前文档要求 JDK 21，宿主默认 JDK 为 11。
- 不以现有 AST 候选图、历史路径解释器或手写等价业务逻辑替代成熟后端。

CodeQL CLI 的许可允许学术研究，但限制其他自动化分析场景；因此本轮只在独立研究探针中
使用，不加入生产依赖或 CI。任何后续产品化都需要重新确认许可。

## 固定配置与资源预算

- CodeQL bundle：`codeql-bundle-v2.27.4`，Python/Linux x86_64 专用包。
- 下载预算：150 MB；下载物和解压内容只放 `/tmp/mockspec-issue24-tools`。
- 每个数据库创建最多 180 秒；每条查询最多 180 秒；整个探针最多两个数据库。
- JVM/查询 RAM 软预算 4 GiB；记录 `/usr/bin/time -v` 可得到的峰值内存。
- 冷运行与复用同一数据库的查询分别记录；不声称 OS、JVM 或包缓存完全冷。
- 产物写入新的 `out/mockbench-study/issue24-python-backend-20261110`，已有目录拒绝覆盖。

## 运行前固定判别预期

机制探针固定包含以下关系：

1. `old = unknown(); old = 0; observe(old)`：应排除 unknown 返回值到 observe 实参的值流；
   unknown 调用可能抛异常仍是后续 observe 能否执行的未知条件。
2. `forward(actual) -> formal -> return -> observe`：应保留匹配调用上下文的跨过程值流，
   不把另一个同名或无关调用上下文串入。
3. 提前 `return` 后的语句不可达；显式 `raise` 后后继不可达；`except` 恢复和 `finally`
   在正常／异常分支的执行均须可区分。
4. 可见 `box.ready = True` 与别名写入应关联到后续 `box.ready` 观察；已证明不同对象且
   无共享状态的写入应排除。
5. 不可见 `dependency.fill(box)` 必须能表示：返回可能与参数别名、可能写入从 `box`
   可达状态、可能抛异常、可能在抛异常前写入。若后端只提供调用节点或缺省无效果，门禁失败。
6. 确定关系、后端分析得到的 may 关系、外加的保守未知关系必须分开，不能把查询命中
   当作 must 或路径可行性证明。

冻结 Flask 输入固定检查 `abort` 中上下文查询、条件调用和 `_wz_abort`：若 app aborter
正常返回，fallback 仍可执行；若它抛异常，fallback 不执行。原样 masked test 和可见产品
源码必须进入同一数据库并映射回路径／行号。仅创建数据库或出现 CFG 节点不算通过。

## 停止条件

只有上述六类机制和真实 Flask 映射全部取得正向、可复现证据，Issue #24 的能力门才通过。
任一关键机制缺失时，保存具体查询结果、工具配置、成本及阻塞，结论保持 `blocked`；不通过
扩大预算、换成旧原型或补造 Mock 行为来取得成功。

## 修复后模型边界（verdict v2）

修复后的门禁只接受 `unknown-effect-flow.ql` 与 `unknown-exception-state.ql` 同一行中携带的
调用点、区域、字段、中间节点、观察点和出口类型；原 `effects.ql` 保持 candidate 身份。
这是 CodeQL CFG／value-flow 之上的显式有界联合模型，不是 CodeQL 原生通用 heap 分析。

支持域限于直接位置实参区域、一次返回别名后的同字段写、唯一 local-source 区域上的支配
覆盖，以及同一 try 中到 handler／finally 字段读的异常 CFG 路径。fresh 负例只使用受信的
`mechanism_probe.py` 中无基类、元类、`__new__` 或 `__init__` 的 `Box` 构造器，并且只证明
所选实参／返回别名通道不相交。

状态边界扩展使用独立的 `state-boundary-flow.ql`、通用 `visible-state-flow.ql` 和统一
observation mapper。可见字段查询不使用案例函数名或字段名选择器；边界查询按具体函数、调用点、
对象区域、字段和观察点返回 receiver、实际模块全局绑定，以及经可见不透明实参传递
后逃逸对象的保守 may-write 路径；这些路径进入与值流、异常流相同的逐观察结果契约，不只是
结尾的 unknown 标签。确定支配的同对象同字段覆盖会排除旧影响；循环中出现在调用前、但可经
回边被调用到达的写入不被误判为覆盖。已知可见纯调用、调用后创建且未逃逸的可信 fresh
对象、局部同名变量和覆盖后的读取作为负例。不同形参的 local source 不证明运行时不别名，
因此 receiver／observed 参数和 retained／observed 参数案例保留为 unknown；模块全局对象重绑定
后的别名关系同样保留为 unknown。该扩展仍不推断具体写入值或 hidden 实现语义，也不声称
receiver、global 或 escaped 关系为 must。

任意传递堆、动态字段、property、`__setattr__`、容器元素、多 local-source 区域、隐式逃逸，
以及没有可见不透明实参传递证据的跨调用状态不在支持域内；这些情况必须保持显式 unknown，
不能被解释为无影响。
