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

QUERY wire 在此基础上使用 `staged-query/v0.4`：模型评估仍仅含 impact/rationale，
另用独立字段更新与原目标关联的问题及当前候选；宿主关联唯一 pending receipt 后补入
内部 v0.1 assessment 的 attempt_sequence。
原始 wire、宿主关联和宿主 quote 分别留证。未知 null 字段的有限纠正不接受宿主投影为结果；
模型重发须与比较快照精确一致，且与 DELIVER 共用一次名额。
候选表达有条件的依赖约束；imports、测试名与最终断言不能独自证明隐藏 Mock 配置，
仅取得声明行且未确定行为时评估为 no_new_information。语义充分性由独立评阅判定。

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

## 首轮 Spec 语义校准（研究草案）

Scenario Agent 根据审核后的 MUT、Masked Test 与授权仓库材料，推断**场景条件下、
有证据支持的部分依赖行为规约**，供后续 Mock 生成使用。正式
`mock-spec.scenario/v0.1` 与生产 CLI 保持兼容；staged-inference 研究入口另行交付
版本化、自包含的生成器消费规格，把正式契约与必要的范围、依据、允许变化和未知绑定在一起。
参考卡与可重放的结构投影见 [合成参考草案](../examples/scenarios/semantic-calibration/README.md)。
这些卡是评阅资料，不是模型推理能力已经提高的证据。

### 最小语义约定

1. **范围**：先固定原始测试目标、输入、场景条件、可替换边界及必须保留的真实行为。
   约束只覆盖这次测试及声明的交互历史，不泛化为完整 API 规范。
   condition 写输入、分支与授权前提；待保证的正常完成和目标结果写在约束中，不能把结论当适用前提。
2. **关系**：每条依赖约束标明适用条件、目标调用、参数关系、允许响应或异常，
   以及相关状态影响；必要时写出跨调用关系。路径事实和生成后的 verify 义务分别判断。
3. **依据**：逐条区分测试要求、实现事实、推导结论、附加假设。
   `required` 是条件内的义务，**不是证据充分度**；推导需列前提，假设不能靠引用存在变成事实。
   假设即使合理，也须说明未确认时对使用的影响。
   当前冻结 MUT 是回归分析参考，用于确定当前执行路径和依赖使用，不等同于正确的业务规范。
   测试要求、实现事实和依赖约定冲突时保留冲突及其影响，不能靠任意 Mock 响应消除冲突。
4. **多解与精确性**：保留关系而非任选见证。例如正整数 `p*q=100` 不固定 `p=10,q=10`；
   `p*2=100` 则要求 `p=50`。不得无据固定次数、顺序、常量或内部实现。
   “具体值仅作实例”不适用于证据已经要求的精确值。
5. **未知**：已知允许变化必须有依据；未知写明缺什么、影响哪个决定、是否阻塞当前使用，
   以及宿主授权下是否还有取证渠道。只看到 import、声明或路径不能认定已读取行为；
   初始 sources 缺项不能证明不可探索。未观察与宿主明确禁读分别记录。
6. **交付**：有用的部分约束可以保存，但未覆盖关键依赖需求时不能宣称可供生成。
   多场景有实质歧义就保留条件与歧义；不得机械合取互斥分支或选最容易通过的分支。
   结构成功、引用有效、查询停止、语义充分是四个不同判断。
7. **分支与多解**：有证据确认满足原目标、依赖约定和适用条件的多个分支，可以作为合法替代；
   生成器可选择其中一条完整分支。尚无法判断某分支是否符合任务时仍是实质歧义，不授权选择。
   相同结果或未找到反证不足以确认分支合法；同一关系中的多个具体值则不构成场景歧义。
8. **消费**：解释 Spec 需要 task 的固定输入、宿主明确提供的探索与替换范围、正式契约的条件/关系/证据/未知、
   选择理由、假设、允许变化、阻塞判断和运行身份绑定。这些内容由研究入口的自包含规格统一交付；
   `inference_record.json` 用于审计，不是生成器理解关键含义的必要输入。
   只读 `dependency_constraints.statement` 的消费者不满足此约定。

### 现有字段适配与具体缺口

下表中“正式可表达”仅指具备清楚承载位置，不意味着验证器会判定语义正确。

| 语义 | 判断 | 位置及实际限制 |
| --- | --- | --- |
| 原目标、固定输入 | 现有正式字段可以清楚表达 | task + `test_objectives`；输入不由 contract 自行改写 |
| 允许替换的依赖边界 | 存在实际表达歧义或信息缺口 | task 的接口声明可为空，masked slot 只给编辑位置；正式 contract 无专门替换边界字段。须结合宿主授权说明与研究记录，R 中尚未确认的调用替换绑定保持阻塞，不由槽位或 import 推定 |
| 条件、调用参数、响应、异常 | 现有正式字段可以清楚表达 | `scenario_ids/condition` + `dependency_constraints`；关系仍是文字，须人工读懂符号与量词 |
| 跨调用/状态关系及历史范围 | 现有正式字段可以清楚表达 | E 的 condition 限定单次 put/get，statement 写对应关系；不具机器可判定的状态机语义 |
| 来源原文 | 现有正式字段可以清楚表达 | `evidence`；验证可追溯性，不验证蕴含 |
| 事实/推导/假设的分类 | 由研究规格补充 | `semantics.premises` 区分 implementation_fact、test_requirement、dependency_semantics 和 assumption；正式三值 Claim status 不承担依据分类 |
| 合法变化与未知影响 | 由研究规格补充 | `allowed_variations` 绑定分支/约束及依据；`unknowns` 绑定影响并区分 blocking、nonblocking 和 out_of_scope |
| 义务与依据充分度正交 | 存在实际表达歧义或信息缺口 | A 的乘积关系条件内必需，但不是验证器证明的结论；不能把 required 当作已证实 |
| 多分支的消费选择 | 正式契约需结合研究规格解释 | 正式契约可保存多条条件路径；研究规格逐条区分有据的合法替代与未决分支，不授权下游合取或选择未决分支 |
| 探索授权与不可探索性 | 由研究规格及运行边界共同表达 | task/宿主范围 + exploration 与 unknowns；F 的禁读不能从 source 列表推断，必须来自明确的访问限制 |
| 部分约束与关键需求覆盖 | 由研究规格显式承载、仍需语义评阅 | `goal_coverage` 为每个目标标记 constrained、real_code 或 blocked；机械完整映射不证明模型没有漏掉问题 |
| 产物配套及假设绑定 | 由研究规格补充 | 正式 Claim 不增加 assumption ID；研究规格把 task、正式契约、依据、假设影响、未知及运行身份绑定后供生成器消费 |

例如 C 读取前后都能做成结构有效契约，但只有读取后才知道 `p=50`；F 即使合法停止仍
无法给出可用完整关系。正式契约可保存多条路径；研究规格必须逐条给出 branch 决定，
不能为了满足旧选择状态的数量限制而删除路径。

### 自包含研究规格

研究入口的 `dependency_spec.json` 使用 `mock-spec.dependency/v0.1`。它嵌入本次完整 task 和
正式 `ScenarioContract`，并通过 `run_id`、`input_sha256`、原目标坐标和探索摘要绑定本次运行。
`regression_reference.status=reference_only` 明确冻结 MUT 只作为路径分析参考。
`input_origin` 区分 `synthetic_fixture` 与 `reviewed_adapter`；`consumption_rule` 随产物说明完整分支选择及部分规格的限制。
live 模式的 task 必须来自审核 Adapter；synthetic_replay 中的合成 task 仍不构成真实输入准入。
`original_goal_mapping` 将每个 EXTRACT 原目标坐标映射到正式 `test_objectives`，并要求两侧全覆盖；
这保护目标身份与范围，不证明模型对目标文字的解释正确。

`semantics` 使用 `staged-spec-semantics/v0.1`，包含：

| 字段 | 消费含义 |
| --- | --- |
| `delivery_status` | `ready` 可具体化；`partial` 保留阻塞项；`conflict` 保留依据冲突 |
| `fixed_inputs` | 本次输入与调用条件、正式 evidence 依据和 `goal_indexes`；每个正式目标须有固定输入说明 |
| `replacement_boundary` / `preserved_real_logic` | 可由 Mock 控制的行为与必须继续真实执行的逻辑；边界可为 confirmed、assumed 或 unresolved |
| `premises` / `constraint_basis` | 实现事实、测试要求、依赖语义和假设，以及每条正式依赖约束所依赖的前提 |
| `branches` | 每个正式 scenario 分别标记 `allowed` 或 `unresolved`；前者必须有证据和前提且不能留未决项 |
| `goal_coverage` | 每个原目标由依赖约束覆盖、由真实代码独立决定，或仍被未知阻塞 |
| `unknowns` | 问题、影响及其关联正式 uncertainty、目标、约束和场景；区分 blocking、nonblocking 与 out_of_scope |
| `allowed_variations` | 有明确依据且绑定适用分支和约束的自由度 |
| `conflicts` | 测试、MUT 或依赖依据之间无法被合理 Mock 消除的冲突及影响 |

宿主检查 ID、索引、覆盖、状态和身份一致性：每条正式依赖约束有一份 basis，每个测试目标有
coverage，每个场景有 branch 决定；悬空依据、把阻塞项标成 ready、无依据的 allowed 分支等会被拒绝。
这些检查仍不证明引用蕴含结论，也不证明遗漏的行为问题不存在。

研究选择状态与消费状态分开：SELECTED 选择一个有据分支；ALTERNATIVES 表示多个分支均为
allowed 且生成器可选一；AMBIGUOUS 必须至少保留一个 unresolved 分支；INCONCLUSIVE 可携带
完整有效的 partial/conflict 规格说明限制。QUERY 的 evidence_sufficient 只结束当前探索，
不替代最终 delivery_status，也不证明规格语义充分。

预算或超时保护停止时，仅可另存已经完整校验的 partial checkpoint，且 checkpoint 之后没有新的查询尝试。
它的探索摘要标记 `budget_exhausted/controller_stop`，研究运行仍未完成；不会发布旧的 ready 提案或把它自动改成 partial。
没有这样的 checkpoint 时，只保留诊断记录。消费示例见 [乘积关系与未知变换](../examples/scenarios/consumer-specs/README.md)。

### 简短评阅表

每卡分别评阅：原目标是否保留；条件与关系是否有据；关键关系是否遗漏；是否过强或过弱；
是否内部冲突；未知是否诚实；交付声明是否匹配覆盖范围。对“通过”写具体理由，
对“待定”写缺失证据及影响。自动独立审阅仅为机器评阅，不称人工确认或金标准。
结构检查结果单列，不以测试通过替代此表。
