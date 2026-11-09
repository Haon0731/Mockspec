# 从测试目标组合值关系：原型与修订实验

2026-11-04。读者为MockSpec研究与实现维护者；归属为独立研究目录。承接[上一轮路径摘要实验](../path-pilot/README.md)，本轮研究从原测试目标组合跨方法值关系，再检查具体Mock响应。原生产Agent、冻结V0.1、审核输入、旧候选与旧执行回执均保留。

**本轮结果支持继续这条路线：自动目标关系识别出了值构造冲突，保留合法替代场景，并在这个开发案例上帮助Agent修正了Stub。** 四次独立修订中，源码组0/2通过真实JUnit，目标关系＋诊断组2/2通过。它是单任务的机制证据，还不能证明跨项目质量提升，或把收益单独归因给切片、关系摘要、检查器中的某一个。

后续[四组组件验证](../component-pilot/README.md)保留本轮算法与输入，新实验中仅诊断2/2、关系＋诊断1/2，源码与仅关系各0/2；Jib迁移均为unknown。因此本页2/2只描述本轮观察，不是稳定性或泛化结论。

## 1. 本轮新增的能力

[协议](protocol.md)预先选定审核CloudFoundry任务的health URL观察目标。普通测试的其余断言保持原样，最终仍执行完整JUnit。库语义与检查器的依据见[方法调查](method-notes.md)。

上一轮R提供逐方法摘要；这轮新增的是可追到原始源码的组合关系：

```text
原测试 assertEquals(expected, app.getHealthUrl())
  ← Application 的 @Data 字段与 @Builder 构造器赋值
  ← DefaultApplicationFactory.createApplication 的 healthUrl 实参
  ← getHealthUrl → getManagementBaseUrl → getServiceUrl
  ← CloudFoundryApplicationFactory.getServiceBaseUrl 的具体覆写
  ← 固定setup中的URI列表与 "http://" 构造

getHealthEndpointPath
  ← getPath(health) / getPath(status) 的独立返回符号
```

前端从18个审核源码单元导出结构化AST；组合器处理固定setup、构造器/super字段关联、具体receiver覆写、局部赋值和控制结构。目标由普通断言提取，查询属性由宿主指定为 `healthUrl`。实现没有按业务类名、Case ID或正确Mock字符串特判。候选响应提取器使用同一个Spoon AST，不执行生成代码。

实际自动输出为[goal-raw.json](goal-raw.json)：**两条正常路径、三条异常终态，未达到32路径上限**。正常路径可简写为下表；`r_h`、`r_s`只是报告对原fresh符号的易读命名。

| 路径 | 自动取得的条件 | 目标构造 |
| --- | --- | --- |
| health直达 | `hasText(r_h)` | `URI_Builder("http://application/Uppercase/", "/", r_h)` 等于原断言期望 |
| status回退 | `!hasText(r_h) && hasText(r_s)` | 同一base与 `r_s` 构造的URL等于原断言期望 |

三条异常终态分别为health调用抛出、status调用抛出、两项都无文本后可见代码抛出。每次Mock调用都有独立call ID/返回符号，参数与先后事件分别保留。其他创建实参、Lombok生成行为、受限库摘要和Mock常量响应无额外写效果等前提保存在 `assumptions`；`unknowns=[]`不表示全程序语义完整。

这里已经能指出上一轮错误的具体位置：`getHealthUrl`使用management **base**，并不把management URL中的web base path自动带入health响应。把 `r_h="health"` 代入，检查器得到 `http://application/Uppercase/health`，与原断言冲突。

## 2. 库语义与合法替代场景

URI操作没有被无条件改写为字符串连接。局部检查器的摘要绑定Spring Framework `5.1.2.RELEASE`，支持受限HTTP(S) base和未编码的简单路径；query、fragment、模板、百分号编码、点路径段等返回unknown。StringUtils与EndpointId也有明确输入范围。

[实际库对照](library-validation/comparison.json)用冻结89个jar运行独立Java oracle：35个输入中，本地摘要支持25个，25个全部与实际库结果一致；其余10个明确返回unknown。这个有限对照不是对整个摘要输入域的形式化证明。版本源码依据与适用范围见[method-notes.md](method-notes.md)。

除了历史候选，还运行了四个人工机制探针，未把它们提供给生成Agent：

| 依赖响应 | 局部检查 | 完整原JUnit |
| --- | --- | --- |
| health=`/actuator//health` | 与目标一致 | 通过 |
| health=null，status=`actuator/health` | 与目标一致 | 通过 |
| health与status均为空字符串 | 与目标冲突 | 失败 |
| health=`actuator/%68ealth` | unknown：编码不在摘要范围 | 失败 |

这说明原型保留了路径归一化和status回退这两种合法变化，而没有把 `actuator/health` 一个字面值当作唯一答案。原始配置见 [probes/](probes/)，真实编译/测试回执见 [junit-probes/](junit-probes/)。这些是人工构造的机制探针，不是新增独立真实任务，也不是模型自动发现的替代场景。

## 3. 检查旧候选与Agent修订

旧6个Java候选使用已保留的真实JUnit结果；4个人工探针新增执行。检查器对10个配置作出8个明确判定，8个均与观察到的JUnit结果一致，另外2个返回unknown：

- 旧S1/G2/R1/R2被定位为目标值冲突；旧S2保留为候选一致。
- G1包含额外 `webEndpoint.setBasePath(...)`，候选解释器不支持该附加状态修改，因此不将其静默忽略，而是返回unknown。
- 四个人工探针的结果如上表。

明确判定包括5个冲突和3个候选一致；没有误拒绝这3个已知通过配置。这里的“候选一致”只针对选定属性和受支持交互，不等于完整测试有效或真实依赖符合该行为。原始判定见 [checks.json](checks.json)，与JUnit的汇总见 [summary.json](summary.json)。

随后进行预定的四次独立修订，输入固定旧R1候选：

| 条件 | 输入 | 一次修订后原JUnit通过 |
| --- | --- | ---: |
| C | 相同审核源码＋同一个待复核候选 | 0/2 |
| E | C＋自动目标关系＋局部冲突诊断 | 2/2 |

C1/C2都保留 `health` 并继续误解释URL构造；E1/E2都改为 `actuator/health`，明确指出health URL不自动追加web base path，且真实JUnit通过。四个候选都保留原普通测试和断言，均编译成功，无目标执行反馈、无第二次修订。见 [revisions/](revisions/)、[junit-revisions/](junit-revisions/)。

生成使用四个fresh native executor上下文，按C1/E1/E2/C2交错启动。角色配置相同；实际服务端模型身份、token、费用及seed未暴露。文件读取是白名单指令和Agent自报，没有OS强制隔离；部分Agent读取了自己的输出做JSON校验。条件间输入长度不同，顺序未随机化。完整公共指令与身份见 [revision-prompt.txt](revision-prompt.txt)、[revision-provenance.json](revision-provenance.json)。

这测的是**已有候选的修订**，不是从零生成；案例同时用于开发分析器，不能当留出集。E同时增加组合关系和诊断，当前对照无法拆分两者贡献。虽然E1代码选择了合法值，其解释中“must return”的措辞也不能升级为唯一合法响应规约。本轮没有新增交互变异实验，不能据此声称Mock Assertions的缺陷区分能力提高。

## 4. 实现和已知边界

| 文件 | 本轮职责 |
| --- | --- |
| [GoalIR.java](GoalIR.java)、[prepare_ir.py](prepare_ir.py) | 经Adapter/SourceStore重新核验选定源码，导出保留构造器/字段/表达式结构的AST；旧逐方法摘要丢失了组合所需结构，故新增实验前端 |
| [compose.py](compose.py) | 从指定观察目标按需组合支持的可见方法、状态和路径；保留位置、fresh符号、假设及截断标志 |
| [candidate_bindings.py](candidate_bindings.py)、[local_check.py](local_check.py) | 解析有限Mockito常量响应/次数断言，检查它们是否与目标关系一致；不生成响应答案 |
| [LibraryOracle.java](LibraryOracle.java)、[library_oracle.py](library_oracle.py) | 在实际冻结库上执行严格白名单表达式，独立核验有限摘要 |
| [build_packet.py](build_packet.py)、[summarize.py](summarize.py) | 去重证据/假设、冻结生成输入、核对输入和候选身份并汇总原始回执 |

本轮是**有界符号求值与目标投影原型**，不是完整Java符号执行器或SDG后端。支持的语法和库摘要是显式白名单。循环、一般try/catch、复杂别名、并发、任意builder、动态反射、复杂Mockito行为及大部分外部库均未覆盖；构造器和setup解释也只支持选定的简单形式。

目标投影要求可见@Data字段、@Builder构造器和对应参数到字段的identity赋值，拒绝显式同名getter覆盖等不满足前提的情况。它没有执行创建对象时所有兄弟实参和构造器行为，因此这些正常完成义务必须保留；完整JUnit用于检查本案例中的具体见证。不能把对一个属性的局部冲突推广成任意候选、任意状态下的全局不可行结论。

候选解释只支持常量 `thenReturn` 序列与有限 `verify/times`。同一方法多次调用获得不同符号；具体Mockito响应序列在支持范围内逐次绑定，末值重复。额外setter、重复stub优先序、sideEffect等不支持的配置不会被悄悄当作无效语句删除。

开发过程中实际修复了会改变结论的错误：Java布尔短路、作为其他调用实参时的异常传播，以及Builder投影前提过弱。第一版所有库调用都分叉导致32路径截断的输出保留在 [goal-initial.json](goal-initial.json)，仅作开发诊断，不作为固定算法的质量基线。修正后重放得到同样5条路径，见 [replay-metrics.json](replay-metrics.json)。

## 5. 成本与下一步选择

这次单次Spoon提取约2.12秒，另有约1.11秒helper编译；独立组合器进程约0.066秒。测量不是多次均值，也未测峰值RSS。分析IR为780KB，原始关系为103KB；给Agent的关系＋诊断包通过共享证据/假设去重后为43,418 bytes。上一轮R额外包为93,318 bytes，但涵盖两个任务，与本轮单一属性的范围不同，不能据此报告等功能压缩率。共同源码仍为82,613 bytes，模型token和费用未测。

本轮执行8次完整目标JUnit、1个正式保留的库对照批次。库工具准备另有4次尝试（2次基础设施失败、2次成功集成），原子预算消耗超出初始安排，已在[协议](protocol.md)中明确重分配并减少目标执行预留；总计13个批次尝试，未超过原14个总批次额度。准备尝试细节为实现代理自报，其pytest临时输出已清理；正式库与JUnit回执完整保留，见 [oracle-preparation.json](oracle-preparation.json)。

**下一阶段继续采用目标关系与局部检查，暂不加SMT。** 本案例的错误无需求解器即可定位；当前主要成本是正确取得字段/调用/异常关系和维护库语义。不要继续无界扩展这个有限解释器：Java下一步应验证WALA/Soot等IR是否能可靠替代手写状态与调用连接层，保留本轮可复用的目标/边界/unknown接口。该后端替换在本轮尚未执行。

正式研究最有区分力的下一组工作是：

1. 加入跨仓库留出的已审核任务，优先不同的跨函数转换、连续响应、void状态变化和异常回退。分别检查目标关系是否正确，再评估Agent使用效果。
2. 在相同总预算下分开比较“紧凑源码”“组合关系”“关系＋检查诊断”，并分别评价从零生成和候选修订，避免把额外上下文或反馈本身当作切片收益。
3. 增加交互条件、错误依赖选择、漏调用/意外fallback的变异与合法替代行为检查；保留原测试通过、规格合法性和缺陷区分能力三种不同指标。
4. 只有在可靠编码的局部关系仍无法判定时接SMT；否则优先修复缺边、别名/状态语义或unknown传播。

## 6. 验证与复跑

整个后向切片研究目录：**33 passed，1 skipped**；跳过项是显式启用的真实库集成测试，本轮正式35输入库对照已有独立执行记录。Ruff与diff检查通过。没有新增项目依赖，没有写入旧数据、修改生产代码或冻结准入。

```bash
# 同一冻结IR重新组合；使用新输出保护原记录
.venv/bin/python experiments/backward-slicing/goal-pilot/compose.py --ir experiments/backward-slicing/goal-pilot/ir.json --output /tmp/goal-replay-new.json --goal-property healthUrl

# 用保存的候选绑定复核局部判定
.venv/bin/python experiments/backward-slicing/goal-pilot/local_check.py --goals experiments/backward-slicing/goal-pilot/goal-raw.json --bindings experiments/backward-slicing/goal-pilot/bindings.json --output /tmp/goal-checks-new.json

# 核对冻结身份并重算汇总，不执行目标
.venv/bin/python experiments/backward-slicing/goal-pilot/summarize.py

# 完整原JUnit复跑：输出目录必须全新
PYTHONPATH=src .venv/bin/python experiments/backward-slicing/path-pilot/java_evaluate.py --candidate experiments/backward-slicing/goal-pilot/revisions/E1.json --output /tmp/goal-junit-new --old-root /path/to/Stub-Tester

PYTHONPATH=src .venv/bin/python -m pytest -q experiments/backward-slicing
PYTHONPATH=src .venv/bin/python -m ruff check experiments/backward-slicing
```

`prepare_ir.py`可重新通过审核接口构建IR；`build_packet.py`拒绝覆盖已存在的inputs目录。库oracle复跑使用保留的 `library-request.json` 和全新输出目录。旧Java evaluator继续负责只读挂载、槽外内容保护和真实JUnit；没有复制成第二份评价器。研究结束状态为本轮机制与修订pilot完成；跨任务质量、完整状态分析和生产接入仍待后续验证。
