# Issue #24 Python 后端能力门结果

结论：CodeQL Python 2.27.4 能提供有价值的成熟分析内核，但 Issue #24 能力门仍为
`blocked`（`gate_passed=false`）。控制流、覆盖后的值流、跨过程值流、字段节点和未知异常由
后端可靠恢复；未知返回别名、潜在写入及“写后抛异常”目前只有 QL 查询产生的保守候选边，
没有注入可传播的 flow／heap 模型，也没有证明异常处理路径上的状态传播，不能算机制通过。

## 实际命中

- 覆盖：`unknown_value()` 的旧返回值没有到达 `observe`，常量 `0` 到达。
- 跨过程：`actual -> formal -> return -> observe` 命中同一调用路径。
- 异常结构：`fill` 同时存在 normal 与 exception 边；exception 分别进入 `except` 与
  `finally`，提前 return／显式 raise 后没有到后续 `observe` 的 CFG 路径。
- 状态：可见 `alias.ready = True` 到 `box.ready` 命中；写入新建 `fresh` 对象没有关联到
  `box.ready`。
- 未知边界：`fill(box)` 的返回值到后续 `returned` 使用由后端恢复；查询也生成了返回可能
  别名实参、可能写入 `box.ready`、可能抛异常、可能写后抛异常的候选行。其中只有异常和普通
  值流是后端关系；alias／may-write／write-before-raise 还不能作为可传播分析证据。
- Flask：冻结 masked test 的 `abort(...)` 解析到 `src/src/flask/helpers.py:265`；
  `ctx.app.aborter` 正常完成时到达 `_wz_abort`，同时后端把 `aborter` 识别为可能抛未知异常并
  从当前函数异常退出。因此 fallback 只属于正常继续路径，不会把“可能抛异常”误写成“无影响”。

逐项机器判定见 `verdict.json`，实际关系见 `probe-*.csv` 与 `flask-*.csv`。14 项关系／结构检查
命中，`unknown_effect_model_propagation_verified` 与 `exception_path_state_propagation_verified`
两项明确为 false。只有调用节点或查询拼出的标签不用于宣称状态能力成功。

## 边界与后续风险

- 当前正向证据只覆盖“直接实参及其直接字段”。任意深度对象图、容器元素、两个形参运行时
  可能别名等仍未证明；不能把不同 local source 当作不别名证明。
- `effects.ql` 按探针函数名和 `fill` 方法过滤，只是测量查询，不是可复用未知效果模型。
- 下一步必须把 unknown return alias／may-write 作为后端模型边注入并验证下游传播，再用异常
  handler 中的状态观察证明“写入可能发生在抛异常前”；仅给调用贴标签不满足能力门。
- CodeQL CLI 本轮只按其条款用于有界学术研究。生产或 CI 使用不在本结论内。
- Joern 4.0.679 因发行包约 1.84 GB 且要求 JDK 21，超过预登记 150 MB 预算并与宿主默认
  JDK 11 不符，本轮未运行；这不是 Joern 能力否定。

## 成本与复现

- 两个最终数据库创建分别 4.70 秒／7.53 秒，峰值约 196／208 MB。
- 最终阶段所有 DB 与查询时间求和 188.13 秒，无超时；峰值 RSS 1,950,700 KiB。
- 首次探针查询冷编译峰值 2,133,984 KiB，单条最长 50.03 秒。
- 二进制、数据库、BQRS 与分析源码树只保存在 `/tmp`；本目录 仅保存哈希、身份、CSV、日志和
  判定。`run_pilot.sh` 会拒绝覆盖既有输出，并始终在临时目录建库。

## 主要外部证据

- CodeQL 的 Python 数据流与控制流 API：
  https://codeql.github.com/docs/codeql-language-guides/analyzing-data-flow-in-python/
  和 https://codeql.github.com/docs/codeql-language-guides/analyzing-control-flow-in-python/
- 未识别调用的未知异常语义：
  https://codeql.github.com/codeql-standard-libraries/python/semmle/python/types/Exceptions.qll/module.Exceptions.html
- Python 自定义摘要仍标注为 beta：
  https://codeql.github.com/docs/codeql-language-guides/customizing-library-models-for-python/
- CodeQL CLI 使用条款：https://github.com/github/codeql-cli-binaries/blob/main/LICENSE.md
- Joern Python frontend、切片与 Apache-2.0：
  https://docs.joern.io/frontends/python/、https://docs.joern.io/cpg-slicing/、
  https://github.com/joernio/joern/blob/master/LICENSE
