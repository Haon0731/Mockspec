# Scenario Contract

[架构导读](architecture.md) · [运行与开发](usage.md)

这是 V0.1 和 V0.2 Agent 共用的当前输出说明；两版均输出
`mock-spec.scenario/v0.1`。Agent 版本与契约 Schema 版本独立。
初次交付的环境状态和验收计数属于历史记录，见 [整理前版本](https://github.com/Haon0731/Mockspec/blob/bc72f6bcbc0d349a4ceecc6e42b30595725799ef/docs/scenario-inference.md)。

## 输出模型

实现位于 [scenario.py](../src/mock_spec/scenario.py)；机器可读结构见
[scenario-contract.schema.json](scenario-contract.schema.json)。

| 字段 | 含义 |
| --- | --- |
| `test_objectives` | 可见测试输入与断言所表达的目标 |
| `scenario_hypotheses` | 分别编号的候选执行路径及条件 |
| `dependency_constraints` | 在指定路径下需要的返回、异常或其他行为 |
| `expected_interactions` | 路径上预期的调用；`assertion_policy=not_implied` |
| `verification_candidates` | 有依据、值得考虑的验证；不能标为 required |
| `evidence` | source_id、原文件行号、原文和简短依据 |
| `uncertainty` | 输入不足、不可见实现或多种合法解释；status 为 unknown |

所有具体 Claim 都具有 `statement`、`status`、`scenario_ids`、`condition` 和
`evidence_ids`。`required` 表示**在所声明条件和场景内**的要求，而非所有合法
执行的全局必要条件。已知行为必须有证据和场景引用；required 还必须有非空条件。
候选场景自身不能标为 required。无法得出结论时可使用 unknown，允许无证据引用，
但这不授权在 unknown 文本中伪装具体、确定的结论。

证据引用使用输入切片的原文件 1-based、闭区间行号。quote 必须逐行完全匹配，
包括缩进；例如 Python MUT 第 282 行仍为 282，不重新编号为 18。
不同 source_id 即使源自同一文件，也只能引用各自允许的范围。

Pydantic JSON Schema 描述字段结构；Python 验证器额外实施跨字段约束、ID 唯一性、
引用存在性、任务身份、行范围和原文一致性检查。仅使用导出的 JSON Schema，
不能替代完整 `validate_contract()`。

## 谁生成，谁验证

模型按版本化 Prompt 提出场景、依赖约束、证据和未知项。
`inference.py` / `inference_v02.py` 解析模型输出，调用
`scenario.validate_contract()`；运行入口随后序列化契约并标记验证状态。
V0.2 的源码工具只补充已有可见输入的事实，不写验证成功状态。

当前 `dependency_constraints` 是带条件、状态和引用的文字 Claim，
并不是每一条都自动翻译成 SMT。局部 cvc5 检查记录位于独立研究路径，
尚未绑定到生产 ScenarioContract；参见 [研究路径](architecture.md#研究路径)。

## 五步研究记录的字段映射

研究 v0.2 使用引用选择协议：模型提供 `source_id/start_line/end_line`，
以及正式证据所需的 `id/rationale`；宿主仅从本次已可见证据生成 `quote`。
模型原始选择保存在 `wire_result`，宿主构造的标准对象保存在 `result`，
`citation_mode` 为 `source_locator_host_materialized_quote/v1`。
非法或未观察范围仍失败，不自动读文件、移动坐标或替换旧错误引用。
正式 Evidence 和以下映射不变，最终契约继续通过原逐字校验；
原文真实不意味着它支持模型结论，证据相关性仍需独立审查。

分阶段研究控制器保持正式 `mock-spec.scenario/v0.1` 不变，并在
`inference_record.json` 保存场景卡所需的研究旁路信息：

| 场景卡内容 | 正式契约或研究记录字段 |
| --- | --- |
| scenario | `scenario_hypotheses` |
| evidence | 正式 `evidence`；动态引用还须属于本次运行的 observed 范围 |
| dependency_behavior | `dependency_constraints` |
| key_behaviors | `critical_behaviors`，最多三项，并用 `semantic_role` 与 `formal_claim_index` 关联正式 Claim |
| counterfactual / discriminator | `degeneracy_contrast`；`execution_status` 固定为 `not_executed` |
| allowed_variations | `allowed_variations` |
| unknowns | 正式 `uncertainty` 加旁路 `unresolved` / `nonblocking_unknowns` |

仓库探索模式下，每项重要旁路判断还记录 `semantic_role`，区分
`dependency_contract`、`mut_path_condition` 和 `test_objective`。判断须以
`support=evidence` 关联正式 evidence ID，或以 `support=assumption` 给出明确的
`assumption_reason`；不能两者混用。`claim_basis` 进一步用 `target` 和
`target_index` 覆盖选择理由、允许变化、阻塞未知与非阻塞未知；静态反事实也须
声明 `semantic_role`。上述字段用于复核内容来源，
不扩展或改写冻结的正式契约 Schema。

## 能证明和不能证明的事项

通过验证意味着结构合法、引用属于允许输入、行号和引用原文匹配。它不能证明：

- 原文支持所写结论，而不只是存在于输入中。
- 场景条件完整，或所有合法路径都被穷尽。
- 模型没有将并非必要的行为标成 required。
- 契约能生成可运行的 Mock，或者目标测试会通过。

Prompt、条件字段、证据引用和明确的 candidate/unknown 可以约束输出并帮助人工审查，
不是语义正确性的证明。空 VERIFY 区域、导入的 Mockito 函数、测试名字都不能单独
构成调用次数或禁止额外调用的要求。
