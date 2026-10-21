# Step 6.2 — 让 V0.2 使用 Python 程序分析证据

发布状态：拆分已确认；目标仓库 Haon0731/Mockspec。仅任务 01 初始标记 ready-for-agent，其余任务在全部阻塞项完成后开放。

## What to build

在已可运行的 V0.2 中，为 Python 提供 inspect_program_structure 和 inspect_dependency_usage。通过 flask_308 的完整离线 SDK 推理链路演示：模型请求分析、收到真实 AST 事实，再输出现有 Scenario Contract。

## Acceptance criteria

- [ ] 使用 Python AST 分析当前 UnifiedTask 的可见文本，不 import 或执行目标程序，不扩大输入或读取依赖目录。
- [ ] 结构工具覆盖调用、条件、分支、返回、抛出及异常处理语法，保留函数切片的原文件行号；真实案例未出现的结构用小型合成测试验证。
- [ ] 依赖工具提供可可靠提取的语法嵌套及局部使用事实；未解析调用、作用域或别名关系明确 unresolved，不以名称相同推断数据流。
- [ ] 输出包含 source_id、位置、事实类别、后端身份及限制；不声称证明 CFG、跨语句 def-use、异常传播或路径可行性。
- [ ] 解析失败、截断、超时和空结果语义明确；incomplete/unresolved 不解释为无依赖，工具失败仍可恢复推理。
- [ ] 沿用共享工具契约、访问边界及调用记录，通过现有 V0.2 CLI 可使用 Python 三个工具，允许零调用。
- [ ] flask_308 经审核 Adapter、实际 AST、SDK 工具调度和 Contract Validator 离线贯通，保存可复现证据；脚本模型轨迹明确标为离线。
- [ ] 覆盖范围隔离、原始行号、分支极性、可靠局部关系及错误恢复的测试通过；V0.1 冻结与回归保持通过。

## Blocked by

- Step 6.1 — 通过只读源码工具跑通 V0.2 推理。

## Scope constraints

初始输入与 V0.1 一致；分析能力不是额外源码访问。保留 masking，不读取 Ground Truth，不扩数据集，不进行正式付费对照，不实现新 CFG/DDG 引擎。

## 通用实现约束

保留输入隔离，冻结 V0.1 实现和历史结果。实现须具有案例通用性，禁止按 flask_308、JarFiles、case_id、固定行号或目标方法名硬编码推理逻辑。案例仅用于验证。

- [ ] 工具只输出有依据的程序分析事实；显式区分 unresolved、unsupported 和 analysis_error，不将语法关系冒充数据流或可执行路径证明。
