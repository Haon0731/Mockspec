# Step 6.3 — 让 V0.2 使用 Java Spoon 程序分析证据

发布状态：拆分已确认；目标仓库 Haon0731/Mockspec。仅任务 01 初始标记 ready-for-agent，其余任务在全部阻塞项完成后开放。

## What to build

在同一 V0.2 工具接口下接入 Java Spoon，让 JarFiles 案例通过结构和依赖使用查询获得真实语法事实，并沿 SDK 推理链路生成经现有 Validator 验证的 Contract。此任务不依赖 Python 后端。

## Acceptance criteria

- [ ] 复用 Step 5B 验证过的 Spoon 能力，记录实际运行版本；本次执行分析当前可见文本，不将历史探针 JSON 伪装成本次结果。
- [ ] 宿主控制解析器和必要临时输入，内容仅来自 UnifiedTask；模型不能提供物理路径、任意命令或任意查询代码，不执行目标程序或读取外部源码。
- [ ] inspect_program_structure 提供调用、条件、分支、返回、抛出及异常处理语法和准确位置；真实案例缺失的结构使用小型合成测试。
- [ ] inspect_dependency_usage 区分局部声明引用、语法嵌套和 unresolved；不可见声明与隐藏实现不得成为已知行为依据。
- [ ] 保留原 source_id、范围和行号；语法关系不称为已证明的数据流、异常传播或路径可行性。
- [ ] Spoon 不可用、解析失败、部分结果和超时返回明确状态与安全诊断；子进程有界退出和清理，失败记录保留且 Agent 可在预算内继续。
- [ ] JarFiles 经审核 Adapter、真实 Spoon、实际 SDK 工具调度和 Contract Validator 离线贯通；若运行环境确实不可用，明确留存验证缺口，不声称通过。
- [ ] 三工具通过现有 V0.2 CLI 可用；测试覆盖隔离、源码定位、局部关系、错误恢复和 V0.1 回归。

## Blocked by

- Step 6.1 — 通过只读源码工具跑通 V0.2 推理。

## Scope constraints

不接 Joern、不补读 JAR 或依赖树、不恢复隐藏源码、不修改 Stub-Tester、不扩数据集、不执行正式付费对照。

## 通用实现约束

保留输入隔离，冻结 V0.1 实现和历史结果。实现须具有案例通用性，禁止按 flask_308、JarFiles、case_id、固定行号或目标方法名硬编码推理逻辑。案例仅用于验证。

- [ ] 工具只输出有依据的程序分析事实；显式区分 unresolved、unsupported 和 analysis_error，不将语法关系冒充数据流或可执行路径证明。
