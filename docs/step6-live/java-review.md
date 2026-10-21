# Java JarFiles — AI辅助语义审查，待人工确认

本审查只使用本轮正式请求中携带的已审核 `UnifiedTask`、本轮原始模型响应、
工具轨迹与验证结果。不使用 Reference Mock、隐藏测试内容、旧数据、历史结果或
Ground Truth。审查对象将包括原始候选以及通过 Schema/Contract Validator 后的有效
Contract；结构验证通过不等于语义正确。

## 可见源码基线与审查准则

可见测试构造一个名为 `classes` 的 layer，其中源路径为
`path/to/tempDirectory/class1.class`，容器提取路径为
`/app/explodedJar/class1.class`。masked setup 之后，测试把五个可见对象传给
`JarFiles.toJibContainerBuilder`，再对 `ContainerBuildPlan` 作如下断言：基础镜像为
`adoptopenjdk:8-jre`；平台为 Linux/amd64；创建时间为 epoch；格式为 Docker；环境、
labels、volumes、exposed ports 均为空；user 与 working directory 均为 null；entrypoint
精确等于 `java`, `-cp`, `/app/explodedJar:/app/dependencies/*`, `HelloWorld`；最后只有
上述 `classes` layer，且 entry 与提取路径匹配。masked mock/stub 与 verification 的内容
均不可见，不能当作测试要求。

目标实现有两个独立分支点：

1. `getFrom()` present 时走 `ContainerBuilders.create(...)`；absent 时才根据
   `processor.getJavaVersion() <= 8` 选择 Java 8，否则选择 Java 11。
2. 自定义 entrypoint 为空时才调用
   `processor.computeEntrypoint(jarOptions.getJvmFlags())`；非空时直接使用自定义值。

随后实现无条件调用 `processor.createLayers()`，并把 entrypoint、layers、ports、
volumes、environment、labels 和 program arguments 写入 builder；user、format、
creation time 只在对应 Optional present 时显式写入。由此至少要保留以下替代路径：

- Java 8 镜像既可能来自 `getFrom()` absent 且 Java version `<= 8` 的默认路径，也可能
  来自 present 的显式 from 配置；仅凭最终 base image 断言不能唯一确定 masked setup。
- 精确 entrypoint 既可能来自空 custom entrypoint 后的 `computeEntrypoint(...)`，也可能
  直接来自非空 custom entrypoint；仅凭最终列表不能唯一确定依赖调用。
- epoch、Docker、Linux/amd64、null working directory 等最终值可能是 builder 默认值，
  也可能由可见或不可见配置间接产生。请求没有提供 Jib/ContainerBuilders 的实现，不能
  从这些断言反推唯一的 mock 行为。
- supporting source 中三个 permitted method 的可见方法体都会抛
  `UnsupportedOperationException`；测试传入的是名称显示为 mock 的对象，但其声明和
  setup 被 masking。Contract 应把必要返回值或替代路径作为候选/未知条件，不能把隐藏
  stub 冒充成可见事实。

逐项审查采用以下判断：

1. **准确性**：结论是否由可见测试或目标源码支持，引用是否精确。
2. **条件完整性**：是否保留 from present/absent、Java version 阈值、custom entrypoint
   empty/non-empty、Optional present/absent 及依赖抛出条件。
3. **替代路径**：是否避免从同一最终 build plan 反推唯一 setup，特别是 base image 与
   entrypoint 的两组合法来源。
4. **不确定性**：是否明确 masked setup/verification、builder 默认值、外部 API 行为和
   mock 具体返回值未知。
5. **行为推导**：是否准确区分条件调用与无条件调用，并把最终 plan 断言和依赖交互分开。

## V0.1 正式结果

- 结果：`completed`；`schema_and_evidence_locations_passed`；Contract 有效。
- 请求：1；工具调用：0（V0.1 不提供工具）。
- Token：输入 5,653；输出 21,498，其中 reasoning 17,149；合计 27,151。
- 保守估费：US$0.0274935。
- 独立只读复核：12 个 Evidence 的范围与原文全部精确匹配。

以下编号是本审查按数组顺序添加的追踪 ID。

### 分类审查

| Contract 类别 | 追踪 ID | 审查判断 | 可见依据与问题 |
| --- | --- | --- | --- |
| `test_objectives` | O0–O3 | 准确 | 完整覆盖 base/platform/time/format、空集合与 null、精确 entrypoint、单一 classes layer 及其 entry。它们写成最终 plan 目标，没有把 masked stub 冒充成断言。 |
| `scenario_hypotheses` | H0 | 候选通过 | 把 absent from、Java `<= 8`、空 custom entrypoint、processor 返回值和空集合组合为主候选，并明确 setup 被 masking。 |
| `scenario_hypotheses` | H1–H2 | 关键替代路径通过 | H1 保留 present from 仍可能产生相同 base image；H2 保留非空 custom entrypoint 直接等于断言列表且不调用 `computeEntrypoint`。两者避免从最终值反推唯一 setup。 |
| `dependency_constraints` | D0–D2、D5 | 准确 | 正确表达 absent-from 下的 Java 版本阈值、`createLayers` 的无条件传播、entrypoint 二选一及 from 两分支互斥。 |
| `dependency_constraints` | D3 | 基本准确，外部行为略强 | 源码直接把四个 collection getter 结果交给 setter；“非空必出现在 plan”还依赖未提供的 builder 行为，但不影响 getter 到 setter 的可见传播关系。 |
| `dependency_constraints` | D4 | 谨慎准确 | 保留 format/time 可由 absent 默认值或 present 的等价值产生；user null 与 empty Optional 的关联合理，但仍受外部 builder 行为边界限制。 |
| `expected_interactions` | I0–I5 | 准确 | 每条 required interaction 都有 condition；正确区分 from、entrypoint 两个条件分支，并将所有 interaction 保持 `assertion_policy=not_implied`。 |
| `verification_candidates` | 空 | 谨慎 | 可见 verify region 为空，V0.1 没有据此猜测隐藏 verification。 |
| `evidence` | E0–E11（原 ID `ev-*`） | 准确 | 12 条引用全部精确匹配；测试断言、mask、目标分支、setter 与 processor partial source 均有覆盖。 |
| `uncertainty` | U0–U7 | 完整 | 明确保留 masked setup、base 与 entrypoint 的两种来源、平台/working directory 的外部默认、Optional 等价路径、layer 等价实例、processor mock 以及隐藏 verification。 |

### V0.1 总体判断

V0.1 是有效且语义校准良好的 Contract。它的主要优点不是选择了最可能的 masked setup，
而是同时保留两个会直接影响 Mock Generation 的合法替代路径：显式 from 可能得到同一
base image，以及 custom entrypoint 可能已经是断言中的四元素列表。对未提供的 builder
实现也基本保持了不确定性。D3 对 setter 后的最终表现稍强，但没有改变总体行为推导。

## V0.2 正式结果

- 结果：`structure_error`；`contract=null`；没有有效 Scenario Contract。
- HTTP 响应：3 次，均完成；应用层无技术故障重试。
- 工具：模型自主请求 4 次；3 次 `inspect_source` 返回 `ok`，
  1 次 `inspect_dependency_usage(mut)` 返回 `incomplete`。
- Token：输入 33,645；输出 15,032，其中 reasoning 8,611；合计 48,677。
- 保守估费：US$0.0281319。
- 独立只读复核：原始候选 15 个 Evidence 的范围与原文全部精确匹配。

### 原始候选与结构失败

第三次响应给出了可解析 JSON，且包含全部十个顶层字段，但
`ScenarioContract.model_validate` 精确报告五处错误：

| 原始候选位置 | 本文 ID | 错误 |
| --- | --- | --- |
| `expected_interactions[0]` | I0 | `status="required"`，但 `condition=null`。 |
| `expected_interactions[3]` | I3 | `status="required"`，但 `condition=null`。 |
| `expected_interactions[4]` | I4 | `status="required"`，但 `condition=null`。 |
| `expected_interactions[8]` | I8 | `status="required"`，但 `condition=null`。 |
| `expected_interactions[9]` | I9 | `status="required"`，但 `condition=null`。 |

required claim 必须声明适用条件，因此整个候选结构无效。下文对其语义的评价只说明原始
候选包含什么，不能把它称为有效 Contract。

### 工具调用、事实链与 `incomplete` 影响

| 顺序 | 模型自主请求 | 实际返回 | 可用于推导的事实 | 在原始候选中的反映 |
| --- | --- | --- | --- | --- |
| T1 | `inspect_source(mut, 58–95)` | `ok` | 完整 from 分支、Java 版本 ternary、layer/entrypoint 分支、setter 链、三个 `ifPresent` 与 return | E-MUT-*；H0–H3、D0–D9、I0–I10、U0/U2/U3/U5 |
| T2 | `inspect_source(test, 30–104)` | `ok` | local layer、masked setup、调用与所有 plan 断言、空 verify markers | E-TEST-*；O0–O4、H0/H4、D0–D8、V3/V4、U0–U3 |
| T3 | `inspect_source(processor, 50–61)` | `ok` | 三个 permitted method 的可见方法体均抛 `UnsupportedOperationException` | E-PROC-DECLS；D10/D11、U4 |
| T4 | `inspect_dependency_usage(mut)` | `incomplete` | 返回 31/58 个 syntax-local facts；确认 from 分支 guard、Java 版本 guard、`createLayers` 无 guard、entrypoint ternary，以及 setter 链前段 | H0–H3、D9、I0–I7、V0–V2 与源码分支一致；没有形成有效 Contract |

T4 因 `output_limit` 丢失 27 个事实。返回的 31 条中包含 line 74 的 `createLayers`，也包含
一个因 Java helper 的单条 quote 400 字符限制而缺少 quote 的 `setProgramArguments` fact，
但没有三个 `ifPresent` 的 facts；缺失部分
可能包含后续 collection getter、Optional getter/`ifPresent` 与局部变量读取事实。
依赖工具只筛选 call、named_expression、local_reference、variable_read，不能将 return
或 method reference 本身声称为这 27 条截断事实的一部分。工具自身同时声明它只提供 syntax，不提供 data flow 或 feasible-path 证明，
且遗漏不能证明不存在。因 T1 已返回 mut 58–95 的完整源码，这些源码行没有从模型上下文
消失，但 T4 没有为它们提供同等级的结构化确认。

原始候选仍在 I8/I9 写出这些后段 getter “各调用一次”，在 D7/D8 和 U0 中讨论 Optional
行为，说明这些结论主要取自 T1 源码，而非 T4 已返回 facts。候选没有在 `uncertainty`
记录 T4 的截断、缺失的 27 个 facts 或 syntax-only 限制，因此不能说 Agent 完整吸收了
Java 工具的不完整性。缺失事实没有阻止识别主要分支，但降低了“工具独立确认完整调用
集合”的可信度；尤其不能用 T4 证明后段调用全部存在、调用次数或可行路径。

### 原始候选逐项审查

以下 ID 按原始数组顺序添加。`通过`只表示该项可作为原始语义候选保留。

#### Test objectives

| ID | 判断 | 说明 |
| --- | --- | --- |
| O0 | 断言准确，场景收窄 | base image 断言准确，但只关联 no-from/Java 8 场景，遗漏 present-from 产生相同 base 的合法路径。 |
| O1 | 断言准确，来源收窄 | entrypoint 值准确；condition 假定来自 processor，未保留完全相同的 custom list。 |
| O2 | 断言准确，实例表述略强 | layer 断言准确；最终相等只要求等价 entries，不强制 `createLayers` 返回测试中同一对象实例。 |
| O3 | 通过 | 空集合、null user/working directory 与 setter 条件均有可见依据。 |
| O4 | 目标值准确，机制无证据 | Docker/EPOCH/platform 断言准确，但 condition 直接声称 builder default 提供平台；请求没有 Jib builder 实现。 |

#### Scenario hypotheses

| ID | 判断 | 说明 |
| --- | --- | --- |
| H0 | 候选通过 | 合理表达最可能的 absent-from、Java 8、processor entrypoint 路径。 |
| H1 | 候选通过 | 正确保留 present-from 分支以及 `getJavaVersion` 不执行。 |
| H2 | 候选通过 | 正确表达 absent-from 且 Java `> 8` 会选择 Java 11，因而不满足当前 base 断言。 |
| H3 | 分支正确，结果措辞错误 | custom entrypoint 会绕过 `computeEntrypoint`，但它不一定“instead of the asserted four elements”；custom list 可以恰好等于断言列表。 |
| H4 | unknown 通过 | 正确保留 masked stub 的具体返回值未知。 |

#### Dependency constraints

| ID | 判断 | 说明 |
| --- | --- | --- |
| D0 | **required 语义错误** | 由 base image 断言推出 `getFrom()` 必须 empty，排除了 present-from 指向同一镜像的合法路径；V0.1 H1/U1 已正确保留该路径。 |
| D1 | 条件内准确 | 一旦限定 absent-from 且要选 Java 8，非 null `<= 8` 正确。问题在于它依赖 D0 的过度收窄。 |
| D2 | 返回值要求过强 | 必须得到等价的单层与 entries，但可见断言不要求返回测试中构造的同一 layer 实例。 |
| D3 | **required 来源错误** | 精确 entrypoint 可以来自 processor，也可以来自完全相同的 custom list；condition 只写“断言必须成立”，不足以把 processor 路径升为 required。 |
| D4 | 条件内准确 | custom list 为空时，源码确实把 `getJvmFlags()` 结果传给 `computeEntrypoint`。 |
| D5 | 候选通过 | 承认空 custom list 只是更可能、不是断言强制；它实际上暴露了 D3 的 required 过强。 |
| D6 | 基本准确 | collection 值被直接交给 setter；最终为空仍依赖 builder 对这些 setter 的常规行为。 |
| D7 | 基本准确 | present user 会被写入且之后没有可见 reset，empty Optional 是满足 null user 的合理必要条件；仍受未提供 builder 实现限制。 |
| D8 | 通过 | 正确保留 Optional empty 与 present default-equivalent 两条路径。 |
| D9 | 通过 | 两个 from 分支互斥，条件完整。 |
| D10 | 候选通过 | 成功到达断言意味着所选路径没有传播声明的异常；没有冒称精确 stub 已知。 |
| D11 | 候选通过 | 参数名与 partial source 支持 test-double 推断，保持 candidate 合适。 |

#### Expected interactions

| ID | 判断 | 说明 |
| --- | --- | --- |
| I0、I3、I4、I8、I9 | **结构无效** | 五条均为 required 且 `condition=null`。内容对应正常执行路径，但没有满足 Contract 的条件约束。 |
| I1 | 通过 | absent-from 条件下调用 `getJavaVersion`，并正确限定 scenario。 |
| I2 | 保守通过 | present-from 时调用 `ContainerBuilders.create` 是源码确定行为；写为 candidate 偏保守但不错误。 |
| I5–I6 | 通过 | 空 custom entrypoint 条件下，`getJvmFlags` 与 `computeEntrypoint` 的调用和传递准确。 |
| I7 | 通过 | 非空 custom entrypoint 时不调用 processor；这条比 H3 的结果措辞更准确。 |
| I10 | 候选通过 | no-from 且 Java `<= 8` 时调用 `Jib.from`，并明确不是 mock target。 |

所有 11 条都保持 `assertion_policy=not_implied`；问题是其中五条 required claim 缺条件，
不是把 interaction 错当成 verify requirement。

#### Verification candidates

| ID | 判断 | 说明 |
| --- | --- | --- |
| V0–V2 | 候选通过 | 分别考虑参数原样传递、present-from 时不调用 Java version，以及 `getFrom` 的路径相关调用次数；V2 是 V0.1 未表达的细粒度候选。 |
| V3 | 内容合理 | `createLayers` 结果进入 setter 值得考虑；candidate 允许 condition 为 null。 |
| V4 | 引用错位且措辞不准 | statement 谈 masked VERIFY region，却只引用 `E-TEST-STUB-MASK`（59–60），没有引用 verify markers（100–101）；“是否可能 verification”也应写成“实际 verification 内容未知”。 |

#### Evidence

| ID | 精确匹配 | 语义审查 |
| --- | --- | --- |
| E-MUT-FROM-CHECK、E-MUT-FROM-BRANCH、E-MUT-JAVA-VERSION、E-MUT-LAYERS-ENTRYPOINT、E-MUT-SETTERS | 是 | 直接支持分支、条件调用和 setter 传播；不能单独证明 masked setup 选择了哪个分支。 |
| E-MUT-IFPRESENT | 是 | 直接证明 only-if-present setter；rationale 对 empty Optional 导致 Docker/EPOCH 默认值的连接仍依赖未提供的 builder 实现。 |
| E-TEST-LAYER、E-TEST-CALL、E-TEST-BASE-PLAN、E-TEST-EMPTY-PLAN、E-TEST-ENTRYPOINT、E-TEST-LAYERS-ASSERT | 是 | 准确覆盖 local layer、调用和主要断言。 |
| E-TEST-STUB-MASK | 是 | 只证明 setup 被 masking，不证明 verify region 内容。 |
| E-TEST-EXTRACTION | 是 | 96–99 行包含 array equality 的收尾和 extraction path；单独引用未包含 equality 的 expected expression，需与 E-TEST-LAYER 合用。 |
| E-PROC-DECLS | 是 | 准确证明 partial supporting source 中三个方法体抛异常；不能揭示 mock stub 返回值。 |

#### Uncertainty

| ID | 判断 | 说明 |
| --- | --- | --- |
| U0 | 通过 | 正确保留 platform/format/time 的来源与外部 Jib 行为未知。 |
| U1 | 部分通过 | masked setup 未知正确；“whether any verification exists”没有引用独立的 verify markers。 |
| U2–U4 | 通过 | 分别保留 jvm flags、program arguments 与 nullable Java version 的未知。 |
| U5 | **过度排除合法路径** | 说 present-from “not exercised by the visible assertions”，但显式 from 仍可能生成断言中的相同 base；真正未知的是该路径的外部实现与 masked setup 是否选择它。 |
| 缺失项 | **不完整性未披露** | 没有任何 uncertainty 记录 T4 只返回 31/58 facts、27 个 facts 被 output limit 截断，以及工具只提供 syntax、不证明 data flow/feasible paths。 |

### V0.2 总体判断

原始候选增加了 Java 11 反例、精细调用清单、`getFrom` 路径相关调用次数和 nullable
unboxing 风险。但它在最关键的 Scenario Contract 推导上退化：D0 把 absent-from 错升为
required，D3 把 processor entrypoint 来源错升为 required，H3 又错误暗示 custom list
不能等于断言列表。这些正是 V0.1 已正确保留的替代路径。此外，五条 interaction 缺少
condition 使整个候选无效，工具 `incomplete` 也没有进入 uncertainty。

## V0.1 / V0.2 对照与工具事实链

| 问题 | Java 结论 |
| --- | --- |
| Agent 是否自主调用程序分析工具？ | **是。** 模型自主发出 4 次调用：3 次源码检查和 1 次依赖使用分析；前三次 `ok`，最后一次因 output limit 为 `incomplete`。 |
| 是否获得 V0.1 没有可靠获取的程序证据？ | **只有细粒度 syntax 证据，没有关键 Scenario 证据改善。** T4 结构化确认了 branch guards、`createLayers` 无 lexical guard、entrypoint ternary，并支持 present-from 路径 `getFrom` 两次调用等细节；但 V0.1 已从同一可见源码可靠保留 base 与 entrypoint 的关键替代路径。T4 又只返回 31/58 facts。 |
| 工具事实是否实际反映在最终候选？ | **部分反映在原始候选。** H0–H3、D9、I0–I7、V0–V2 与返回的 guard/call facts 对应；T1–T3 的源码内容也进入 Evidence。后段调用主要来自 T1，而 T4 的截断和限制没有进入 uncertainty。由于 `contract=null`，不存在可称为最终有效 Contract 的工具增强结果。 |
| Scenario Contract 的行为推导改善、无变化或退化？ | **正式结果退化，原始语义候选也总体退化。** V0.2 增加一些交互细节，但错误收窄两个关键替代路径、遗漏工具不完整性，并从有效 V0.1 退化为 `structure_error`。 |
| Java `incomplete` 对结论可能产生什么影响？ | 缺失 27 个结构化 facts 意味着不能用 dependency tool 完整核对后段 getter、Optional ifPresent 调用或完整调用集合。return 和 method reference 本身也不属于该工具筛选的事实类别。完整源码仍由 T1 可见，所以主要分支判断不缺源码；影响集中在“工具是否独立确认”和调用清单/次数的置信度。 |
| 是否支持进入下游 Mock Generation？ | **Java 结果不支持。** V0.2 没有有效 Contract；V0.1 虽有效，但工具增强版本在替代路径和结构上退化，无法作为进入下游的改进证据。 |

以上是观察性、AI 辅助语义审查，待人工确认。工具调用、工具返回事实、原始候选变化、
Schema 有效与行为推导改善是不同事件；本案例证明了自主调用，但没有证明工具增强改善了
可消费的 Scenario Contract。
