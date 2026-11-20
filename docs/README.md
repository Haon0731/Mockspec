# 文档目录

这里区分当前说明、最新研究结果和历史复现材料。阶段文档中的“当前”“本轮”只表示记录时点。

## 当前使用

| 文档 | 用途 |
| --- | --- |
| [架构导读](architecture.md) | 从审核输入走到契约输出；生产模块、静态导航和研究边界 |
| [运行与开发](usage.md) | 安装、测试、版本选择、完整 MockBench 与配对实验预检 |
| [Scenario Contract](scenario-inference.md) | V0.1 / V0.2 共用的字段、引用与验证边界 |
| [正式 JSON Schema](scenario-contract.schema.json) | 机器可读输出结构；宿主仍需执行完整校验 |
| [输入审核记录](input-audit.md) | 历史三例和完整冻结 agent 数据的准入策略 |
| [术语表](../GLOSSARY.md) | 原始测试目标、替换边界、未知和工程验收 |

任务与维护规则见[任务跟踪](agents/issue-tracker.md)、[分诊标签](agents/triage-labels.md)和
[领域文档](agents/domain.md)。

## 当前 MockBench 研究

| 材料 | 状态与边界 |
| --- | --- |
| [navigation-v2 165 例 r2 体检](https://github.com/Haon0731/Mockspec/issues/36) | 165/165 生成完整 facts；有界视图均不超过 8,192 tokens。静态证据不足处保留 Unknown |
| [Base / v2 最终离线预检与运行配置](https://github.com/Haon0731/Mockspec/issues/37) | 158 例沿用 r2，7 例使用 fix23 对齐视图；165/165 身份对齐，330/330 组合窗口通过，尚未启动批量模型调用 |
| [后向切片研究入口](../experiments/backward-slicing/README.md) | 候选切片、成熟后端、失败分类和复现命令；构图完成不等于语义恢复成功 |
| [完整 MockBench 输入框架](../experiments/mockbench-study/README.md) | 冻结案例清单、Adapter、输入检查和历史语法基线 |

生产 facts 投影器与有界视图的职责见[架构导读](architecture.md#后向切片的事实导航与代码槽位)，
离线命令见[运行与开发](usage.md#v2-事实投影和源码槽位)。研究代码可复用生产投影器，生产包不依赖研究脚本。

## 其他研究路径

| 材料 | 已验证范围 |
| --- | --- |
| [分阶段推断 M1-5](../experiments/staged-inference/acceptance-report.md) | B/C 回放、定向查询、共享预算和失败留证；真实模型效果单独判定 |
| [Scenario Contract Research](scenario-research/decision.md) | 人工契约及有限模型 PoC；结论为 Limited-Go |
| [源码绑定局部检查](source-bound-local/report.md) | 受支持局部返回值条件；未接入生产 Agent |
| [单案例人工见证](dependency-witness/report.md) | 固定参数行的人工候选及诊断对照；不是自动推断 |

## 历史阶段与复现材料

历史报告、协议和证据保持原样。需要理解 2026-11-09 至 2026-11-11 的全量切片演进时，
从[后向切片交接记录](mockbench-backward-slicing-handoff-20261109.md)和
[研究发布清单](../out/mockbench-study/backward-analysis-publication-20261111-r1/README.md)进入。

| 阶段 | 材料 | 用途 |
| --- | --- | --- |
| V0.1 初次交付 | [整理前版本](https://github.com/Haon0731/Mockspec/blob/bc72f6bcbc0d349a4ceecc6e42b30595725799ef/docs/scenario-inference.md) | 早期离线验收；当前字段以本分支契约文档为准 |
| Step 4 | [报告](step4/report.md)、[协议](step4/protocol.md) | V0.1 真实推理与语义审查记录 |
| Step 5A / 5B | [调研](step5a/report.md)、[后端实测](step5b/report.md)、[复现](step5b/reproduce.md) | 分析能力选择与固定工具验证 |
| Step 6 | [规格](step6/spec.md)、[决策](step6/decisions.md)、[交付](step6/report.md) | V0.2 工程交付及仍适用的冻结约定 |
| Step 6 Live | [报告](step6-live/report.md)、[协议](step6-live/protocol.md) | 自主工具调用对照；工程成功与契约质量分开解释 |
| 冻结仓库探索 | [PRD](workspace-exploration-prd.md) | #13–#17 的历史需求和失败记录，不代表当前能力 |
| 原始任务材料 | [任务说明](MockSpec_Scenario_Agent_Codex_Pack.md)、[发布草案](step6/issues.md) | 背景和发布记录；实时状态以 GitHub Issues 为准 |

新增当前说明应修改本页上方对应入口。一次性诊断和阶段结论继续保存在原研究目录，避免形成第二份当前文档。
