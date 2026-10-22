# 源码绑定的局部依赖行为检查

本阶段实现一条离线研究链路：审核 Adapter → 可见 SourceStore → AST/Spoon
事实及有限模式识别 → 类型化 cvc5 terms → 原生隔离检查 → 宿主契约绑定。
不调用 `models.py`、`build_examples.py` 获取答案，不更改正式 Schema、Prompt 或 Agent。

## 支持边界

唯一目标是：条件已到达、依赖调用正常返回时，该返回值能否使**当前局部条件**
选择指定真假分支。条件之前的路径可达性不在证明中；目标不是测试 oracle。

- Java：直接、零参数调用返回 int / Integer，参与简单整数常量比较（含反向操作数）。
  接收者静态类型须解析到可见接口声明的完整所有者名称；不凭实现类兼容关系绑定。
  整数范围固定为类型的 Int32 范围，方法、变量、运算符和阈值来自当前源码。对于装箱返回值，非空与正常返回是显式前提，不模拟拆箱异常。
- Python：直接返回值或可明确绑定的局部返回值参与 `is None` / `is not None`。
  跨语句仅支持紧邻条件的单一局部 Name 赋值，排除 global/nonlocal、别名和复杂控制流。
  使用 Bool 表示“返回值为 None”，不是 truthiness；不推断对象类、外部调用实现。
- 不展开跨过程数据流、循环、别名、异常传播、外部库语义，也不证明全局必要性。
  不能可靠识别的片段记录 unsupported / unresolved / not_formalized。
- Python 外部调用目标与可 Mock 控制性仍 unresolved。支持返回值谓词检查不等于
  证明可实现相应 Mock。研究接口只能约束局部观察值，不能配置 Mock。

每份模型记录保存完整任务身份摘要、各允许 source_id 的内容摘要、调用与条件事实、
符号类型与来源、可控性状态、场景与假设、局部目标以及未形式化部分。
这些摘要只是绑定已审核输入，不能用于自动更新准入白名单。

## Agent / 宿主职责

| 字段或动作 | 提议方 | 宿主 |
| --- | --- | --- |
| source_id、pattern_id | 选择 catalog 中已有引用 | 校验当前输入及允许引用 |
| question | `check_local_branch` / `infer_local_branch` | 拒绝其他目标 |
| goal_branch | 选择当前条件的 true / false 分支 | 从源码条件构造目标 term |
| candidate | 条件真假、整数正常返回值、None 身份的有限列表 | 检查类型，构造合取；空列表为 true |
| 类型、符号、边界、调用绑定 | 不可提交 | 从审核源码事实生成 |
| 假设、未知项、可控性 | 不可重写 | 保留未解析状态，不添加外部公理 |
| SMT / 任意公式、路径、脚本、oracle | 不允许 | 不作为提议字段接收 |
| 检查状态与公式身份 | 不可提交 | cvc5 工作进程生成并绑定 |

`LocalResearchHost.prepare(language)` 是受信准备入口，内部仅通过现有审核 Adapter
加载两个冻结输入，不接受调用方构造的任务或路径。`prepare_synthetic` 只用于宿主机制测试，
始终标记 synthetic，并核对来源摘要；Python 对象不是安全能力令牌，
恶意宿主代码不在此接口的隔离承诺内。未来 Agent 只暴露 `propose`，不暴露构造器。
没有新增通用 DSL，行为选择只映射到宿主支持的有限 term 构造器。

## 检查与契约一致性

cvc5 在工作进程内构造一次类型化 terms，并序列化实际查询的公式。
宿主根据来源与目标生成 model_id，保存检查产物的不可变副本，生成 check_id。
研究契约只复用该检查的公式；`bind_contract` 逐字段核对模型身份及完整公式，
不以“可解析”代替相等，不接受另一个手写模型的检查成功状态。原生 abduction
的文法仅为源码条件、其否定、true、false 四个原子；文法、调用目标及返回候选也纳入
公式身份，返回候选必须重新通过同样三项检查。这不是开放式行为发现能力。

充分性必须同时满足：候选与背景 SAT、候选与局部目标 SAT、候选与目标否定 UNSAT。
因此矛盾候选不能通过空洞蕴含成为充分条件。SAT/UNSAT 始终局限于显式前提下的
当前局部模型，`whole_test_verified` 恒为 false。

## 原生故障隔离

复用 `mock_spec.analysis_process.communicate` 的固定子进程、受限环境、超时终止和
输出限额；在工作进程导入 cvc5 之前设置地址空间 512 MiB、CPU 4 秒、
文件大小 1 MiB、core=0；宿主墙钟 5 秒、每路输出 256000 字节。
同时修正共享 communicate 的轮询取消问题：用同一个 shielded wait task 等待退出，
异常时 kill 并确认已回收才关闭临时文件；增加滞后协议等待的回归测试。
解析、检查和原生 abduction 均在此边界内；宿主不导入 cvc5。
错误不会成为 UNSAT 或“无合法场景”。原有
`experiments/scenario-research/results/parser-error.json` 保持原样；这里隔离故障，
不声称修复了上游 parser。

## 复现

沿用已有研究环境和固定依赖，不需要模型 API、目标执行或新数据：

```bash
PYTHONPATH=src /tmp/mockspec-contract-cvc5/bin/python \
  experiments/scenario-research/run_local.py --output /tmp/mockspec-local-result
PYTHONPATH=src /tmp/mockspec-contract-cvc5/bin/python \
  experiments/scenario-research/probe_local_isolation.py --output /tmp/mockspec-local-isolation.json
PYTHONPATH=src /tmp/mockspec-contract-cvc5/bin/python -B -m pytest -q \
  -p no:cacheprovider experiments/scenario-research/tests
PYTHONPATH=src .venv/bin/python -B -m pytest -q -p no:cacheprovider \
  tests --ignore=tests/test_real_data.py
```

结果目录必须不存在。冻结输入与 Spoon 工具路径沿用项目已有配置，缺少工具或审核输入
明确记录失败或未支持，不改走归档输入。`tests/test_real_data.py` 中含归档生成后
Mock 对照，延续上一阶段排除；其余适用回归和原研究测试继续运行。
合成用例只验证机制，不计入 MockBench 样本数或效果评价。

## 实际运行证据

2026-10-22，参考提交 `cacef46f2f57931e3893bb11ebcb785bb9c939a6`。
实际产物：[local-results/report.json](../../experiments/scenario-research/local-results/report.json)，
同目录保存 12 个由实际 terms 导出的查询 SMT2 文件、各文件摘要与源码实现摘要。
故障产物：[isolation.json](../../experiments/scenario-research/local-results/isolation.json)。

| 审核案例 | 实际绑定 | 检查结果及未支持部分 |
| --- | --- | --- |
| Java 固定 JarFiles 案例 | `mut:69`，`processor.getJavaVersion() <= 8`，阈值和调用位置来自 Spoon | **unresolved，无 solver 检查**。静态接收者为 ArtifactProcessor，可见返回声明属于 StandardExplodedProcessor；缺少足够绑定证据，不用实现类代替接口，亦不恢复隐藏实现。其余条件与 builder 效果未建模。 |
| Python `flask_308` | `mut:282`，`(ctx := _cv_app.get(None)) is not None`，同表达式返回绑定 | Bool `response` 表示返回值为 None；true 分支目标 `(not response)`，false 分支目标 `(not (not response))`。两分支各执行候选检查与原生 abduction，共 4 份精确绑定结果；每份一致性 SAT、目标可达 SAT、反例 UNSAT。动态目标与 Mock 可控性仍 unresolved，后续 aborter/fallback、异常和消息断言未建模。 |

Java 类型完整的合成源码实际经过 Spoon → 宿主 → cvc5，验证重命名、阈值变更及
`<=` → `>` 会改变公式与结论；Python 合成源码实际经过 AST → 宿主 → cvc5，
验证 None / 非 None 不混淆。真实 Java 的降级是本轮结果，不计作通过。

故障探针包含 malformed InputParser 输入、注入 SIGSEGV、挂起超时、输出溢出、
超地址空间分配和普通 worker 异常。分别记录 parse_error、native_crash、timeout、
output_limit、worker_error、worker_error；每次之后新工作进程均能成功检查。
SIGSEGV 是机制注入，未声称本轮合法输入再次触发上游缺陷。旧 parser-error 原样保留。
新路径 unknown 透传由受控 worker 响应测试覆盖；旧研究的真实 RESOURCEOUT 测试继续保留。

最终研究测试 **89 passed**，适用项目回归 **191 passed**；命令与保留项核对见
[verification.json](verification.json)。项目没有独立 lint/typecheck 配置；
执行 Python compileall 与 git diff --check，不声称进行了未配置的类型检查。
一个辅助验证曾运行完整项目测试（194 passed），包含 4 项只读 test_real_data 对照；
最终验收单独排除该文件重新运行，不把归档对照作为本链路推导证据。

## 结论与停止范围

受支持片段可由源码事实自动构造并检查模型，契约与检查公式一致；Python 真实局部
片段及两种语言的合成机制通过。Java 真实调用的类型绑定缺口仍未解决，不能声称
两个真实案例都已完成自动形式化。未验证真实 Agent 提议、测试通过或 Mock 质量改善。
本轮在该受控链路停止，不新增求解器、Agent 框架、数据集或付费请求。
