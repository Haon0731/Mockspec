# Step 6.1 — 通过只读源码工具跑通 V0.2 推理

发布状态：拆分已确认；目标仓库 Haon0731/Mockspec。仅任务 01 初始标记 ready-for-agent，其余任务在全部阻塞项完成后开放。

## What to build

研究者通过宿主 CLI 显式选择 V0.2，在完整可见 UnifiedTask 上使用同一 OpenAI Agents SDK 自主选择 inspect_source，取得原始源码证据，完成多轮推理并生成通过现有 Validator 的 Scenario Contract。默认 CLI 仍运行 V0.1。

这是首个完整纵向切片，不要求先完成 AST/Spoon。它建立后续两个分析工具复用的受限访问、结果状态和调用记录约定。正式付费实验留待最终验收任务。

## Acceptance criteria

- [ ] 核对归档基线 f699554；V0.1 推理核心、Prompt 逐字不变，历史记录不修改，原有默认 CLI 和回放回归通过。
- [ ] V0.2 推理实现与 Prompt 独立版本化，复用现有 SDK、UnifiedTask、Scenario Contract 和 Validator，不创建第二套框架。
- [ ] V0.1/V0.2 初始序列化任务一致；允许零次工具调用，不强制指定工具。
- [ ] inspect_source 仅按当前任务 source_id 和受限行范围返回源码，保留原行号与缩进；路径、未知 source_id 和跨切片查询被拒绝。
- [ ] 工具不能访问 Ground Truth、未遮蔽测试、旧生成模板或额外源码；无物理路径或命令参数，不执行目标代码。
- [ ] 工具返回明确成功、部分、未解析或错误状态及诊断，结果大小有界；建立后续结构/依赖工具沿用的结果约定。
- [ ] 设置并测试有限轮次、总时间和工具调用次数；全局耗尽后不额外生成收尾回答。
- [ ] 暴露实际工具调用参数、结果、状态及顺序的捕获接口；单工具错误允许在剩余预算内恢复。持久化失败留证、全局预算门禁、费用核算和重试控制归任务 04；本任务只负责核心循环、版本路由、源码查询和基本运行限制。
- [ ] 离线实际 SDK Runner 测试覆盖工具请求→执行→收到结果→Contract 验证、零调用、工具错误恢复、全局停止和非法证据；不得把脚本模型测试称作真实自主行为。
- [ ] Python/Java 已审核输入均能使用源码工具；提供可复现离线演示和验证结果，不运行正式 live 对照。

## Blocked by

None — can start immediately.

## Scope constraints

仅推进 Scenario Inference；不改 Stub-Tester、不扩数据集、不接 Joern、不做 Mock Generation 或 Behavioral Feedback。不做基线核心预重构，允许少量重复代码。

## 通用实现约束

保留输入隔离，冻结 V0.1 实现和历史结果。实现须具有案例通用性，禁止按 flask_308、JarFiles、case_id、固定行号或目标方法名硬编码推理逻辑。案例仅用于验证。
