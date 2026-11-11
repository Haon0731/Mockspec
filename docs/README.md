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

本轮已完成[后向分析修复与165例正式对照](../out/mockbench-study/backward-slice-formal165-20261111-r3/report.md)：
Jib、Flask、langflow三例实际后端重跑并保留已核验的部分关系；162例仍超出成熟后端输入适配范围。
完整语义恢复成功数为0，不能把候选触及产品源码的110例当作成功数。
原r3、两轮正式失败、开发重跑与全部逐例结果均保留；253项研究回归通过。
代码和压缩证据的发布范围及恢复方法见[发布清单](../out/mockbench-study/backward-analysis-publication-20261111-r1/README.md)。

当前后向切片方案调研：[完整 MockBench 上的研究结论](../experiments/backward-slicing/full-benchmark-research.md)。
包含主源文献、后端能力边界、165例目标测试结构盘点与验证设计；尚未报告全量切片成功率。
实现入口：[目标导向候选切片批处理](../experiments/backward-slicing/README.md#全量候选切片入口2026-11-09)，
从目标测试准则到有界选源、后向候选图和逐例失败；不把构图完成当作语义恢复成功。
[首次全量执行结果](../out/mockbench-study/backward-slice-full-20261109-r3/report.md)：
165例全部尝试，155例候选图、10例构图失败，包含逐准则证据与具体失败归因。

后续修复按[父规格 #19](https://github.com/Haon0731/Mockspec/issues/19)及其子任务推进：
结果契约、Jib 静态调用绑定与 langflow 加载来源分别验证；WALA／Python 后端能力门与
独立验收通过前，不把这些入口改进计为完整依赖恢复，也不启动新的正式165例对照。
最新[根因与修复记录](../out/mockbench-study/root-cause-repair-20261111/report.md)
区分异常捕获库模型、未知效果传播和 Jib 传递堆规模；保留失败尝试与支持范围。
后续[WALA能力门收尾](../out/mockbench-study/issue23-capability-closure-20261111-r4/report.md)
补齐受限接收者、可达对象和静态逃逸区域传播；#23已独立核验关闭。
[Jib逐观察独立验收](../out/mockbench-study/issue25-acceptance-20261111-r1/acceptance.json)
已完成#25的限定验收：19个观察均有字段状态影响证据，当时保留诊断观察adapter和未知Mock效果；
[追加的自动观察核验](../out/mockbench-study/issue25-jib-observation-adapter-revalidation-20261111-r3/independent-review.json)
进一步从原测试自动提取观察，生成源码与已分析输入一致；未知Mock效果仍保留。
[Java按需独立验收](../out/mockbench-study/issue28-java-demand-acceptance-20261111-r1/independent-review.json)
已完成#28，保留原关系并验证缓存及预算；真实Jib只减少72个方法上下文，不宣称加速。
[最终独立门禁 #30](../out/mockbench-study/issue30-final-gate-20261111-r1/acceptance.json)
已通过并关闭；限定真实案例、状态机制及按需分析验收通过，不代表165例均具备语义分析能力。
[langflow最终后端契约](../out/mockbench-study/langflow-backend-bridge-20261111-r6-contract-revalidation/verification.json)
保留两个原观察、31条类型化关系及未知效果；动态加载仅静态核验，不执行loader。
[Flask逐观察接入](../out/mockbench-study/python-issue26-27-20261111-r11/issue26-report.md)
已完成#26的独立验收，结果保留精确异常值、路径可满足性及支持域外效果未知。
[Python状态接入](../out/mockbench-study/python-issue26-27-20261111-r12/issue27-report.md)
已完成#27验收，9个机制观察与真实Flask分开；潜在参数别名和全局重绑定保持未知。
[Python按需查询验收](../out/issue29-python-demand-cold-20261111-r3/acceptance-revalidation.json)
已完成#29：观察根驱动的关系查询、冷／暖缓存等价及预算边界通过；
数据库仍完整解析。正式165例阶段由#31单独留证，候选诊断与成熟后端支持范围分别报告。
前轮[逐观察接入与摘要传播](../experiments/backward-slicing/README.md#逐观察接入与摘要传播2026-11-11)：
Flask 输出三个原始观察的关系与未知映射；WALA 修复摘要选择器时序，并更正先前对
空 IR 调用目标的过强判断。两者仍不代表完整案例或全量语义恢复成功。

全量研究入口：[完整 MockBench 研究框架](../experiments/mockbench-study/README.md)。
覆盖冻结 Java/Python 案例清单、实际加载检查、源码读取接口、历史语法基线和失败记录；
数据接入与关系恢复质量分别验证。

需求基线：[冻结仓库主动只读探索 PRD](workspace-exploration-prd.md)。工程切片 #13–#16 已交付，
真实 M2 仍未通过，见 [#17 验收尝试](../out/staged-inference/issue17-m2-20261026/report.md)；
PRD 不作为当前能力或已通过验收的说明。
后续执行使用该 PRD 第 9 节的 `M2-CITATION-1`：研究引用选择协议、案例适用性和真实验收门禁。
引用协议与离线工程验证已交付；[工程与案例门禁记录](../out/staged-inference/issue17-citation-protocol-20261026/report.md)
保留当时两例未通过适用性核对的结论；后续[案例寻找与准入记录](../out/staged-inference/issue17-case-search-20261026/report.md)
新增合格 CloudFoundry 案例，G2 已通过。后续[单次真实验收](../out/staged-inference/issue17-cloudfoundry-m2-20261026/report.md)
通过 G3，模型发起自主搜索后因回执评估含额外字段失败；G4 未通过，#17 仍开放。
随后[独立结构化输出诊断](../out/staged-inference/issue17-response-conformance-20261026/report.md)
核验了实际 HTTP Schema；唯一合成对抗请求合规，但未复现或修复原故障，不计作 M2 通过。
后续修复设计归同一 PRD 第 10 节 `M2-QUERY-RECOVERY-1`：严格 QUERY 类型、宿主回执关联、
条件假设表达和全局一次的有限格式纠正；[工程记录](../out/staged-inference/issue17-query-recovery-20261027/report.md)
已通过 R1；新协议单次真实验收因零查询停止及 CONSTRUCT 输出截断未通过，#17 保持开放。
下一步开发需求归同一 [PRD 第 11 节](workspace-exploration-prd.md#11-17-两个工程工作包query-aci-与-construct-输出可靠性)
`M2-ACI-CONSTRUCT-1`：A 为 QUERY 能力呈现、问题状态与有依据停止，B 为 CONSTRUCT 上下文/
输出减负及截断计量留证。当前研究实现已增加原目标问题账本、构造/对照后的继续调查、
自包含 `dependency_spec.json` 和 provider 失败响应留证；离线工程验证与真实 M2 仍分别报告，
不能据此宣称真实模型的规格推导效果已经提高。

以下研究结果不等于生产 Agent 已具备对应能力；阅读案例分析也不能作为未来无指导生成的输入。

| 材料 | 已验证的范围 | 复现入口 |
| --- | --- | --- |
| [后向切片与 Mock 生成探索](../experiments/backward-slicing/README.md) | 三个审核案例的候选证据图、一次A/B生成、Flask原测试与交互变异；含失败与未验证范围 | [协议与复现](../experiments/backward-slicing/README.md#7-复现与工程验证) |
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
