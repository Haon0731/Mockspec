> 历史研究任务包：以下“本轮”指原 Scenario Contract Research 阶段，不是当前待执行任务。
> 阶段结论见 [研究决策](scenario-research/decision.md)；后续进展和当前指南见 [文档目录](README.md)。

# Mock Spec：Scenario Agent 与 Scenario Contract 基础材料及执行任务包

**用途：** 将本文整体交给 Codex，完成一次“方法设计收敛＋开源能力复用 PoC”，而不是继续扩建 Agent 框架。

**整理日期：** 2026-10-22。
**项目：** `/home/haon/projects/Mockspec`；仓库：`Haon0731/Mockspec`。
**本材料核查基线：** `0cbfb3d — refactor: centralize run evidence finalization`。这不是要求重置本地到该提交；如果当前 HEAD 更新，先识别差异，保留用户已有工作。
**本轮范围：** 来源核对、设计 RFC、两份人工建模的真实案例规格、有限约束的离线 PoC、反例与限制报告。**不修改生产 Agent/现有 Schema/历史实验，不运行新的付费模型实验。**

> 核心意图：最大程度复用已有规格推断思想、Mock 行为表示和求解器接口，让 Scenario Agent 从允许看到的测试与代码中推导“有条件、有证据、保留替代解释”的依赖行为约束。不要重造程序分析引擎、表达式语言、求解器、Mock 运行时或测试运行器。

---

## 0. 先读这一页：我们要什么，不要什么

### 我们要的结果

Scenario Agent 最终需要回答：

> 在当前可见测试输入与断言下，哪些依赖行为能够构造相容的测试场景？每条行为要求在什么条件下成立？其依据来自哪里？还有什么替代解释或未确定的外部语义？

其输出应是一组**可检查的、条件化的 Mock 行为方案**，不是恢复出的“参考 Mock 答案”，也不是更长的自然语言代码分析报告。

后续，这份输出将服务于两个环节：

- Mock Generation：选择一种允许的场景，生成 Mock Objects、Stubs、Mock Assertions。
- Behavioral Feedback：将候选配置与实际观察对照，发现响应、路径、交互检查方面的偏差。

**本轮只设计和验证这一中间表示及有限推断能力，不实现上述两个下游环节。**

### 不要误解成以下任务

1. 不是“把 JSON 改得更复杂”，也不是“规定 Agent 必须调用更多工具”。
2. 不是“有了 SMT，就能证明 LLM 理解了源码”。错误的源码到公式映射，也可以得到 SAT/UNSAT。
3. 不是“选一条能通过测试的路径，就将它写为唯一必要路径”。
4. 不是“手工写出正确约束后求解成功，就证明自动场景推导有效”。
5. 不是“必须采用 cvc5”。它是优先实测的现成溯因组件候选；采用与否由本轮证据决定。
6. 不是重新开展 Joern、Serena、CodeQL 等通用工具选型。已有决策保持；没有新缺口证据就不增加组件。

---

## 1. 已有项目状态与本轮出发点

### 1.1 必须保留的现有能力

项目已经具备统一 Java/Python 输入、审核 Adapter、SourceStore、Scenario Contract、验证器、V0.1 单次推理、V0.2 自主工具调用，以及 Python AST/Java Spoon 工具。只接入了 Java JarFiles 与 Python `flask_308` 两个审核案例；不能把语言支持等同于全数据集接入。

先在本地阅读下列实际文件。以本地存在的接口为准，不复制聊天中的猜测版本：

```text
AGENTS.md
GLOSSARY.md、docs/adr/（存在时阅读）
src/mock_spec/schemas.py
src/mock_spec/validation.py
src/mock_spec/scenario.py
src/mock_spec/inference.py
src/mock_spec/inference_v02.py
src/mock_spec/program_tools.py
src/mock_spec/python_analysis.py
src/mock_spec/java_analysis.py
src/mock_spec/prompts/scenario_v0.1.txt
src/mock_spec/prompts/scenario_v0.2.txt
docs/step5b/report.md
docs/step6/spec.md
docs/step6-live/report.md
docs/step6-live/java-review.md
docs/step6-live/python-review.md
```

当前契约 Schema 与 Agent 版本是两条不同的版本线。V0.2 Agent 仍使用 `mock-spec.scenario/v0.1`，不要因名称相同而混淆。新结构暂称 **Contract Research Draft**；本轮不升级生产 Schema。

### 1.2 已观测问题，不是待证明的普遍结论

基线报告记录了两案例、两版本的真实运行：V0.2 两例都自主调用了工具，但两份候选均因 required claim 缺少适用条件而未通过现有验证。报告还记录了 Python 候选的局部替代路径补充，以及 Java 候选过度排除显式镜像、自定义 entrypoint 等解释的情况。[P1]

这是两个开发案例的观察，不能估计总体成功率或证明工具导致退化。已有语义审查是 AI 辅助审查，不能改写为研究者已完成的人工审核。

**本轮要解决的设计缺口：**

- 自然语言 `required` 混合了测试要求、路径内必要条件与实现选择。
- 真实源码引用存在，并不保证它支持相关结论。
- 工具事实与推理假设、缺失语义、检查结果没有充分分离。
- 目前行为描述未形成足够清晰的下游生成接口。

### 1.3 数据和权限边界

允许推断的数据仍只有已审核 UnifiedTask：MUT、Masked Test、允许接口、允许上下文。开发者可读取现有实现和历史失败作回归设计，但不得将参考 Mock、历史评分、旧候选答案或人工分析结论注入待评测 Agent。

原始数据及 Stub-Tester 只读；不修改生产源码、原测试断言、masking、数据准入记录、旧评测口径。接口遮蔽产生的占位方法体不是原依赖语义，尤其不能把 `implementation-hidden` 对应的抛异常占位体当成真实行为公理。

---

## 2. 基础材料：精读什么、采用什么、不要照搬什么

下面是原始资料入口，不是“这些系统已经与 Mock Spec 集成”的声明。优先级 P0 表示本轮应读到相关段落；P1 表示按需要查阅。阅读结论要保留出处、原假设与本项目假设之间的差异。

### S1 / P0 — Maximal Specification Synthesis，POPL 2016

**作者：** Aws Albarghouthi、Isil Dillig、Arie Gurfinkel。
**入口：** [会议官方页面](https://popl16.sigplan.org/details/POPL-2016-papers/9/Maximal-Specification-Synthesis)；[作者论文](https://pages.cs.wisc.edu/~aws/papers/popl16.pdf)；DOI `10.1145/2837614.2837628`。

**已核实内容：** 研究根据调用方验证目标合成未知过程的规格，采用反例引导及 multi-abduction，并追求不过度限制实现的规格。

**采用：** 将问题表述为“调用方测试目标驱动的依赖行为推断”；分别检查目标是否得到支持和规格是否过强。

**不照搬：** 不将其验证基础设施移植成新的 Java/Python 验证器，也不把单次求得的约束称为最弱或完整规格。

**Codex 必须回答：** 本项目推导的是一个测试内的 Mock 配置约束，还是对所有输入成立的库契约？默认采用前者；任何扩大范围都需要额外依据。

### S2 / P0 — Data-Driven Abductive Inference of Library Specifications，OOPSLA 2021（Elrond）

**入口：** [论文 HTML](https://arxiv.org/html/2108.04783v2)；[会议官方页面](https://2021.splashcon.org/details/splash-2021-oopsla/20/Data-Driven-Abductive-Inference-of-Library-Specifications)；[正式工件](https://zenodo.org/records/5130646)；DOI `10.1145/3485493`。

**重点阅读：** Data-Driven Abduction、Identifying Spurious Counterexamples、Weakening、Implementation。

**已核实内容：** 根据库行为样本提出规格，用 SMT 反例细化，并尝试削弱过度限制；实现面向 OCaml 数据结构程序，后端使用 Z3。

**采用：** 区分“太弱，不能支持目标”和“太强，排除其他可能行为”；为反例保留其依赖的模型假设。

**不照搬：** 我们不能默认执行不可见库来采样；无法使用其采样前提时，不得声称继承了它的保证。

**工件提醒：** 正式 Zenodo 记录提供较大的镜像；先读说明，避免下载整个环境。此前提及的其他仓库，未建立论文工件对应关系前，不当作 Elrond 官方实现。

### S3 / P0 — StubCoder: Automated Generation and Repair of Stub Code for Mock Objects

**入口：** [论文 HTML](https://arxiv.org/html/2307.14733v1)；[正式工件](https://zenodo.org/records/7816758)；[出版页面](https://doi.org/10.1145/3617171)。论文 2023 年已接受/上线，卷期可显示为 TOSEM 33(1), 2024；引用时核对出版元数据。

**重点阅读：** §2.1–2.2 的测试表示、§3.2 的 Stub 表示、§3.1 的反馈目标。

**已核实内容：** Stub call 以接收对象、方法、参数 matcher 匹配调用，并指定 Return/Throw 反应；多条匹配规则可以组成顺序反应。该工作利用保留的测试 oracle 合成 Stub。

**采用：** 复用“调用匹配 → 反应”作为行为原语，不重新发明返回、异常和响应序列的底层概念。

**不照搬：** 本项目的 Mock Assertions 也被遮蔽，信息条件不同；不移植进化搜索流水线，也不把测试通过当成行为忠实性的充分证据。

**工件提醒：** 已核实公开记录包含 JAR 和数据；这不等于已经核实有可导入的源码库或稳定 API。优先复用已有本地接口与表示，不重下基准。

### S4 / P0 — cvc5 原生 abduction 与 SMT 接口

**入口：** [Python Solver API](https://cvc5.github.io/docs/latest/api/python/base/solver.html)；[C++ Solver API 的完整语义说明](https://cvc5.github.io/docs/latest/api/cpp/classes/solver.html)；[官方仓库](https://github.com/cvc5/cvc5)；[SMT-LIB 标准入口](https://smt-lib.org/language.shtml)。

**重点接口：** `getAbduct`、`getAbductNext`、`checkSat`、grammar/term 构造、结果和错误处理；启用选项及返回类型以安装版本为准。

**已核实内容：** `getAbduct` 可以在背景约束 A 下寻找 C，使 A∧C 可满足、A∧C∧¬B 不可满足。grammar 可限制候选形状。接口标注为实验性；下一解仅保证语法不同。

**采用：** 优先运行现成 API，验证有限条件的推断和检查；可用同一求解器复核候选，不先引入第二套公式体系。

**不照搬：** 不把 cvc5 当成 Java/Python 解析器，不假定返回最弱约束、所有场景或真实程序证明。没有找到 abduct 不等于真实场景不存在。

**版本要求：** 文档 `latest` 会变。PoC 固定可安装版本、对应文档/源代码提交、API 兼容性、许可和环境；不根据本材料猜测具体调用签名。

### S5 / P0 — Mock Assertions 的实证基础与交互表达

**论文：** Understanding and Characterizing Mock Assertions in Unit Tests，FSE 2025。
**入口：** [论文](https://arxiv.org/abs/2503.19284)；[会议页面](https://conf.researchr.org/details/fse-2025/fse-2025-research-papers/59/Understanding-and-Characterizing-Mock-Assertions-in-Unit-Tests)；DOI `10.1145/3715741`。

**已核实内容：** 研究 Mock Assertions 对外部资源、路径经过、侧作用等行为的验证作用；它是经验依据，不是恢复每个隐藏断言的完备规则。

**表达参考：** [Spock Interaction Based Testing（固定文档版本）](https://spockframework.org/spock/docs/2.4-M4/interaction_based_testing.html)。阅读对象、方法、参数、次数、顺序、Stub/Mock 区分。

**采用：** 独立保存“执行预期”和“建议验证的行为”；验证建议必须解释其保护的行为及可见依据。

**不照搬：** 不因为源码有某次调用就要求 verify；不将 Spock/Groovy 引入当前 JUnit/Mockito 与 pytest 协议。

### P1 — 补充材料，只在对应问题出现时阅读

| 材料与原始入口 | 本项目用途 | 复用限制 |
|---|---|---|
| [SymPrompt / Code-Aware Prompting，FSE 2024](https://arxiv.org/html/2402.00097v2) | §3 的近似路径条件、相关上下文、分路径推理 | 近似条件不是路径可达性证明；该版本论文称原型当时为 proprietary。不要拿同名 GitHub 仓库冒充本论文实现；当前开源状态需另核实 |
| [LLM-Sym / Python Symbolic Execution with LLM-powered Code Generation](https://arxiv.org/abs/2409.09271) | 类型推断、模板检索、分步生成约束表达 | Python→Z3 翻译是其研究核心，不是任意 Java/Python 项目的现成通用能力；本材料按预印本入口列出，不假定录用状态 |
| [Mock-object Generation with Behavior，ASE 2006](https://www.microsoft.com/en-us/research/publication/mock-object-generation-with-behavior/) | 了解通过符号执行生成 Mock 行为的先例 | .NET 原型；避免将“按程序约束生成 Mock 响应”单独声称为新颖点 |
| [MockMill / Improving LLM-Driven Test Generation by Learning from Mocking Information](https://arxiv.org/abs/2604.19315) | 参考 Mock 信息如何指导下游测试生成 | 它读取已有开发者 Mock；本项目不能读取隐藏 Mock，信息方向不同 |
| [OpenJML：Multiple Method Behaviors](https://www.openjml.org/tutorial/MultipleBehaviors) | 理解 guard、正常/异常行为与条件化义务 | 多个适用 specification cases 不是任选其一；不引入完整 JML 验证环境 |
| [Python 3.10 unittest.mock](https://docs.python.org/3.10/library/unittest.mock.html)、[Mockito Javadoc 入口](https://javadoc.io/doc/org.mockito/mockito-core) | 核对返回、异常、side effect、序列和交互验证的已有能力 | 用案例实际冻结版本，不因当前文档版本而升级实验依赖 |

**阅读停止规则：** P0 足以开始设计；不要为了“文献完整”无限扩展调研。P1 只读与明确缺口有关的段落。论文、接口、工件分别标注“文档核实／源码核实／本地实测”，不能混为一谈。

---

## 3. 已收敛的设计原则

以下是本项目设计要求，不是宣称某篇论文已经实现了同一方案。

### 3.1 三种命题必须分开

1. **Oracle requirement：** 可见测试明确要求的结果。例如最终 base image 等于某个字符串。
2. **Scenario-local obligation：** 选择某场景并满足前提后，依赖响应或交互必须满足的关系。
3. **Implementation choice：** 生成器选择的具体实现，如从 `version <= 8` 中选取 `version = 8`。

不得把第 3 类写成第 1 类；也不得把第 2 类变成所有合法场景的全局要求。

### 3.2 场景、证据强度、检查结果是不同维度

不要让一个 `required/candidate/unknown` 同时表示全部内容。

最小要求是能分别回答：

- 作用域：哪个测试参数行、场景、调用位置和前置条件？
- 逻辑角色：测试要求、场景内约束，还是具体实现选择？
- 依据：可见断言、可见程序结构、受支持语义翻译，还是模型假设？
- 检查：未运行、SAT、UNSAT、solver unknown、超时、不支持、失败？
- 完整性：只覆盖局部片段、有限路径，还是有依据的更强覆盖？

字段命名可优化，但这五个问题不能用一个“confidence=0.9”代替。

### 3.3 源码事实与未知语义必须分离

- Python AST/Spoon 的词法嵌套、局部声明引用不等于 def-use、控制流可达性或异常传播证明。
- 方法签名、类型注解、名称、注释不是完整运行时规格。`NoReturn` 也不能代替隐藏实现的行为证据。
- 隐藏方法占位体不能成为真实外部语义。
- 相同方法的两次调用不能未经说明合并为相同返回值。重复 getter、顺序响应、可变状态都可能影响推断。
- `None/null`、正常返回、抛异常必须有清楚的类型和出口区分；不能把 Java 包装类型直接当成不可空的数学整数。
- 原测试中的结果断言不能因为候选路径变化而被改写或缩小适用范围。

### 3.4 程序补全与外部假设不能偷换

允许控制的 Mock 返回、异常、对象属性可以成为候选变量；MUT 输出、最终断言值、不允许模拟的库行为不能当成任意可配置变量。

未知外部行为可以作为未解释关系保留在模型中，但不能将“它产生目标结果”加入 H 后宣称已经推导出可信 Mock 配置。必要时只能给出**依赖该外部假设的条件性方案**。

对一次调用的正常返回与抛异常应互斥；调用的响应还应受“该调用确实发生”约束。没有执行的调用不能通过随意设置返回值来满足测试。

### 3.5 检查状态由宿主和工具填写

Agent 提议候选规则、解释和证据 ID；宿主解析器绑定源码、求解器执行检查，宿主写入实际结果。不要让 LLM 自报 `verified=true`、虚构 SAT/UNSAT 或伪造执行记录。

条件为空时，必须区分“明确无额外 guard”“无法确定”“漏填”。禁止将旧候选的 `null` 静默补为 `true`。

---

## 4. 推导机制：复用优先的最小闭环

### 4.1 期望的职责划分

```text
审核 UnifiedTask
    ↓
固定测试输入、参数行和 Oracle
    ↓
解析器提供源码事实、依赖调用及可见绑定
    ↓
Agent 提议相关场景和候选依赖约束
    ↓
有限语义适配：已支持表达式 → 现成 solver terms
    ↓
原生 abduction / 一致性 / 反例检查
    ↓
Agent 解释结果、保留替代场景与未知项
    ↓
宿主验证并封装 Scenario Contract
```

现有 SDK 管调度，AST/Spoon 提供结构，cvc5 候选负责有限约束。只补薄适配层，不开发新的通用符号执行器。

本轮允许人工建立小范围公式，用来验证表示和检查机制；必须标注 `authoring_mode=handcrafted_from_visible_input`。**自动源码到公式、自动 Agent 推断的覆盖程度需要另外报告；本轮没有实现的不能算完成。**

### 4.2 检查含义

记 T 为已建模的程序关系与固定输入，G 为候选场景条件，H 为候选依赖行为，O 为指定可见目标。

基本检查：

```text
SAT(T ∧ G ∧ H)                 —— 候选在模型内不矛盾
UNSAT(T ∧ G ∧ H ∧ ¬O)          —— 在当前模型和前提下足以保证该目标
SAT(T ∧ O ∧ ¬C)               —— 模型内存在反例，C 不是全局必要条件
```

这些公式表达的是检查思路，不能省略执行出口与目标可达性。PoC 必须说明 T 如何表示调用发生、正常/异常终止、断言到达；对于有路径选择的模型，要排除“跳过目标检查也算成功”的空洞结论。最小要求：额外检查目标/预期异常观察可达，并将其纳入成功目标的定义。

重要边界：

- 在某个 G 中检查 C，只能支持该 G 内的结论，不能支持所有场景。
- 不能在 T 中先加入 O，再声称 H 推出了 O。
- SMT 中的模型反例可能因抽象而是假反例，不能直接称为可执行的测试输入。
- 未覆盖的路径或缺失库语义会限制结论。局部公式 UNSAT 不等于整段真实程序已证明。
- 不完整模型中的 SAT 只表示未被当前约束排除；不是隐藏依赖实现真实可行的证明。

### 4.3 cvc5 PoC 要做什么

优先直接使用其公开 API 完成：有限布尔/数值约束构造、受 grammar 限制的 `getAbduct`、独立可满足性检查及可用时的下一候选。

**grammar/符号控制是必要的：** H 只能使用审核的可控依赖响应符号与允许的固定输入。禁止将整个 oracle、`test_passes`、任意真实库语义开关或最终返回值直接作为可随意设定的 abduct。

每个候选在独立的检查上下文中复核，避免 abduction 调用的背景状态意外污染后续查询。候选去重与比较可用求解器等价/蕴含查询，不自己编写 SAT 搜索。

若 cvc5 原生 abduction 在选定版本不支持某个片段、超时或不可用，保存实际结果。可以用同一 solver 检查人工候选验证表示；必须命名为 **candidate checking**，不能称作自动 abduction。不要因此自动引入 Z3＋CEL＋自制转换器三套体系。

---

## 5. 输出设计：薄封装，不造新的通用 DSL

### 5.1 建议的最小内容

| 内容 | 必须说明的语义 |
|---|---|
| identity / coverage | task、input hash、设计版本、覆盖片段、人工或自动产生 |
| oracles | 可见断言、参数行、观察表达式；与依赖方案解耦 |
| symbols / bindings | 类型、对象/方法/调用位置、发生序号或明确未定、哪些可控 |
| scenarios / alternatives | guard、组合/替代关系、是否已排除、可行性未知项 |
| response rules | StubCoder 式调用匹配与 Return/Throw/序列；值或关系约束 |
| runtime expectations | 场景内的调用、参数、次数/顺序，及其成立条件 |
| verification candidates | 候选断言与理由；不把预期调用自动升级为义务 |
| evidence / checks / unknowns | 来源、假设、宿主检查结果、工具限制与覆盖边界 |

不要机械照搬这个字段列表造十几层模型。Codex 可以合并字段，前提是关键语义不丢失、两例能够表达、消费者能够清楚解释。

### 5.2 表达式和持久化

优先采用一个主约束表示：solver term 的受支持构造，加标准 SMT-LIB 可读导出/导入。JSON 仅存场景、绑定、证据和检查元数据。

不手写 SMT-LIB parser，不实现新的 AND/OR 运算语言，不设计 CEL↔SMT 双向翻译。使用现成解析/序列化能力，检查符号表、允许的命令和资源限额；不执行模型提交的任意 Python/Scala/Shell。

不是所有信息都需要 SMT 化。复杂文本、暂不支持的关系保留原始表达式与 `unsupported/not_formalized`，不要强行编码成无依据布尔值。

### 5.3 建议的单条规则片段（仅设计示意）

下面不是仓库实际输出，也不是已求解的结论；省略的符号和证据必须在最终完整样例中定义。

```json
{
  "id": "R_version_for_default_image",
  "scope": {
    "scenario_id": "default_java8",
    "guard_ref": "G_no_explicit_from",
    "applies_when": "the bound call site is reached"
  },
  "call_binding": "C_getJavaVersion_1",
  "reaction": {
    "kind": "return",
    "value_symbol": "version_value",
    "constraints": ["F_version_nonnull", "F_version_le_8"]
  },
  "logical_role": "obligation_within_selected_scenario",
  "oracle_refs": ["O_base_image"],
  "evidence_refs": ["E_default_branch", "E_base_assertion"],
  "check_refs": [],
  "whole_test_verified": false
}
```

这条规则只解释所选默认路径中的版本响应；不是所有场景的要求，也不单独保证整个测试通过。`version=8` 可以作为另存的实现见证，但不能覆盖原 `version<=8` 约束。

### 5.4 对下游的消费说明必须写出来

虽然本轮不生成 Mock，但对两个样例都需提供简短的消费说明：

1. 生成器可以选择哪个场景，哪些选择互斥或仍未知？
2. 选定之后，哪个对象/方法需要什么响应，哪些具体值仍可选择？
3. 哪些调用是运行预期，哪些只是验证建议？
4. 哪些 unknown 需要阻止自动承诺，哪些只是非关键的未覆盖部分？

没有有效且自洽的候选时，要输出“当前无可消费方案及原因”，而不是制造一个必然通过的规格。下游是否允许消费“有明确假设的 partial 方案”须在 RFC 中定义，不能等同于已验证方案。

---

## 6. 两个真实案例的建模任务

以下方向来自当前可见源码与已记录问题，不是隐藏标准答案。最终样例逐条引用审核输入，不能将本节文字作为代码证据。

### 6.1 Java JarFiles

**测试目标：** 保留可见的最终镜像、entrypoint、layer 及其余断言。可以只形式化局部目标，但必须显式列出未形式化部分，不能把局部检查标成全测试通过。

**必须表示的两组选择：**

- 镜像：默认分支；显式 from 分支。
- Entrypoint：空 custom list 后计算；非空 custom list 直接使用。

**必须保留的结论边界：**

- 默认路径中，版本返回非空且不大于 8 是选择 Java 8 镜像分支的条件；不代表必须恰好返回 8。
- 显式 from 路径不能仅因调用了另一个 builder 就被排除；其外部语义不可见时，保留未知，不凭想象证明可行。
- 非空 custom list 可以等于断言要求的列表；“自定义”不等于“值不同”。
- 两组选择可以表达组合，但未经支持的组合只列候选，不宣称已经验证全部笛卡尔积。
- `getFrom()` 的多次调用应按调用点区分；假定稳定返回属于明确实现选择或有依据的模型假设，不能默认偷加。
- `getJavaVersion()` 的包装类型空值和异常出口要说明建模方式。
- builder 默认值、库实现和被遮蔽方法体保留 unknown；工具截断和未解析状态不能消失。

**最小演示目标：** 某条默认路径内可以推导局部约束；系统能阻止“最终镜像断言 ⇒ getFrom 必为空”的无依据升级。对未知显式路径，接受“无法证明必要”，不强求一个真实可执行反例。

### 6.2 Python flask_308

**测试目标：** 保留全部可见参数行、`pytest.raises(Exception)` 和对应消息子串。不得新增精确异常类型或凭 slot 名猜测具体 patch/verify 内容。

**必须区分的路径：**

- 上下文查询正常返回 `None`：到达 `_wz_abort`。
- 返回非 `None` 的上下文且 aborter 抛出：后续 fallback 不执行。
- 返回非 `None` 的上下文且 aborter 正常返回：继续到达 `_wz_abort`。

**边界：**

- 条件是 `is not None`，不能偷换成普通 truthiness。
- 以上路径以正常 lookup、相关属性可访问等明确前提为界；其他异常/动态行为未覆盖时必须记录，不能宣称路径枚举完备。
- 没有 else 只提供顺序结构；不能直接推出 fallback 一定被执行。
- 不能仅凭异常消息确定异常来源；fallback 仍可能被配置成相同消息。是否符合开发者隐藏意图未知。
- 参数透传要保留位置参数和关键字参数；不能把所有参数行合并后丢掉目标关联。

**最小演示目标：** 在明确前提和有限模型下检查 throw/return 对 fallback 到达性的影响，保留两种应用上下文候选；不强行恢复隐藏响应与验证。

---

## 7. Codex 本轮执行顺序

### A. 先写一页问题与复用决策，不停留在论文摘要

阅读本材料、P0 来源与本地基线。建立简短 source-to-design 表：原方案的前提、可直接调用的能力、仅借鉴的思想、本项目差异、是否实测。

明确已有代码可以复用什么；不要重写 Agent、SourceStore、两语言解析器或既有测试运行器。遵循仓库 AGENTS/术语/任务追踪规则，但不要因本任务擅自将未获批准的设计发布为已完成 Issue。

### B. 建立两份人工规格样例和最小公式

只通过审核 Adapter 读取允许数据，先手工建模 Java/Python 相关片段。每个符号和公式关联原始 source_id、位置、语义假设、支持范围。

每份样例均标注人工建模；完整 JSON 能由草案验证器读取，所有引用可解析。不得把历史模型输出修成有效后覆盖原文件。

### C. 实测一个主求解器方案

在隔离的新环境中固定 cvc5 版本，先测试 API、grammar 限制、公式 round-trip 与基本 SAT/UNSAT；再运行两例的有限模型。

保存实际调用、输入公式、输出、超时和工具身份。当前没有模型请求预算授权，本轮不调用远端 LLM。

如果原生 abduction 不可用，继续完成与它无关的契约设计、候选检查和负例验证；报告具体阻塞，不擅自实现一套替代求解器。必要时给出下一步候选，而非自动接入全部候选组件。

### D. 编写反例和负面测试

至少覆盖第 8 节的关键验收项。测试以观测行为为准，不写“因为 case_id 是 flask_308 所以返回预设结论”的实现。

真实案例 fixtures 可以包含具体项目符号；通用适配逻辑不能基于 Case ID 或目标方法名硬编码答案。

### E. 交付 RFC、PoC 和明确决策后停止

报告哪些能力是借鉴、调用已有组件、薄适配或尚未实现。推荐一个主方案，说明是否值得进入下一轮生产集成。

**不能把交付收缩为“建议以后做 PoC”；在环境可用且本轮边界内，应实际运行小型离线 PoC。也不能从 PoC 一步跳到改正式 Agent。**

---

## 8. 验收矩阵：什么结果算完成

### 8.1 核心验收

| 编号 | 必须演示 | 预期边界 |
|---|---|---|
| A1 | 原测试目标与候选配置分开保存 | 路径选择不改变 oracle |
| A2 | 两语言真实可见输入各有一份完整人工样例 | 来源明确；不是模型生成成效 |
| A3 | 候选不矛盾、局部充分性检查有真实结果 | 若仅检查局部，就只声称局部 |
| A4 | Java 默认路径的版本约束与显式 from 备选并存 | 不将默认选择升级为全局必要 |
| A5 | Java 自定义列表可等于期望 entrypoint | 不以来源不同推断结果不同 |
| A6 | Python 区分 aborter 抛出与正常返回后的 fallback | 不将词法无 guard 解释为无条件必达 |
| A7 | unknown / unsupported / solver unknown / timeout / not_run 分开 | 缺证据不等于 false；失败不等于无解 |
| A8 | 源码、场景、符号、公式、检查引用可追溯 | 不靠 LLM 自报已验证 |
| A9 | 检查 grammar/符号边界 | 不能用直接设定最终 oracle 的方式作弊 |
| A10 | 输出可解释如何指导 Stub 和交互验证 | 不生成最终 Mock，不修改测试 |

### 8.2 必需的负面测试

- 矛盾 H：不能利用背景不可满足使任意目标“被证明”。
- 漏掉/跳过目标观察：不能把没有走到断言或预期异常视作成功。
- 无条件/条件未知/漏填：明确区别；缺条件不自动补 true。
- 不支持的动态行为或未解析调用：保留 partial/unknown，不硬编码语义。
- 条件化结论冒充全局必要：能够拒绝或明确降级为未证明。
- 参数行漏失：报告哪些目标缺少对应场景，而不是自动声称完整。
- 引用位置正确但结论没有对应逻辑支撑：不能仅凭引文合法记为语义检查通过。
- 两个语法不同但逻辑等价的候选：不得计为两个已证实不同的测试场景；未作等价检查则保留该限制。
- 普通运行交互未经论证升级为 verify obligation：拒绝或保留为候选。
- 未知外部库行为被设成有利布尔值：结果必须显示依赖假设，不能写“由源码证明”。

调用次数与响应序列可用一个独立小型合成例检查表示能力，但必须单列 `synthetic_representation_test`，不当作第三个真实 Benchmark 结果；不要为此开发状态机运行时。

### 8.3 完成不等于效果成立

可接受的交付包括：PoC 得出不适用结论、abduction 暂不能完成、某些真实片段只能 partial 表示。前提是已完成可独立完成的设计、实际尝试和证据记录，明确指出缺口。

以下不能作为本轮完成依据：新增多少字段、多少测试、下载多少框架、JSON 能保存、求解一个手写公式得到 SAT、人工规格与参考 Mock 一致。

真正需要清楚回答的是：**哪些错误被现有技术可靠挡住了，哪些仍取决于源码语义翻译或 LLM 推理？**

---

## 9. 建议交付结构与最终报告模板

在不冲突于仓库现有布局的前提下，只新增类似目录：

```text
docs/contract-foundation/
  sources.md                  # 经过核对的来源与复用矩阵
  rfc.md                      # 语义、边界、Agent 协议与输出设计
  decision.md                 # Go / Limited-Go / No-Go 及证据
  reproduce.md                # 独立环境、命令、版本、许可来源
experiments/contract-foundation/
  schema_draft.py             # 仅本轮草案，不改生产 ScenarioContract
  examples/java.contract.json
  examples/python.contract.json
  models/                     # 标准公式、符号与源码绑定
  poc.py                      # 现成 API 的薄封装
  tests/
  results/                    # 小型结果与诊断，不放整份数据集或凭据
```

不要求机械创建每个文件；可以合并，必须保证内容可发现、可运行、可复核。公式用现成工具生成与读取，不另写 DSL。

最终报告按下面顺序输出：

1. **一句话决策：** 推荐哪条复用路线；哪些条件尚未满足。
2. **来源与实现：** 每项能力对应论文/官方 API/实际调用/适配代码位置。
3. **Agent 推导协议：** 输入、允许动作、约束检查、unknown 和终止条件。
4. **实际输出：** 两份完整人工契约及简短可读视图，明确作者模式。
5. **真实 PoC：** abduction 与 checking 分别跑了什么，结果、耗时及失败。
6. **错误拦截：** 哪些负面测试能被阻止；哪些只是被标为未证明。
7. **未完成部分：** 自动源码翻译、真实 Agent 推导、运行时验证和下游效果分别列明。
8. **下一轮最小改动：** 最多一组必要的生产集成任务；不要自动执行。

---

## 10. 本轮不可越过的工程边界

- 不修改 `inference.py`、`inference_v02.py`、两版 Prompt、生产 Schema/Validator、已冻结报告与结果。
- 不重写已有数据 Adapter，不扩展全量 59/106 案例，不修改准入名单。
- 不读取参考 Mock、隐藏测试、评分或完整旧源码树来“完善”推断。
- 不启动真实 LLM 请求，不沿用上一轮 US$5 预算作为本轮授权。
- 不恢复 Joern 修复线，不引入多 Agent/新 SDK，不写 CFG/DDG/通用符号执行器。
- 不将手工例子、合成数据、抽象模型或 AI 审查伪装成真实自动推导、真实执行或人工签字。
- 不为了让样例成立削弱原断言、隐去不支持特性或选择性删除失败。
- 不把文档 latest、开发分支、论文工件或二进制库视为可互换版本；实际版本及复用许可必须记录。
- 可下载必要的小型依赖到独立环境；不要默认拉取大型论文镜像、完整数据或改系统 JDK/旧 venv。
- API、安装、许可或输入无法核实时，完成独立可做部分并准确说明阻塞；不伪造结果。

---

## 11. 最终研究意图与停止条件

本轮不是为了“证明 cvc5 比 LLM 好”，也不是为了发明一个新的 JSON 名称。

我们要建立最小可信接口：

```text
已有测试与代码
    → 有出处的程序事实
    → 明确作用域的依赖行为候选
    → 现成技术能检查的部分 + 不能检查的部分
    → 可供生成器消费、不会偷换测试意图的行为方案
```

前人技术负责表达、求解和已有 Mock 机制；Mock Spec 只补充测试目标映射、依赖行为绑定、候选场景组织与证据解释。

完成 RFC、两份样例、离线 PoC 和复用决策即停止。下一轮再决定是否接入现有 Scenario Agent，以及何时开展下游 Mock 生成效果验证。**不要求本轮证明 Mock 质量已经提升。**

---

## 附：项目证据入口与材料可信范围

### 项目证据

- [P1：Step 6 Live 报告，固定提交](https://github.com/Haon0731/Mockspec/blob/9d91c1f/docs/step6-live/report.md)
- [P2：当前 Scenario Contract 与验证器](https://github.com/Haon0731/Mockspec/blob/9d91c1f/src/mock_spec/scenario.py)
- [P3：Java 案例审查](https://github.com/Haon0731/Mockspec/blob/9d91c1f/docs/step6-live/java-review.md)
- [P4：Python 案例审查](https://github.com/Haon0731/Mockspec/blob/9d91c1f/docs/step6-live/python-review.md)
- [P5：Step 5B 分析后端验证](https://github.com/Haon0731/Mockspec/blob/9d91c1f/docs/step5b/report.md)

### 本材料做了什么核实

已核对主要论文的官方页面或作者提供的 HTML、cvc5 官方 API、相关工件页面，以及指定提交的 Live 报告。材料中的推导流程、输出结构、验收矩阵是为 Mock Spec 提出的设计要求，不是原论文已经提供的完整实现。

本次整理没有安装运行 cvc5，没有执行 Mock Spec 项目、没有重跑真实模型，也没有审计所有第三方代码或许可条款。PoC、源码到公式的可靠性和正式集成均由本任务要求 Codex 进一步验证。

尤其注意：SymPrompt 同名仓库不能自动当作 FSE 2024 的工件；Elrond 的正式工件入口已给出；StubCoder 的公开 JAR/数据不等于已核实其源码可直接导入。任何新颖性结论仍需单独研究，不能由本材料的组件组合直接推出。
