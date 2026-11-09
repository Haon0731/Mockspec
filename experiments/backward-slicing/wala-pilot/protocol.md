# WALA 可行性验证协议

目标：在审核后的 CloudFoundry 与 Jib 真实测试上，验证成熟 SDG 能否替代自研语法关系图，保留可追溯的跨过程、控制、heap 与异常证据，并暴露未知 Mock 边界。沿用四轮已有案例；不是新留出集，不测 Agent 成功率。

研究范围为本目录，不修改生产 Agent、冻结 Prompt、旧结果或准入记录。旧 snapshot 只通过 Adapter/SourceStore 复核既有投影；不复制数据集、不运行旧工程脚本或目标测试、不扫描/重算依赖摘要。库只使用 runtime-lock 已列出的 archives。实验临时编译审核源码；不执行被分析测试。

## 对照与验收

- 既有基线：CloudFoundry 有受限 healthUrl 组合与冲突检查，Jib 两个目标均 unknown、零 Mock 事件。保留其原结果作为历史能力对照，不比较不同环境的运行速度。
- 新分析：WALA bytecode SSA/call graph/SDG FULL/FULL，固定版本与配置写入结果。先记录原 mask 的编译可用性；若声明随 slot 被删除，则单独使用显式参数分析 harness，保留全部普通 setup/调用/断言与行号。参数表示未知 Mock 对象，不配置返回值或异常。
- 必须单列：测试入口→MUT、跨过程返回/参数、条件分支、可见对象状态、异常 CFG/SDG、Mock frontier、每个断言的切片及来源。建图成功不代表这些关系都成立。
- Mock 边界方法体不可成为语义证据；尤其 Jib 的 UnsupportedOperationException 是占位。记录隔离策略，以及未知返回/抛异常/写堆是否真正传播；无法建模的效果明确 unknown。无 target 不等于无影响。
- 外部库的 bytecode 事实属于库配置下的分析证据，不冒充审核可见 source_id 原文。完整性受动态调用、反射、入口建模、别名和 opaque effects 约束。
- 合成小程序仅用于隔离参数返回、字段、异常和边界机制，不替代真实案例结果。失败、超时、编译错误与语义缺口分别记录。

## 资源与停止

本轮选择两个真实 Java 案例、一个合成机制程序。单次分析最长 180 秒、Java heap 2 GiB；最多 12 次真实分析尝试（包括配置修正和失败），构建/依赖准备最长 10 分钟，整个本地执行预算 45 分钟，不调用付费模型 API。达到两例实测与可复核结论后停止；若框架或关键边界不可用，交付负结果与具体缺口。Python Flask 只作历史机制参考，WALA 不提供 Python 后端。

选择 bytecode 路径是本轮自主技术决策：现有冻结 runtime 提供编译依赖，可直接测试成熟 JVM SSA/SDG；源码 frontend 的不完整类型恢复不是本轮已验证能力。此前 source-first 建议不作为本轮完成声明。
