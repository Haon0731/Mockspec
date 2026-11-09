# 技术依据与历史失败定位

本轮固定 WALA 1.7.2，独立 Java bytecode 后端，不改变生产工具链。

## 四轮证据的作用

1. [首轮](../README.md)：Spoon 候选图修正 CloudFoundry 调用关系后仍混入 sibling subtype；JarFiles 修正后只剩测试节点。Flask 的成功候选也获得了局部 MUT，不能把成功归因于切片。
2. [路径摘要](../path-pilot/README.md)：Flask 6 条路径在 12 组见证中匹配；Java guard/值摘要未完成跨过程组合，更多证据不自动提高生成质量。
3. [目标组合](../goal-pilot/README.md)：CloudFoundry 的 health/status 目标关系可以诊断 URL 冲突，存在多种合法响应。实现 `compose.py::_project_goal` 使用 Lombok builder 属性投影；`_eval_call` 分别解释可见调用、受支持库和边界；`_boundary_call` 建正常/异常候选，但 heap effects 未建模。这些是有限解释器的能力，不是通用 SDG。
4. [组件与迁移](../component-pilot/README.md)：短诊断 2/2、关系加诊断 1/2，样本不足以证明稳定收益；Jib 的两个目标均 `assertion_target_not_projectable`。对象/集合返回链没有可解释的投影，不能仅扩充语法规则掩盖该缺口。

因此本轮评价“是否获得可靠结构关系”，不复测随机 Agent 生成，也不声称替代已校准的 URI 语义检查器。

## 上游能力与边界

- [1.7.2 release](https://github.com/wala/WALA/releases/tag/v1.7.2)：Java 11 可用的固定版本。[Slicer 源码](https://github.com/wala/WALA/blob/v1.7.2/core/src/main/java/com/ibm/wala/ipa/slicer/Slicer.java)提供基于 SDG 的、调用返回匹配的后向切片；并非路径可行性求解器。
- [SDG 源码](https://github.com/wala/WALA/blob/v1.7.2/core/src/main/java/com/ibm/wala/ipa/slicer/SDG.java)连接参数、普通返回、异常返回与 heap 参数/返回；[PDG 源码](https://github.com/wala/WALA/blob/v1.7.2/core/src/main/java/com/ibm/wala/ipa/slicer/PDG.java)产生数据、heap 与控制依赖。FULL/FULL 保留这些类别，精度仍受调用图与指针分析约束。
- [BypassMethodTargetSelector](https://github.com/wala/WALA/blob/v1.7.2/core/src/main/java/com/ibm/wala/ipa/summaries/BypassMethodTargetSelector.java)可替换方法摘要，但 ignored method 的 no-op 不是未知 Mock 的保守语义。必须分开“隔离实现”与“为任意返回/异常/写效果建立 sound 模型”。
- [ClassHierarchyFactory API](https://wala.github.io/javadoc/com/ibm/wala/ipa/cha/ClassHierarchyFactory.html)明确 phantom 的已知局限；缺类/无 target 不可解释为无行为。
- [SootUp 官方文档](https://soot-oss.github.io/SootUp/)提供 IR/调用图/数据流基础。当前选择 WALA 的依据是现成跨过程 SDG/slicer，而不是宣称 Soot 无法表达这些分析。

源码行来自 bytecode line table；编译器生成方法只有字段/注解行时，不能将其显示为原文存在的方法体。库内部事实须标为 library bytecode，不能伪造 source_id。

## Agent 证据接口的技术判断准则

一条有用证据应包含 goal、调用点和上下文、receiver/参数/返回 SSA、带类型的依赖边、可见分支/CFG、来源行、未解析项与配置。静态 callsite 不等于运行次数；control-dependence 不等于一条可行路径；切片成员不等于必须断言的业务交互。

未知边界若没有可传播的引用对象、异常与写堆模型，完整性必须为 false。标注 unknown 能防止错误承诺，但本身不会修复已缺失的依赖边。这一点将作为是否值得晋升为基础后端的核心判据。
