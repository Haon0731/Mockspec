# Step 5A — 辅助开源组件复用评估

**范围。** 本文是 Scenario Inference Agent 的辅助组件资料卡，不替代 Joern / Spoon / Python AST 的主方案评估。资料于 **2026-10-21** 从项目官方文档、官方 API 参考和上游仓库取得；本轮**没有安装、运行或向任何工具提供案例或旧 Stub-Tester 数据**。因此所有能力均为“文档确认，尚未实测”，不能当作对 Masked Test 的保证。

## 结论

| 组件 | Java | Python | 可复用结论 | 是否纳入最小主链 |
| --- | --- | --- | --- | --- |
| [Serena](https://github.com/oraios/serena) | 是（LSP 后端） | 是（LSP 后端） | 面向符号的定位、声明/引用和诊断；没有文档化的 CFG、def-use 或切片 API。 | 否；若需要给人工/Agent 提供只读符号导航，再单独评估。 |
| [ast-grep](https://github.com/ast-grep/ast-grep) | 是 | 是 | 结构化匹配、文件/行/列/字节区间 JSON 事实。 | 否；可作为小型只读预筛选器。 |
| [CodeQL](https://github.com/github/codeql) | 是 | 是 | AST、CFG、局部/全局数据流、污点路径和可编程查询。 | **否**；技术上强，但当前 CLI 使用授权未获确认，故不作默认 Agent 基础。 |
| [CrossHair](https://github.com/pschanely/CrossHair) | 否 | 是 | 对带类型/契约的 Python 运行符号执行，产生反例模型。 | 否；会执行目标代码，不能用于不受信任或含秘密的分析输入。 |
| [Z3](https://github.com/Z3Prover/z3) | Java API | Python API | 对已由 Mock Spec 翻译出的有限路径约束检查 SAT/UNSAT、模型和替代组合。 | 否；仅在确有路径约束求解缺口时作为可选库。 |

主链不应为了上述组件而扩张：它们都不从“测试目标 + 依赖调用 + 代码路径”自动产出 Mock Spec。Scenario 专用的候选路径排序、依赖响应语义、替身边界、未解析证据、分支组合以及来源/授权审计，仍必须由 Mock Spec 的小型适配层定义。

## A–D：能力与真实接口

### Serena：符号导航 MCP，而非程序分析引擎

官方的工具清单确认其 MCP/LSP 接口包含 `find_symbol`、`find_declaration`、`find_implementations`、`find_referencing_symbols`、`get_symbols_overview` 和诊断工具；REPL 还展示 `s.lsp.find_symbol("...", depth=1)` 的可编程调用。LSP 后端的受支持语言名单包含 Java 与 Python。 [Tools and APIs](https://oraios.github.io/serena/01-about/035_tools.html)；[language support / features](https://oraios.github.io/serena/01-about/025_features.html)

这足以协助定位测试断言所在符号、目标方法定义和已解析的引用调用；`find_referencing_symbols` 的文档明确说明它为代码引用而非文本 grep。接口返回的符号地址/文件信息可作机器可读导航事实，但官方文档没有承诺跨语言、跨语言服务器的稳定行列区间协议，且实现/声明解析受具体 LSP 限制。因此分析契约必须保存原始工具输出、相对路径、工具版本和解析失败，而不能把“无结果”解释为“无调用”。

Serena 文档只声明符号检索和 IDE/LSP 能力；没有公开 CFG、def-use、控制依赖、backward slice 或路径条件 API。因此它不能回答依赖响应如何流入条件，也不能表示分支组合。它可经 `start-mcp-server` 直接供支持 MCP 的客户端使用；封装为 OpenAI Agents SDK Function Tool 需要本项目自己写只读 RPC 适配器，并非官方现成 SDK。 [运行与 CLI](https://oraios.github.io/serena/02-usage/020_running.html)

隔离风险需要特别处理。Serena 可把项目数据目录放到项目外，便于只读项目；不过其文档也说明 `read_only` 只会拒绝 Serena 自身编辑，REPL 提交的 Python 不受该限制，真正的限制需由进程/容器沙箱提供。项目内、全局 memory 默认可读/可写，且某些语言服务器可能下载或启动依赖。若以后做 PoC，只能以每个审计 case 的临时目录作为 project root，只暴露工具接口（禁用 edit、shell、REPL、memory），使用无网络容器和只读挂载。 [安全考虑](https://oraios.github.io/serena/02-usage/070_security.html)；[memory 存储](https://oraios.github.io/serena/02-usage/045_memories.html)；[语言服务器依赖下载源码](https://github.com/oraios/serena/blob/main/src/serena/cli.py)

版本/许可：当前上游变更日志为 **Serena v2.0.0（2026-10-13）**；该 Serena v2 application 为 GPL-3.0-or-later、SolidLSP 为 MIT。旧 MIT 版本的权利不追溯，但不得假设未锁定版本仍为 MIT。GPL 进程集成、分发和镜像应在任何采纳前进行许可证审查。 [README 许可证说明](https://github.com/oraios/serena/blob/main/README.md?plain=1)；[v2 变更记录](https://github.com/oraios/serena/blob/main/CHANGELOG.md)

### ast-grep：确定性语法事实，不是语义/流分析

ast-grep 基于 tree-sitter 的结构匹配，官方列出的可用语言包括 Java 和 Python。规则可以用 YAML `language`、`pattern`、`constraints`、组合规则表达；Python API 有 `SgRoot`、`SgNode`、`Rule`、`Config`、`Edit`，而 Node API 仍标为 experimental。CLI 的 `--json` 输出匹配文本、文件、0-based 行/列/字节范围及捕获变量，正适合把断言、`if`、mock/网络调用等候选点记录成可审计事实。 [简介及语言](https://ast-grep.github.io/guide/introduction)；[YAML 规则 API](https://ast-grep.github.io/reference/yaml)；[Python / Node API](https://ast-grep.github.io/reference/api)；[JSON 格式](https://ast-grep.github.io/guide/tools/json.html)

因此，它能在冻结 case 目录内以明确 pattern 找到断言、目标/依赖调用和条件节点，也可用 JSON 的 range 给出确定位置。它不能解析跨文件绑定、动态派发、def-use、CFG、控制依赖、backward slice 或“响应值是否决定分支”；结构命中不得升级为调用解析结论。对于分支组合，只能报告 AST 嵌套，不能报告可达性或替代路径。

集成方式为 CLI（`ast-grep run ... --json=stream`，可限制 glob/路径）或 Python API；Function Tool 适配只需把 case 目录、固定规则和 JSON 输出包住。CLI 默认按启发式使用线程，适配层应固定线程数并在隔离工作目录执行以改善资源上界/重复性。官方 CLI 明确给出 `--threads`、`--globs`、`--stdin` 和 JSON 输出。 [CLI 参考](https://ast-grep.github.io/reference/cli)

风险较低但不是零：它读取提供的树中可见的所有源码，所以绝不能以整个旧仓库为工作目录；tree-sitter 对不完整/预处理/缺依赖代码的恢复不是语义正确性证明，必须将解析失败或未匹配显式写入事实。当前仓库许可证为 MIT；调研时 releases 页显示最新标签为 0.45.3（页面显示 8 月 31 日发布），实现/API 仍可能演进，应锁定二进制和规则版本。 [MIT LICENSE](https://github.com/ast-grep/ast-grep/blob/main/LICENSE)；[releases](https://github.com/ast-grep/ast-grep/releases)

### CodeQL：能力最全，但默认纳入受 CLI 使用授权限制

CodeQL 的数据库包含 AST、数据流图和 CFG；官方当前支持 Java 7–26 与 Python 2.7、3.5–3.14。Python 库公开 `ControlFlowNode` / `BasicBlock`，并可把 `DataFlow::Node` 映射到 CFG 节点；Java 和 Python 均有局部/全局 DataFlow 和 TaintTracking 文档。Java 文档给出 `DataFlow::ConfigSig` 与 `DataFlow::Global<ConfigSig>`，以 source/sink/barrier/additional flow step 定义并查询 `flow(source, sink)`；Python 文档给出 `localFlowStep`、`localFlow`、`ExprNode`、`CallCfgNode` 等。它可以由自定义 query 将依赖调用定义为 source、条件/断言定义为 sink，返回路径事实，从而实现有方向的（含 backward 查询重写的）切片；控制依赖、路径可达性和业务语义仍需自行建模，不能由标准安全库自动推出。 [数据库模型](https://codeql.github.com/docs/codeql-overview/about-codeql/)；[语言/版本支持](https://codeql.github.com/docs/codeql-overview/supported-languages-and-frameworks/)；[Java 数据流](https://codeql.github.com/docs/codeql-language-guides/analyzing-data-flow-in-java/)；[Python 数据流](https://codeql.github.com/docs/codeql-language-guides/analyzing-data-flow-in-python/)；[Python CFG](https://codeql.github.com/docs/codeql-language-guides/codeql-library-for-python/)

CLI 是 `database create` → `database analyze` 的批处理接口，结果可为 SARIF；自定义 QL 查询和解析输出能被封装成 Function Tool，但没有官方 OpenAI Agents SDK 集成。**Java 不是必须成功构建：**官方支持 Java 的 `none` build mode，直接从全部 Java 文件创建数据库；它会尝试运行 Gradle 或 Maven 取得更准确的依赖信息，但不调用构建。`autobuild`/`manual` 才需要可成功构建的环境。缺失/不可得依赖会降低类型和调用目标信息的完整度：官方曾将“至少 5% expression 缺类型或 5% call target unknown”定义为 Java `none` 模式的显著分析问题。故 Java 的最小隔离 PoC 可先使用 `none`，并把 extractor 的依赖/未知调用诊断作为事实输出；不能以“Java 必须构建”作为排除理由。仅可在每个 case 的临时、最小源树生成数据库，且数据库与 SARIF 必须视作受控中间产物，不能吸收未审核路径。 [compiled-language build modes](https://docs.github.com/en/code-security/concepts/code-scanning/codeql/codeql-for-compiled-languages)；[Java `none` 说明](https://docs.github.com/en/enterprise-cloud%40latest/code-security/reference/code-scanning/codeql/build-options-for-compiled-languages)；[none-mode 缺依赖行为](https://codeql.github.com/docs/codeql-overview/codeql-changelog/codeql-cli-2.21.0/)；[未知类型/调用阈值](https://codeql.github.com/docs/codeql-overview/codeql-changelog/codeql-cli-2.18.1/)

最重要的阻断是许可：`github/codeql` 查询库是 MIT，但 CodeQL CLI/engine 单独许可。没有 GitHub Code Security 授权时，上游条款只允许学术研究、演示、或特定开放源码情形；其二进制仓库明确禁止在一般专有/非 OSI 源代码上生成数据库或进行自动化/CI/CD。Mockspec 的未来运行形态与分析源码的许可均未在本报告中证明符合条款，所以不可把 CodeQL CLI 作为默认 Agent 组件；需要法务/授权和隔离 database-creation PoC 后才能重新评估。 [CodeQL 仓库许可说明](https://github.com/github/codeql)；[官方 license note](https://github.com/github/codeql/blob/main/docs/codeql/reusables/license-note.rst)；[CLI binaries 条款](https://github.com/github/codeql-cli-binaries/blob/main/README.md)

### CrossHair：Python 执行式符号探索，不是静态切片器

CrossHair 仅分析 Python，使用带 Z3 表达式的代理值，重复调用目标函数并在 `if` 等位置添加路径约束；目标发生时可从 Z3 模型报告反例。它不做 AST 或 bytecode 分析。因而对于已写明、可安全运行的 Python 函数和契约，可能帮助发现可行输入/条件组合；它不能定位 Java，不能提供一般 CFG/def-use/backward slice，也不能在无契约的 Masked Test 中可靠推断 mock 响应语义。 [工作原理](https://crosshair.readthedocs.io/en/latest/how_does_it_work.html)

它有 CLI（`crosshair`）和 Python 包入口，但不是 MCP/Function Tool；必须由项目封装。上游主分支 `pyproject.toml` 在调研时标记为 0.0.110、Python >=3.8、MIT，运行依赖含 `z3-solver` 与 `pygls`；ReadTheDocs 页面则标为 0.0.111，因而采纳前必须锁定 wheel/tag，而不能混用文档和主分支。无论版本，应与现有实验 venv 隔离。 [官方 pyproject](https://github.com/pschanely/CrossHair/blob/main/pyproject.toml)；[官方文档版本页](https://crosshair.readthedocs.io/en/latest/how_does_it_work.html)

安全结论是**默认排除**：官方限制页警告它实际执行代码和参数，audit hook 对磁盘/网络并不完备，尤其不能阻止 C 扩展动作；还要求确定性且不保证无反例即正确。即使 case 目录只读，也必须采用无网络、最小权限、资源限额的独立容器，且不挂载参考 Mock/评分/宿主秘密。 [限制](https://crosshair.readthedocs.io/en/latest/limitations.html)

### Z3：约束求解后端，输入语义由 Mock Spec 负责

Z3 是 MIT 许可的 SMT 求解器，提供 Java 和 Python API；z3py 的 `Solver` 支持 `add`、`push`、`pop`、`check`、`model`、`unsat_core` 等标准求解操作。它适合在 AST/Spoon/Joern 已提取条件后，检查有限的路径谓词能否同时成立，并取得模型或记录 `unsat`/`unknown`。它本身不解析 Java/Python，不定位调用，不建 CFG/def-use，也不将依赖调用翻译为变量；这些翻译和受限理论选择是 Mock Spec 适配层的责任。 [上游 README](https://github.com/Z3Prover/z3)；[FAQ / API 与 MIT](https://github.com/Z3Prover/z3/wiki/FAQ)；[z3py Solver API](https://z3prover.github.io/api/html/classz3py_1_1_solver.html)

其官方 releases 页在调研时列 5.1.0 为 Latest（页面所示发布时间为 2026-08-16）；有预编译包和多语言绑定。必须限定每个问题的变量数、递归深度、超时与内存，并把 `unknown` 视为信息不足而非否定证据。 [releases](https://github.com/Z3Prover/z3/releases)

## 资源、启动与隔离：已知与未知

下列资源项目均**尚未在 Mockspec 或两个审核案例实测**；本轮不能给出启动时间、峰值内存、磁盘用量或成功率，也不能以未测数值阻断选型。

| 组件 | 官方已知运行形态 | 目前不能声称的量化结论 | 最小隔离要求 |
| --- | --- | --- | --- |
| Serena | Python MCP server，按语言启动 LSP；部分语言服务器会下载/启动依赖。 | 启动时间、索引大小、Java/Python LSP 内存均未测。 | 单 case project root；无网络；只读挂载；仅工具接口，禁用 REPL/edit/shell/memory。 |
| ast-grep | Rust CLI 或 API；CLI 默认线程数为 heuristic，可设 `--threads`。 | 二进制启动、峰值内存、复杂规则吞吐未测。 | 单 case 目录；固定版本/规则/线程；只输出 JSON。 |
| CodeQL | CLI 产生数据库并执行查询；Java `none` 可不构建但可能尝试取得依赖信息。 | CLI 解压大小、数据库大小、Java/Python case 耗时/内存及 `none` 模式精度均未测。 | 单 case 临时源树和数据库；无网络或显式控制依赖解析；不挂载未授权源码；保留 extractor 诊断。 |
| CrossHair | Python 包实际调用被分析函数，并依赖 Z3。 | 单函数路径爆炸、执行时间、内存和对本项目代码的安全性均未测。 | 默认不运行；若单独批准，使用无网络、无 secrets、最小权限、CPU/内存/时间限额容器。 |
| Z3 | 本地求解器及 Java/Python bindings。 | 求解时间/内存取决于翻译出的公式；本项目公式规模未测。 | 只接收已审核的有限约束；逐 query 设置 timeout/内存边界并记录 `unknown`。 |

## Scenario 专用缺口（不可交给辅助组件）

1. 将 UnifiedTask 的允许路径映射成“测试断言、目标方法、依赖调用、外部响应字段”四类事实，并拒绝未授权文件。
2. 从语言工具的局部事实构造跨工具的、版本化 provenance；包括 Case ID、masking 状态、源码 hash/冻结身份（由既有准入记录核验而非自动重新批准）、相对位置和失败原因。
3. 以目标结果反向选择候选路径，区分值流、控制依赖和纯 AST 邻近；保留替代路径、短路、异常和未知调用，而非强行只给一个 mock。
4. 把语言级条件归约为有限、可解释的响应约束；只在支持的子集交给 Z3，并把不可翻译表达式标记为 `unsupported` / `unknown`。
5. 在无完整代码、缺依赖、解析失败或 Masked Test 隐藏了关键调用时输出明确的 `insufficient_evidence`，禁止回退到原始测试、参考 Mock、评分或隐藏规格。

## 对主方案的辅助纳入准则

* **当前不纳入 Serena、CodeQL、CrossHair 或 Z3。** 它们不消除方案 A/B 的核心选择；尤其 CodeQL 的 CLI 使用授权尚未确认、CrossHair 违反“不可执行不受信任输入”的默认安全边界、Serena 过于宽泛且 Serena v2 application 的 GPL-3.0-or-later 集成需审查。
* **ast-grep 仅为可选、固定规则的预筛选。** 只有当 Java/Python AST/Spoon PoC 证明缺少稳定的跨语言源码区间输出时，才在隔离 case 目录以锁定版本运行。它不能替代 AST/Spoon 或 Joern 的语义判断。
* **Z3 仅在最小 PoC 已拿到可翻译的条件事实、且需要判定两个以上候选路径是否同时可满足时加入。** 先用固定超时的 `sat`/`unsat`/`unknown` 输出，不做全程序符号执行。
* **CodeQL 只作为未来的研究型备选。** 重新进入范围的前提是：确认被分析源码和使用方式符合 CLI 条款、以 Java `none`（或需要时的构建模式）成功生成最小 case 数据库并检查依赖/未知调用诊断、资源测量可接受，以及自定义 query 能输出所需 response-to-branch 路径。以上均尚未实测。

## 本轮验证状态

本文件只完成了官方文档/API/许可核对；**没有实际运行 Serena、ast-grep、CodeQL、CrossHair 或 Z3**，也没有触碰任何 Java/Python 审核案例。故没有组件可声称已经在本项目的真实案例上定位依赖、提取条件、追踪响应数据流或正确表示分支组合。后续若获准做最小 PoC，应复用主报告规定的两个已审核 UnifiedTask case，在每个 case 的临时目录、只读输入、无网络/无 secrets 容器中逐项测量，并把解析错误与缺失证据作为正常机器可读输出。
