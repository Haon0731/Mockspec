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

## 能证明和不能证明的事项

通过验证意味着结构合法、引用属于允许输入、行号和引用原文匹配。它不能证明：

- 原文支持所写结论，而不只是存在于输入中。
- 场景条件完整，或所有合法路径都被穷尽。
- 模型没有将并非必要的行为标成 required。
- 契约能生成可运行的 Mock，或者目标测试会通过。

Prompt、条件字段、证据引用和明确的 candidate/unknown 可以约束输出并帮助人工审查，
不是语义正确性的证明。空 VERIFY 区域、导入的 Mockito 函数、测试名字都不能单独
构成调用次数或禁止额外调用的要求。
