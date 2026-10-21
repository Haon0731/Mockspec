# Scenario Inference Agent V0.1

本轮新增能力是从允许的测试和源码推导场景契约。没有实现 Mock 生成、反馈、评分、
目标程序执行、AST 或符号执行。两个冻结案例的准入记录和原 Adapter 保持不变。

## 输出模型

实现位于 `src/mock_spec/scenario.py`；机器可读结构见
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

## 推理和访问边界

System Prompt 单独版本化为 `src/mock_spec/prompts/scenario_v0.1.txt`，
版本为 `scenario-inference/v0.1`。依次要求识别预期、追踪依赖、推导场景、检查证据、
生成契约；只输出结论和可核查的简短依据。

宿主 CLI 只调用现有 Adapter。SDK Agent 只获得 `task.to_dict()` 的 JSON：
没有源码路径读取工具、命令执行、检索、handoff 或其他 Agent。
System Prompt 将源码和注释视为数据，不能授权越界读取。SDK tracing 关闭，
请求设置 `store=False`，每次任务不使用先前会话。

采用官方 [Agent 结构化输出接口](https://developers.openai.com/api/docs/guides/agents/define-agents)
和 [tracing 控制](https://developers.openai.com/api/docs/guides/agents/integrations-observability)。
本次安装验证版本：OpenAI Agents SDK 0.24.0、Pydantic 2.14.1、Python 3.12.13。
SDK 由宿主运行，不是能访问宿主文件系统的代码执行 Agent；这也不是 OS 级隔离。

## 能证明和不能证明的事项

通过验证意味着结构合法、引用属于允许输入、行号和引用原文匹配。它不能证明：

- 原文支持所写结论，而不只是存在于输入中。
- 场景条件完整，或所有合法路径都被穷尽。
- 模型没有将并非必要的行为标成 required。
- 契约能生成可运行的 Mock，或者目标测试会通过。

Prompt、条件字段、证据引用和明确的 candidate/unknown 可以约束输出并帮助人工审查，
不是语义正确性的证明。空 VERIFY 区域、导入的 Mockito 函数、测试名字都不能单独
构成调用次数或禁止额外调用的要求。

## 本轮运行状态

环境没有 `OPENAI_API_KEY` 或 `MOCKSPEC_MODEL`，因此没有发起真实模型请求。
`examples/scenarios/*.synthetic.json` 是基于允许输入人工编写的合成响应；
相应 `*.replay.json` 是 CLI 对真实冻结输入校验后产生的实际回放输出。
这些文件不是模型推理结果，也不证明模型质量。不能据此宣称真实推理已验证。

测试分别覆盖 SDK 结构化输出、仅传入 UnifiedTask、超时/调用失败/无效输出、
非法证据、条件缺失、未知行为以及原有输入隔离行为。示例中的关键歧义和运行命令见
[示例说明](../examples/scenarios/README.md)。

2026-10-20 本机验证：

- `.venv/bin/python -B -m pytest -q -p no:cacheprovider`：**92 passed**，
  原有 54 项全部保留通过，新增 38 项，无跳过。
- 两个真实输入通过实际 SDK Runner + SyntheticModel 的离线集成测试。
- 构建 wheel、安装到项目 `.venv`，确认 Prompt 与严格输出 Schema 可加载。
- 通过已安装的 `mock-spec-scenario` 分别回放 Java/Python，退出码均为 0，
  共 12 条证据引用的 source_id、行号、原文通过验证。
- `compileall` 和 `git diff --check` 通过。项目未配置独立 lint/typecheck 工具；
  未将语法检查宣称为静态类型检查。

## 本轮文件

新增：

- `src/mock_spec/scenario.py`：Pydantic 契约和完整验证器。
- `src/mock_spec/inference.py`：共享 SDK Agent。
- `src/mock_spec/prompts/scenario_v0.1.txt`：版本化 System Prompt。
- `src/mock_spec/cli.py`：单案例入口和带来源标记的输出。
- `tests/test_scenario.py`、`tests/test_inference.py`、
  `tests/test_scenario_cli.py`、`tests/test_scenario_real.py`。
- 本文、`docs/scenario-contract.schema.json`。
- `examples/scenarios/README.md`、两个 `*.synthetic.json` 和两个 `*.replay.json`。

修改：`pyproject.toml`（依赖、CLI、Prompt 打包）、`README.md`（新能力与使用方法）。
未修改旧 Stub-Tester、已审核输入记录、原 Adapter 或原有测试。已有未提交文件
保持原状；本轮没有创建提交或发布远端任务。
