# 条件化后向分析规格：验收案例与判据

本文为[方法决策](../../docs/adr/0002-mock-oriented-backward-evidence.md)提供可执行验收材料。它不是完整数据集的人工答案，也不把历史分析输出当作真值。真实案例只记录已有可见证据能够支持的判定；缺少依据的关系明确留为待核验或未知。补充的合成反例用于隔离机制，不能替代真实案例验收。

## 验收对象

每个原始观察分别验收。分析结果至少应能投影为以下三类记录：

1. **共同约束候选**：约束内容、适用边界、跨分支成立依据，以及是否仍可能存在未探索分支。只有具有跨路径依据的要求才能提升为共同约束。
2. **条件化分支规格候选**：从任务入口到原始观察的目标相关完整解释，包括分支条件及方向、依赖调用身份、参数／返回／异常／状态用途、交互顺序和原始表达式。
3. **证据与覆盖记录**：每条解释的完整性、成立依据、具体实现依据、源码位置、假设、阻塞性未知、已排除反例、未展开区域与预算截断。

状态定义唯一来自[分析规格第 5 节](../../docs/backward-analysis-spec.md#5-证据与消费状态)，按三个正交维度记录：
`explanation=complete|partial`、`support=supported|conditional|unresolved|refuted`、
`realization=grounded|conditional_only|blocked`。本文不另设简化状态，也不把历史 relation modality 机械映射到这三轴。
特别地，消费者愿意接受某个假设不会自动提高 support 或 realization；若该假设的真假会改变目标行为而尚无依据，
相应约束仍为 `conditional` 或 `unresolved`，并在无法落实具体行为时保持 `conditional_only` 或 `blocked`。
`supported` 不代表已枚举全部合法路径，也不证明具体 Mock 唯一。若路径集合分母未知，全局覆盖只能列出已探索、未展开和截断项，不能报告“全部路径覆盖率”。

## 真实案例验收矩阵

下表中的“必须恢复／拒绝／保留”是新规格的验收判据；“已有证据”说明判据来自何处。历史结果中标为 `not_evaluated` 的关系仍需按此判据重新评阅。

| 案例与原始观察 | 必须恢复 | 必须拒绝 | 必须保留的未知 | 已有证据与当前结论 |
| --- | --- | --- | --- | --- |
| `flask_301`，`criterion:0/1/2`（测试第 92／93／94 行值观察） | 三个观察分别绑定真实产品函数；返回点 181、184 及分支 180 只能按各自分支方向形成候选解释；产品返回到测试使用的值链必须具有实际调用／返回身份 | 把函数跨度 138 当成内部返回边；把两个返回点与条件无差别合并成一条路径；因三个观察最终值相同而合并观察身份 | 两个返回分支是否均可行、依赖调用的具体响应条件、尚未建模的异常和状态影响 | [r7 真实结果](../../out/mockbench-study/entry-repair-20261111/python-preserved-resume-r7/results.json)保留 3 个观察、两个返回点和分支候选，但其精度字段明确写有路径可行性未评阅。因此当前只能作为分支构造输入，不能直接赋予 `support=supported`。 |
| `langflow_186`，`criterion:0`（测试第 130 行值观察） | 将产品返回 502、505、507、508 分成独立候选解释，并为每个返回关联其实际控制条件（501、504、506 等）和调用来源 | 将四个返回拼成一条路径；仅因都到达同一观察就宣布它们都是合法替代；把函数跨度 487 解释为返回值边 | 各分支条件是否同时／分别可满足、是否存在未发现返回解释、动态环境与外部依赖语义 | [r7 真实结果](../../out/mockbench-study/entry-repair-20261111/python-preserved-resume-r7/results.json)证明多个返回候选确实存在，同时明确路径可行性未评阅。该案例验收“多解释分离与覆盖披露”，不预置四条均合法。 |
| `crawl4ai_448`，`criterion:0` 异常观察 58，`criterion:1/2/3/4` 值观察 63／65／70／75 | 对观察 65，分别保留产品返回 38、52 及与它们相关的条件（当前候选含 25）；对异常观察 58，若主张重抛或异常依赖，必须给出连续的调用—异常出口—处理／重抛—观察链 | 把后续或正常返回关系投影到异常观察；把文件系统相关状态仅凭名称猜成确定效果；把所有产品体调用未知都算作正向关系 | 63／70／75 的文件状态来源和区域别名；异常类型兼容；外部文件系统与不可见调用效果；返回分支的可行性 | [r7 真实结果](../../out/mockbench-study/entry-repair-20261111/python-relationship-resume-r7/results.json)保留返回 38／52、控制条件 25 和观察级未知；[代表门禁审计](../../out/mockbench-study/entry-repair-20261111/verification/representative-gate-audit.json)要求显式保留文件状态与异常缺口。现有候选不是完整规格。 |
| `crawl4ai_449`，`criterion:0/1` 值观察 57／58，`criterion:2` 异常观察 65，`criterion:3` 值观察 67 | 产品写 `src/crawl4ai/async_configs.py:761` 到观察 58 的状态关系必须保留精确字段／对象区域、实际—形式参数链、正常完成条件及观察身份 | 将该正常完成写入连接到 `pytest.raises` 之后的观察 67；不同对象、重绑定对象、不可达写入或被确定覆盖的旧写不得影响观察 | 运行时分派、路径可行性、复杂别名以及异常通道中的状态效果 | [r7 真实结果](../../out/mockbench-study/entry-repair-20261111/python-relationship-resume-r7/results.json)仅向观察 58 发布状态候选，但精度仍声明运行时分派和可行性未评阅；[效果机制 r8](../../out/mockbench-study/entry-repair-20261111/python-effect-resume-r8/summary.json)仅提供 fresh／rebind／overwrite／异常通道的合成反例。真实关系的 support 域仍待独立审查，不能由候选存在或机制测试预授等级。 |
| `hummingbot_416`，`criterion:0` 值观察 50、`criterion:1` 异常观察 52 | 返回 44 到观察 50 的完成值链；异常观察 52 若由产品异常解释，必须保留准确调用身份、异常传播／捕获关系和原始观察 | 原始协程未执行却连接产品体；仅凭 `pytest.raises` 语法或同名异常宣称类型兼容；把正常返回链复用到异常观察 | 产品调用 40／43／44 的异常类型、可达性与副作用；异常兼容性 | [r7 真实结果](../../out/mockbench-study/entry-repair-20261111/python-relationship-resume-r7/results.json)只给观察 50 发布返回候选，观察 52 保留未知；允许交付条件式异常规格，但不能编造可直接实现的异常行为。 |
| Jib `JarFilesTest#testToJibContainerBuilder_explodedStandard_basicInfo@69ae66df`，`criterion:0..17`（原观察 71–88）、`criterion:18`（原观察 97） | 测试 63 行唯一绑定五参数 `JarFiles.toJibContainerBuilder(...)`；19 个观察身份和所有预算前沿分别保留；只有通过匹配调用／返回与时序验证的值、状态、控制关系才能进入分支规格 | 已被否定的 4 条通用异常关系；共享库路径拼接；将 163 条控制候选整体视为已确认；将 19／19 根映射等同于 19 条语义解释成功 | 163 条控制候选的上下文连续性、getter／phi／间接状态、所有 50k 前沿后的遗漏、未展开堆关系 | [r9 结果](../../out/mockbench-study/entry-repair-20261111/java-targeted-resume-r9/results.json)支持唯一入口绑定和观察保留；[独立 Java 路径审查](../../out/mockbench-study/entry-repair-20261111/verification/java-exception-route-review-r5.md)明确撤回 4 条异常关系并要求重审控制候选。入口事实可验收，行为规格仍未验收。 |
| Hadoop `TestAbfsOutputStream#verifyShortWriteRequest@b612c310`，`criterion:6/7`（均在原测试第 89 行） | 任何产品控制／异常解释必须具有匹配的跨方法调用—返回上下文和从产品事件到原始观察的可实现时序；观察根身份不得因同一行或同一断言而折叠 | `append:400`、`flush:426`、token `get:195`／`update:178` 等后执行事件影响此前第 89 行 `Random` 构造／`nextBytes`；混合 SDG 边跨不匹配上下文拼接 | 尚未通过匹配路径证明的其余控制、异常和正常返回候选；多目标调用歧义和搜索截断 | [独立 Java 路径审查](../../out/mockbench-study/entry-repair-20261111/verification/java-exception-route-review-r5.md)证明至少 8 条异常和 12 条控制关系错误，并将其余关系降为未核验。该案例是强制负门禁：只要时序错误关系仍发布，Java 跨过程规格验收失败。 |

## 补充机制反例

这些反例回答真实案例难以单独隔离的问题。它们只验证机制，不提供真实案例合法路径的分母。

| 机制 | 必须通过的正／负判据 | 现有材料 |
| --- | --- | --- |
| 定义—使用与覆盖 | 直接返回值／字段写入能够到达观察；确定、可达且与旧值无关的覆盖排除旧来源；条件覆盖、自复制、取反、恢复旧值和不可达覆盖不能错误切断依赖 | [Python effect r8](../../out/mockbench-study/entry-repair-20261111/python-effect-resume-r8/summary.json)包含 definite／conditional overwrite、self-copy、inversion、restoration 和 unreachable overwrite 检查。 |
| 对象与调用身份 | 同一行的不同调用保留列身份；fresh object、变量重绑定、错误静态目标和测试／loader 支持源码不得冒充产品效果 | [Python effect r8](../../out/mockbench-study/entry-repair-20261111/python-effect-resume-r8/summary.json)包含 same-line、fresh、rebind、wrong-target 和源角色反例。 |
| 异步与异常 | 已 await 或认证 `asyncio.run` 的产品体可以参与解释；原始协程体不得参与；正常完成写入不能穿过只接受异常完成的观察；异常类型不明时保留未知 | [Python query r28](../../out/mockbench-study/entry-repair-20261111/python-query-resume-r28/summary.json)和 [effect r8](../../out/mockbench-study/entry-repair-20261111/python-effect-resume-r8/summary.json)。 |
| 多分支和不可达代码 | 相关分支分别表达；无关、重汇合但不控制目标的 guard 不发布；dead return、`if False` 写入和不可达调用被排除 | [Python query r28](../../out/mockbench-study/entry-repair-20261111/python-query-resume-r28/summary.json)及其 22 项机制检查。 |
| 外部条件不可求解 | 对 `if external_validate(data)` 一类条件，保留原表达式、真假方向、数据来源和调用边界；不得猜测具体 `data`，不得因无法求解而删除该分支 | 新方法判据；当前真实结果没有可作为完整真值的独立实例，因此先作为合成验收案例，后续须补真实代表案例。 |
| Java 跨过程时序 | 正向例需原始观察及连续的匹配调用／返回或异常处理路径；负向例需覆盖错误调用到同目标、后执行事件到前观察、多目标歧义、低预算前沿 | [独立 Java 路径审查](../../out/mockbench-study/entry-repair-20261111/verification/java-exception-route-review-r5.md)给出必需反例；当前实现尚无通过该门禁的真实正向证据。 |

## 通过标准

小范围实现门禁通过需同时满足：

- 每个真实案例保留全部原始观察，关系引用精确观察 ID；
- 每条候选分支输出目标相关的完整解释，或标为 `explanation=partial` 并指出断点；
- 分支条件保留原表达式、真假方向、所属调用／返回及源码证据；
- 依赖响应和交互按分支组织，不跨分支拼接；
- `explanation`、`support`、`realization` 三轴的判定依据分别可审查，且关键未知不会被其他高等级关系抵消；
- 已知错误关系不得以 `analysis_may`、候选或低置信度形式继续进入可消费规格；
- 未展开源码、外部摘要、别名／异常兼容缺口及预算截断进入全局路径覆盖信息；
- 共同约束有独立跨路径依据，没有依据时保持为空或限定为“已探索分支共有”；
- 真实案例与补充机制反例分别报告，不以合成测试通过替代真实语义验收。

只有上述门禁通过后，才冻结分析器版本并执行一次完整 165 例研究运行。完整运行用于测量支持范围、错误、遗漏、未知和成本；普通失败保留为研究结果，不触发边跑边修。

## 已知验收缺口

- 现有结果契约以关系和边界为主，尚未原生表达共同约束、完整分支解释、证据等级及全局路径覆盖。
- Java 已确认入口和根映射，但通用跨过程控制／异常关系缺少匹配上下文保证；不得用旧 Jib 数量门禁放行。
- Python 已有较强的局部返回、状态和反例机制，但真实结果仍普遍声明路径可行性和运行时分派未评阅。
- 尚未找到经独立证据确认的真实“外部条件不可求解”完整案例；不能用人为构造的表达式代表全量分布。
- 本文没有建立所有合法路径的人工全集，因此不能计算路径召回率；只能按已核验正／负关系和未知披露评价。
