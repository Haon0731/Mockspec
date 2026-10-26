# 文档目录

## 当前使用

| 文档 | 用途 |
| --- | --- |
| [架构导读](architecture.md) | 从输入走到输出；Agent 核心、运行管理和研究路径的位置 |
| [运行与开发](usage.md) | 安装、版本选择、回放、留证与测试命令 |
| [Scenario Contract](scenario-inference.md) | 两版共用的约束字段和校验范围 |
| [正式 JSON Schema](scenario-contract.schema.json) | 机器可读输出结构；不能替代宿主完整校验 |
| [输入审核记录](input-audit.md) | 三个冻结案例的准入依据与允许源码 |
| [术语表](../GLOSSARY.md) | 工程验收、自主工具调用与场景推导质量 |

## 独立研究验证

需求基线：[冻结仓库主动只读探索 PRD](workspace-exploration-prd.md)。工程切片 #13–#16 已交付，
真实 M2 仍未通过，见 [#17 验收尝试](../out/staged-inference/issue17-m2-20261026/report.md)；
PRD 不作为当前能力或已通过验收的说明。
后续执行使用该 PRD 第 9 节的 `M2-CITATION-1`：研究引用选择协议、案例适用性和真实验收门禁。
引用协议与离线工程验证已交付；[工程与案例门禁记录](../out/staged-inference/issue17-citation-protocol-20261026/report.md)
保留当时两例未通过适用性核对的结论；后续[案例寻找与准入记录](../out/staged-inference/issue17-case-search-20261026/report.md)
新增合格 CloudFoundry 案例，G2 已通过。后续[单次真实验收](../out/staged-inference/issue17-cloudfoundry-m2-20261026/report.md)
通过 G3，模型发起自主搜索后因回执评估含额外字段失败；G4 未通过，#17 仍开放。

以下研究结果不等于生产 Agent 已具备对应能力；阅读案例分析也不能作为未来无指导生成的输入。

| 材料 | 已验证的范围 | 复现入口 |
| --- | --- | --- |
| [M1-5 集成验收](../experiments/staged-inference/acceptance-report.md) | B/C 四状态、静态对照、有界修订修复、定向查询与共享预算；真实检查状态及需求映射见验收记录 | [运行说明](usage.md#分阶段研究回放m1-1--m1-4)；[架构边界](architecture.md#研究路径) |
| [Scenario Contract Research](scenario-research/decision.md) | 人工契约及有限模型 PoC；Limited-Go | [复现说明](scenario-research/reproduce.md) |
| [源码绑定局部检查](source-bound-local/report.md) | 可见源码的受支持局部返回值条件；未接入 Agent | [run_local.py](../experiments/scenario-research/run_local.py)；命令和限制见报告 |
| [单案例人工见证](dependency-witness/report.md) | 固定参数行的人工候选及诊断对照；不是自动推断 | [run_witness.py](../experiments/dependency-witness/run_witness.py)；环境要求见报告 |

## 历史阶段与复现材料

阶段报告中的“当前”“本轮”“未验证”均指记录时点，不是项目的最新总状态。
历史源码、证据和相对路径保留，避免破坏复现和既有引用。

| 阶段 | 材料 | 如何使用 |
| --- | --- | --- |
| V0.1 初次交付 | [整理前版本](https://github.com/Haon0731/Mockspec/blob/bc72f6bcbc0d349a4ceecc6e42b30595725799ef/docs/scenario-inference.md) | 早期离线验收；当前字段说明使用上方契约文档 |
| Step 4 | [报告](step4/report.md)、[协议](step4/protocol.md) | V0.1 真实推理与语义审查记录 |
| Step 5A | [报告](step5a/report.md) | 分析能力调研及 `poc/`，不是生产运行入口 |
| Step 5B | [报告](step5b/report.md)、[复现](step5b/reproduce.md) | AST/Spoon 等后端实测；固定工具身份仍被当前实现使用 |
| Step 6 | [规格](step6/spec.md)、[决策](step6/decisions.md)、[交付](step6/report.md) | V0.2 工程交付；冻结基线等约定仍适用 |
| Step 6 Live | [报告](step6-live/report.md)、[协议](step6-live/protocol.md) | 后续真实自主工具调用对照；工程成功与契约质量分开解释 |
| 研究任务包 | [原始任务说明](MockSpec_Scenario_Agent_Codex_Pack.md) | 已完成阶段的任务背景，不作为新一轮执行指令 |
| 发布草案 | [Step 6 Issues](step6/issues.md) | `ticket-drafts/` 保留发布记录；实时任务状态以 GitHub 为准 |

## 维护规则

[任务跟踪](agents/issue-tracker.md) · [分诊标签](agents/triage-labels.md) · [领域文档](agents/domain.md)

新增当前使用说明应更新上方对应文档；阶段证据按原阶段保存，不再混入入门页。
