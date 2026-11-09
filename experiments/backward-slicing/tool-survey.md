# 面向 Mock 行为推导的跨过程后向切片：工具调查

日期：2026-11-04。范围是已审核输入投影上的离线分析；不读取旧 Stub-Tester、Reference Mock 或未审核依赖。本文件区分上游实现能保证的图关系、现有仓库实际观察，以及 Mock 场景仍需由本项目完成的推断。

## 结论

**WALA 是最贴近题意的 Java 专用候选，值得作为下一阶段的受控对照；本轮未运行或接入生产。** 它的 `Slicer` 确实以调用图、指针分析和 SDG 为输入，使用部分平衡 IFDS tabulation 求可实现的跨过程后向路径；可选择保留堆、异常和跨过程控制依赖。因此它能回答“一个已选测试准则受哪些可见调用返回、参数、堆读写和条件控制”。本轮已有可用的 Spoon/AST 原型用于跨语言源码位置和词法关系候选，但它不是 SDG 的等价验证。

它不解决 Mock 的关键最后一步：把断言反推成唯一可行路径、赋予不可见依赖一个唯一返回值/异常、或决定某次交互是否应断言。那些是由测试目标、可见代码和 Agent 共同完成的场景解释，必须以多候选/unknown 表示。WALA 有 Java source frontend（JDT/ECJ）和 Java bytecode 分析路径，但没有 Python frontend；不能成为本项目 Java 与 Python 的统一后端。

Joern 已经在本仓库的完整受限输入上做过更强的实测，结果不支持本轮作为统一后端：Java 完整测试输入的 `ReachingDefPass` 失败，Python 的深切片超时且固定版本的过滤/输出选项不可靠；详见 [`docs/step5b/report.md`](../../docs/step5b/report.md) 与 [`docs/step5b/compare.md`](../../docs/step5b/compare.md)。不应丢弃这些失败后再重复小样本成功。

Soot/FlowDroid 不是这项任务的优先后端。旧 Soot 的内置 PDG 是 `UnitGraph`（单个方法体）上的控制依赖结构；把它扩展成跨过程 SDG/切片需要本项目自己补关键部分。FlowDroid 是 source-to-sink taint/IFDS 工具：可作 Java/Android 的定制数据流实验，却要求源/汇定义、摘要和别名策略，且不产出通用控制依赖后向切片或 Mock 交互模型。

## 需求到能力的判据

| 必需事实 | 对 Mock 的用途 | 可接受输出 |
| --- | --- | --- |
| 测试准则（断言值、异常断言、交互事件、参数/次数） | 切片起点，不限于 MUT return | `criterion_kind`、测试/源码位置、观察对象 |
| 跨过程数据依赖 | 找到依赖返回/异常/写状态如何到达准则 | 有向关系和调用上下文；不能把“可能”写成确定响应 |
| 控制依赖和 guard polarity | 说明依赖值为何选择某分支、调用何时发生 | 条件源码、true/false/exception 边、支配范围 |
| 堆/别名与异常 | 覆盖 stateful mock、void side effect、throw/catch | `heap`/`exception` 关系或明确 `unknown` |
| 缺失实现边界 | 防止分析器用猜测补全 mock | `external_call`、resolved-target count、分析配置 |
| 交互事件 | 让 Mock assertion 成为可追溯候选 | 调用点、receiver/参数、guard、可达上下文；次数仍需专门的 CFG/path 摘要 |

“有 data-flow path”不等于路径可满足、某一调用必达或一次调用；切片是证据收集层，不能替代场景求解。

## 候选工具：实现审计与适配性

| 工具（锁定调查版本） | 实现实际做什么 | 对本课题的覆盖 | 主要缺口/成本 | 判断 |
| --- | --- | --- | --- | --- |
| **WALA 1.8.0** | `Slicer.computeBackwardSlice` 构造 `SDG(cg, pa, ModRef, dataOptions, controlOptions)`；`Slicer` 将 SDG 反转成 supergraph，交给 partially-balanced tabulation solver。SDG 为每个 call-graph node 懒建 PDG；`ModRef`/pointer analysis 建模跨过程 heap mod/ref。 | Java source frontend 或 bytecode 路径上的跨过程、context-sensitive slice；`FULL` 可保留 scalar、heap、exception 数据边以及跨过程/异常控制边。调用实参与被调参数、返回值和可能 target 的连接在 SDG 中明确实现。 | 需要入口点、analysis scope、JDK/标准库和选定 frontend 所需的源码或 bytecode；调用图过近似与无目标调用决定结果上界。source frontend 的缺失第三方 JAR 行为也需实测。没有 Python；源码位置投影、unknown frontier 和 interaction criterion 都要由适配层实现。 | **下一阶段 Java-only 对照首选**。 |
| **Joern 4.0.658（仓库已实测）** | CPG 的 CFG/CDG/REACHING_DEF 与 `joern-slice data-flow`；后者从 call argument 反向做 DDG slice。 | Java/Python 统一图面、源码位置、调用/控制/数据关系，理论上适合事实提取。 | 本仓库完整 Java 测试输入 DDG 构建失败；Python 深度 20 超时，深度 4 的范围控制不可靠。 | **本轮统一后端 No-Go；可保留为隔离研究辅助。** |
| **Soot 4.7.0 / SootUp** | 旧 Soot 提供 Jimple、调用图和 points-to；其 `HashMutablePDG` 从 `UnitGraph` 构造 PDG，接口重点为 control-dependence regions。上游明确说 SootUp 尚未 feature-complete。 | Java bytecode 上可作为自定义 IFDS/IDE 的底层；控制依赖可局部取得。 | 内置 PDG 不是现成跨过程数据+控制 SDG slicer；LGPL-2.1；要编写汇总、跨过程边、源映射和 traversal。 | **不用于本轮原型。** |
| **FlowDroid 2.12.0（README 坐标）** | `Infoflow` 以 source/sink manager 扫描语句，并以双向 ICFG、alias problem 和 IFDS 求解器传播 taint；反向 solver 用于 aliasing/reverse rules。 | 若把每个依赖调用标 source、断言标 sink，可测试敏感的跨过程数据传播和异常/静态字段开关。 | 它解决的是 taint reachability，不是任意 criterion 的 complete backward SDG；没有通用 CDG/guard/call-count 输出，source/sink 与 summary 的选择会把 Mock 先验写进分析。构建需要 JDK 11+、Maven 和当前 Soot snapshot，完整测试还有 Android/DroidBench 前置条件。 | **仅作以后“数据传播召回”消融，不作主切片器。** |

### WALA 的源码证据

以下均为上游 `v1.8.0` 的固定 tag，而非移动分支。

* `Slicer.java` [L31–38](https://raw.githubusercontent.com/wala/WALA/v1.8.0/core/src/main/java/com/ibm/wala/ipa/slicer/Slicer.java) 把实现定义为 context-sensitive、使用 pointer analysis 的 slicer；[L164–173](https://raw.githubusercontent.com/wala/WALA/v1.8.0/core/src/main/java/com/ibm/wala/ipa/slicer/Slicer.java) 直接以 `CallGraph`、`PointerAnalysis`、`ModRef` 和选项构造 SDG；[L235–253](https://raw.githubusercontent.com/wala/WALA/v1.8.0/core/src/main/java/com/ibm/wala/ipa/slicer/Slicer.java) 使用 partially-balanced tabulation solver。此处的“context-sensitive”是切片路径的调用/返回匹配语义，不是对动态反射或未知依赖行为的保证。
* `DataDependenceOptions` 明确提供 `FULL`、去 heap、去 exception 等配置，[L45–108](https://raw.githubusercontent.com/wala/WALA/v1.8.0/core/src/main/java/com/ibm/wala/ipa/slicer/Slicer.java)；`ControlDependenceOptions` 可保留/去掉异常与跨过程控制依赖，[L115–158](https://raw.githubusercontent.com/wala/WALA/v1.8.0/core/src/main/java/com/ibm/wala/ipa/slicer/Slicer.java)。Mock 场景原型应采用 `FULL/FULL`，同时输出每类边，不能为了缩小图静默关掉 heap 或 exception。
* SDG 源码声明“每个 method 一个 PDG”，并记录 governing call graph/pointer analysis 与 transitive `mod`/`ref` 集，[`SDG.java` L50–95](https://raw.githubusercontent.com/wala/WALA/v1.8.0/core/src/main/java/com/ibm/wala/ipa/slicer/SDG.java)；对 caller return 它遍历 call graph 的 `getPossibleTargets`，分别连接普通、异常和 heap return，[L267–331](https://raw.githubusercontent.com/wala/WALA/v1.8.0/core/src/main/java/com/ibm/wala/ipa/slicer/SDG.java)。WALA 不会把 `target_count=0` 自动概括成“保守的未知 Mock effect”；本项目适配层必须把零/未解析/synthetic target 转成 Mock frontier，而不是让 Agent 填补。
* PDG 对调用定义出的普通/异常返回分别建 `NormalReturnCaller`/`ExceptionalReturnCaller` data edge，[`PDG.java` L519–550](https://raw.githubusercontent.com/wala/WALA/v1.8.0/core/src/main/java/com/ibm/wala/ipa/slicer/PDG.java)；heap reaching-def 依赖 pointer analysis/mod/ref 并以 `HEAP_DATA_DEP` 落边，[L598–673](https://raw.githubusercontent.com/wala/WALA/v1.8.0/core/src/main/java/com/ibm/wala/ipa/slicer/PDG.java)。这正覆盖 return、throw 与 stateful mock 的三类候选影响，但其精度受 points-to/call graph 约束。
* WALA 的官方 release 页面表明 1.8.0 是最新 release，运行 JDK 17+；仓库 README 也明确 Java/Android/JavaScript frontend 范围，而没有 Python frontend。[release notes](https://github.com/wala/WALA/releases) [repository README](https://github.com/wala/WALA) WALA-start 的 `SourceDirCallGraph` 是 Java source directory 经 `JavaSourceAnalysisScope` 与 ECJ frontend 建图的上游示例。[source driver](https://github.com/wala/WALA-start/blob/master/src/main/java/com/ibm/wala/examples/drivers/SourceDirCallGraph.java) 这证明 source 路径存在，不证明它已在本项目的缺依赖审核投影上通过。许可证为 [EPL-2.0](https://raw.githubusercontent.com/wala/WALA/v1.8.0/LICENSE)。

### Soot/FlowDroid 的源码与上游证据

* Soot 上游称旧 Soot 已由 SootUp 继任、但 SootUp 尚未 feature-complete；旧 Soot 提供 bytecode/Jimple/Shimple 等 IR，发布坐标为 4.7.0。[Soot README](https://github.com/soot-oss/soot) 旧 PDG API 的 `HashMutablePDG` 以 `UnitGraph` 构造，并将 `dependentOn` 定义为 control dependence，而非系统级 data slice。[PDG API](https://www.sable.mcgill.ca/soot/doc/soot/toolkits/graph/pdg/ProgramDependenceGraph.html) 这支持“不能直接当成 WALA SDG 等价物”的判断。
* FlowDroid 官方 README 说明它静态计算 Android/Java 的 data flows、CLI 以 Sources/Sinks 文件为输入，并把“源/汇定义与摘要”交给用户配置；它还公开 `-ne`（不追踪异常）、超时和 JDK/Maven 构建条件。[README](https://raw.githubusercontent.com/secure-software-engineering/FlowDroid/develop/README.MD) `Infoflow.java` 的源代码可见 `ISourceSinkManager` 扫描、`BiDirICFGFactory`、`BackwardsInfoflowCFG` 和 alias 策略，而不是从任意 criterion 导出 CDG 的通用后向切片。[Infoflow.java](https://raw.githubusercontent.com/secure-software-engineering/FlowDroid/develop/soot-infoflow/src/soot/jimple/infoflow/Infoflow.java) FlowDroid 为 LGPL-2.1，该项目 README 也说明修改扩展的许可义务。

## 本仓库实测证据如何改变选择

现有 [Step 5B report](../../docs/step5b/report.md) 不是工具宣称：它在只读、隔离的审核投影中实际观察到 Python 完整输入和 Java 生产子集的 CFG/CDG/REACHING_DEF；同时记录 Java 完整测试输入在 `ReachingDefPass` 异常、Python 深度 20 slice 在 180 秒超时，以及 v4.0.658 CLI 不保留部分 filter/output 配置。它已给出 Joern 为何不能满足“测试目标导向、跨文件、跨过程”完整链路。

这也说明新原型应避开“成功运行一个图工具”的弱验收。即使 WALA 以后通过，必须在**同一审核 Java 投影**上，以测试 assertion/exception/interaction criterion 作为 root，检查是否同时包含：测试 → MUT 调用、MUT → 可见 helper、external call frontier、branch guard，及至少一个异常或 heap 关系。否则只能称为 Java 切片可运行，不能称为 Mock 推导改善。

## 推荐的下一阶段最小对照（尚未执行）

本轮没有运行 WALA。保留现有 Spoon/AST 跨语言原型，并把 WALA 设为下一阶段的**一个 Java-only、可否决的实验**：

1. 先以一例已审核 Java 测试及允许产品源码走锁定的 JDT/ECJ source frontend；不下载项目依赖、不运行目标测试、不扩大 source root。若 source frontend 因缺失类型失败，将该失败保留为 source-path 结果；bytecode path 是独立的后续对照，不能用临时补全依赖掩盖 source-path 失败。锁 `WALA 1.8.0`、JDK、scope、entrypoint、call-graph builder 与超时/内存。
2. 对三个 root 分别调用 `computeBackwardSlice(..., FULL, FULL)`：断言使用值、预期异常的 throw/catch 或 `assertThrows` 参数、一次目标交互调用。保存 slice node、边类、CG context、对应 source range 和 unresolved frontier；不把 WALA 内部/stdlib 节点当源码证据。
3. 将 slice 归一化为给 Agent 的小事实包：`criterion`、`relation(data/control/heap/exception/call)`、`from/to source_id:range`、`call_context`、`resolution_status`、`evidence_level`。从 test/MUT/可见 helper 以外的节点只输出摘要和 `unknown`，不泄露未审核源码。
4. 与无 slice 的 Agent 在完全相同的遮蔽测试、模型、token/工具预算下比较：Mock slot 中可证实约束的 precision/recall、错误断言数、遗漏的 guard/exception/state 约束、以及人工判断的可替代合法场景数。测试通过率单独报告，不能替代 Mock 语义准确度。

停止条件：若 WALA 无法把完整测试准则映射到稳定 source ranges，或其 call graph 将关键 mock 调用解析为无目标/过宽目标而不能给出有用 frontier，则停止把 WALA 作为主路线；保留 Spoon/AST 证据和显式未知项，而不是自建另一套 SDG。

## 可复用结论

现成的跨过程后向切片能力确实存在：WALA 的 Java SDG 是最直接的开源实现，Joern 在 Python/Java 有统一图能力但已在本仓库的完整 Java 输入上被否决，FlowDroid/Soot 更适合定制数据流研究。研究主线应是“受控切片事实包 → Agent 场景候选 → Mock 约束/交互候选 → 对照评价”，而不是把任一 slice 当成 Mock specification。
