# Scenario Agent P0 来源核对与复用矩阵

**核对日期：** 2026-10-22
**适用基线：** `0cbfb3d`（仅作为设计/实现对照基线；本文件不声明已修改其生产代码。）
**证据分级：** `文档核实` = 已阅读下列固定 URL 的一手资料；`源码核实` = 已阅读固定提交源码；`本地实测` = 已在本工作区实际运行。文献条目为文档核实；cvc5 本地实测由单独宿主结果记录支持。

## 结论

建议采用一条受限的“**人工绑定可见源码事实 → 有限公式 → cvc5 检查/可选 abduction → 宿主记录结果**”路线。它复用的是既有研究中的问题分解、Mock 的调用匹配/反应原语，以及 cvc5 的现成 API；它**不**复用论文的完整合成、采样、搜索或程序验证基础设施。

本轮对象应定义为**一个固定可见测试参数行和场景内的 Mock 配置约束**，不是对所有输入或所有实现有效的库契约。任何 `SAT`/`UNSAT` 只适用于已记录的公式及其源码绑定，不能证明 Java/Python 源码语义翻译正确。

## 来源—设计—实现复用矩阵

| ID / 一手来源 | 原方案的前提和已核对能力 | 本轮可直接调用 / 可借鉴 | 不可照搬及本项目差异 | 证据状态 |
|---|---|---|---|---|
| S1 [Maximal Specification Synthesis, POPL 2016](https://popl16.sigplan.org/details/POPL-2016-papers/9/Maximal-Specification-Synthesis)；[作者 PDF, pp. 1–4](https://pages.cs.wisc.edu/~aws/papers/popl16.pdf) | 假设一个带未知过程、可验证的开放程序及全执行的 postcondition。论文将 safe、non-trivial、maximal 分开；multi-abduction 同时为多个未知谓词、同一谓词的多个调用点求解释。 | 借鉴“目标是否被支持”和“候选是否过强”是两个检查面；每个候选保留 scope，且同一依赖的重复调用不可无说明合并。 | 不导入其 LLVM verifier/CEGIS 实现；单个可见测试不能被称为 maximal、weakest 或完整 library contract。 | 文档核实；无可直接导入 API；未实测。 |
| S2 [Elrond / Data-Driven Abductive Inference, 2021](https://arxiv.org/html/2108.04783v2)；[OOPSLA 页面](https://2021.splashcon.org/details/splash-2021-oopsla/20/Data-Driven-Abductive-Inference-of-Library-Specifications) | 以库行为样本提出候选，利用 SMT 反例细化；spurious cex 通过能否抽取触发安全违例的输入来识别；论文明确指出有限采样会漏掉行为而产生过强/过拟合规格，并用 weakening 缓解。其实现是 7,267 LOC OCaml、以 Z3 为后端，前端以 weakest liberal precondition 生成 query，且要求递归 client 提供 invariant。 | 借鉴反例分类、把“目标不成立”与“候选排除了合理实现”分开、为结果保留模型假设。 | 本轮不能执行不可见库或收集行为样本，也没有 OCaml/WLP/invariant 前提，因此不继承其 safe/consistent/maximal 保证，也不声称实现 Elrond。正式工件仅作可获得性记录：[Zenodo 5130646](https://zenodo.org/records/5130646)，未下载。 | 文档核实；未下载工件、未实测。 |
| S3 [StubCoder §3.2, arXiv 2023](https://arxiv.org/html/2307.14733v1)；[ACM DOI](https://doi.org/10.1145/3617171) | Stub call 由 mock 接收者、方法、参数 matcher 和反应构成；反应是 `Return(v)` 或 `Throw(v)`；多个命中同一调用的规则按出现顺序反应。 | 复用为 Contract 的最低行为原语：`call matcher → return/throw response`，并允许显式 sequence/call-count 字段。 | 不移植其 CFG、遗传搜索、fitness 或“测试通过”目标；遮蔽 Mock assertion 和不公开的 setup 意味着本轮不能以通过测试判定行为忠实。 | 文档核实；其 Zenodo 工件未下载；无稳定可导入 API 的证据。 |
| S4 [cvc5 1.3.1 Base Python API](https://cvc5.github.io/docs/cvc5-1.3.1/api/python/base/solver.html)；[1.3.1 Python API 安装页](https://cvc5.github.io/docs/cvc5-1.3.1/api/python/python.html) | Base Python API 是 C++ API 的镜像；`checkSat()` 返回可满足性结果；`getAbduct(conj[, grammar])` 需要 `produce-abducts`，且标记 experimental；grammar 可限制生成项；`getAbductNext()` 需要 incremental 且只保证语法不同。 | 后续 PoC 可直接安装并固定 `cvc5==1.3.1`，调用 `setOption`、term/grammar 构造、`checkSat`、`getAbduct`/`getAbductNext`。必须另做独立候选检查，记录 SAT/UNSAT/unknown/timeout/API error。 | cvc5 不解析 Java/Python、不证明源码绑定，也不保证最弱 abduct 或语义不同的多个场景。语法不同不等于逻辑不同，需保留等价性未检查限制。 | 文档核实；现已本地实测 cvc5 1.3.1，见 [实际结果](../../experiments/scenario-research/results/solver-results.json) 与 decision.md。 |
| S5 [Understanding and Characterizing Mock Assertions, FSE 2025](https://arxiv.org/abs/2503.19284)；[FSE 页面](https://conf.researchr.org/details/fse-2025/fse-2025-research-papers/59/Understanding-and-Characterizing-Mock-Assertions-in-Unit-Tests) | 对 11 个 Java 项目的 4,652 个测试的经验研究：mock assertion 常用于外部资源调用、路径经过、侧作用和内部计算的补充验证。 | 将 `expected_interaction` 与 `verification_suggestion` 分开；后者要说明保护的行为、证据和场景，默认不是 oracle requirement。 | 这是实证动机，不是从任一源码调用恢复 hidden assertion 的规则，更不能自动把普通交互升级为 verify obligation。 | 文档核实；不适用 API/本地实测。 |
| S5 表达参考：[Spock 2.4-M4 Interaction Based Testing](https://spockframework.org/spock/docs/2.4-M4/interaction_based_testing.html) | interaction 由 cardinality、target、method、argument constraint 组成；interaction 有 feature/`when`/`then` 作用域；默认不约束顺序以避免过度规定，跨 `then` 才可表达顺序；同一 interaction 可同时 mock+stub。 | Contract 可表示次数、目标、方法、参数、可选响应和可选顺序/作用域；顺序字段必须显式而非默认。 | 仅作为表达模型参照，不引入 Groovy/Spock，也不把 Spock 语义混同 JUnit/Mockito/pytest 的运行时语义。2.4-M4 是固定 milestone 文档，不是依赖升级建议。 | 文档核实；未实测。 |

### S1/S2 的精确采用边界

* S1 作者 PDF **p. 1** 把目标定义为：已知程序 `P`、未知调用 `Fi` 与 postcondition，寻找保证 `P` 正确的最 permissive 规格；**p. 2** 规定 safety、non-triviality、maximality，且用 verifier 为整个程序找反例；**p. 3–4** 定义 simple/multi-abduction，并说明同一未知谓词可以在不同参数处重复出现。故本轮只采用 `non-vacuity + sufficient-for-O + multiple-call-site` 这三个局部检查思想，绝不能沿用其全程序 maximality 结论。
* S2 [§2.2 “Identifying Spurious Counterexamples”](https://arxiv.org/html/2108.04783v2) 区分：无法由模型抽取实际触发输入的 cex 可因 method-predicate 语义不一致而是 spurious；若 cex 特征全已是已知 positive，则也可能是 client assertion 本身不成立。Contract 因此应把 `counterexample`, `source/semantic assumption`, `unknown/unsupported` 分字段保存，不能把每个 solver cex 都翻译成 Mock 行为。
* S2 [§2.2 “Weakening”](https://arxiv.org/html/2108.04783v2) 明说样本不足或实现本身不展示某行为都会使初始规格过强。当前没有合法的 hidden-library sampling，所以只能保留 alternative scenario/unknown，不能自动执行 weakening 或报告“弱规格”。
* S2 [§6 “Implementation and Evaluation”](https://arxiv.org/html/2108.04783v2) 是其 OCaml/Z3/WLP/invariant 前提的来源；它解释了为何 cvc5 PoC 不应被称为 Elrond replication。

## cvc5 固定版本、API 和许可证

### 推荐的 PoC 固定点

* **固定版本：`cvc5==1.3.1`。** 官方 GitHub release `cvc5-1.3.1` 发布于 **2025-09-24**。其 tag 是 lightweight tag，直接指向源码 commit [`ea1b484fa54bfe56c0f8b3ac90a6e3e2f46441e7`](https://github.com/cvc5/cvc5/commit/ea1b484fa54bfe56c0f8b3ac90a6e3e2f46441e7)（commit message `Bump version to 1.3.1`），不是一个独立的 annotated-tag object：[release](https://github.com/cvc5/cvc5/releases/tag/cvc5-1.3.1)。该 release 对应的版本化 API 文档存在，且明确给出 `pip install cvc5` 和 base API。
* **当前上游背景：** 检索日的最新官方 release 是 [cvc5-1.4.2](https://github.com/cvc5/cvc5/releases/tag/cvc5-1.4.2)，发布于 **2026-10-08**。这不是本轮应浮动采用的版本；不要以 `latest` 文档取代 1.3.1 的实验记录。
* **许可证：** 标签 `cvc5-1.3.1` 的 [COPYING](https://github.com/cvc5/cvc5/blob/cvc5-1.3.1/COPYING) 说明 cvc5 源码使用 modified BSD；构建可选链接 GPLv3 库，`--no-gpl` 是避免这类依赖的构建选项（文件称默认）。Python wheel 的实际依赖/许可仍须在安装后记录，不能仅由源码许可证推断。

### 已核对、可用于薄适配的 1.3.1 API 语义

1. [Base Python API](https://cvc5.github.io/docs/cvc5-1.3.1/api/python/base/python.html) 明确它镜像 C++ API；因此 PoC 应选择 `import cvc5` 的 base API，避免把 pythonic API 的不同表面混入实验。
2. [`Solver.checkSat`](https://cvc5.github.io/docs/cvc5-1.3.1/api/python/base/solver.html) 是候选一致性与反例检查的接口，返回 solver 的结果。`unknown` 是必须保存的实际状态，不能折算为 `false` 或 `UNSAT`。
3. [`Solver.getAbduct`](https://cvc5.github.io/docs/cvc5-1.3.1/api/python/base/solver.html) 要先启用 `produce-abducts`，支持可选 grammar，且官方标记为 **experimental**。C++ 语义说明为：在当前 assertions `A` 和目标 `B` 下，返回 `C` 时必须同时满足 `(A ∧ C)` 可满足及 `(A ∧ ¬B ∧ C)` 不可满足；找不到则返回 null term：[同版本 C++ Solver API](https://cvc5.github.io/docs/cvc5-1.3.1/api/cpp/classes/solver.html)。
4. [`Solver.getAbductNext`](https://cvc5.github.io/docs/cvc5-1.3.1/api/python/base/solver.html) 只能紧随成功的 `getAbduct`/`getAbductNext`；还要求 incremental，并仅保证与上一项**语法不同**。结果记录不得写成“两个语义不同的 scenario”，除非另做逻辑等价检查。
5. 使用 grammar 的安全目的仅是限制 `C` 可引用的变量/结构，以禁止“直接把最终 oracle 当作可设置布尔变量”。grammar 本身不验证变量是否是合法 Mock 控制点；该边界需由宿主符号表和源码绑定检查。

### PoC 前置条件与不可声称的结果

* 记录：wheel 版本、`cvc5.__file__`、Python 版本、平台、`Solver.getVersion()`（若该安装版本暴露）、选项、完整公式、wall-time、API exception 和 solver result。
* 自动 abduction 前先显式断言背景 `A` 可满足；否则不运行/不接受 abduct，以阻断“矛盾背景推出任意目标”。
* checking 至少分别检查 `A ∧ G ∧ H` 的可满足性和 `A ∧ G ∧ H ∧ ¬O` 的不可满足性；前者单独防止空洞证明。`G` 是场景 guard，`H` 是可控依赖行为，`O` 是不可直接赋值的可见 oracle。
* 动态调用、未绑定调用、hidden implementation、异常类型/消息等未被有限翻译的语义，必须写入 `unknown`/`unsupported` 或显式 `assumption`；不得用有利的自由 Bool 伪装为源码事实。

## 设计可复用项与接口边界

| 目标 | 可复用的已有能力/思想 | 本轮薄适配应承担的最小责任 | 不应新增的组件 |
|---|---|---|---|
| 依赖行为 | StubCoder 的 matcher→Return/Throw、顺序响应概念 | 将每项响应绑定至 dependency call site、guard、evidence ID 和控制权；区分 response 与 interaction suggestion | 新 Mock runtime、DSL、搜索器 |
| 候选生成 | S1 multi-abduction 的“足够且不过强”问题框架 | 允许多个替代 scenario，给每项局部 scope；将 implementation choice 与 scenario obligation 分开 | maximal-spec synthesis 或全程序 verifier |
| 反例 | Elrond 的 spurious/overfit 警示 | 保存导致失败的公式、模型/反例和它所依赖的假设；候选过强时降级，不删替代路径 | 行为采样平台、不可见库执行 |
| SMT | cvc5 1.3.1 `checkSat` + experimental `getAbduct` | 只翻译显式支持的小片段；宿主填写真实 check state | Java/Python CFG、DDG、符号执行器、第二套求解器/DSL |
| 交互验证 | Mock-assertion FSE 经验研究 + Spock 结构化 interaction 表示 | `expected_interaction`/`verification_suggestion` 各自带 scope/evidence/rationale；默认 `not_implied` | 自动把 source call 转为 verify assertion |

## 版本和证据限制

* S1–S3 与 S5 是研究论文本身，不是可调用库或对本仓库的实现证明；本表只借鉴其明确假设下的概念。
* cvc5 `getAbduct*` 是 experimental，故每次 PoC 必须写入实际版本、选项和错误输出；固定 1.3.1 后，不能引用浮动 `latest` 的签名作成功依据。
* Spock 2.4-M4 是文档版本化参照。它的 interaction 语法/生命周期不应被表述为现有 Java/Python 案例的运行时事实。
* 本文件没有读取参考 Mock、隐藏答案、评分或未审核原始输入，也没有下载 S2/S3 大型工件。

## 可复用结论

可以开始小型离线 PoC：固定 `cvc5==1.3.1`，以 grammar 和宿主符号表限制可 abduct 的依赖行为，用 `checkSat` 复核每个候选的非空洞性与局部充分性。S1/S2 只提供“候选必须既支持目标又不过强”的设计准则；S3/Spock 只提供结构化行为/交互表示；S5 支持将交互验证作为有证据、可选的建议，而非从调用自动推导出的义务。
