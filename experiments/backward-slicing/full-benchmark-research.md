# 完整 MockBench 上的测试目标导向后向切片：调研结论

日期：2026-11-09。本文研究的是：从已遮蔽测试中可见的观察目标出发，跨过程反向追到影响它的依赖调用，把数据、控制、堆/别名、异常与未解析边界作为可追溯事实交给 Coding Agent。它不是把切片本身当作 Mock 答案，也不是全量实验结果。

## 结论

方案值得在完整数据集上系统验证，但目标应是先测量“现有可见证据能恢复什么、在哪断裂”，而不是先承诺某个后端或完整符号执行。经典 SDG 后向切片说明了跨过程调用上下文、参数和返回关系为何必须被显式建模；Mock 行为生成已有从测试中分析 Mock 使用的先例；近期 LLM 测试工作也支持把路径和相关上下文分阶段提供给模型。三者都不能推出：切片会自动给出唯一正确 Mock，或一次测试通过就有缺陷区分能力。

## 方案边界

```text
测试观察目标（值 / 异常 / 状态 / 交互）
  → 后向切片（可见的 data、control、heap、exception、call 边）
  → Mock frontier（未解析依赖调用及其参数、normal/raise、可能写集合）
  → 有界条件化事实包
  → Agent 选择一个场景并填充授权 Mock 区域
  → 原测试、故障变异、合法替代分别评价
```

切片准则不能只取 `assertEquals` 的返回值。应至少收集：(1) 普通值/状态断言；(2) 预期异常及其 message/type；(3) 已有交互观察；(4) 目标调用的实参和其可见副作用。对每个 Mock frontier，输出调用位置、接收者/实参、调用上下文、normal 与 exceptional return、可能写入、解析状态和对应源码范围。无目标、动态派发过宽、库摘要缺失或别名不明必须保留为 `unknown`，不能被改写为“无影响”。

## 研究依据

### 已确立的机制

- Horwitz、Reps、Binkley 的跨过程切片以系统依赖图表示跨过程数据、控制及参数关联；其难点正是调用上下文和由调用效果形成的传递依赖。[Interprocedural Slicing Using Dependence Graphs（TOPLAS 1990）](https://doi.org/10.1145/77606.77608)
- Tillmann 与 Schulte 的 ASE 2006 原型从单元测试中对 Mock 的使用做 .NET 符号执行，生成带行为的 Mock，且不要求被替代行为已有真实实现。这直接支持“以测试可见用法约束未知依赖”的问题设定，但不提供 Java/Python masked-test 的现成实现。[Mock-object Generation with Behavior](https://www.microsoft.com/en-us/research/publication/mock-object-generation-with-behavior/)
- Jaffar 等的路径敏感后向切片说明：普通后向依赖集合会含有不可行路径，需要用路径约束过滤；代价与路径爆炸一起出现，因此它是精度方向，不是默认应全量启用的承诺。[Path-Sensitive Backward Slicing（论文 PDF）](https://jorgenavas.github.io/papers/BackwardSlicing-SAS12.pdf)

### 与 LLM 协作的可借鉴点

- SymPrompt 从 AST 收集近似路径条件与返回值，为每条路径构造提示，并选择性加入类型/依赖上下文；它显式允许无法符号化的调用保留原状，并通过路径最小化控制数量。[SymPrompt, FSE 2024](https://arxiv.org/abs/2402.00097)
- PALM（预印本）也把路径枚举与 LLM 生成分开，并指出符号编码与求解器建模限制会使复杂路径无法覆盖。它说明“路径事实 + 生成”可行，但其目标是测试生成与路径理解，不是未知 Mock 行为的跨过程恢复。[PALM（arXiv 预印本）](https://arxiv.org/abs/2506.19287)

这些来源支持的共同推论是：分析层应提供小而有来源的候选关系/路径，不应把未解析库和 Mock 行为伪装成已证实语义；Agent 负责在候选场景中具体化。把这一点用于“行为正确且可检错的 Mock”仍需要本仓实验验证，不能由上述工作直接证明。

## 不应作出的结论

1. Slice 包含某调用，只说明该调用可能与准则相关；不证明必达、只调用一次、正常返回，或必须写 `verify`。
2. 只满足原断言的 Mock 可能绕过原测试想覆盖的行为；反过来，过强的次数、顺序或精确值约束可能误杀合法替代。故应分开评价原测试通过、关系一致性、预定义故障检出与合法替代误拒绝。
3. `void`、写状态和异常不能因没有普通返回值而被丢弃；它们可能通过后续读取、catch/finally 或事件顺序影响观察目标。
4. 未知 frontier 不能交给模型补成事实，也不应在切片器找不到路径时宣称“依赖无副作用”。

## 可复用后端与先验证的输入问题

下面是基于官方能力契约的候选比较，不是本仓胜负结论；本次未安装或实跑这些新后端。

| 候选 | 可复用的能力 | 本方案仍需验证的部分 |
| --- | --- | --- |
| WALA（Java） | 已有 SDG 后向切片，可配置数据、控制、heap、exception 依赖 | 先构造可用入口/call graph/pointer analysis；遮蔽测试能否进入图、未解析 Mock 调用是否保留、源码映射是否完整 |
| Joern（Java/Python） | 源码 CPG 与跨过程 backward DDG slice，JSON位置输出 | 默认切片准则是调用实参；不能直接当作任意测试 oracle 的完整数据/控制/heap/异常切片器 |
| CodeQL（Python） | 局部/全局数据流与 path query，可配置 source/sink 和附加传播 | 不是通用 SDG 后向切片；动态加载、异常、void/状态与交互须单独验证和建模 |
| SootUp（Java） | Jimple、调用图及 IFDS/IDE 自定义分析框架 | 框架能力不等于已有可直接复用的本任务切片器，需计入额外实现成本 |

官方依据：[WALA Slicer 固定实现](https://github.com/wala/WALA/blob/v1.8.0/core/src/main/java/com/ibm/wala/ipa/slicer/Slicer.java)、
[Joern slicing 契约](https://docs.joern.io/cpg-slicing/)、
[CodeQL Python data flow](https://codeql.github.com/docs/codeql-language-guides/analyzing-data-flow-in-python/)、
[SootUp IFDS/IDE](https://soot-oss.github.io/SootUp/v3.0.0/write_analyses/)。
CodeQL CLI 有独立使用条款，研究选型时另核用途；不能默认作为任意用途的开放许可组件。
[官方发布与条款入口](https://github.com/github/codeql-cli-binaries)

**首个探针必须是原样 masked test 能否进入分析。** 待填区域可能连 Mock 变量声明也一并
遮蔽，因此源文件可读不代表测试可编译。只分析产品 bytecode 而丢掉测试观察点，不算方案通过。
首先验证容忍不完整源码的入口；若后端确实需要编译，可以单列实验性的分析骨架，但新增声明
只能来自可见类型信息，保持未知行为并记录源映射。不能从隐藏答案补变量，更不能填行为 stub
帮助工具“成功”。原样入口与骨架入口必须分别统计，不混称原始输入支持率。

已有本仓反证仍需保留：[Joern 4.0.658 记录](../../docs/step5b/report.md)中，Java含测试
输入构图失败、Python深度20切片超时。这是固定旧版本/配置的结果，不足以排除所有新版本。
仓库还有 [WALA pilot 协议](wala-pilot/protocol.md)，但仅凭协议与准备脚本存在不能宣称
已经完成实验。本次未取得它的完成报告，因而不把它计入新的后端成功证据。

## 完整数据集的验证设计

第一阶段只测分析证据，不让生成结果掩盖分析错误。冻结当前后端与配置，按案例记录每个准则能否定位、切片是否到达 MUT/可见 helper/Mock frontier、边类别、未解析原因、时间和证据大小。人工双人或独立复核一个按语言和失败类别分层抽样集，标注“支持、遗漏、错误、未知”，不把未评阅关系计为正确。

第二阶段才做配对生成：同一模型、遮蔽输入、授权区域、token/时间预算下比较 `(a) 原始任务`、`(b) 同量源码上下文`、`(c) 切片事实包`。这能区分“多给源码”与“关系结构”的贡献。每例先要求候选在未变异程序上可执行；随后分别报告交互/状态/异常变异检出、合法替代误拒绝、编译/配置/运行失败和成本。开发用例与最终留出项目分开，不能在修复切片规则后把同一案例当泛化证据。

技术选择门槛应来自第一阶段失败簇：若主因是跨过程参数/返回、heap 别名或异常控制，再以这些簇为代表样本比较成熟 Java 分析后端；若主因是 Python 动态绑定或 Mock 框架替换点，则先研究语言特定事实与不确定性表达。只有在关系已可靠、仍难判定具体路径是否可行时，局部求解才是有依据的下一步。

先以全量盘点分层选择6–12例做入口与边界探针，覆盖值、条件、状态、异常、void事件和
遮蔽后缺普通oracle的任务；探针解决执行机制，不作为总体效果估计。冻结可运行配置后，
必须保留全部165例的成功、部分、未知、失败与超时分母，分别记录：

- 准则是否来自可见明确断言，还是候选事件/预期数据推断；
- test→MUT→可见helper→未知依赖边界在哪一段断裂；
- data/control/heap/exception各类证据的来源、范围和缺口；
- 是否需要合成入口或库摘要，以及这些额外假设；
- 人工复核发现的错边/漏边、用时、峰值内存和证据体量。

未知边界必须在分析阶段保持未知：若工具消费了Mock的隐藏实现或占位throw作为真实行为，
之后再从输出图中过滤该节点，不能补救已发生的错误传播。

## 本仓全量观察

本次对全部 Java 59 例和 Python 106 例重新核验冻结输入，仅分析完整 masked test 文件。
Python 使用 AST，Java 使用去掉注释/字面量后的词法模式；均不做绑定或可达性判定。
统计单位是案例，类别可重叠，不能相加得到总量。没有测量 test→MUT 定位成功率或任何切片成功率。

| 观察线索 | Java（59） | Python（106） |
| --- | ---: | ---: |
| 规则识别到值/状态断言语法 | 58 | 99 |
| 规则识别到异常观察语法 | 4 | 37 |
| 规则识别到固定交互观察语法 | 2 | 0 |
| 未识别到上述三类观察 | 0 | 6 |
| 参数化语法线索 | 0 | 106 |
| 动态加载/反射相关调用名线索 | 11 | 67 |

这些是整份目标测试文件的语法线索，辅助函数也可能被计入；不是精确目标方法内的语义分类。
别名、自定义断言和测试框架扩展可能漏识别。Java 的 `invoke` 等名称匹配也可能误报。
Python 的0个固定交互观察尤其不能解释为“不需要交互验证”：待生成 verification 区域已经遮蔽。

对6个未匹配案例，额外人工检查了可见测试函数主体：

| Case ID | 仍可见的目标线索 |
| --- | --- |
| `click_423` | pager调用、`expected_text`参数；验证区为空 |
| `crawl4ai_161` | 安装调用、预期命令/日志次数和错误情形；验证区为空 |
| `crawl4ai_164` | 写缓存调用、`expected_payload`及异常情形；验证区为空 |
| `flask_313` | banner调用、`expected_calls`与reloader分支；验证区为空 |
| `hummingbot_177` | 展示池信息调用、`expected_notifications`；验证区为空 |
| `langflow_188` | migration配置调用、URL和阈值预期参数；验证区为空 |

这里没有恢复隐藏 verification。观察是：这6例没有找到保留的常规可执行断言，但仍有可见
调用和预期数据。推论是：**只从现存 assert 做后向切片，会漏掉一类核心任务。**
应分别评估“已有明确 oracle 的切片”与“从调用事件/预期数据提出候选观察目标”；后者必须
标明推断及未确定的绑定，不能从 `expected_*` 变量名自动推出必须验证的次数或参数。

另一个直接约束是 Python 的动态加载结构：AST加载器、函数全局字典注入与参数化情形在
目标测试中存在。传统 import/call graph 能否连接测试到产品函数，需要单独实测，不能把
加载包装函数的调用边当作真实 MUT 连接。当前未执行待测程序，也未读取隐藏答案。

复现与证据：

- [逐例观察与行号、源码摘要](../../out/mockbench-study/backward-slicing-target-survey-20261109.json)
- [盘点脚本](survey_targets.py)；[计数回归](test_survey_targets.py)，3项通过，Ruff通过。

```bash
PYTHONPATH=src .venv/bin/python experiments/backward-slicing/survey_targets.py \
  --inventory out/mockbench-study/intake-20261109-inventory.json \
  --output /tmp/mockbench-target-survey.json
```

三个历史开发案例按Case ID标记；当前flask快照与历史实验版本不同，因此该标记不意味着
历史运行可以直接作为当前输入的基线。全量静态盘点不会消除开发集与留出集的区别。

## 状态

本文是文献与验证设计调研。尚未在 165 例上运行后向切片、未选择 WALA/Joern/其他后端、未执行模型生成或目标测试，也未报告 Mock 质量提升。

上述状态对应调研交付时点。后续已实现目标准则驱动的源码候选图批处理，入口与精度边界
见[全量候选切片入口](README.md#全量候选切片入口2026-11-09)；候选图不等于成熟SDG或
经过语义验证的切片，也不改变本文对Mock质量需要独立实验的要求。
