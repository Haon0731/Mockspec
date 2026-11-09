# 完整 MockBench 研究框架

本目录供研究维护者建立全量分母、检查输入准入、执行有界基线并归档失败。
入口为 `run.py`，操作命令归 [docs/usage.md](../../docs/usage.md#完整-mockbench-研究框架)。
这是独立研究路径；可复用的数据读取实现归 `src/mock_spec/adapters/frozen_dataset.py`，
旧三例 Adapter、Schema 和冻结 Prompt 保持兼容。

当前已完成**冻结数据接入**，Java 59/59、Python 106/106 实际加载通过。
这不代表全量分析实验或生成实验完成。
长期研究问题是：从可见测试目标出发，自动恢复了哪些数据关系、控制条件和 Mock
依赖，遗漏或错误集中在哪些任务类型，哪些程序分析机制值得做后续对照。
不预设 WALA、完整符号执行或其他后端为必选路线。

## 清单和信任边界

`inventory.py` 读取 Stub-Tester agent 所用冻结 registry 的
`reports/readiness/{java,python}-snapshot-validation.json`，要求成功状态、有效身份、
唯一 Case ID，以及 `indexed_cases == validated_cases == len(cases)`。
同时绑定 `developer-realistic-readiness.json` 的通过状态、报告摘要、dataset/revision
和 inventory/frozen 数量，再核对正式索引的 Case ID 与 snapshot ID，防止把子集当作完整分母。
原 Case ID 原样保留，Java/Python 各自独立计数。报告路径、报告内容摘要、inventory revision
随清单保存；默认使用 `frozen-agent` 输入策略，不读取历史三例审核目录。

2026-11-09 对 `.omx/developer-realistic-registry-max` 的清单核对得到 Java 59 例、
Python 106 例，共 165 例。这是该冻结 agent inventory 的案例数，不是参数化执行行数、
模型调用数或重复运行数。`suite_manifest.json` 当时仅包含各三例的 cohort，不能用作全量。
当前证据为 [frozen-agent 清单](../../out/mockbench-study/intake-20261109-inventory.json)
和[逐例实际加载汇总](../../out/mockbench-study/intake-20261109/summary.json)。

默认 `frozen_candidate` 表示索引中候选；`check-inputs` 重新核验控制身份、实际 masked target、
injector 与空区域后报告 `loaded`。读取源码无需逐例手写目录；只在读取选定文件时核对其
已有摘要，不扫描依赖实体。准备协议审查与固定上下文差异见
[输入记录](../../docs/input-audit.md#完整冻结-agent-数据接入2026-11-09)。

原 `--input-policy reviewed` 保留给历史三例基线，状态如下；它不再作为全量接入入口：

| 状态 | 意义 | 后续动作 |
| --- | --- | --- |
| `needs_review` | 安装的审核目录没有该案例 | 审核选定源码、masking、冻结身份和可见范围后另行准入 |
| `review_identity_mismatch` | 已有审核，但 snapshot/dataset/revision 不同 | 核对版本差异；不得静默使用另一个快照 |
| `snapshot_missing` | 身份匹配，但 snapshot 目录不可用 | 恢复指定只读挂载；不回退原始测试 |
| `reviewed_candidate` | 清单身份匹配，目录存在 | 批运行时仍须通过现有 Adapter 的实际内容核验 |

首次使用旧三例策略时为 162 个 `needs_review`、2 个 `reviewed_candidate`、1 个
`review_identity_mismatch`。后一例是 `flask_308`：已有审核来自 fix23 registry，
当时清单属于主 registry，不能把同一 Case ID 当同一输入。
`reviewed_candidate` 不是已通过本轮审核，清单的 `admission_verified` 全为 false。

不读取生成后 `masked_templates`、原始测试答案、gold 或 private semantic IR；不复制数据集，
不执行 Stub-Tester 实验，不计算依赖树哈希。新策略沿用用户明确选择的上游冻结准备信任，
不会扩大旧三例目录。缺失元数据、重复 Case ID 或数量不一致使清单生成失败。

## 数据读取接口

`FrozenDataset` 读取闭合索引，`load_case(case_id)` 返回可关闭的 `FrozenCase`。
`masked_test` 保留完整目标测试，`source_paths` 列出可读源码，`read_source(path)` 返回
带原始行号的源码。此时不需要预先推断 MUT，也没有把测试当作 MUT 填进 Schema。
调用者明确选择 MUT 后，可用 `to_task(mut_path=..., supporting_paths=(...))` 构造现有
`UnifiedTask`，保留 source_id 与输入策略。示例和命令只维护在 [usage](../../docs/usage.md#完整-mockbench-研究框架)。

## 批量基线和可证明范围

`batch.py` 的历史 `syntax_baseline` 只接受 `reviewed` 清单：对通过 Adapter 的 `UnifiedTask` 中 masked test、
MUT 和支持片段调用现有 `SourceAnalysis` 结构/依赖使用接口。保留原始工具事实、限制、
错误和逐例记录，记录每例超时；不导入或执行目标项目，不调用模型。
超时为异步分析的合作式取消，不抢占 Adapter 同步读取；预算、输入和实现摘要随运行保存，
每完成一例立即落盘，已完成记录不因后续中断而丢失。
它观察的是当前可见片段，尚未执行全仓发现或目标导向后向切片。

这个后端提供统一执行和失败留证的起点，**语法事实不是恢复正确的数据流或控制流**。
数据流、控制流与 Mock 依赖三个语义维度默认 `not_evaluated`。工具成功最多说明
取得了事实，空事实也不说明没有相关行为。现有三例的候选证据图和组合器保留在
`experiments/backward-slicing/`，不复制其案例专用边界到全量框架。
后续应在同一逐例身份和报告约定下接入泛化的目标种子/候选图后端，再评价恢复质量。

批次保留完整清单分母、选择/准入/尝试/观察数量及语言分组。未准入、未选择、输入拒绝、
解析或后端故障、超时分别记录；单例失败不会丢掉其余案例。新输出目录不覆盖旧证据。
`--limit` 用于获准候选的有界烟测，不把未尝试案例从全量分母删除。

## 后续语义评价与失败归因协议

评价对象须分三层，不能用一个 pass 字段代替：

1. **可运行性**：来源/准入、解析、资源、后端和输出是否可用。当前批处理自动记录这一层。
2. **关系质量**：测试目标是否识别正确，数据变换/调用返回/堆状态关系、分支/异常条件、
   Mock 替换位置/响应消费/交互关系是否有依据。当前没有独立评阅结果，不能报告准确率或召回率。
3. **生成效果**：在相同任务可见性和预算下比较生成结果；原程序行为、缺陷检出和合法替代
   分开评价。当前不生成 Mock、不执行目标测试，也不报告这一层的收益。

关系质量评阅至少绑定 `language + case_id + snapshot_id + backend/version`，记录
`dimension`（data_flow/control_flow/mock_dependency）、`outcome`
（supported/missing/incorrect/unknown）、具体证据位置、分析输出中的对应关系，以及评阅理由。
没有完整独立参考关系集合时，仅报告已检查关系的正确性和明确遗漏，不能把未标注边算正确或算无遗漏。
宿主评价材料须与未来 Coding Agent 的输入隔离；不得把评阅答案补回分析器作为案例特判。

| 失败类别 | 需要的实际证据 | 后续技术比较的问题 |
| --- | --- | --- |
| 测试目标未识别 | 可见断言/异常/状态/交互目标及漏掉的位置 | 目标准则和测试框架适配是否足够 |
| 数据定义/使用断裂 | 应连接的赋值、参数、返回和变换位置 | reaching definitions、SSA 或跨过程摘要是否补足 |
| 接收者/堆关系不清 | 别名、构造、字段、builder、覆写关系的歧义 | points-to、对象/上下文敏感性是否有必要 |
| 控制/异常关系错误 | 分支条件、异常后继与路径冲突证据 | CFG/CDG、异常建模或局部约束检查是否有效 |
| 外部依赖语义未知 | 无可见实现或可靠契约的依赖边界 | 摘要、受控库验证或保留 unknown 的成本如何 |
| 动态语言解析不足 | 动态绑定、装饰器或 monkeypatch 的具体未解析点 | 有限候选解析或混合证据是否足够 |
| Agent 误解已有证据 | 证据已提供但生成代码违反该关系 | 呈现、局部诊断或反馈机制是否改善 |

这些是待检验的归因标签和技术候选，**不是已经确认的根因或解决方案**。
按语言、项目和任务类型分层统计，保留无法归因与多重原因；基础设施失败不算分析语义失败。
用真实失败样本选择小范围对照，冻结开发/留出划分，记录引入成本和不支持范围，
之后才决定是否采用 WALA 等后端。三例历史结果不能替代这一选择过程。

## 接入与历史验证

全量加载检查为165/165通过，全部保留原 Case ID 和当前索引 snapshot。
检查只读取冻结控制记录、目标测试和 injector；实际源码按需核验，未复制数据集。
接入、masking策略、清单、批处理及原 Adapter/Schema/源码工具相关回归 **255 passed**；
Ruff 与新 Adapter/研究入口的定向 mypy 检查通过。文档中的 `flask_308` 示例已验证
按需读取 `helpers.py` 并转换为 `UnifiedTask`，payload 无宿主根路径。
全量关系分析/生成实验、语义参考集、新分析后端以及 Mock 缺陷检测对照仍为后续工作。

### 2026-11-09 首次框架验证（历史三例策略）

清单、批处理及既有源码工具/Adapter 相关回归为 **143 passed**；新增研究代码 Ruff
及 `MYPYPATH=src mypy --follow-imports=skip` 检查通过（后者只检查新增三个文件）。

真实 `--limit 2` 无模型烟测完成，见
[summary.json](../../out/mockbench-study/syntax-smoke-20261109-sync/summary.json) 和
[运行身份与预算](../../out/mockbench-study/syntax-smoke-20261109-sync/run-manifest.json)：
165 条记录全部保留，2 例实际准入并进入分析，163 例阻塞，2 例均为 `analysis_failed`。
工具返回 `failure_kind=backend_failure`，诊断为 `The Java syntax parser failed.`。
直接调用同一 Java 后端也复现这一回执；尚未确定内部解析原因，不能归因为数据流算法失败。
这一结果证明失败链路可以完成与归档，不证明分析后端在这两例上可用。
后续全量实验前需处理该后端问题、其余案例准入以及 flask 的快照版本选择。

初次烟测使用线程加载 Adapter，在当前宿主出现等待/退出停滞；已改为现有的同步有界读取。
初次逐例记录及[中断说明](../../out/mockbench-study/syntax-smoke-20261109/interruption.json)
保留，未覆盖为成功运行。上述修正后的目录才是本轮最终框架烟测结果。
