# Step 5A：Joern、Joern Slice 与 Spoon 组件复用评估

> 研究日期：2026-10-21。本文只使用项目官方仓库、官方文档、官方制品页和官方 issue tracker；本代理没有安装或运行任何组件，也没有读取案例、旧 Stub-Tester 或其他项目数据。因此，除明确标注外，本文全部结论均为**文档/API 确认，尚未实测**。真实案例 PoC 结果应由 Step 5A 总报告单独合并，不能由本文推断。

## 结论

在“不要自行重做 CFG、Def-Use、Backward Slicing 和控制依赖”的约束下，主方案应选 **方案 B：OpenAI Agents SDK + Joern CPG + Joern Slice + 小型工具适配层**，同时保留 Spoon/Python AST 作为定位、降级和结果核对工具。

理由是 Joern 已经为 Java 和 Python 提供统一 CPG，CPG schema 原生包含 CFG、`REACHING_DEF`（DDG）和 CDG；CPGQL 提供调用、控制依赖和 data-flow path 查询；`joern-slice data-flow` 提供跨过程、从 call arguments 向后的 DDG slice，并输出带文件、行、列的 JSON。相反，Spoon core 的成熟能力集中在 Java AST、符号引用和源码位置；`spoon-control-flow` 仍未发布到 Maven Central，`spoon-dataflow` 官方 README 明确称其为 proof of concept，且列出跨过程分析、函数摘要和更多语言构造仍待实现。若以 Spoon/Python AST 作为唯一主基础，要满足本轮 Program Analysis 要求仍需自行建设较多程序分析能力，这与复用目标冲突。[Joern CPG schema](https://cpg.joern.io/)、[Joern Slice](https://github.com/joernio/joern/blob/master/joern-cli/JOERN_SLICE.md)、[Spoon control-flow README](https://github.com/INRIA/spoon/blob/master/spoon-control-flow/README.md)、[Spoon dataflow README](https://github.com/INRIA/spoon/blob/master/spoon-dataflow/README.md)

该推荐带有明确门槛：真实 Java/Python 案例必须证明 Joern 能在缺依赖、Masked Test 和最小输入快照中产生比 AST 基线更完整、可解释的事实；若不能通过门槛，则只保留方案 A 的定位能力并明确缩小范围，或重新选型；不能把 A 宣称为满足完整程序分析需求。Joern 不能直接完成“从断言目标值推导可满足执行路径和 Mock 响应”，这部分仍是 Mock Spec 的领域逻辑。

## 版本、维护、许可证与供应链

| 组件 | 截止研究日的证据 | 许可证 | 采用判断 |
|---|---|---|---|
| Joern | 官方 releases 在 2026-10-17 发布 `v4.0.658`，仓库说明默认每天自动发布一次；仓库约 3.6k stars。Linux x86_64 发行 zip 约 1.67 GB。[releases](https://github.com/joernio/joern/releases)、[repository](https://github.com/joernio/joern) | Apache-2.0；官方 LICENSE 包含专利授权与 NOTICE/归属要求。[LICENSE](https://github.com/joernio/joern/blob/master/LICENSE) | 维护活跃，无 12 个月不活跃风险；但高频发布意味着必须固定版本和 digest，不能跟随 `latest`。官方 issue 记录过同一 release asset 被重新上传、digest 改变的事件，内部制品镜像和首次验收后的 SHA-256 固定是必要缓解措施。[issue #6407](https://github.com/joernio/joern/issues/6407) |
| Spoon core | 官方 GitHub release 将 `11.5.0` 标为 latest stable；Maven Central 还持续发布 `11.5.1-beta-*`，说明项目仍活跃。仓库约 2k stars。[releases](https://github.com/INRIA/spoon/releases)、[Maven Central namespace](https://central.sonatype.com/namespace/fr.inria.gforge.spoon)、[repository](https://github.com/INRIA/spoon) | 双许可证 MIT / CeCILL-C，可按 MIT 使用；保留版权与许可文本。[official README](https://github.com/INRIA/spoon#license)、[MIT text](https://github.com/INRIA/spoon/blob/master/LICENSE-MIT.txt) | 主线活跃，适合作为 Java AST 基础。后续评估可选择稳定版并固定版本；本机11.2.1不得未经兼容性验收直接升级。Spoon 的安全策略只支持 latest major，旧 major 不回补安全修复。[SECURITY.md](https://github.com/INRIA/spoon/blob/master/SECURITY.md) |
| spoon-control-flow | 位于 Spoon 主仓库，但 POM 仍是 `0.0.2-SNAPSHOT`；官方 README 明确说明未发布到 Maven Central，需从源码构建。[POM](https://github.com/INRIA/spoon/blob/master/spoon-control-flow/pom.xml)、[README](https://github.com/INRIA/spoon/blob/master/spoon-control-flow/README.md) | 模块 POM 和源码头为 MIT。[POM](https://github.com/INRIA/spoon/blob/master/spoon-control-flow/pom.xml)、[ControlFlowBuilder.java](https://github.com/INRIA/spoon/blob/master/spoon-control-flow/src/main/java/fr/inria/controlflow/ControlFlowBuilder.java) | 可用于受控 PoC 或复用既有代码，不宜把未发布 snapshot 当成稳定外部依赖；如采用应固定 commit，并由本项目构建可重复制品。 |
| spoon-dataflow | 官方说明它基于 Z3，可做符号执行、控制流和引用别名，但同时明确称其为 proof of concept，并列出跨过程分析/函数摘要、循环和语言构造支持仍待完善。[README](https://github.com/INRIA/spoon/blob/master/spoon-dataflow/README.md) | 随 Spoon 仓库分发；最终采用前仍需对该子模块做一次文件级许可证清点。[repository](https://github.com/INRIA/spoon) | 不作为 Scenario Agent 的生产级 data-flow 或 solver 基础；最多作为研究参考。 |
| cpgqls-client-python | 官方 Python 客户端仓库仅约 40 次提交、约 47 stars；PyPI 最新发布为 `0.0.9`（2021-08-27），已超过 5 年无发布。[GitHub](https://github.com/joernio/cpgqls-client-python)、[PyPI](https://pypi.org/project/cpgqls-client/) | Apache-2.0。[GitHub](https://github.com/joernio/cpgqls-client-python) | 不建议新增为运行时依赖。优先用固定命令模板调用 Joern CLI；如确需长驻服务，直接实现极小的 HTTP 客户端并限制查询模板。 |

官方页面未提供 Joern CLI 或 Maven 制品的可靠周下载量，因此本文不虚构下载统计。stars 只作为采用规模的弱证据，维护活跃度以官方 release/commit 活动为主。GitHub security 页面在研究时显示 Joern 无 SECURITY.md 且无已发布 advisory；Spoon 有安全策略且页面显示无已发布 advisory。这只能说明公开披露状态，不能证明“没有漏洞”。[Joern security](https://github.com/joernio/joern/security)、[Spoon security](https://github.com/INRIA/spoon/security)

项目本身的许可证未在本代理的 external-only 范围内核对。Apache-2.0 和 MIT 通常允许研究与内部集成，但最终发布前仍需由项目负责人核对 NOTICE、归属和再分发要求。

## A. Code Understanding

| 能力 | Joern / Joern Slice | Spoon |
|---|---|---|
| 定位测试断言 | 没有测试框架专用 assertion API；可把 `assert*`、Hamcrest/AssertJ 调用建模为 `CALL` 并用 `cpg.call.name(...)`、argument、AST parent 等 CPGQL 查询定位。Java/Python 的调用 AST 均有官方建模说明。[CPGQL call steps](https://docs.joern.io/cpgql/complex-steps/)、[Java calls](https://docs.joern.io/frontends/java/#calls)、[Python calls](https://docs.joern.io/frontends/python/#calls) | Java `assert` 是 `CtAssert`，JUnit/AssertJ 调用是 `CtInvocation`；可通过 `CtModel.getElements(new TypeFilter<>(CtInvocation.class))` 或 processor/visitor 定位。断言框架语义仍需 Mock Spec 自己登记。[first analysis](https://spoon.gforge.inria.fr/first_analysis_processor.html)、[CtInvocation API](https://spoon.gforge.inria.fr/mvnsites/spoon-core/apidocs/spoon/reflect/code/CtInvocation.html)、[official TypeFilter usage](https://github.com/INRIA/spoon/blob/master/src/test/java/spoon/test/architecture/SpoonArchitectureEnforcerTest.java) |
| 定位目标方法与依赖调用 | `cpg.method`、`cpg.call`、`callee`、`caller`、`callIn`、`inCall` 可定位方法和调用关系；Java/Python 共用 CPGQL，但接收者/参数编号模型有语言差异。[complex steps](https://docs.joern.io/cpgql/complex-steps/)、[Java calls](https://docs.joern.io/frontends/java/#calls)、[Python calls](https://docs.joern.io/frontends/python/#calls) | `CtInvocation.getExecutable()`、`getExecutableDeclaration()` 和 AST 父子关系可定位 Java 调用；缺源码/字节码时 declaration 可能为 `null`，不能把 unresolved 当作“无依赖”。[launcher/no-classpath semantics](https://spoon.gforge.inria.fr/launcher.html)、[CtInvocation API](https://spoon.gforge.inria.fr/mvnsites/spoon-core/apidocs/spoon/reflect/code/CtInvocation.html) |
| Java / Python | 官方成熟度表将 Java 标为 Very High、Python 标为 High，并分别提供 `javasrc2cpg`、`pysrc2cpg`。[overview](https://docs.joern.io/)、[frontends](https://docs.joern.io/frontends/) | Spoon 只分析 Java；Python 需要独立的 Python `ast` 基线，不共享 Spoon 符号模型。[Spoon README](https://github.com/INRIA/spoon) |
| 确定性源码位置 | CPG node 具有 `lineNumber`/`columnNumber`，查询结果和 slice schema 都能输出；但两者是 optional，缺失时必须显式返回 `location_status=missing`。[data-flow query example](https://docs.joern.io/cpgql/data-flow-steps/)、[slice schema](https://github.com/joernio/joern/blob/master/joern-cli/JOERN_SLICE.md#schema) | 每个源元素可由 `getPosition()` 返回文件、起止 offset、行列；先检查 `isValidPosition()`。对于输入源解析出的有效位置可形成稳定 location key。[SourcePosition](https://spoon.gforge.inria.fr/mvnsites/spoon-core/apidocs/spoon/reflect/cu/SourcePosition.html)、[SourcePositionHolder](https://spoon.gforge.inria.fr/mvnsites/spoon-core/apidocs/spoon/reflect/cu/SourcePositionHolder.html) |

判断：两者都能复用“定位”能力。Spoon 对 Java AST 和精确 offset 更直接；Joern 的优势是 Java/Python 共用查询模型和后续图分析。断言识别、目标方法选择和“哪些调用属于外部依赖”均是 Mock 专用分类，不能外包给通用分析器。

## B. Program Analysis

| 能力 | Joern / Joern Slice | Spoon |
|---|---|---|
| CFG | CPG schema 用 `CFG` edge 表示控制流，CPGQL 有 `dominates` / `postDominates`，也可通过 `dotCfg` 或 `joern-export` 导出。[CPG schema](https://cpg.joern.io/)、[control-flow steps](https://docs.joern.io/cpgql/control-flow-steps/)、[export](https://docs.joern.io/export/) | `spoon-control-flow` 的真实 API 是 `new ControlFlowBuilder().build(CtElement)`，结果为基于 JGraphT 的 `ControlFlowGraph`；模块未发布到 Maven Central。[README](https://github.com/INRIA/spoon/blob/master/spoon-control-flow/README.md)、[ControlFlowBuilder.java](https://github.com/INRIA/spoon/blob/master/spoon-control-flow/src/main/java/fr/inria/controlflow/ControlFlowBuilder.java)、[ControlFlowGraph.java](https://github.com/INRIA/spoon/blob/master/spoon-control-flow/src/main/java/fr/inria/controlflow/ControlFlowGraph.java) |
| Def-Use / Data Flow | `REACHING_DEF` edge 构成 DDG；`reachableBy` / `reachableByFlows` 从 sink 回查 source，其中 `reachableByFlows` 返回具体 data-flow path。[CPG schema](https://cpg.joern.io/)、[data-flow steps](https://docs.joern.io/cpgql/data-flow-steps/) | `spoon-control-flow` README 只把 `InitializedVariables` 作为 data-flow use case；没有官方文档证明它提供通用 Def-Use/DDG API。`spoon-dataflow` 能做基于 Z3 的符号执行，但官方自称 PoC，并承认 unknown function 会重置参数值。[control-flow README](https://github.com/INRIA/spoon/blob/master/spoon-control-flow/README.md)、[dataflow README](https://github.com/INRIA/spoon/blob/master/spoon-dataflow/README.md) |
| Backward Slicing | `joern-slice data-flow` 是从 call arguments 开始的 backward DDG slice；跨过程，默认深度 20，可按文件、方法、sink code 过滤，输出 JSON graph。[Joern Slice](https://github.com/joernio/joern/blob/master/joern-cli/JOERN_SLICE.md) | 未发现 Spoon 官方提供通用 backward slicer。要实现该能力需在 CFG/变量引用之上自行做 reaching-def 和 slice traversal，不能算“直接复用”。[spoon-control-flow README](https://github.com/INRIA/spoon/blob/master/spoon-control-flow/README.md) |
| 控制依赖 / 分支嵌套 | CPG 原生 `CDG` edge；`controlledBy` 返回节点递归依赖的控制条件。AST 查询还提供 `condition`、`whenTrue`、`whenFalse`，可表达结构化嵌套。[CPG schema](https://cpg.joern.io/)、[control-flow steps](https://docs.joern.io/cpgql/control-flow-steps/)、[syntax-tree queries](https://docs.joern.io/c-syntaxtree/) | `CtIf`/`CtLoop`/parent chain 能表达结构化 AST 嵌套；`spoon-control-flow` 能建 CFG，但没有官方 CDG API。异常流支持被官方标为 limited，`try/finally + return` 默认拒绝建模，放宽选项会产生 partially incorrect graph。[control-flow README](https://github.com/INRIA/spoon/blob/master/spoon-control-flow/README.md) |

Joern 的 `reachableByFlows` 返回的是数据流路径，不是“所有可行执行路径”；`controlledBy` 返回控制依赖，也不是路径条件求解器。`joern-slice` 的 seed 限定为 call arguments，不能假定任意 return/branch node 都能直接作为 slice criterion。[data-flow steps](https://docs.joern.io/cpgql/data-flow-steps/)、[Joern Slice](https://github.com/joernio/joern/blob/master/joern-cli/JOERN_SLICE.md)

## C. Scenario Inference

### 可直接复用

Joern 可直接提供以下候选事实：

1. assertion、target method、dependency call 的 `METHOD`/`CALL`/argument 节点和源码位置；
2. 从 dependency response variable 到比较表达式或 assertion argument 的 DDG path；
3. 目标语句的递归 `controlledBy` 条件、CFG dominance/post-dominance 和 AST `whenTrue`/`whenFalse`；
4. `joern-slice` 的 JSON nodes/edges，作为与语言无关的 slice input；
5. 缺依赖环境下的 robust parsing 和 type recovery/dummy type，但只能作为“不完整分析仍可产出候选”的能力，不能视为解析正确性的证明。[Joern overview](https://docs.joern.io/)、[Java frontend options](https://docs.joern.io/frontends/java/)、[Python frontend options](https://docs.joern.io/frontends/python/)

Spoon 可直接提供 Java 侧的 AST 元素类型、调用参数、父子/嵌套关系、符号引用和精确 source offsets；当 Joern location 或 call resolution 不完整时，它适合做轻量核对或降级抽取。[Spoon README](https://github.com/INRIA/spoon)、[SourcePosition](https://spoon.gforge.inria.fr/mvnsites/spoon-core/apidocs/spoon/reflect/cu/SourcePosition.html)

### Mock Spec 必须自行设计

以下能力不是三个组件现成提供的：

1. **Assertion semantics registry**：识别 JUnit/AssertJ/Hamcrest/Python unittest/pytest 的 expected、actual、matcher 语义。
2. **Scenario target selection**：从目标结果选择切片 seed，区分返回值、状态变化、抛异常和交互验证。
3. **External-response boundary**：判断调用是 mockable dependency、纯本地 helper、框架调用还是不可控环境输入。
4. **Path-condition normalization**：把 CDG/AST 条件转成 `AND`、`OR`、取反、短路、switch/case、异常路径和替代路径的显式布尔结构；保留 unknown，而不是猜测。
5. **Feasibility and value constraints**：Joern 不求解路径可满足性。若后续引入 Z3，也必须先定义受限表达式 IR、类型语义、null/exception 语义和超时策略；不能直接把 arbitrary source expression 交给 solver。
6. **Mock response provenance**：把条件中的变量追溯到某次依赖调用的返回值/异常/回调，并处理别名、多次调用、集合元素和字段更新。
7. **Evidence schema**：每条事实记录来源工具、工具版本、source location、node id、边类型、confidence、missing reason 和 input `source_id`。
8. **信任边界**：仅接收 UnifiedTask 暴露的审核文件；路径和输出中不得出现未授权仓库文件，更不能读取 reference Mock、评分或隐藏规格。

因此，Joern 应作为“事实生产器”，不应直接充当推理 Agent。Scenario Agent 应在受限事实 schema 上推理，而不是获得任意 CPGQL 或文件系统访问权。

## D. Agent Integration

### Joern

推荐调用路径是短生命周期 CLI：

```text
approved UnifiedTask files
  -> isolated read-only input directory
  -> joern-parse / language frontend -> cpg.bin in isolated output directory
  -> fixed Joern script and/or joern-slice data-flow -> JSON facts
  -> schema validator + path redaction
  -> OpenAI Agents SDK Function Tool result
```

真实 API/入口：

- `joern-parse <input> --language JAVASRC|PYTHONSRC` 生成完整 CPG；Python frontend 可显式传 `--venvDir`，也可设置 ignore paths。[frontends](https://docs.joern.io/frontends/)
- `joern --script analysis.sc --param key=value` 支持非交互脚本；脚本可写固定的 CPGQL 查询和 JSON 输出。[interpreter](https://docs.joern.io/interpreter/)
- `joern-slice data-flow <cpg-or-source> -o <output> --file-filter ... --method-name-filter ... --sink-filter ...` 输出 JSON graph。[Joern Slice](https://github.com/joernio/joern/blob/master/joern-cli/JOERN_SLICE.md)
- `joern --server` 暴露 HTTP `/query`、`/query-sync` 和 WebSocket；官方同时明确声明 server 只是远程 interpreter，**不提供 sandbox，不能作为安全边界**。[server docs](https://docs.joern.io/server/)

不把 server 暴露给 Agent；也不允许 Agent 生成任意 Scala/CPGQL。Function Tool 只接收枚举化操作（例如 `locate_calls`、`slice_to_assertion`、`control_conditions`）和已验证的 source ids，由适配层选择固定脚本。源目录以只读方式挂载，Joern workspace、CPG 和 JSON 都写入一次性临时目录；容器/进程禁网、限 CPU/内存/时间，不挂载旧 Stub-Tester 仓库。X2CPG 的 raw file content 默认不写入 FILE node，只有 `--enable-file-content` 才开启；本项目不应开启。[X2CPG options](https://docs.joern.io/frontends/x2cpg/)

资源方面，官方最新 Linux x86_64 分发 zip 约 1.67 GB；运行需要 JDK 19（官方安装页说明更新 JDK 可能可用但未充分测试）。官方还说明 `importCode` 会启动第二个 JVM，并继承相同最大堆，峰值内存可能接近两份 `-Xmx`。大型 CPG 的 RAM 需求可能很高；小型隔离案例的启动时间、峰值 RSS、CPG 大小尚无官方可迁移基准，必须在 PoC 实测。[release assets](https://github.com/joernio/joern/releases)、[installation](https://docs.joern.io/installation/)

### Spoon

Spoon core 是 Java library/CLI；真实入口包括 `Launcher.parseClass(...)`、`Launcher.addInputResource(...)`、`launcher.buildModel()` 和 model/visitor/query API。Python Agent 需要调用一个固定 Java wrapper，由 wrapper 输出 JSON；不能把 Java AST 对象直接暴露给 Agent。[Spoon README](https://github.com/INRIA/spoon)、[library usage](https://spoon.gforge.inria.fr/launcher.html)、[command line](https://spoon.gforge.inria.fr/command_line.html)

Spoon 11.x 运行需要 JDK 17+，可消费更旧 Java 源码。`spoon-core` 可直接从 Maven Central 固定稳定版；`spoon-control-flow` 需额外从源码固定 commit 构建。官方没有为小型输入给出启动时间或峰值内存保证，PoC 需要测量。[Spoon README](https://github.com/INRIA/spoon)、[control-flow POM](https://github.com/INRIA/spoon/blob/master/spoon-control-flow/pom.xml)

Spoon `noclasspath` 可在缺 classpath 时建模，但 unresolved reference 的 declaration/type declaration 会是 `null`；普通 compilation error 在该模式只记消息。`setIgnoreSyntaxErrors(true)` 会从 compilation batch 移除有语法/JLS 错误的文件。适配层必须把这些诊断和 skipped files 作为 `incomplete` 返回，不能用空结果表示“没有依赖调用”。[library usage](https://spoon.gforge.inria.fr/launcher.html)、[Environment API](https://spoon.gforge.inria.fr/mvnsites/spoon-core/apidocs/spoon/compiler/Environment.html)

## E. Engineering Risks

| 风险 | 证据 | 缓解 |
|---|---|---|
| Joern release churn / reproducibility | 官方每天自动 release；`v4.0.653` 曾出现同 tag asset digest 改变的 issue。[repository](https://github.com/joernio/joern)、[issue #6407](https://github.com/joernio/joern/issues/6407) | 固定 `v4.0.658` 或 PoC 验证版本、首次验收后固定 SHA-256、保存内部只读制品；升级单独跑回归基准。 |
| Joern 大体积和 JVM 峰值 | 发行 zip 约 1.67 GB；`importCode` 会启动第二 JVM并复制 max heap 设置。[releases](https://github.com/joernio/joern/releases)、[installation](https://docs.joern.io/installation/) | 与旧实验环境分离成容器/独立 worker；不把 Joern/JDK 注入旧 Stub-Tester virtualenv；为单任务设置资源限额。 |
| server 是代码执行面 | Joern server 接收 Scala interpreter query，官方明确不做 sandbox。[server docs](https://docs.joern.io/server/) | 不向 Agent 暴露 server/query string；只允许固定脚本的 CLI wrapper。若长驻服务确有必要，置于无网、无宿主源码、非特权容器内，并做参数白名单。 |
| Java/Python frontend 对缺类型的过近似 | Joern 强调能解析缺失代码，同时 Java/Python frontend 提供 type recovery、dummy types 和 venv 配置；这意味着“能建图”不等于“调用解析正确”。[overview](https://docs.joern.io/)、[Java frontend](https://docs.joern.io/frontends/java/)、[Python frontend](https://docs.joern.io/frontends/python/) | 所有 unresolved/dummy type 都降置信度；事实中保留 resolution status；依赖类型缺失时不声称完整 data flow。 |
| Joern data-flow 有已知前端/构造边界 | 官方 issue tracker存在 Java catch 分支 data-flow 缺失、Python attribute flow 缺失等报告。[Java issue #2109](https://github.com/joernio/joern/issues/2109)、[open issues](https://github.com/joernio/joern/issues) | 用真实审核案例建立 golden facts；失败显式返回 unsupported/missing edge；用 AST 基线核对关键调用和条件是否被图漏掉。 |
| Slice 并非场景求解 | `joern-slice` 是从 call argument 开始的 backward DDG slice；深度默认 20且文档提示昂贵。[Joern Slice](https://github.com/joernio/joern/blob/master/joern-cli/JOERN_SLICE.md) | 先过滤文件/方法/sink；把 slice 当候选相关性图，再由 Mock Spec 组合 CDG/CFG 并求值。超深/超时返回明确缺失原因。 |
| Spoon CFG 模块成熟度 | 未上 Central、版本 `0.0.2-SNAPSHOT`；异常流是 limited，部分 try/finally 情况会拒绝或生成部分错误图。[README](https://github.com/INRIA/spoon/blob/master/spoon-control-flow/README.md)、[POM](https://github.com/INRIA/spoon/blob/master/spoon-control-flow/pom.xml) | 固定 commit；只把它用于 AST 基线可覆盖的结构化控制流；异常路径标 unsupported；不将其作为主切片引擎。 |
| Spoon no-classpath 的假阴性 | unresolved declaration 返回 `null`；ignore syntax errors 可移除文件。[library usage](https://spoon.gforge.inria.fr/launcher.html)、[Environment API](https://spoon.gforge.inria.fr/mvnsites/spoon-core/apidocs/spoon/compiler/Environment.html) | 严格诊断 schema；被跳过文件、无效位置和 unresolved symbol 都令结果 incomplete，禁止静默成功。 |
| Ground Truth / 未授权源码泄露 | Joern 与 Spoon 都接收目录路径；Joern server query还能执行 `importCode("/path")`。[server docs](https://docs.joern.io/server/)、[Spoon library usage](https://spoon.gforge.inria.fr/launcher.html) | Adapter 只按审核清单复制允许文件到新临时目录；工具进程只能看该目录；不挂载完整旧仓库；拒绝绝对路径、`..`、symlink 和超出 manifest 的 source id；输出路径相对化。 |

## 最小 PoC 验收门槛

本文没有运行 PoC。下面是建议交给真实案例验证器的最小、可判定门槛：

1. 对每个 Java/Python 审核案例，仅在临时目录放入 UnifiedTask 允许的文件，记录确切 manifest；运行结束后证明输入/输出中没有其他仓库路径。
2. AST 基线和 Joern 均输出同一最小事实 schema：`fact_kind`、`language`、`source_id`、相对文件、起止位置、code、symbol resolution status、tool/version、evidence edges、missing reason。
3. 必须定位 assertion、目标方法和关键依赖调用；若任一工具失败，返回结构化 `incomplete`，禁止空列表冒充成功。
4. Joern 必须至少在一个真实案例中输出依赖响应到条件/assertion 的 `REACHING_DEF` path，并输出该目标的 `controlledBy` 条件；否则它没有证明相对 AST 方案的价值。
5. 分支组合至少覆盖一个 `AND`/嵌套分支和一个替代路径；验证器按源码人工核对 edge direction、true/false branch 和 source locations。
6. 记录 wall time、冷启动、峰值 RSS、CPG 大小、slice JSON 大小；给出硬上限。当前没有文档证据可替代这些实测。
7. 在删除一个必要类型/模块后重跑，验证 unresolved、dummy type、skipped file 或 missing edge 能被显式报告。
8. Joern 版本和制品 digest 必须固定；CLI wrapper 不能接收任意 CPGQL/Scala 字符串。

建议通过条件：两个案例均满足 1、2、3、7、8，且 Joern 至少满足 4、5；资源上限可接受。若 Joern 只复现 AST 已有的调用/分支定位，或 data-flow 在两个案例均 incomplete，则不采用方案 B，仅保留 A 的定位基线，不宣称已实现完整需求。

## 推荐架构与下一步最小集成任务

推荐架构：

```text
UnifiedTask / source_id allowlist
        |
        v
Isolated Input Materializer  -- rejects absolute path, symlink, unreviewed file
        |
        +--> Joern CLI Backend (primary: CPG + CFG/CDG/DDG + slice JSON)
        |
        +--> Spoon / Python AST Backend (location + AST baseline/fallback)
        |
        v
Fact Normalizer + Diagnostics Gate
        |
        v
Scenario-specific inference (assertion semantics, mock boundary,
path formula, alternatives, unknowns)
        |
        v
Read-only Function Tools exposed to the Scenario Agent
```

下一步只做以下最小集成，不继续完整 V0.2：

1. 定义统一 JSON fact schema 和 `complete/incomplete/unsupported/error` 四态结果。
2. 固定一个 Joern 版本与 digest，在独立容器/worker 中实现三个白名单操作：`locate_calls`、`control_conditions`、`backward_slice`。
3. 实现一个 Spoon/Python AST 基线操作 `ast_facts`，输出相同 location/symbol/branch 字段；Java 先复用已有 Spoon 代码，不引入 `spoon-dataflow`。
4. 对两个审核案例按上述门槛运行隔离 PoC，并人工核对关键 edge 与分支方向。
5. 依据 PoC 结果做一次 go/no-go：Joern 通过则作为主分析后端；未通过则保留 AST 定位基线并重新评估完整需求，不自动自研分析引擎或追加辅助组件。

停止条件：完成上述两案例 PoC、记录资源与缺失信息并作出 go/no-go 后停止；不改冻结 Prompt、不改 Stub-Tester、不实现 Mock Generation、Behavioral Feedback 或完整 Scenario Agent V0.2。
