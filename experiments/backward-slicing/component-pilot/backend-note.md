# WALA / Soot 后端可行性笔记（有界调查）

日期：2026-11-04
范围：只判断现有 Java 分析后端能否替代 GoalIR 中不断扩展的“调用/状态关系提取”部分；不安装依赖、不改生产、不承诺完整 SDG。

## 结论

下一步应只做一个 **WALA 1.7.2 的字节码后端实验**。它可以候选替代 GoalIR 自研解释器中的跨过程调用、SSA def-use、控制依赖、异常边和 heap mod/ref 关系层；它不能替代测试目标提取、Mock 边界语义、近似路径约束组合或 Mock 生成。

暂不选 Soot/SootUp：旧 Soot 4.7.1 能在 Java 11 上提供 Jimple、调用图和 Spark points-to，但没有与 WALA `Slicer` 等价的现成跨过程 SDG 切片入口，仍需自行拼接 IFDS/IDE/控制依赖；SootUp 当前 3.x 已把 source/target level 提到 Java 17，需要单独准备分析运行时。冻结目标测试的 JDK 11 **并不禁止**用另一个分析运行时读取 Java 11 字节码，因此版本差异只是部署成本，不能作为算法排除依据。优先 WALA 的主要依据是现成 SDG/slicer。Soot 官方也说明 SootUp 尚未 feature-complete。[Soot README](https://github.com/soot-oss/soot/blob/master/README.md)；[SootUp 3.0.0 release（Java 17 变更）](https://github.com/soot-oss/SootUp/releases/tag/v3.0.0)

这不是“切换到完整 WALA SDG”的决定。WALA 自己把 `SDG` 实现标作 “Prototype implementation. Not efficient”，所以实验必须以单案例、显式入口、显式 scope 和可中止预算为边界。[WALA 1.7.2 `SDG.java`](https://github.com/wala/WALA/blob/v1.7.2/core/src/main/java/com/ibm/wala/ipa/slicer/SDG.java#L66-L75)

## 本机事实与固定版本

- `/tmp/mockspec-step5b-tools` 只有 `spoon.jar` 和一套 JDK；项目内未发现 WALA、Soot 或 SootUp JAR。
- 系统运行时是 OpenJDK 11.0.32，Maven 3.9.12 可用，Gradle 不可用。后续实验在 `experiments/` 或 `/tmp` 建隔离 Maven runner，不给生产模块加依赖。
- 固定 `com.ibm.wala:com.ibm.wala.core:1.7.2`。WALA 1.7.2 发布说明明确说它是要求 JDK 17+ 之前的最后一个版本；1.8.0 起不适合本实验。[WALA 1.7.2 release](https://github.com/wala/WALA/releases/tag/v1.7.2)
- 走 Java **bytecode frontend**，不用 source frontend。官方 starter 要求最低 JDK 11，但同时注明 `SourceDirCallGraph` 从 WALA 1.6.4 起需要 JDK 17。[WALA-start requirements](https://github.com/wala/WALA-start/blob/master/README.md#requirements)

最小 Maven 声明只有：

```xml
<dependency>
  <groupId>com.ibm.wala</groupId>
  <artifactId>com.ibm.wala.core</artifactId>
  <version>1.7.2</version>
</dependency>
```

按已发布 POM，非 optional 运行闭包为：

- `com.ibm.wala.core:1.7.2`
- `com.ibm.wala.shrike:1.7.2`
- `com.ibm.wala.util:1.7.2`
- `org.jspecify:jspecify:1.0.0`
- `org.json:json:20251224`
- `com.google.code.gson:gson:2.13.2`（runtime）

不需要把 AssertJ、JUnit、Ant 或 JUnit Platform 带入 runner；它们在 core/util POM 中均为 optional。证据：[core 1.7.2 POM](https://repo.maven.apache.org/maven2/com/ibm/wala/com.ibm.wala.core/1.7.2/com.ibm.wala.core-1.7.2.pom)、[util 1.7.2 POM](https://repo.maven.apache.org/maven2/com/ibm/wala/com.ibm.wala.util/1.7.2/com.ibm.wala.util-1.7.2.pom)、[shrike 1.7.2 POM](https://repo.maven.apache.org/maven2/com/ibm/wala/com.ibm.wala.shrike/1.7.2/com.ibm.wala.shrike-1.7.2.pom)。

WALA 采用 EPL-2.0；Soot/SootUp 采用 LGPL-2.1。当前只是隔离研究 runner 且不修改/分发上游，许可证风险低于把库嵌入生产，但项目自身许可证未在本次外部依赖调查中核对，若晋升生产仍需单独确认分发兼容性。[WALA license](https://github.com/wala/WALA/blob/v1.7.2/LICENSE)、[Soot license](https://github.com/soot-oss/soot/blob/4.7.1/LICENSE.txt)

维护性和版本变化查上游发布记录；本轮固定 1.7.2 是为了复现，不把它称为最新版本。[WALA releases](https://github.com/wala/WALA/releases)、[Soot releases](https://github.com/soot-oss/soot/releases)、[SootUp releases](https://github.com/soot-oss/SootUp/releases)。

## 为什么 WALA 只替换“关系层”

WALA 1.7.2 的 `Slicer` 是 demand-driven、context-sensitive slicer；它先构建 SDG，通过 pointer analysis 建 heap data dependencies，再用 tabulation 找 realizable paths。[固定版本实现](https://github.com/wala/WALA/blob/v1.7.2/core/src/main/java/com/ibm/wala/ipa/slicer/Slicer.java#L37-L47)

对 Mock 任务有价值的不是最终那一袋代码行，而是它已经区分：普通语句、caller/callee 参数、normal/exception return、heap 参数/返回、方法入口/出口。[`Statement.Kind`](https://github.com/wala/WALA/blob/v1.7.2/core/src/main/java/com/ibm/wala/ipa/slicer/Statement.java#L26-L43) 这正好可以投影为现有证据图中的：

- `CALL / ARG / RETURN / THROW`
- `DATA_DEP / CONTROL_DEP`
- `HEAP_READ / HEAP_WRITE` 的 may-edge
- caller/callee 参数与返回的跨过程连接

`Slicer` 也允许独立控制 heap、exception、base-pointer、跨过程控制依赖；第一次实验应使用 `DataDependenceOptions.FULL` + `ControlDependenceOptions.FULL`，另跑一次 `NO_HEAP` 只作为成本/召回消融，不能把它作为面向状态 Mock 的正式配置。[固定版本选项和 backward-slice API](https://github.com/wala/WALA/blob/v1.7.2/core/src/main/java/com/ibm/wala/ipa/slicer/Slicer.java#L51-L112)

WALA 不直接提供以下 Mock 语义，仍由 GoalIR/上层组合器负责：

- “这个调用是被 Mock 的边界”以及可见实现必须停止遍历；
- 返回值内容、nullability、相等关系与异常选择；
- 分支谓词的近似路径约束及可满足性；
- `times(n)`、顺序、禁止调用等 Mock assertion 语义；
- 从 masked test 的 assertion/verification 提取测试目标。

因此接口应是 `WALA relation facts -> GoalIR normalized facts`，而不是让 WALA IR 泄漏到 Agent prompt。

## 未知 Mock 实现的边界策略

首个实验不要分析依赖的真实方法体，也不要为返回值编造具体 WALA summary。应在已审核的 Mock boundary manifest 中列出声明方法签名，在 IR/调用图构建前就阻止进入这些方法体，再在投影阶段执行下列操作。**分析真实实现之后再过滤输出不符合隐藏实现边界。** 这种截断是否保留所需 may-effect，正是尚未实测的核心缺口：

1. 保留 invoke statement、receiver、实参 SSA、normal-return SSA、exceptional edge 和 may-heap-effect。
2. 把返回和异常归一为带调用点、上下文和调用次序的新鲜符号；同一静态调用点被执行两次不能误当同一个返回值。内容、nullability、identity 都是 unknown，未展开循环只保留多次调用的近似信息。
3. 切片可以继续沿 **调用者中** 对返回 SSA 的 def-use 和控制依赖传播，但不跨入真实 callee body。缺失方法体时，FULL 选项本身不能恢复未知 heap 写集合；必须保留 unknown-effect，不能把缺边解释为没有副作用。
4. 后续虚调用若因 unknown reference 的 points-to 为空而丢失目标，只允许增加一个标注为 `declared-type may-dispatch` 的 CHA fallback；它不能生成等值、非空或具体实例事实。
5. 输出每个 unresolved target 和 fallback，不能静默把缺边当成“调用不会发生”。

暂不采用 WALA XML method summary。官方维护者说明这些 summary 基本靠手写且缺少正式文档；1.7.2 同期还有 synthetic-summary return 的 points-to 为空问题报告。它们适合后续针对少量稳定框架 API 建模，不适合用来表示任意 Mock 返回。[官方讨论](https://github.com/wala/WALA/discussions/1334)、[summary return 问题 #1889](https://github.com/wala/WALA/issues/1889)

## 最小可执行实验

实验只需一个已审核的 component-pilot 案例，不接入生产：

1. 用冻结 JDK 11 对已审核可见源码执行 `javac --release 11 -g`。classpath 只能来自该案例已经审核的 runtime-lock；不搜索旧数据集或自动补依赖。
2. masked test 若可编译，直接把测试方法设为显式 entrypoint；若不可编译，不用 LLM 填 Mock，而由 AST 机械生成 analysis-only harness：把 masked Mock 局部变量改成方法参数/opaque receiver，保持普通 setup、MUT 调用和 assertion/verification 目标不变。
3. Scope 只含 JDK 11、案例 class files、显式审核 runtime jars。入口仅为 harness/test 与 MUT，不做 all-application-entrypoints。reflection 先设 `NONE`；若链路必须依赖反射，记录为本实验不支持，而不是扩大 scope。
4. 用官方示例同类的 0-1-container-CFA builder 得到 call graph 和 pointer analysis，再构造 FULL/FULL SDG。官方 starter 的 bytecode call-graph driver 可作为 API 骨架。[WALA-start `ScopeFileCallGraph`](https://github.com/wala/WALA-start/blob/master/src/main/java/com/ibm/wala/examples/drivers/ScopeFileCallGraph.java)
5. Seed 两种 criterion：
   - value goal：MUT 的 return/throw 或测试 assertion 所消费的 SSA；
   - interaction goal：目标 Mock invoke statement 本身，加实际参数与调用 guard。
6. 将 slice/SDG 边投影成稳定 JSON：`method_signature, statement_kind, source_file, line, callsite, relation, confidence, boundary_flag`。字节码指令可通过 `IBytecodeMethod.getBytecodeIndex()` 再到 `IMethod.getLineNumber()` 回映源码行；必须用 `-g` 编译并保留 line table。[`IBytecodeMethod`](https://github.com/wala/WALA/blob/v1.7.2/core/src/main/java/com/ibm/wala/classLoader/IBytecodeMethod.java)、[`IMethod`](https://github.com/wala/WALA/blob/v1.7.2/core/src/main/java/com/ibm/wala/classLoader/IMethod.java)
7. 同一 criterion 同时跑现有 GoalIR 和 WALA 投影，只比较关系证据，不先比较 LLM 文案。

建议 runner 设硬预算（验收门槛，不是已测性能）：单案例 wall time 60 秒、RSS 2 GiB；超限即停止并输出 call-graph/SDG 阶段计时，不自动换成全仓分析。

### 通过条件

- 自动恢复至少一条跨文件、跨方法的可见调用链，且动态分派落到案例预期实现；
- value slice 包含 Mock frontier 的返回 SSA 到目标 return/branch 的 def-use；
- interaction slice 包含调用实参来源和控制其发生的 branch；
- FULL 相比 NO_HEAP 至少显式展示是否多得到状态/副作用关系；
- slice 中不存在 Mock 边界真实实现内部节点；
- 每条事实能回到方法签名和源码行，所有 unresolved target 有诊断；
- 对比现有 GoalIR 时，新增的关系是可人工核验的真关系，不能仅以“节点更多”判优。

### 停止条件

出现任一项即不继续扩展 WALA adapter：冻结 JDK 11 无法启动 1.7.2；审核 classpath 仍无法构建 MUT call graph；Mock frontier 导致关键 downstream def-use 全部丢失且 declared-type fallback 也无法恢复；FULL/FULL 在单案例预算内无法完成；或源码回映不足以让 Agent理解证据。此时保留 GoalIR，并把 WALA 降级为离线对照工具。

## 替代路线的保留判断

| 后端 | JDK 11 | 现成跨过程关系 | 本轮判断 |
|---|---:|---|---|
| WALA 1.7.2 | 是 | call graph、SSA、pointer analysis、SDG、backward slicer | 唯一进入小实验 |
| Soot 4.7.1 | 是（POM source/target 11） | Jimple、CFG、Spark call graph/points-to；跨过程切片需自行组装 | 只作 WALA 失败后的 IR fallback，不再并行实现 |
| SootUp 3.x | 分析进程需 Java 17 | 新 API、call graph、IFDS/IDE，但需定义分析问题 | 暂缓；另备分析运行时并不要求改变目标测试 JDK |

旧 Soot 仍然活跃且有 all-in-one JAR，若 WALA 仅因 class loading/IR 兼容失败，可做第二阶段的“Jimple 关系抽取”试验；但这会重新引入自研跨过程依赖组合，不应作为当前默认方案。[Soot 4.7.1 release](https://github.com/soot-oss/soot/releases/tag/4.7.1)、[Soot POM（Java 11）](https://github.com/soot-oss/soot/blob/4.7.1/pom.xml)、[Soot options（CHA/Spark）](https://soot-oss.github.io/soot/docs/4.4.1/options/soot_options.html)

后端决策：**WALA 1.7.2 是下一次单案例实验的首选，尚未被实测选为生产后端。冻结自研解释器的新语法扩展，先验证调用与状态关系以及 Mock 边界是否能同时保留。** 本组件实验按预先协议没有安装或执行 WALA。
