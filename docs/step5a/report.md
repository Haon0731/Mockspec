# Step 5A — Scenario Agent 组件复用报告

评估日期：2026-10-21。交付类型：**技术选型 + 有界 PoC；不是 V0.2 实现或上线批准。**

## 1. 决策

**推荐方案 B：OpenAI Agents SDK + Joern CPG + Joern Slice + 小型工具适配层，作为完整 Scenario Inference 的主技术基础。** 推荐依据是官方提供的跨 Java/Python 图表示、CFG、数据依赖与切片 API，而不是本轮的运行性能或准确率。**Joern 文档确认，尚未实测；采用前必须通过下述两例验收。**

方案 A（SDK + Python AST + Spoon）保留为低成本、确定性源码定位和证据校验基线，不作为完整程序分析方案。当前本地 wrapper 和核心 AST API 未提供成熟、已集成的 CFG、跨语句 def-use、backward slicing（Spoon 的额外实验模块另见附件）；若自行补齐这些，便违背本轮“不从零实现引擎”的目标。不会因为 A 的局部 PoC 成功，就声称它已经满足完整 Scenario Agent。

当前不引入 Serena、ast-grep、CodeQL、CrossHair、Z3。它们各有作用，但没有证据表明此时需要在主链再增加一个组件。方案 B 若不能通过最小运行验收，应明确缩小交付能力或重新选型；不自动转为自行实现数据流/切片引擎。

证据附件：[核心组件官方研究](core-components.md)、[辅助组件官方研究](auxiliary-components.md)、[实际运行 JSON](results.json)、[PoC 脚本](poc/run.py)、[离线核验](poc/verify.py)。附件的“文档确认”与这里的“实际运行”是不同证据等级。

## 2. 能力矩阵：理解、程序分析、场景推导

下表“有”仅表示上游提供对应能力，**不表示已在本项目实测**。跨语言支持不等于两种 frontend 精度一致。

| 组件 / 官方仓库 | Java / Python | A：断言、目标、依赖调用及位置 | B：CFG / Def-Use、DF / Backward Slice / 控制关系 | C：从目标反向找路径及响应 |
|---|---|---|---|---|
| [Python AST](https://github.com/python/cpython/tree/3.12/Lib/ast.py) | 否 / 是 | 语法节点、`lineno/end_lineno`；动态调用目标未知 | 核心 API 均不提供；只有 AST 嵌套 | 只能提供条件、赋值和调用语法，不能证明可行路径 |
| [Spoon](https://github.com/INRIA/spoon) | 是 / 否 | 类型化 AST、`CtInvocation`、`CtMethod`、`SourcePosition`；缺 classpath 时部分解析 | 核心 Spoon 不是完整 CFG/DF/切片引擎；`CtIf/CtConditional` 是结构嵌套 | 可提供候选表达式，不能把局部变量声明链接当 reaching definitions |
| [Joern](https://github.com/joernio/joern) + [Slice](https://github.com/joernio/joern/blob/master/joern-cli/JOERN_SLICE.md) | 是 / 是 | CPG METHOD/CALL/CONTROL_STRUCTURE，filename/line/column（可缺失） | CPG CFG、REACHING_DEF、CDG；数据流 traversal；Slice 有界反向数据依赖 | 可筛选 source→sink 数据路径；图路径不是可执行路径，也不是充分/必要 Mock 条件 |
| [Serena](https://github.com/oraios/serena) | 是 / 是 | LSP 符号、声明、引用；精度随后端变化 | 无文档化 CFG/DF/切片工具 | 符号导航，不推导响应约束 |
| [ast-grep](https://github.com/ast-grep/ast-grep) | 是 / 是 | tree-sitter 模式匹配与 JSON 区间；不是符号绑定 | 无 CFG/语义 DF/切片；可匹配嵌套 | 只能找到候选语法，不判路径可行性 |
| [CodeQL](https://github.com/github/codeql) | 是 / 是 | 数据库 AST、调用、位置 | CFG、局部/全局 DF；自定义 QL 路径查询，非即用场景切片器 | 可查询候选依赖路径，仍需 source/sink、库语义和目标映射 |
| [CrossHair](https://github.com/pschanely/CrossHair) | 否 / 是 | 不以源码定位/静态图为主 | 符号执行，不是静态 CFG/切片 API | 可探索可执行、带契约的函数；实际执行代码，不适合当前缺实现输入 |
| [Z3](https://github.com/Z3Prover/z3) | 绑定均有；不解析源码 | 无 | 无程序图；SMT 求解 | 可检查已翻译约束的 SAT/UNSAT/unknown；不会提取约束 |

官方 API 与限定见两个研究附件；Python 的节点、位置和语法兼容性见 [Python 3.12 AST 文档](https://docs.python.org/3.12/library/ast.html)。断言识别还需要 JUnit/pytest 的小型规则；识别名称不等于证明测试语义。所有源码位置必须重新映射到允许的 `source_id`，并由原 `SourceStore` 复核。

## 3. 可直接复用的接口与旧代码

### Joern / Slice（文档确认，尚未实测）

复用 CPG frontend、图层与查询执行，不重新实现控制流和数据流算法。主要接口为 CLI 的 `joern-parse`、`joern --script`、`joern-slice data-flow` / `usages`，以及 Scala DSL 的 `cpg.call`、`cpg.method`、`reachableBy` / `reachableByFlows`。Slice 是面向选定 criterion 的有界数据切片，不能等同于保留所有控制条件的可执行程序切片；更不是自动反推所有测试满足路径。参数、输出与资源要求以[核心组件附件](core-components.md)中的上游 API 为准，后续必须锁定一个发行版做 `--help` 和 query 编译验收。

以测试 assertion 参数为目标进行逆向追踪时，需要把测试→MUT→builder getter 的调用/状态联系起来。缺失的库实现不会因创建了 CPG 自动补全。对 `getFrom().isPresent()` 等外部调用，值流和具体语义都可能不完整；需要保留 unresolved，不允许从全旧仓库解析。

### 旧 Spoon（只读源码/API 核查；分析尚未实测）

旧根目录为 `/home/haon/projects/Stub-Tester`。以下均为工具代码，不是案例输入：

| 源码相对路径 | 已有能力 | 复用边界 |
|---|---|---|
| `scripts/test_support_metadata/spoon_backend/README.md` | JSON stdin/stdout sidecar，Spoon 11.2.1，Java 17+，默认 noClasspath | 可复用单进程协议与 diagnostics；不执行旧实验管线 |
| `.../spoon_backend/src/main/java/org/openai/stubtester/testsupport/spoonbackend/SpoonMetadataHelper.java:864` | `Launcher`、`setNoClasspath(true)`、compliance 17 | 只传入临时允许源码，不能继承旧 source roots |
| 同文件 `:673`、`:692`、`:714`、`:450` | 确定顺序遍历、结构事件、控制构造、调用参数与链关系 | AST facts，不是 DF/CFG/CDG；raw snippet 与位置需复核 |
| `.../spoonbackend/DeveloperRealisticCallableLocator.java:55` | 方法/构造器定位、源码区间 | 可借鉴定位接口，不宣称缺依赖时完成类型绑定 |
| `scripts/test_support_metadata/backends.py:116`、`:436` | `SpoonBackend`、JSON request、timeout/exit-code/response 校验 | 原 wrapper 将文件父目录纳入 source_roots，不能对旧路径原样调用 |

实际存在 shaded JAR `scripts/test_support_metadata/spoon_backend/target/spoon-metadata-helper-0.1.0.jar`，15,218,742 bytes。读取包内 Maven metadata 确认 Spoon **11.2.1**，Launcher 和 main class 的 class major 均为 **61（Java 17）**。这证明本地版本，不用“最新”代替实际版本。

### 旧 Python（只读源码核查；本轮运行同一 stdlib 基础）

- `scripts/python_converter/analyzers/ast_parser.py:56`：`ASTParser` / `ast.parse`、导入、函数/方法及区间；`:217` 提供 `find_first_assertion_line`。旧实现遇 SyntaxError 将 tree 置空，适配时必须把 `is_valid=False` 转成显式失败，不能把空列表当“没有依赖”。它用 `errors="ignore"` 读取源码，不应继承这种可能改变证据文本的行为。
- `scripts/python_converter/analyzers/cut_detector.py:67`：基于 import/use 的 CUT 启发式；已有 UnifiedTask 目标时不必再次猜测。
- `scripts/python_converter/analyzers/test_analyzer.py:36`：测试/mock 模式识别；不需要把旧 mock 提取流程搬入 Scenario Agent。
- `scripts/python_converter/extractors/dependency_resolver.py:69`：导入分类，不是数据流或运行时调用解析。

本轮没有导入/运行旧 Python 管线。PoC 直接调用相同的 stdlib `ast.parse`，只添加 JSON 序列化和 if 的词法祖先标签，不构建新程序分析引擎。后续优先复用 stdlib 和旧协议设计，避免复制整个转换器及其数据访问能力。旧自研代码的再分发许可本轮未审查，不能因 Spoon 开源就推定旧 wrapper 许可相同。

## 4. 最小 PoC：实际结果与缺口

### 输入及运行方式

使用原 Adapter 和原准入记录加载：

- Java：`com.google.cloud.tools.jib.cli.jar.JarFilesTest#testToJibContainerBuilder_explodedStandard_basicInfo@69ae66df`。
- Python：`flask_308`。

`poc/run.py` 在 `TemporaryDirectory` 中仅物化 `task.mut_source`、`masked_test`、`supporting_sources`。路径由脚本生成，源码设为只读；Python MUT 前补 264 个空行以保持原始 265–285 行，未恢复缺失行。Java 三个允许源码共 **10,481 bytes**；Python 三段（含补空行）共 **4,756 bytes**。所有临时输入和临时工具二进制在运行后清理；持久化的仅为位置、调用名、条件和诊断 JSON，不保存函数/分支完整源码。交叉审查发现首轮JSON包含过长容器节点原文后，已删除该输出、移除原文字段并重新运行PoC；当前结果为修正后实际输出。

分析子进程只接收临时单文件路径及 source_id；环境仅 PATH/LANG；不 import 或执行被分析源码。PoC 没有挂载整个旧仓库。宿主 Adapter 仍按原规则读取少量冻结准入文件，这不是向分析工具开放控制文件。

本机 `bwrap` 的 namespace 试探被内核权限拒绝。因此这里只证明**临时输入目录与静态解析约束**，不宣称 OS 沙箱或未知工具完全不能读宿主；没有继续启动可执行被分析程序的 CrossHair。后续正式集成应使用已配置的隔离运行环境。

### 六项比较

| 验证项 | Python AST 实测 | 现有 Spoon / Java | Joern Java/Python |
|---|---|---|---|
| 1 关键调用 | `_cv_app.get` mut:282；`ctx.app.aborter` :283；`_wz_abort` :285；目标调用 test:103 | 输入通过准入；未进行案例分析 | 文档确认，尚未实测 |
| 2 条件分支 | `If` :282；测试 :86、:111；assert :120、:126 | 启动失败，见下 | 文档确认，尚未实测 |
| 3 响应→条件 DF | `NamedExpr` 报告 ctx 和 `_cv_app.get(None)`；仅同一表达式语法关系，**未得到通用 DF 图** | 未测 | 文档支持 DF，但本例未测 |
| 4 组合/替代路径 | 正确记录 :283 属 if 真分支，:285 guards=[]；**未自动推出异常路径或路径组合** | 未测 | 未测；Slice 不保证路径完备 |
| 5 位置与机器事实 | MUT 6、test 10、imports 1 条所选结构事实；全部位置位于对应允许区间 | 可用原 JSON 协议，尚未输出本例事实 | 未测 |
| 6 缺失/失败 | 合成非法语法返回 `parse_error`；动态调用解析与异常流明确列为 unknown | 实际 `UnsupportedClassVersionError`，结构化保留 exit=1，未冒充解析成功 | `not_run`，未伪造失败/成功结果 |

Python 每段启动+解析+JSON 约 **0.027–0.028 秒**，是三次单样本，不是性能基准。内存峰值未测，不能据此推出规模化表现。Python 运行时 **3.12.13**，与冻结执行环境 CPython 3.10 不同；只做解析不执行，未来应锁定 grammar 版本与源语言契约。

Spoon 将已有 JAR 复制到临时目录，仅运行 `java -jar ... --help` 做启动探针；Java **11.0.32.1** 只支持 class major 55，而 JAR 要求 61，exit=1，约 **0.057 秒失败时间**。这是运行时失败而非 Java 案例解析失败。未下载 JDK、未运行 Maven、未修改旧环境。Joern 在 PATH 中不存在，未下载/构建/运行；不是据此断言整台主机没有任何安装。

### 需要保留的场景语义（人工检查允许源码，不是工具自动结论）

Java `mut:60–71` 有两类 base image 候选：显式 from 分支，或缺省分支内 `getJavaVersion() <= 8` / `> 8`。`test:71` 的 8-jre 断言不能单独证明必须走缺省分支；显式 from 路径也可能产生同一目标，具体 builder 语义不可见。`mut:75–79` 的 entrypoint 又有 custom 非空 / compute 两个选择，不能把目标结果简化成一个唯一依赖响应。语法上的组合不证明所有组合可行。

Python `mut:282–285` 的候选包括：ctx 为 None→调用 `_wz_abort`；ctx 非 None 且 aborter 抛出→不继续；ctx 非 None 且 aborter 正常返回→继续 `_wz_abort`。`NoReturn` 注解/自然语言说明不能替代对隐藏调用实现的证明。test:102 要求异常，:120/:126 检查文本；Masked Test 缺少响应配置，不能补读或推定某个 Mock 实现。PoC 只确认 AST 布局，未验证上述候选的全程序可行性。

## 5. Agent 集成、资源与风险

**可以封装为 Function Tool，但不是把任意 CLI/Scala/Python 暴露给模型。** 工具参数仅允许 source_id、受限节点 ID、固定查询类型和预算；宿主持有 allowlist、临时目录和解析器。后端调用使用 argv 数组、固定脚本、期限和输出大小上限；模型不能指定宿主路径、classpath、source roots 或任意 query。工具返回 `status/facts/diagnostics/coverage/limitations`，不能用空列表隐去解析失败。

已核对本地 SDK 0.24.0 的 `agents.function_tool`，其 strict JSON schema 仅限制参数结构，不提供文件权限隔离。[官方 Agent 定义](https://developers.openai.com/api/docs/guides/agents/define-agents)支持 Function Tool 与宿主 context。离线核验脚本只包装已记录事实，不创建或改造 V0.1 Agent，不调用模型，不发送源码到远端。实际 SDK smoke 已通过：严格参数 schema、异步 Function Tool 回调读取已记录事实，以及拒绝未知 source_id；不证明模型会正确使用工具或后端具有 OS 隔离。

| 组件 | 依赖/启动/存储 | 维护与许可证关注 | 不完整输入及授权风险 |
|---|---|---|---|
| Python AST | 已有 Python；无需安装；本轮耗时见上 | CPython/PSF，grammar 随版本变化 | 能解析不等于能执行/绑定；原区间映射必须保留 |
| Spoon | JVM/Java AST sidecar；现有 JAR 约15.2 MB；本轮未建模，峰值内存未测 | 上游 MIT；本地11.2.1需17；上游版本不应无条件替换 | noClasspath 允许部分模型，不保证所有坏语法可解析；不恢复 implementation-hidden 方法体 |
| Joern / Slice | JVM、frontend、CPG/overlay/切片落盘；官方推荐资源较重，见核心附件；本轮无耗时/RSS数据 | Apache-2.0；需锁发行版、Java要求、query和schema | 缺库/动态绑定可能造成假阳性或缺失边；CPG缓存也是受控源码衍生产物；不得启用拉依赖或扩展source root |
| Serena | Python+LSP/JDT等服务，可能启动/下载后端，缓存需单独目录 | 当前上游v2许可变更见附件 | 项目、memory、编辑/REPL能力过宽，read_only不等于沙箱 |
| ast-grep | 原生CLI或Python包，语法树与线程预算；耗时/RSS未测 | MIT；固定版本、语法和规则 | 语法恢复不能当语义解析成功；限制扫描路径 |
| CodeQL | CLI、数据库、query packs，Java支持无构建模式但精度需验证；资源未测 | 查询库MIT不等于CLI/engine开源许可；研究/使用条款另核 | 可行技术备选，但当前并非纯开源默认基础；不得让构建/依赖解析越过allowlist |
| CrossHair | Python+Z3等；搜索时间不定，需超时；资源未测 | MIT；版本与Python语义兼容需锁定 | 实际执行目标代码，本轮排除 |
| Z3 | 原生求解器+Python/Java绑定；资源取决于理论与公式，未测 | MIT；固定版本 | 只解决正确建模的公式；unknown不等于unsat，SAT不证明现实程序可达 |

所有前端和后端都必须只分析准入投影；不能为了提高解析率查 Maven cache、site-packages、原始测试或整个旧仓库。额外 JDK/Joern 安装应是独立工具镜像/目录，不改系统 Java、不动旧 venv、不运行旧实验。工具输出若带物理路径，需移除并映射为 source_id；对 padding/synthetic 节点不能生成原文证据。

## 6. Mock Spec 仍需自行设计的最小机制

这些是场景语义与安全适配，不是另造程序分析引擎：

1. **目标映射**：JUnit/pytest 断言、MUT、参数化场景与 dependency source/sink 的受限规则；不能靠测试名字推断强制交互。
2. **候选场景表示**：显式分支极性、组合/替代关系、正常/异常出口，以及 candidate/unknown；不把一条图流升级成唯一必要路径。
3. **响应语义边界**：依赖返回值/异常/属性到条件的映射，只接受可见实现或已审核接口/模型；隐藏实现保留 unknown。库模型也必须有版本与来源，不由模型随意补全。
4. **证据归一化**：版本化 node ID→source_id/行/原文；同一文件不同允许切片保持独立范围；规范排序，CPG内部ID不作为跨运行稳定主键。
5. **预算与失败协议**：parse_error、unresolved_symbol、unsupported_syntax、truncated、timeout、not_run 分开；缺边不能推出“无依赖”。
6. **原契约验证**：沿用既有证据和结构校验；required 仍需场景/条件/证据，不把 expected_interactions 转成必须生成 Mock Assertion。Z3 若未来加入，仅接收有限、可审计公式，不自行实现求解器。

## 7. 下一步最小集成任务（仅建议，本轮不执行）

1. 独立准备锁版 Joern 运行环境，测安装大小、冷启动、两例 CPG/切片大小、墙钟和峰值 RSS；Java/Python 分别建立无网络、只读输入的案例工作区。先验证 frontend、walrus、三元表达式与缺依赖诊断，再接 SDK。
2. 固定少量 query：调用及位置、条件与分支、外部响应到条件的 DF、目标 sink 的有限反向依赖与 CDG。验收必须包含 Python :285 非 else、异常路径未知，以及 Java 显式from替代路径；未解析的库边不可伪造。
3. 将事实映射为只读 Function Tool 返回值，沿用 SourceStore 再验证；测拒绝未知ID/路径、超时、空图和截断，不给 Agent 任意查询执行能力。仅在这一步通过后再讨论 V0.2 的新版本 Prompt，原 V0.1 保持冻结。

验收失败就报告 gap，不引入一串辅助组件、不自动下载依赖源码、不继续 Mock Generation/Behavioral Feedback。

## 8. 验证记录与过程边界

- 本轮新增内容仅在 `docs/step5a/`，原源码、Prompt、Step4 记录和旧仓库未修改。Prompt SHA256 为 `e7bf55a8bb78b52701ab20bbee894513931642b772bc1a08bda8a79a5cb61e6d`，与 Step4 留证一致。
- `poc/run.py`：两例 Adapter 准入及临时物化完成，Python解析成功，Spoon启动失败被明确记录，Joern未运行。
- `.venv/bin/python -B -m pytest -q -p no:cacheprovider -k 'not archive'`：**99 passed, 2 deselected**。两项归档对照会读取生成后产物，本轮有意排除；不是测试失败或全量实验。
- `.venv/bin/python -B docs/step5a/poc/verify.py`：通过调用位置、词法分支、walrus、断言位置、解析失败、Spoon运行时失败及 SDK 工具调用/未知ID拒绝检查。初次回调探针误用 `RunContextWrapper`，按已安装API改为 `ToolContext`；同步回调尝试未及时返回后中止，最终仅证明异步回调通过，未诊断同步等待原因。
- Java 临时投影使用合成文件名，仅验证准入/物化；由于 JVM 启动失败，文件命名和模型构建兼容性均未验证。后续应使用审核逻辑路径到原类文件名的安全映射。
- PoC Python 语法检查、交付目录逐文件尾随空白/本地链接/JSON 检查及 `git diff --check` 均通过（后者不覆盖未跟踪文件，故另做逐文件检查）。本项目没有独立配置 lint/typecheck，本轮不安装新工具，也不把语法检查宣称为类型检查。

**过程偏差披露：** 旧工具检索子任务越过了预期的“仅工具源码”范围，误读了 `GOLD_EXTRACTOR_VALIDATION.md` 中的旧提取器验证摘要。已立即停止该方向，摘要及指标未纳入组件判断、PoC输入或结果；没有读取该文档背后的参考 Mock、隐藏任务规格或案例评分文件。不能声称整个检索过程完全未接触验证记录。后续检索应使用精确工具源码白名单，排除 audit/validation 资料。所有本轮实际分析输入仍只来自既有 Adapter 的 UnifiedTask。

本轮在报告和 PoC 证据完成后停止，不开发完整 V0.2。
