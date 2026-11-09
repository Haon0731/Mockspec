# 组件对照、交互敏感性与冻结迁移

2026-11-04。读者：MockSpec 研究维护者；归属：独立研究路径。承接
[目标关系实验](../goal-pilot/README.md)。本轮结束：八次独立修订、十次真实
Java 执行、一次不修改分析器的 Jib 迁移探针，以及后端实现调查均已完成。
生产代码、审核记录、冻结分析器和旧实验结果保持不变。

**本轮支持优先研究“小型目标关系＋候选冲突诊断”，但不支持把大份关系摘要
直接作为稳定改进方案。** 仅诊断组通过 2/2，关系加诊断组通过 1/2，源码组和
仅关系组各 0/2。Jib 迁移没有建立可用目标关系，说明现有组合器还不能泛化。

## 1. 四组实验实际测量了什么

[执行前协议](protocol.md)与[冻结输入清单](inputs/manifest.json)先于生成建立。
四组共享同一个原始错误候选、82,613 字节的可见源码/任务包及完全相同的修订
指令；每组两个全新 `executor` 上下文，一次输出，无测试反馈或择优重跑。
不同 packet 由上一轮自动生成的 E packet 机械删字段得到。

| 组 | 额外材料 | packet 字节数，含共同候选 | 原测试通过 | 实际响应 |
|---|---|---:|---:|---|
| C | 无 | 612 | 0/2 | 两次都是 `health` |
| R | 目标关系、路径及依据 | 42,090 | 0/2 | 两次都是 `health` |
| D | 原候选局部检查诊断及适用范围 | 2,268 | 2/2 | 两次都是 `actuator/health` |
| RD | 目标关系与诊断 | 43,418 | 1/2 | 各一次 |

八个候选全部编译成功。五个失败都在原普通 URL 断言处，实际值缺少
`actuator/`，不是基础设施故障。完整候选在 [candidates](candidates/)，
原执行回执在 [junit-original](junit-original/)，汇总由
[summarize.py](summarize.py)从回执计算为 [summary.json](summary.json)。

D 的信息不是一个“失败”标签，它给出了可复核的局部冲突：

```text
当前候选：health 响应 = "health"
组合得到：http://application/Uppercase/health
原断言要求：http://application/Uppercase/actuator/health
范围：当前投影属性、正常完成假设和受限 URI 库摘要
```

它没有提供正确 Stub 文本；Agent 仍需结合源码选择修复。RD2 尽管获得同一诊断，
仍把管理基础 URL 解释成含 `/actuator`，保留错误 Stub。完整工具轨迹没有导出，
因此不能断言它忽略了哪段输入，也不能把失败原因确定为上下文过长。

**可观察结论是：本次小样本中，短诊断比完整包的生成结果更好。** “关键冲突被
长包淹没”只是待验证解释。上一轮 RD 等价条件为 2/2，本轮为 1/2，不能把上一轮
两次成功当稳定效果；也不能从本轮 n=2 推出删除关系一定更优。

这些结果支持保留生成诊断所需的后台组合关系，同时单独研究 Agent 的呈现接口。
一个合理的下一版本是先展示目标、冲突和最短证据链，按需展开完整路径与来源。
本轮没有实现或评价这个新版本。

## 2. 数据流与近似路径约束各自提供什么

该案例的有效关系是：

```text
health 调用返回 rh
  ├─ hasText(rh)  → append("http://application/Uppercase/", rh) == 原目标
  └─ !hasText(rh) → 再调用 status 得到 rs
                    hasText(rs) 且 append(base, rs) == 原目标
```

数据关系确定“哪个响应经过什么变换影响断言”；条件确定“采用哪个响应，以及
哪些调用会发生”。只给分支条件不足以发现 URL 拼接错误，只给数据来源又不足以
区分 health 与 status 的互斥场景。组合两者后，检查器才能指出具体候选与目标
冲突，或保留 `unknown`。

不能把第一条路径硬化为“唯一正确响应必须等于 `actuator/health`”。上一轮真实
执行已验证斜杠变体和 status 回退场景也能通过，见
[合法替代场景回执](../goal-pilot/README.md)。本轮三个成功候选恰好选择了同一种
直接 health 场景，并没有再次测量这些替代场景。

## 3. Mock Assertions：发现了什么，尚未发现什么

通过原测试的 D1、D2、RD1 注入源码 SHA 完全相同。只对代表 D2 执行两次事先
固定的交互变异，再通过 [interaction-map.json](interaction-map.json)映射回
三个候选；这是两个实际变异执行，不能称六次独立检出。

| 临时源码变异 | 目标值 | 真实结果 | 说明 |
|---|---|---|---|
| 重复一次 health 查询，忽略新返回值 | 保持 | Mockito `TooManyActualInvocations` | 生成的 `verify(...).getPath(health)` 检测到两次调用 |
| 在非空 health 分支额外查询 status，忽略返回值 | 保持 | JUnit 通过 | 没有检查 status 未调用或额外交互 |

第一种失败定位在生成的 verify 行，证据见
[重复查询 JUnit 输出](junit-mutants/D2/duplicate_health_lookup/test.stdout.txt)。
第二种见 [额外 status 查询回执](junit-mutants/D2/unexpected_status_lookup/result.json)。
变异只改既有 evaluator 创建的临时审核源码副本，回执保留原/变异源码哈希、唯一
匹配数量、diff、候选身份、命令和失败分类；普通断言与输入不变。

**这测的是交互敏感性，不是两个已确认业务缺陷。** 当前 health 路径不会查询
status，不自动意味着任何额外查询都违反测试契约。若业务只要求最终 URL，强加
`never(status)` 可能过约束合法实现；若有明确的“不触发回退查询”目标，它才应
成为候选交互断言。本轮没有证明该额外业务要求，因此不把存活直接计为错误。
后续证据接口应分开表达调用发生条件、观察目标要求与可选断言的契约依据。

## 4. 冻结迁移：Jib 暴露了实际缺口

[transfer_probe.py](transfer_probe.py)重新通过 Adapter/SourceStore 核验已保留的
三份 Jib 源码，运行上一轮相同哈希的 GoalIR 与 Composer，没有补目标适配器。

- 编译前端加解析约 2.44 秒，组合约 0.00086 秒；三个类型、七个不支持的 AST 节点。
- 识别两个字面量 `assertEquals` 目标：`baseImage` 与 layer `name`。
- 两个目标都只有 `unknown` 路径，零 Mock 事件，目标证据只涉及测试文件。
- 核心诊断为 `assertion_target_not_projectable`；builder 到 build plan、集合元素
  getter 的连接没有建立。另有 setup 调用不支持的显式诊断。

见 [原输出](transfer/goals.json)及[运行和身份记录](transfer/metrics.json)。
这不是“成功获得两条路径”：两条都是未解析结果，也没有证明 Mock 边界处理已
迁移成功。GoalIR 解析了允许可见的占位源码，但目标结果没有消费 processor 方法
体证据。占位 `throw UnsupportedOperationException()` 不能被解释成真实依赖行为。
Jib 先前已探索过，因此这不是新留出集；本轮也没有生成或执行 Jib Mock。

## 5. 技术路线决定

1. **继续目标导向切片＋近似路径约束。** 后台保留返回值、异常、条件与调用事件的
   关系；候选检查输出一致、冲突或未知，而不是把“没有发现矛盾”叫正确。
2. **优先缩小呈现单元。** 先测试一个目标、一段值变换、一个冲突及可追踪依据，
   完整关系按需查询。诊断不能脱离产生它的假设；不要把 40 KB 包直接当稳定接口。
3. **暂停扩写自研 Java 解释器的新语法。** Jib 已显示依赖目标对象/库状态投影的
   脆弱性。WALA 1.7.2 是下一次后端实验首选，调查发现其现成 SDG/slicer 可覆盖
   调用参数、返回、heap 与控制关系；它不能替代 Mock 边界、路径约束或测试契约。
   [固定版本 Slicer 实现](https://github.com/wala/WALA/blob/v1.7.2/core/src/main/java/com/ibm/wala/ipa/slicer/Slicer.java)
4. **下一次后端实验设明确门槛。** 在隐藏 Mock 方法体的前提下，自动接通一条跨文件
   目标值链和一条带条件/参数的调用事件链，保留未知 heap 效应与源码位置。先构图
   后过滤真实 Mock 实现不合格。完整 SDG 在预算内做不到就保留失败证据，不缩小
   输入后冒称通过。本轮 WALA 只有[上游实现调查](backend-note.md)，没有运行结果。
5. **暂不加入完整 SMT。** 本轮直接暴露的瓶颈是证据呈现与跨对象状态投影；没有
   证据表明求解器能解决它们。后续只在已取得正确关系、仍存在难以判定的具体路径
   可满足性问题时，单独验证小型求解器是否提供增益。

正式质量研究需要新审核案例、按仓库留出和多次配对生成；分别测完整测试通过、
交互故障检出、合法替代实现误拒绝、关系错误/缺失率及成本。组件比较还应控制
证据长度、顺序与候选锚定，不能沿用本轮四格 n=2 来估算主效应或交互效应。

## 6. 成本、产物与复现范围

本轮预算 24 次目标执行，实际 10 次：8 原程序＋2 变异，无基础设施失败，无额外
库 oracle。编译加 JUnit 累计约 309.51 秒，原程序每次中位约 31.31 秒；这是本机
本轮观察，不是性能基准。生成实际 token、费用、服务端模型 ID 和随机种子未由
原生工具提供；角色配置、启动次序、读取自报和候选哈希见
[generation-provenance.json](generation-provenance.json)。没有操作系统级盲化。

新增代码限于本研究目录：机械拆分输入、冻结迁移探针、临时交互变异包装、限额
执行和回执汇总。沿用既有 Java evaluator、GoalIR/Composer、检查器、Docker
镜像和审核依赖，未复制第二套引擎，未给项目新增依赖。旧引擎保留是因为其输入
与算法哈希构成本次对照；本轮没有替换生产路径。

```bash
# 对已保存回执审计并重建汇总，不执行目标
.venv/bin/python experiments/backward-slicing/component-pilot/summarize.py

# 验证研究代码；默认不运行旧数据依赖的库 oracle 集成项
PYTHONPATH=src .venv/bin/python -m pytest -q -p no:cacheprovider experiments/backward-slicing
.venv/bin/python -m ruff check experiments/backward-slicing
```

新研究运行用独立 checkout，保护已留证文件。先运行 `prepare_inputs.py`，再按
schedule 用全新上下文生成一次候选；通过 `run_evaluations.py --stage original`
以及 `--stage mutants` 执行。两者需 `--candidates` 和本机 `--old-root`；脚本
拒绝覆盖原执行，账本累计限制 24 次。`transfer_probe.py --old-root ...` 同样
拒绝覆盖已有结果。LLM 生成本身不能靠脚本精确重现。

工程检查为研究目录 **38 passed、1 skipped**，Ruff 与 diff 检查通过，见
[verification.json](verification.json)。跳过的是默认关闭的旧库 oracle 环境集成项；
本轮没有重跑该批纯库查询。通过测试不等于已证明生成质量提高。没有修改生产
推断链、晋升研究能力或更新输入准入记录。
