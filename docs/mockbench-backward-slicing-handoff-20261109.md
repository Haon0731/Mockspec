# MockSpec 全量 MockBench 后向切片研究：对话与工作交接

日期：2026-11-09。用途：把本次对话中的需求、实现、实测结果与未决问题交给新的研究者或网页版 GPT，供其分析下一步方案。

**核心结论：完整数据已经接入，165例均已运行目标导向的后向候选图。155例取得候选图、10例构图失败，但尚未建立可靠的语义恢复成功率。当前主要障碍包括源码入口与调用绑定、图规模，以及缺失的对象状态／异常依赖分析。**

本文自包含，不要求读者访问原始聊天或本地数据。此次发布的是交接总结；本轮研究代码与逐例完整产物仍在本地工作区，未随这个文档提交。因此，不能把远程其他源码的当前版本当作下述 r3 实现，也不能把文中的本地路径当作已经发布的附件。

## 1. 用户真正希望研究什么

用户希望 MockSpec 不再局限于三个示例，而是面向 Stub-Tester 在 agent 实验中使用的完整 Java/Python 数据集，系统回答：

1. 从测试目标出发，自动恢复哪些数据流、控制流和 Mock 依赖关系？
2. 在什么类型的任务上失败，具体断在哪里？
3. 哪些失败能通过成熟程序分析技术解决，哪些是输入、测试意图或未知行为本身的问题？
4. 这些程序分析证据最终能否帮助 Coding Agent 生成行为正确、同时具备缺陷检测能力的 Mock？

用户明确强调：**让真实案例的困难决定技术选择，不能预先认定必须采用 WALA 或完整符号执行。**

用户还纠正了方法方向：本次要研究的是早先的“测试目标导向后向切片”方案，不是继续扩写后来针对少数案例的 GoalIR／场景组合／自研符号解释器。

最终目标与当前生产行为要区分：生产 Scenario Agent 当前输出带证据的依赖约束；本次全量研究是独立实验路径，没有把新的候选图接入生产，也没有执行 Mock 生成与评分。

## 2. 原方案及其边界

原方案主线是：

```text
可见测试目标：值、异常、状态、交互
    ↓
后向切片：追踪影响观察目标的代码与依赖
    ↓
到达 Mock／未知依赖边界，保留参数、返回、异常、可能写状态
    ↓
必要时在原 CFG 上做有界路径分析，形成条件化证据
    ↓
Coding Agent 选择场景并生成 Mock
    ↓
分别验证原测试通过、缺陷检出和合法替代误拒绝
```

关键约束：

- 切片包含某调用，不代表该调用必达、恰好一次、必须正常返回或必须生成 verify。
- 依赖图中的路径不能直接当成可执行 CFG 路径。
- Mock 的未知返回、异常和副作用必须保持未知，不能借隐藏实现或占位 throw 推出真实行为。
- 未解析调用不等于 Mock；断言框架、标准库、加载器也可能未解析。
- 只让测试通过不等于 Mock 有缺陷区分能力；过强约束还可能误拒绝合法实现。
- 原方案中的有界路径分析、条件求解、Agent 对照，本轮均未完成。

## 3. 对话中完成了哪些工作

### 3.1 从三例策略扩展到完整输入接入

早期研究框架仍受历史三例准入目录限制。用户指出，既然 Stub-Tester 的完整 agent 实验输入已经能使用，就应直接复用其冻结输入。

随后新增独立的 `FrozenDataset` 接入路径，实际加载了全部165例，保留原Case ID、snapshot、masking与普通测试上下文。它不要求把165例逐个登记进历史三例白名单。

该接口提供目标测试、源码路径列表、按需源码读取，以及明确选择被测文件后转换为 UnifiedTask 的能力。加载成功不要求提前知道被测方法，也不伪造被测方法。

### 3.2 找回原后向切片方案，重新调研

查阅了早先的后向切片协议、路径约束讨论，以及历史 Joern/WALA 准备材料；核对经典跨过程切片、符号 Mock 生成及路径引导测试生成的研究依据。

结论是方案有研究价值，但不能由文献推出“切片能自动给出唯一正确 Mock”。也没有证据表明某个现成后端可以直接覆盖本数据集的遮蔽测试、动态加载与未知边界。

### 3.3 对165例可见测试做目标结构盘点

只分析完整 masked test：Python 使用 AST，Java 使用去掉注释和字面量后的词法规则。类别可重叠，统计不是切片成功率。

| 整份测试文件中的语法线索 | Java 59 | Python 106 |
| --- | ---: | ---: |
| 值／状态断言 | 58 | 99 |
| 异常观察 | 4 | 37 |
| 固定交互观察 | 2 | 0 |
| 未识别到上述三种观察 | 0 | 6 |
| 参数化线索 | 0 | 106 |
| 动态加载／反射相关调用名 | 11 | 67 |

这些是语法线索，辅助函数也可能被计入；不是绑定后的目标函数语义分类。Python交互观察为0不能解释为“不需要交互验证”，因为待生成的验证区域已经遮蔽。

6个Python案例没有保留常规显式观察，但仍有目标调用和预期数据：`click_423`、`crawl4ai_161`、`crawl4ai_164`、`flask_313`、`hummingbot_177`、`langflow_188`。仅从 assert 出发会遗漏这类任务；`expected_*` 参数名也不能自动证明次数、参数或行为要求。

### 3.4 实现完整批量研究链路

实现了：

```text
冻结清单 → 目标测试范围定位 → 提取观察准则
        → 有界源码候选选择 → 后向候选图
        → 每准则闭包、源码身份、未知边界、失败阶段与汇总
```

先跑6例探针，再全量运行。期间发现并修复实现缺陷，保留了中断与旧版本结果，最终完成 r3 全量运行。

### 3.5 对结果继续做只读归因与可行性探针

分析不只关注10个报错案例，也关注155个已构图案例中的断裂。新增了路径候选匹配探针和三个合成语义探针，具体见第7节；这些探针没有修改或重新评分 r3。

## 4. 数据在哪里，接入意味着什么

实际数据没有复制进 MockSpec，保存在宿主的 Stub-Tester 冻结 registry：

```text
/home/haon/projects/Stub-Tester/.omx/developer-realistic-registry-max/
  formal-snapshots/<dataset>/<snapshot_id>/tree/repo/
```

| 语言 | Dataset | 案例数 | 冻结索引 revision |
| --- | --- | ---: | --- |
| Java | java-bechmark-strong | 59 | e8ed1a717304ae6437602a4da6389aae46d96f4a805bc140883d0a626742811f |
| Python | python-benchmark-new | 106 | 0759c27f3d4cc7257d78d9ac2ced70089aedc60fb8d0f562b63cc8f750d7e8dc |

输入策略为 `stub-tester-frozen-agent/v1`：信任用户指定的上游冻结准备协议，核验索引、控制身份、manifest、masked target和injector；实际读取源码时核对已有摘要。历史三例策略继续保留，尤其不能静默混用历史 Flask fix23 版本与本次主 registry 版本。

本轮没有运行旧项目脚本、原始目标测试，没有读取隐藏 Mock 答案，也没有扫描／补算全量依赖哈希。接入核验不是全仓无泄漏的独立证明。

## 5. 当前实现到底是什么方法

**它是 Python AST＋Spoon 源码后向候选图原型，不是完整 PDG/SDG，也不是符号执行器。**

| 组件（本地文件） | 行为及限制 |
| --- | --- |
| `src/mock_spec/adapters/frozen_dataset.py` | 加载冻结案例、提供已核验的按需源码读取 |
| `experiments/backward-slicing/criteria.py` | 按selector定位目标测试；提取显式观察，无观察时列候选调用事件 |
| `source_selection.py`（同目录） | 从import、有限字面加载路径、Java同包类型名选择源码，再扩展一层；不扫描全仓内容 |
| `python_probe.py` | 先前同名写入候选、赋值定义连接、词法分支、按函数名匹配的返回／事件边，再做后向闭包 |
| `SliceProbe.java` | Spoon无classpath解析；声明／先前写入候选、表达式包含、词法分支、候选参数／返回边，再做后向闭包 |
| `batch_slice.py` | 逐例运行，保存准则身份、源码摘要、图、每准则来源与失败分类；全量保留分母 |

Python批处理关闭普通语句前驱边，避免把“出现在前面”当成依赖。窄模式识别到的占位方法不展开内部节点。未知调用只留下边界标记，尚未构建完整的未知副作用摘要。

算法上的主要限制：

- 局部数据候选按名字与源码顺序连接，未实现CFG上的到达定义／赋值覆盖。
- 词法分支包含关系不是后支配意义上的控制依赖。
- Python按短函数名查找候选，缺少可靠导入别名、接收者绑定及实参→形参传播。
- Java无classpath调用解析与名称／参数个数候选不能证明实际派发。
- 没有完整对象字段／别名、异常传播与跨调用上下文分析。
- 没有证明切片完整性或健全性；结果可能同时有多余边和漏边。

固定运行配置：每例最多8文件（含测试）、200000字节选定源码、一层扩展、后端30秒；Java堆768MiB、图上限2000节点／10000边；Python上限10000节点／50000边。支持源码读取最多7次，单个原子读取可能超字节预算一次，随后停止。Adapter读取不计入后端时限，峰值内存未测量。

## 6. 最终全量结果：r3

配置标识：`test-goal-backward-candidates/v1`。165例全部尝试，无未选择项、无超时。

| 指标 | Java | Python | 总计 |
| --- | ---: | ---: | ---: |
| 输入加载及目标测试范围定位 | 59 | 106 | 165 |
| 生成候选图，状态partial | 49 | 106 | 155 |
| 构图失败 | 10 | 0 | 10 |
| 闭包触及至少一个产品源码候选 | 21 | 55 | 76 |
| 已构图但未触及产品源码 | 28 | 51 | 79 |
| 没选到产品文件 | 2 | 38 | 40 |
| 文件／字节选择预算不足 | 26 | 10 | 36 |
| 只有候选事件，没有规则识别的显式观察 | 3 | 6 | 9 |

不同缺口可重叠。540条准则记录不等于540个独立场景。这里目标方法内的观察统计，与第3节整份测试文件统计口径不同。

155个图中，152例含数据候选边、87例含词法控制边、17例含Java参数绑定候选边。146例包含未解析调用，其中包括断言框架、标准库和加载器，不能称为146例Mock恢复失败。

**155/165不是语义分析成功率，76/165也不是被测方法或Mock边界恢复成功率。** 后者可能只表示碰到了一个无关辅助类。尚未给出经人工标注校准的关系正确率／召回率，也没有Mock质量提升结果。

运行过程保留：

- 6例先导；
- 首次全量在完成20例后中断，以修复作用域与seed定位问题；
- r2完成165例，149 partial／16 failed；
- 修复空支持模块与循环seed问题后，r3为155 partial／10 failed。

这些是开发重跑，不是独立留出实验，不能把差值解释为泛化收益。

## 7. 失败原因与新探针：证据、推论分开

### 7.1 十个硬失败

**证据：** 9个Java案例触发当前原型2000节点上限，分别涉及gRPC、Druid、4个Hadoop Abfs、Hadoop Job、ZooKeeper、Neo4j案例。发生在后向闭包完成之前的节点构建阶段。不是超时，也不是外部工具声明程序不可分析。

**推论：** 当前“选入整份文件、先构大量节点、再切片”的策略是重要规模因素。需求驱动展开、方法摘要、符号范围定位或不同后端值得比较；仅扩大上限不证明语义更可靠。

**证据：** Apollo `DefaultConfigServiceTest#testLoadConfigWithGrayRelease` 报重复类型。选择器同时选中setup前缀和普通路径中的同名类。两份DefaultConfigService内容摘要均为 `ccf872725054ceccff986794d3f757f988c9957e5fac8ee0deb143548434f658`；两份AbstractConfigService也相同。

**推论：** 可合并内容相同的重复定义，保留所有来源；同名但不同内容时应保留歧义，不能随意挑一个。

### 7.2 已构图却未到产品：79例

**证据：** 40例没有选到产品文件，另外39例已经选到文件，但闭包没有到达产品节点。Python的51例未到产品中，38例只选到测试，13例选到了产品文件；后13例中6例同时有选源预算不足，7例没有该标记。

**新增只读可行性探针：** 对Python未选产品的38例，仅从可见测试AST提取赋值字符串路径、`Path / 字符串`路径后缀和`from package import submodule`，与冻结source_paths做匹配。29例能找到唯一的源码路径候选：7例字符串路径、21例Path拼接、1例子模块导入。

这29例结果来自本次终端内存探针，未写回r3，也没有执行loader、绑定目标调用或重跑切片。它证明入口扩展有可行线索，**不证明29例已经解决**。

代表案例：

- `langflow_189`、`crawl4ai_164`：Path拼接后read_text＋exec/compile载入函数，原定位器没有识别这种模式。
- `ansible_452`：可见字符串路径，通过open＋exec载入，未命中原固定loader名。
- `stable-diffusion-webui_424`：需要从package导入继续解析子模块，原流程只读到空支持模块。
- `tqsdk-python_447`：字面路径已经定位到api.py，但文件约225KB，超过200KB选入预算。不能把该例归为缺少源码路径。

**推论：** 可建立受限的路径表达式与加载器协议解析，禁止直接执行目标exec；候选路径、函数名字／范围与调用绑定仍应分别确认。大文件需要保持上下文的按需分析，不能简单截断源码。

### 7.3 Jib说明“触及产品”也会误导

**证据：** `JarFilesTest#testToJibContainerBuilder_explodedStandard_basicInfo`有19个断言准则。只有测试第87行闭包到达辅助类CommonCliOptions；没有到达已选入的JarFiles.java。

可见测试第63行调用 `JarFiles.toJibContainerBuilder(...)`，对应产品文件第50行有同名五参数静态方法，第93行明确返回containerBuilder。当前图仍将测试中的该调用记为unknown。该方法不是void。

**推论与未知：** 调用绑定确实未建立；无classpath及候选筛选是待检验因素，但本轮没有对Spoon中间表示做足以锁定唯一底层原因的插桩。不能仅凭结果断言“补某一个类型就一定修好”。应单独验证完整限定名、参数个数、接收者与声明之间的连接。

### 7.4 简单语义探针证实分析本身存在漏边／多余边

在内存中将合成源码交给当前Python Probe，不执行合成目标程序：

```python
# 探针A：旧值已被覆盖
x = dependency.read()
x = 0
assert x == 0
# 实际观察：第一行仍保留在后向候选图中。
```

```python
# 探针B：有源码的对象写入
def fill(box):
    box.ready = True

def test_value(box):
    fill(box)
    assert box.ready
# 实际观察：闭包只有box参数与assert，fill调用及字段写入被漏掉。
```

```python
# 探针C：未知依赖可能修改对象
def test_value(box):
    dependency.fill(box)
    assert box.ready
# 实际观察：闭包同样漏掉前面的未知调用。
```

**证据：** 当前按名字／位置建数据候选，没有CFG上的赋值覆盖；字段写入不在Name赋值模型里，未知调用也没有完整may-write传播。

**推论：** 改进定位器只能解决一部分任务。可靠Mock证据需要真正的局部数据流、跨过程参数返回、对象状态／别名与异常控制分析。不能用“加更多语法边”直接宣称解决健全性问题。

### 7.5 观察目标有时本身不完整

9例在选定测试范围中没有规则识别的显式观察，只能列出candidate_event。`flask_313`会同时列出show_server_banner和辅助enumerate；技术上定位到调用不代表它们同样属于测试意图。

**未知：** 可见参数与上下文能恢复多少测试意图，需要单独研究和标注。分析器无法凭空恢复已遮蔽的验证内容；不同候选目标可能对应不同合法Mock约束。

## 8. 已修复什么，验证证明了什么

本轮修复并回归覆盖：

- Python嵌套函数同名变量污染外层关系；
- Java同行条件与断言的seed错选；
- 准则身份丢失、多次未知边界调用相互覆盖；
- Python循环变量节点误作观察起点；
- 源码读取预算只按成功选入文件计数的问题；
- 空支持模块使整例中断的问题：仅省略已核验的空模块并留证，其他身份／哈希错误仍传播。

最终33项相关回归通过，Ruff、diff检查和文档链接检查通过。机械复核了165个唯一案例身份、155个图的边端点／source_id／逐准则绑定，以及已识别opaque方法内部节点排除。实现摘要与r3 manifest一致。

这些是工程与结构验证，**不证明关系语义正确，也不证明所有Mock替换边界已识别**。本轮未执行目标测试、模型生成、缺陷变异或合法替代对照。

## 9. 探索过的技术方向，尚未做出的选择

目前较有依据的方向是：**薄的测试入口适配层＋成熟依赖分析＋显式未知边界**。

| 需要解决的问题 | 可探索能力 | 不会自动解决的问题 |
| --- | --- | --- |
| 哪次赋值影响观察 | CFG、到达定义／SSA | 被测方法入口、测试意图 |
| 参数返回跨函数传播 | 跨过程依赖、调用上下文匹配 | 动态loader适配、缺失声明 |
| 字段状态、别名、副作用 | 指针／别名、读写效果摘要 | 未知Mock的具体行为 |
| 异常影响后续行为 | 异常CFG、控制依赖 | 隐藏oracle、路径真实可行性 |
| 关系可靠后仍有路径冲突 | 有界路径分析、局部SMT | 前面没有恢复出的依赖 |

已核对的官方能力：

- [WALA Slicer](https://github.com/wala/WALA/blob/v1.8.0/core/src/main/java/com/ibm/wala/ipa/slicer/Slicer.java)：已有SDG后向切片，需要criterion、call graph和pointer analysis；可作为Java代表案例的语义对照，不能自动解决masked test编译入口。
- [SootUp分析框架](https://soot-oss.github.io/SootUp/latest/write_analyses/)：提供CFG和数据流／IFDS等基础；框架不等于本任务现成切片器，需要定义事实域与传播。
- [Joern切片接口](https://docs.joern.io/cpg-slicing/)：提供跨过程backward DDG，默认起点为调用参数，不等于任意断言上的完整SDG。
- [CodeQL Python数据流](https://codeql.github.com/docs/codeql-language-guides/analyzing-data-flow-in-python/)：局部／全局数据流适合验证当前Python名称候选的不足，但需要入口与库模型，不直接解决任意exec。
- [Joern外部调用语义](https://docs.joern.io/dataflow-semantics/)：传播摘要会影响保留／消除哪些流。对未知Mock不能随意给出确定性摘要，避免人为漏掉可能行为。

本轮没有安装或实跑上述新后端。历史Joern v4.0.658有含测试Java构图失败、Python深度20切片超时记录，不能据此排除所有版本／配置。仓库存在WALA pilot协议与准备材料，但没有据此宣称已完成后端实测。

文献背景已核对：[跨过程SDG切片（1990）](https://doi.org/10.1145/77606.77608)、[Mock-object Generation with Behavior（2006）](https://www.microsoft.com/en-us/research/publication/mock-object-generation-with-behavior/)、[路径敏感后向切片（2012）](https://jorgenavas.github.io/papers/BackwardSlicing-SAS12.pdf)、[SymPrompt](https://arxiv.org/abs/2402.00097)。这些支持机制可借鉴，不直接证明本项目方案的新颖性或有效性。

## 10. 请下一位研究者／网页版 GPT 重点判断

请基于上述实测事实分析，而不是把候选图完成率当作成功率，或先默认使用某个工具：

1. 入口适配应恢复哪些最小事实，才能可靠连接遮蔽测试、动态加载函数与真实被测方法？哪些可确定，哪些必须标为候选？
2. 当前自研AST/Spoon图应保留哪些职责、替换哪些职责？继续补局部规则与引入成熟分析框架的合理分界在哪里？
3. Java与Python是否需要不同分析后端？提出少量可区分技术优劣的机制案例与真实案例，并说明判断标准。
4. 当Mock没有真实实现或只有占位throw时，如何表示normal return、exception和may-write，既避免漏掉依赖，又避免所有未知调用污染整张图？
5. 如何处理状态／void交互、异常目标及缺少显式oracle的任务？不能从expected变量名或方法名直接推出规格。
6. 如何避免全文件构图触顶，同时保留必要导入、字段、控制与调用上下文？如何评价按需展开是否漏边？
7. 应如何构建人工关系标注、开发／留出划分，分别衡量入口定位、关系正确性／遗漏、成本？
8. 什么证据门槛满足后，才值得做“原始任务／等量源码／切片证据”三组Agent生成对照，评价原测试通过、缺陷检出和合法替代误拒绝？

希望得到：按证据排序的根因判断、可行架构备选及取舍、最小判别实验、必须保持的unknown边界，以及哪些结论当前仍无法作出。不要只列工具名，也不要把完整符号执行作为默认答案。

## 11. 本地证据索引（未作为远程附件发布）

以下路径相对MockSpec仓库，仅供有本地工作区的协作者复现：

```text
src/mock_spec/adapters/frozen_dataset.py
experiments/mockbench-study/{inventory.py,batch.py,run.py}
experiments/backward-slicing/{criteria.py,source_selection.py,batch_slice.py}
experiments/backward-slicing/{python_probe.py,SliceProbe.java,survey_targets.py}
experiments/backward-slicing/full-benchmark-research.md
out/mockbench-study/intake-20261109-inventory.json
out/mockbench-study/intake-20261109/
out/mockbench-study/backward-slicing-target-survey-20261109.json
out/mockbench-study/backward-slice-full-20261109-r3/
  report.md
  summary.json
  results.json
  run-manifest.json
  verification.json
  observations.json
  <case-hash>.json
```

r3实现指纹示例：`batch_slice.py` 为 `9bf409b5fc3044b5ee44d149164895ce250282861d573838d8b525a38e81ce4d`；`python_probe.py` 为 `184b4fab20a15caa20064ca01e5fa3e04aa6941070aa7263de79752efc9ecf4b`；`SliceProbe.java` 为 `01f4b3a51c9d9f237571abe3ed621127f32113704b414f7f8cd01a30a1b09ff2`。完整指纹在本地manifest中。

本地全量复现命令（结果目录必须不存在，要求原冻结数据和固定Spoon/JDK仍可用）：

```bash
PYTHONPATH=src .venv/bin/python experiments/backward-slicing/batch_slice.py \
  --inventory out/mockbench-study/intake-20261109-inventory.json \
  --output /tmp/mockbench-backward-slice-reproduction
```

上述复现依赖尚未随本总结发布的本地代码与外部数据。网页版阅读者可先依据本文分析；不能声称已经独立重现运行或审核全部逐例关系。
