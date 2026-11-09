# 测试目标导向后向切片与 Mock 生成：有界探索

2026-11-09 更新：[完整 MockBench 上的方案调研](full-benchmark-research.md)回到测试目标导向
后向切片主线，结合165例目标测试盘点重新提出验证设计。下文保留三例历史探索；其中的
后端优先级属于当时建议，不代表当前已经选定 WALA 或符号执行。

## 全量候选切片入口（2026-11-09）

已完成165例：[全量结果与失败归因](../../out/mockbench-study/backward-slice-full-20261109-r3/report.md)。
155例取得候选图、10例构图失败；不是155例语义恢复成功。

`batch_slice.py` 接入完整冻结清单，研究当前源码候选图在哪一步失败。它复用本目录的
Python AST/Spoon 探针，不使用 `goal-pilot` 或 `component-pilot` 的符号解释器。
`criteria.py` 只在 selector 对应的测试函数/方法里提取观察；没有普通观察时，保留
`candidate_event`，不把调用或 `expected_*` 命名推定为已知 oracle。
`source_selection.py` 从可见 import、动态加载字面路径和 Java 同包类型名选源码，
最多再扩一层，所有读取经过 `FrozenCase.read_source`，不扫描或复制整个源码树。

图边指向受影响节点，按每个准则反向闭包。Python 批处理关闭普通语句前驱边，并连接
赋值语句到定义节点；局部数据候选不跨嵌套函数作用域。占位方法的内部节点与调用效果
不参与图传播。未知调用节点只是待分类边界，不等于已确认 Mock；被排除方法的可能
正常返回、抛异常、写状态仍是 unknown。历史探针默认配置保留用于旧材料复现。

默认最多8个文件（含测试）、200000字节选定源码、每例后端30秒、Java堆768MiB。
实际产品文件读取尝试最多7次，单个完整文件可能使读取字节超过预算一次，随后停止；
不截断源码。适配器读取不计入后端时限，峰值内存没有测量。后端输出、图节点和边另有
硬上限，失败不自动扩大预算重试。所有案例含失败/超时仍保留分母。
已核验但为空的支持模块按原Adapter的拒绝结果记录 `source_rejections` 并省略，
继续其他候选；身份/哈希拒绝不被吞掉。省略空模块不代表其导入绑定已经解析。

```bash
PYTHONPATH=src .venv/bin/python experiments/backward-slicing/batch_slice.py \
  --inventory out/mockbench-study/intake-20261109-inventory.json \
  --output /tmp/mockbench-backward-slice
```

输出目录必须不存在。可重复 `--case-id` 选择探针，其余案例记 `not_selected`。
`run-manifest.json` 固定实现摘要、输入清单与配置；逐例JSON和 `results.json` 保存
目标定位、准则身份、选源证据、源码摘要、图、每准则闭包及失败阶段，`summary.json`
汇总。`partial` 只代表取得候选图，`with_product_candidate` 只代表闭包中出现产品源码
节点；二者都不是正确切片、MUT绑定或Mock恢复成功率。数据/控制边保留精度标签，
heap、异常控制流、动态派发、真实Mock边界和路径可行性都需要后续独立验证。

回归包括作用域隔离、断言种子定位、未知占位边界、逐准则归因、预算和超时留证：

```bash
.venv/bin/python -m pytest -q experiments/backward-slicing/test_criteria.py \
  experiments/backward-slicing/test_source_selection.py \
  experiments/backward-slicing/test_batch_slice.py \
  experiments/backward-slicing/test_python_probe.py \
  experiments/backward-slicing/test_java_probe.py
```

以下为2026-11-04历史三例探索。

2026-11-04。**现有工具足以提供关键分析基础，但还没有一个直接满足本项目全部要求的即插即用方案。** 本轮真实案例显示：跨过程源码证据能自动取得；完整 heap/异常/调用上下文仍有缺口；Agent 看到更多关系后仍会误读组合语义。建议保留“程序分析取证、Agent 解释与生成”的分工，下一步优先验证 Java WALA SDG，不立即引入 SMT，也不把本轮近似源码图接入生产。

这是独立研究原型与一次生成对照，不是生产 Scenario Agent 升级。生产包、冻结 V0.1 Prompt、准入记录和既有用户修改均保留。研究问题与预先确定的评价范围见 [protocol.md](protocol.md)，上游实现调查见 [tool-survey.md](tool-survey.md)。

后续材料：[近似路径约束调查](path-constraints.md)与[三组快速实验及路线选择](path-pilot/README.md)。后者新增真实Java执行、六次独立生成和Flask路径见证；结果支持继续研究目标值关系与局部检查，但没有证明摘要提高生成质量。下文保留首次实验的历史范围与结论，其中“Java未执行”指首次实验阶段。

进一步实验：[从测试目标组合值关系](goal-pilot/README.md)，自动连接CloudFoundry的目标、跨方法返回构造与Mock响应。单任务修订对照为源码组0/2、关系＋诊断组2/2通过；其开发案例范围、合法替代场景和unknown均单独留证。

最新验证：[组件对照、交互敏感性与冻结迁移](component-pilot/README.md)。新八次修订中，源码/仅关系各0/2、仅诊断2/2、关系＋诊断1/2；Jib两个目标均unknown。它修正了对稳定收益的期待，支持研究短诊断接口，并暂停扩写自研解释器。十次真实Java执行与全部负结果均已归档。

## 1. 实际做了什么

- 经现有 Adapter 载入三个已审核真实案例：CloudFoundry、JarFiles、flask_308；保留原 Case ID、masking、普通测试及断言。
- 用固定 Spoon 11.2.1/JDK 和 Python AST 实现源码后向**候选证据图**。图包含节点位置、有向关系、种子、未解析边界与精度标签，不输出 Mock 答案。
- CloudFoundry 使用受限 SourceStore 发现并读取 17 个产品文件，加原 masked test 共 18 个文件。搜索回执为 `incomplete/access_denied`，含 5 个策略排除对象；这里只分析返回的允许文件，**不声称全仓完整发现**。
- 两个全新原生 executor 上下文各一次生成两个案例的 Mock。A 只看 UnifiedTask；B 看同一任务与自动图/图中选中行的原文。未提供人工场景答案、参考 Mock、历史结果或生成后反馈。
- 在断网、只读、资源受限容器中执行 Flask 原测试全部 5 个参数行，以及预先规定的三种交互变异。Java 生成结果做源码审查，未编译执行。

角色配置为 native `executor`（已安装角色模型配置 `gpt-5.6-sol`），工具未返回可核验的实际服务端模型 ID、token 或费用。上下文隔离采用新上下文和读取白名单指令，不是 OS 访问隔离；读文件清单来自子代理自报，未导出完整工具轨迹。见 [生成来源与输入摘要](results/generation-provenance.json)。本次没有额外模型 API 请求。

## 2. 工具实现调查：可用能力与缺口

| 路线 | 实现层面已经存在的能力 | 对本任务的关键缺口 | 本轮状态 |
| --- | --- | --- | --- |
| WALA | SDG、pointer/mod-ref 分析、参数与返回连接、heap/异常依赖；基于 IFDS 的调用/返回匹配切片；Java 源码或字节码前端可选 | 审核输入到 analysis scope/entrypoints、缺失依赖效应摘要、source_id 投影、Mock 交互准则需要适配；`FULL` 不会自动解决未知调用 | 官方源码审计；未实跑，推荐下一阶段 Java 对照 |
| Joern | Java/Python CPG、CFG/CDG/REACHING_DEF、数据流查询和后向 slice | 固定版本的完整 Java 输入构建失败，Python 深切片成本和范围过滤问题；未知调用与场景语义仍需处理 | 复用仓库 Step5B 实测，不算本轮新测量 |
| Soot / FlowDroid | Java IR、调用图、points-to/IFDS 数据传播与摘要机制 | Soot 内置 PDG 不等于现成跨过程 SDG；FlowDroid 的 taint source/sink 不是通用断言/事件控制切片 | 源码/API 调查，未实跑 |
| Spoon / Python AST 原型 | 无需构建完整目标依赖即可提取源码位置、候选调用、局部读写、词法条件和事件 | 没有 sound reaching definitions、post-dominator CDG、heap alias、完整异常流或路径可行性 | 本轮三个真实案例实跑 |

依据：[WALA Slicer 固定版本实现](https://github.com/wala/WALA/blob/v1.8.0/core/src/main/java/com/ibm/wala/ipa/slicer/Slicer.java)、[SDG 实现](https://github.com/wala/WALA/blob/v1.8.0/core/src/main/java/com/ibm/wala/ipa/slicer/SDG.java)、[Soot PDG API](https://www.sable.mcgill.ca/soot/doc/soot/toolkits/graph/pdg/ProgramDependenceGraph.html)、[FlowDroid 上游](https://github.com/secure-software-engineering/FlowDroid)。版本、许可证、前端与具体实现证据集中在工具报告，避免把工具名当能力证明。

Joern 的历史结果仅针对仓库测试的 v4.0.658：Java 成功的构图排除了测试，完整测试输入 DDG 失败；Python 深度20在180秒超时。不能据此断言所有新版本失败，也不能把历史子集成功当作本任务端到端成功。详见 [原实测报告](../../docs/step5b/compare.md)。

## 3. 实际切片结果

新结果和首次失败保留在 [results/](results/)。下表时间为单次进程墙钟，Java 不含一次约1.06秒的 helper 编译；不是多次运行均值。Java heap 上限768MiB，不等于实际 RSS；本轮未测峰值 RSS。

| 案例 | 分析输入 | 最终图 | 单次时间 | 能支持的结论 |
| --- | --- | --- | --- | --- |
| CloudFoundry | 18文件，57,082 bytes，5个断言种子 | 313节点 / 477边，涉及5个文件 | 2.45s | 追到父类创建链、子类 URL 覆写与外部 health/status 调用；仍混有不可行分派候选 |
| JarFiles | 3文件，10,481 bytes，16个自动匹配断言种子 | 90节点 / 142边，仅测试文件 | 1.39s | **未连接到 MUT，失败**；不能据此得出依赖不存在 |
| Flask：B收到的包 | MUT、masked test、允许导入片段；断言和MUT调用事件联合种子 | 16节点 / 20边 | 约0.005s | 展示上下文条件、调用参数与正常完成关系；并未证明由断言自动闭合整条调用链 |
| Flask：生成后准则诊断 | 同输入，自动加入 `pytest.raises` 事件准则 | 31节点 / 39边，其中2条跨函数事件候选边 | 约0.005s | 可把无 `return` 的异常型过程接到测试调用；只代表 `may_execute`，不保证实际异常来自该调用 |

Java 数字见 [probe-metrics.json](results/probe-metrics.json)。Flask 的 B 原包与 [额外异常准则诊断](results/flask/exception-criterion-slice.json) 分开保存，后者没有提供给生成 Agent，也不用于解释 B 的收益。

### CloudFoundry：跨文件证据确实取得，但切片还不够精确

自动图选中了以下源关系：

```text
测试 app / 五个断言
  ← DefaultApplicationFactory.createApplication :70–76
  ← getHealthUrl :147–155
  ← getHealthEndpointPath :196–205
  ← PathMappedEndpoints.getPath(health/status)〔依赖边界〕

getServiceUrl / getManagementBaseUrl
  ← getServiceBaseUrl 的覆写候选
  ← CloudFoundryApplicationFactory :43–49 的 URI 列表与 "http://" 拼接
```

图中有61条 `call_return` 候选边、164条声明/先前写入候选边、25条词法控制边；外部 health/status 调用保留在父类197/201行，不进入依赖实现。返回 `health` 非空走第一分支；否则才可能查询 `status`，两个响应方案都应保留为候选场景。

初版只有30节点/52边，漏了覆写路径，还混入无关 ApplicationRegistrator 同名方法。根据真实失败补上表达式值包含关系，并按接收者继承关系限制同名候选后，目标链进入图；初始图及指标保留为 `initial-*`。最终仍含 ServletApplicationFactory 这个 sibling subtype，说明尚无运行时接收者敏感性。不能将整个闭包视为唯一合法场景。

源码图还没有 builder 对象、getter/setter 与 constructor field 的完整 heap 连接；静态种子定位采用行号和有限断言模式，不覆盖所有断言框架。**文件命中、候选调用链、sound interprocedural slice 是不同的完成标准。**

### JarFiles：精度修补带来漏报，保留负结果

初版宽泛同名连接曾进入 MUT；接收者过滤修正版只返回测试节点，未恢复 test→MUT 关系。16个行种子来自有限断言匹配，并非完整测试目标枚举。本轮没有把宽泛初版包装成成功，也没有按案例硬编码补边。这个失败说明无 classpath 的类型/调用解析需要更强后端或明确的 unresolved frontier。

### Flask：异常和调用事件需要独立准则

源码 `helpers.py:282–285` 是：查询上下文；非空则调用 app.aborter；随后调用 `_wz_abort`。fallback **不是词法 `else`**：app.aborter 正常返回时仍可能执行，抛异常时才阻断后继。原型把词法 guard 与正常完成后继分开，而不推断任一调用必定抛出。

B实际拿到的是断言与MUT事件联合种子图；其中没有选中的跨函数事件边。这仍能展示局部关系，却不足以证明测试目标自动传播。生成后诊断把 `pytest.raises` 纳入种子，才取得2条 `call_event_candidate`。这直接支持将观察准则分成返回值、异常与交互事件，而不是只对 `return` 切片。

## 4. Agent 生成与行为验证

原始候选：[A](results/generation-a.json)、[B](results/generation-b.json)。候选生成后未修订。注入器只替换10个slot区域，普通测试逐字保留；运行观察器检查真实 MUT 代码与测试绑定，call报告阶段再次检查绑定/代码身份。分析阶段从未执行被分析代码；执行仅发生在独立研究 evaluator。

| Flask 条件 | A：原始任务 | B：任务＋自动证据 |
| --- | --- | --- |
| 原程序5行 | 0通过 / 5失败 | 5通过 / 0失败 |
| 错误 code 实参 | 不计变异检出：原程序已失败 | 5行被生成调用参数断言拒绝 |
| 丢失 kwargs | 不计变异检出 | 2个含kwargs的参数行被生成断言拒绝，另3行通过 |
| 重复 context lookup | 不计变异检出 | 5行被生成调用次数断言拒绝 |

A 尝试 `mocker.patch("flask.helpers._cv_app.get", ...)`，在进入 MUT 前因 `ContextVar.get` 只读而失败。B 替换 `flask.helpers._cv_app`，再设置 `.get.return_value`，因此可执行。B 的错误参数变异失败定位到生成候选第100/106行的 `assert_called_once_with`；重复查询定位到95行。由此可以说：**B这一次生成的测试能通过原程序，并以Mock Assertions检出这三种预定义交互故障。** A的变异失败全部是配置失败，不是kill。

原始运行记录见 [generation-metrics.json](results/generation-metrics.json) 与各 `eval-*/<condition>/execution.json`。8次容器运行共约38.62秒；完整输入在运行后重新通过 Adapter 核验。`valid_execution` 仅表示测试收集、阶段与身份检查有效，不代表 MUT 已被调用或候选正确；A正是有效记录的配置失败。

**这仍不能证明后向切片提高了 Mock 质量。** Flask 两组初始输入本就包含同一局部 MUT；图没有新增“ContextVar方法只读”的证明。修复patch目标可能来自生成随机性，而非切片。每条件仅一次输出，模型实际token与费用未测，也没有等token/检索对照。

Java 还给出了反方向证据：A返回 `/actuator/health`，B返回 `health`，B解释为默认 WebEndpointProperties 会补上 actuator。但父类 `getHealthUrl:151–154` 使用 `getManagementBaseUrl` 后直接拼 `getHealthEndpointPath`，并未调用补充web base path的 `getEndpointsWebPath`。**源码审查因此指出B的路径组合解释有误**；没有执行Java测试，不能报Java通过率或变异分数。

另一个可见问题是A声称 `verify(mock).method(...)` 不约束次数；Mockito这种写法默认一次。未来应分别评价生成代码实际语义与Agent文字解释，不只看其自述。

## 5. 最有希望的技术路线

1. **测试观察准则提取。** 从普通断言、异常上下文、已有交互语义和MUT目标构造多个criterion；缺失Mock Assertions本身不可作为唯一seed。保留可达依赖调用事件，再由Agent判断哪些值得验证。
2. **语言特定分析，统一证据接口。** Java优先试WALA的真实SDG；Spoon负责源定位与不完整项目的降级。Python先明确 import/alias、异常出口和状态边界，再验证固定版本Joern或其他后端，不能从Java成功外推Python。
3. **在Mock边界截断实现，保留未知效应。** 节点至少有 receiver、实参、正常返回、异常返回、可能写集合、解析状态及调用上下文。禁止把隐藏实现中冻结的throw方法体或空方法当真实行为摘要；无目标调用也不能当无副作用。未知heap/异常效应应显式外露。
4. **证据包要能解释组合关系。** 输出 source_id/行号、关系类型、guard极性、callsite context、未解析frontier与遗漏标记；按目标按需查询。CloudFoundry的包约107KB，原始源码仅57KB，说明图可能比代码更贵；不应默认整图塞prompt。
5. **Agent生成完整场景并逐项绑定证据。** 响应值、抛异常、状态变化和交互断言分别记录依据与未知项，允许health直达/status回退等替代场景。调用次数须结合路径、循环与异常条件，不能从静态调用点数量直接推定。
6. **执行反馈验证，避免只优化过测。** 保留原断言与输入身份，检查patch位置、MUT不可替换、调用参数与次数，并对合理行为等价变体检查过约束。测试通过、故障检出和错误拒绝率分开报告。

不建议继续将本轮候选图扩建成自研完整SDG引擎。下一阶段最能改变决策的工作是：在同一审核Java案例上跑通真正数据/控制/heap/异常切片，验证它比源码检索多提供了哪些可靠关系。

## 6. 下一步正式研究

- 首先增加需要跨文件转换、别名/容器写入、异常后继、循环次数和void副作用的已审核案例。现有3例不足以支持泛化；新增案例仍遵循内容审核，不自动以哈希批准。
- 至少比较：初始任务；同预算自由源码检索；相同选中源码无图；数据切片；数据＋控制/异常/heap切片。最后两个对照才能区分“多看源码”与“关系结构”的贡献。
- 固定模型与token/工具/时间预算，按任务配对、多次独立生成。配置和试验次数先确定；按仓库分层留出评价，不在用于修原型的三个案例上宣称泛化。
- 分别统计完整原测试通过率、可执行候选率、预定义交互变异检出、合法替代行为被误拒绝率、证据覆盖/错误关系率、未解析比例和总成本。每个变异先要求候选通过原程序，单列编译、配置和执行失败。
- WALA准入门槛：完整测试入口与criterion可定位；跨方法参数/返回、控制、heap及异常有可查边；Mock边界未知效应不被丢弃；source投影不越权。任一项失败就记录具体缺口，不以缩小输入替代成功。
- 仅当受控实验表明Agent在充分证据下仍稳定犯路径一致性错误，再评估符号执行/SMT。当前结果更直接暴露的是准则选择、调用解析、证据体量及Agent组合解释问题。

本轮结束状态：调查、三个真实输入的候选图、两组一次生成、Flask八次执行及负结果归档完成；完整SDG后端与统计性质量提升仍未验证。

## 7. 复现与工程验证

环境使用现有项目 `.venv`、固定Spoon/JDK与已有Docker镜像；不添加项目依赖、不下载运行时、不重算旧依赖树哈希。脚本入口：

```bash
# 只读审核加载和图构建；旧根由本机传入
PYTHONPATH=src .venv/bin/python experiments/backward-slicing/prepare.py --old-root /path/to/Stub-Tester
PYTHONPATH=src .venv/bin/python experiments/backward-slicing/run_probes.py
PYTHONPATH=src .venv/bin/python experiments/backward-slicing/build_packets.py

# 已有候选不重新生成；evaluate必须使用全新eval输出目录，存在则拒绝覆盖
PYTHONPATH=src .venv/bin/python experiments/backward-slicing/evaluate.py --old-root /path/to/Stub-Tester

PYTHONPATH=src .venv/bin/python -m pytest -q -p no:cacheprovider experiments/backward-slicing/test_java_probe.py experiments/backward-slicing/test_python_probe.py experiments/backward-slicing/test_evaluate.py
PYTHONPATH=src .venv/bin/python -m ruff check experiments/backward-slicing
```

上述前3条会刷新当前图产物；已留证的生成输入有摘要，复现时使用独立checkout保护本轮记录。原生Agent单次输出无法靠Python命令精确重现，条件和输入身份见provenance。Docker命令、镜像ID、运行时锁、候选与evaluator摘要均保留在results；`command.json`含本机挂载路径，仅作本机审计。

定向回归5项通过，覆盖跨文件继承、边界、重赋值、正常后继与异常、slot外保持；Ruff、Java编译与diff检查通过。这些是工程验证，不是额外的真实任务成功数。
