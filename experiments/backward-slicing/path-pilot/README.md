# 近似路径约束快速实验与路线选择

2026-11-04；读者为 MockSpec 研究与实现维护者。归属为独立研究路径，承接[工具调查](../tool-survey.md)、[首次切片实验](../README.md)和[路径约束文献调查](../path-constraints.md)。本轮结束状态：两个审核任务、六次独立生成、真实执行、机制检查与独立审查完成；不修改生产 Scenario Agent、冻结输入或 V0.1 Prompt。

后续已完成[目标关系组合与修订实验](../goal-pilot/README.md)。本文保留三组摘要实验的历史结果；后续采用不同的目标组合方法和候选修订协议，不能把两轮结果直接当作同一质量提升序列。

## 决策

**继续研究“测试目标导向切片＋条件化值关系＋调用效果＋局部一致性检查”，不采用“把路径 guard 或逐方法摘要整包塞给 Agent”作为最终方案。暂不引入完整符号执行或全局 SMT。**

本轮得到的最重要证据是负结果：条件与表达式可以自动提取，但 Agent 仍会错误组合跨方法的值。Java 的摘要组没有胜过同源码基线。这表明当前证据表示和使用方式尚不够，不意味着目标导向的路径约束无效；当前原型根本没有完成目标等式的跨过程闭合。

Mock 确实与数据流有关，但需要共同考虑五类信息：返回值怎样传播、什么条件使调用发生、异常怎样阻断后继、状态怎样影响下一次调用、Mock 框架允许替换哪个绑定。路径条件主要解决其中的控制一致性，不能独自解决值构造或只读方法 patch 问题。

## 1. 实验做了什么

预先冻结的[协议](protocol.md)使用已审核 `flask_308` 与 CloudFoundry 任务；保留原 case ID、masking、普通测试和断言。每组两个新 native executor 上下文，每个上下文一次生成两个任务；无执行反馈、无候选修订，总计12份任务输出。

| 条件 | Agent 得到的内容 | 额外 JSON 大小 |
| --- | --- | ---: |
| S | 任务＋共同可见源码 | 59 bytes |
| G | S＋自动提取的 guard 与位置 | 53,267 bytes |
| R | S＋guard、局部赋值/返回表达式、调用参数与效果摘要 | 93,318 bytes |

共同输入为82,613 bytes。Java各组都看到同样18个审核源码单元，Python为原测试、MUT和允许导入片段；S不是最小程序切片。G/R由同一冻结分析结果机械投影，未加入人工正确 Mock、参考答案或 URI 规则。源码准入经 Adapter/SourceStore 复核，输入摘要见 [manifest](inputs/manifest.json)。

三组使用相同角色配置；实际服务端模型身份、token、费用、随机种子没有可核验接口。生成顺序未随机化、输入长度不等，每条件只有两次输出。上下文隔离依靠 `fork_turns=none`、读白名单和自报文件清单，不是操作系统强制盲测。完整公共指令见 [generation-prompt.txt](generation-prompt.txt)，输入/候选身份见 [generation-provenance.json](generation-provenance.json)。**本实验用于机制和工程选型，不能作统计性质量提升或因果归因。**

## 2. 真实生成结果

“通过”要求完整原测试通过；Flask一次候选必须通过全部5个参数行，不能把5行当5个独立研究任务。Java运行真实 JUnit 和冻结依赖库，不使用手写 URI 仿真。

| 条件 | Flask 完整通过 | CloudFoundry 完整通过 | 可用 Flask 候选的预设变异检出 |
| --- | ---: | ---: | --- |
| S：共同源码 | 1/2 | 1/2 | 1个候选，各检出3/3 |
| G：再加 guard | 2/2 | 0/2 | 2个候选，各检出3/3 |
| R：再加值/效果摘要 | 2/2 | 0/2 | 2个候选，各检出3/3 |

逐候选：S1两任务均失败；S2两任务均通过；G1、G2、R1、R2均为Flask通过、Java失败。六个Java候选均编译成功。

失败原因可定位：

- **S1 Flask：** patch `flask.helpers._cv_app.get`，进入MUT前因 `ContextVar.get` 只读而失败。其变异运行不计检出。
- **五个 Java 失败：** Stub 返回 `health` 或 `/health`，运行时得到 `.../health`，原断言要求 `.../actuator/health`。G1额外配置 `WebEndpointProperties` 的 base path也没有改变这条 health URL 构造链。
- **S2 Java：** 返回 `actuator/health`，真实测试通过。它说明同样源码已经包含解题证据，更多摘要并不自动带来更准确的解释。

五个可用Flask候选检出了错误 code、丢失 kwargs、重复 context lookup。失败行均落到生成的调用参数/次数断言；实际记录与注入行见 [summary.json](summary.json)。这只覆盖三类预设交互故障。G1没有在无上下文分支断言 `aborter.assert_not_called()`，仍得到3/3，说明该分数不能代表路径交互断言完整。Java本轮没有交互变异实验。

原始回执：[Python 24次执行](python-generated/metrics.json)、[Java 六个候选](java-generated/)。Python执行后经独立审查补强了退出码/observer状态/变异名称的计数检查；使用保存回执重算，全部分类不变，没有重新生成或重新运行候选。旧执行器hash、新分类器hash及重算结果均留在 [summary.json](summary.json)，不能把新分类器hash当作原24次进程执行版本。

## 3. 哪些分析能力得到实际验证

### Flask：调用效果比只有 if 条件更有信息

实际代码为先查询上下文；非空时调用 `ctx.app.aborter`；之后调用 `_wz_abort`。后者不是 `else`。

[Python分析器](python_paths.py)对未知调用区分正常返回与抛异常，为每次调用分配独立事件/结果符号，保留值绑定、参数转发和位置。它在该函数产生6条候选路径：lookup抛异常；空上下文后fallback正常/异常；非空后aborter抛异常；非空后aborter正常，再由fallback正常/异常结束。

[有限轨迹检查](trace_matrix.py)直接编译审核可见MUT源码，以可控依赖响应执行12组配置，未手写等价业务逻辑。**12/12实际轨迹匹配，6条分析路径各有见证**，包含aborter正常返回后继续fallback的路径。见 [trace-matrix.json](trace-matrix.json)。匹配器检查事件身份、目标、正常/异常顺序、guard和终态；虽然保留实参记录，但不机械核对参数关系。因此此处证明的是该有限矩阵中的控制/效果匹配，不是通用数据流正确性、soundness或完整性。

该分析器只解释选定函数，可见辅助函数也按未知调用边界处理；尚无Python跨过程摘要组合。属性求值、heap、循环、try等保留unknown，`complete=false`。合成回归能检查简单null/整数冲突，但本真实任务没有展示额外的矛盾剪枝收益。

### CloudFoundry：跨文件信息取得了，目标值关系尚未闭合

[Java原型](JavaPaths.java)用Spoon从18个源码单元选出15个相关方法，保留继承/覆写候选、guard、赋值、返回表达式和调用参数。实际摘要能够展示：

```text
createApplication
  → getHealthUrl
    → getManagementBaseUrl
    → getHealthEndpointPath
      → PathMappedEndpoints.getPath(health/status) 〔未知依赖边界〕
```

第一版只收集词法嵌套guard，漏掉了提前返回的反条件。冻结生成输入前修正通用顺序规则后，status返回同时带有 `!hasText(health)` 和 `hasText(status)`，CloudFoundry URI构造带 `!getUris().isEmpty()`。初版保留为 [java-lexical-initial.json](java-lexical-initial.json)，修正结果为 [java-raw.json](java-raw.json)。这说明不能把“给节点附一个if”当成路径分析。

但必须区分实际实现与预期方案：本轮 `mode=value_effect` 输出的是**带guard的逐方法值/效果摘要**。`max_observed_expanded_paths_per_method=0`；没有把多个方法的返回值、对象字段与测试断言组合成目标等式，也没有将每个调用的通用normal/raise标签串成Java执行轨迹。类型/同名/arity解析仍有多报与漏报，Java `status=ok`只表示原型未标记截断，不表示语义完整。

Java还没有调用实例的fresh identity。相同 `getUris()` 文本可能对应不同调用和不同连续响应，不能直接当成同一符号，更不能据此硬剪枝或送入SMT当纯函数。

Java历史候选的真实校准补上了上轮验证缺口：[历史正确候选通过](java-eval-a-lombok/result.json)，[错误候选失败](java-eval-b/result.json)；另一个[人工status回退见证](java-eval-status-fallback/result.json)也通过——health为空、status给出完整路径，并验证两次交互。它证明这个替代场景在固定测试中可用，不证明原型已自动生成它，也不是系统性的误拒绝率测量。以上校准均未提供给生成Agent。

## 4. 文献与实现支持什么选择

完整来源与工具限制在[路径约束调查](../path-constraints.md)和[工具调查](../tool-survey.md)。这里保留影响本轮决策的证据：

- [SymPrompt，FSE 2024](https://arxiv.org/html/2402.00097v2)从AST提取近似路径条件、返回表达式并引导LLM生成测试，支持轻量分析与模型推理的分工；其目标不是跨过程Mock交互正确性。
- [Path-Sensitive Backward Slicing，SAS 2012](https://jorgenavas.github.io/papers/BackwardSlicing-SAS12.pdf)将路径可行性用于依赖筛选，说明切片和路径条件组合已有方法基础；不是本仓Java/Python即插即用后端。
- [Mock-object generation with behavior，ASE 2006](https://www.microsoft.com/en-us/research/publication/mock-object-generation-with-behavior/)用.NET符号执行从使用方式推导Mock行为，不要求真实依赖实现存在，直接支持在未知实现边界推断行为这一问题设定。
- [Clang RangeConstraintManager实现](https://clang.llvm.org/doxygen/RangeConstraintManager_8cpp_source.html)体现了先维护简单等式/不等式和区间的做法；可借鉴分层检查思想，不能直接作为Java/Python后端。

因此“切片＋路径约束”本身不宜作为唯一新颖性主张。更值得研究的是：在不可见Mock边界上如何组织目标相关约束、保留多个合法场景，并改善交互错误检出而不过约束。

## 5. 选定的下一阶段方案

选择**按测试目标查询的条件化值与调用效果证据**，由Agent选择场景，受支持片段的检查器检查一致性。保留当前项目“生产Agent输出依赖约束”的边界；本轮Mock生成、执行和评分继续留在研究目录。

具体顺序：

1. **切片确定相关范围，再从实际控制结构恢复候选路径。** 包括返回值、异常与调用事件准则。漏掉的test→MUT/跨文件边不能用路径条件补猜；既有JarFiles失败仍是后端准入问题。
2. **给每次依赖调用独立身份。** 保留callsite、调用上下文、调用序号、receiver、参数、返回符号、normal/raise出口及未知写效果。相同文本或参数不意味着相同返回值。
3. **围绕目标组合值关系。** 从普通断言的期望值反向追到对应返回构造，再连接相关参数/返回与可见状态。只展开目标需要的调用，不能把整组方法摘要当成已经完成的传播。
4. **形成少量可检查的候选场景。** 每个场景保留goal、guard、值关系、事件前缀/后继、证据位置与unknown，Agent给出的返回值或异常需要逐项对应这些关系。支持多个候选，不把某个参考Mock当唯一答案。
5. **先做局部一致性检查。** null、常量等式、整数区间、受支持的字符串构造、normal/raise后继和参数转发可以逐步验证。只有前端语义有保证的冲突才能剪枝；其余保留unknown。库调用若没有可信摘要，就保留调用表达式或使用独立验证的真实运行见证，不能擅自改成字符串拼接。
6. **补独立的Mock可替换性证据，再用执行验证。** 路径正确仍可能patch失败；调用条件正确也不等于精确次数/顺序值得断言。执行可提供局部见证，不能据此断言全局正确。

CloudFoundry所需的目标关系可以人工解释为下式；这是下一阶段的表示目标，**不是本轮自动完成的约束**：

```text
goal: actual_health_url == test_expected_health_url
actual_health_url = URI_Builder(management_base, "/", mapped_path)

候选一：hasText(r_health)                         → mapped_path = r_health
候选二：!hasText(r_health) && hasText(r_status)  → mapped_path = r_status
```

分析层还需把 `management_base` 连到固定输入和实际可达的覆写实现，才能检查谁贡献了 `actuator`。这与罗列 `hasText(r_health)` 有实质区别。未知URI语义不应被隐藏；本轮实际JUnit已给出错误候选的反例。

工具选择：Java下一阶段优先验证WALA的SDG/IR/CFG能否可靠提供参数、返回、heap及异常边，Spoon继续负责源码定位和项目不完整时的降级。WALA在本轮没有实跑，不列为已验证生产方案。Python保留AST快速前端，下一步补明确的可见函数摘要和异常控制关系；不要将当前局部成功外推为跨过程成功。不把自研词法图继续扩建成完整程序分析引擎。

**SMT暂缓。** 本轮主要失败是目标值关系未闭合、调用身份与库语义缺失，以及Agent误读证据；求解器不能补出这些前提。只有在关系已可靠编码、仍出现受支持的关联约束矛盾时，再为该片段接Z3/cvc5。SAT只表示当前抽象可满足，不保证真实Mock可执行；unknown不能当不可行。

## 6. 成本、缺口与正式研究门槛

单次分析复跑与冻结JSON结构完全一致，见 [analysis-replay/metrics.json](analysis-replay/metrics.json)：Python约0.044s；Java约2.51s，另有约1.19s helper编译。Java heap上限768MiB不是实测RSS；未测峰值内存。这是单次进程墙钟，不是多次均值。

24次Python容器调用累计约141.19s；六个Java候选编译＋JUnit累计约182.73s，含进程启动和运行时开销，未计校准。部分执行并行，所以不能相加当整轮墙钟。输入体积由约83KB增到约176KB（不含公共指令）；增加摘要的上下文成本明显，实际模型token/费用未测。下一版应按目标按需返回关系，避免重复整个源码和整包摘要。

正式研究建议先做如下准入，再扩大已审核、跨仓库案例：

- 能自动闭合至少一个跨文件转换目标，给出每步来源，并明确未解决边界；支持health直达和status回退，且不会合并连续Mock调用的结果。
- 分开评价调用解析、目标约束组织、Agent生成三个环节。准备一个人工校验的紧凑关系包作为单独上限对照，以区分“分析漏证据”和“模型不会用证据”；不能混入自动条件。
- 使用相同模型与总预算，任务配对、多次生成、随机执行顺序、按仓库留出。比较共同源码、guard、目标闭合的值/效果关系、再加检查器；当前R不能冒充第三项已实现版本。
- 在现有参数/次数变异外，加入错误分支、漏调用、意外fallback、health/status选择与有状态连续响应；同时测合法替代场景误拒绝率。原测试失败者不计变异检出。
- 单列patch/编译/配置失败、原测试通过、Mock Assertion检出、约束冲突/unknown、证据正确性、成本。若紧凑正确关系包仍无收益，应先改Agent推理/验证接口，而不是继续堆分析细节。

## 7. 复现与验证

使用现有 `.venv`、固定Spoon/JDK、已有Docker镜像与冻结依赖。没有添加项目依赖、修改准入记录或扫描/重哈希旧依赖树。Java只挂载runtime-lock列出的89个jar；SourceStore搜索是 `incomplete/access_denied`，仅使用获准17个产品文件加测试，足以编译，不声称读取完整仓库。

```bash
# 源码分析复跑；输出目录必须全新，不覆盖冻结记录
PYTHONPATH=src .venv/bin/python experiments/backward-slicing/path-pilot/run_analysis.py --output /tmp/mock-path-analysis-new

# 独立机制复跑；显式另存，保留原记录
.venv/bin/python experiments/backward-slicing/path-pilot/trace_matrix.py --output /tmp/mock-path-traces-new.json

# 用保存的执行回执重算，不启动目标测试
PYTHONPATH=src .venv/bin/python experiments/backward-slicing/path-pilot/audit_results.py

# 一个冻结Java候选的真实复跑；输出目录必须全新
PYTHONPATH=src .venv/bin/python experiments/backward-slicing/path-pilot/java_evaluate.py --candidate experiments/backward-slicing/path-pilot/candidates/S2.json --output /tmp/mock-path-java-new --old-root /path/to/Stub-Tester

# 六个冻结Python候选复跑；输出目录必须全新
PYTHONPATH=src .venv/bin/python experiments/backward-slicing/path-pilot/evaluate_python.py --old-root /path/to/Stub-Tester --output /tmp/mock-path-python-new experiments/backward-slicing/path-pilot/candidates/{S1,S2,G1,G2,R1,R2}.json

PYTHONPATH=src .venv/bin/python -m pytest -q experiments/backward-slicing/path-pilot
PYTHONPATH=src .venv/bin/python -m ruff check experiments/backward-slicing
```

新生成不可由这些命令精确重现：native模型身份和随机种子未暴露；保留的是当时输入、公共指令、候选与自报读取范围。Docker命令、镜像身份、运行时锁digest、槽外摘要与进程输出均有回执。独立审查指出的Java语义缺口保留为下一阶段边界，没有回修冻结输入来美化生成结果。

最终工程验证：整个后向切片研究目录19项回归通过，Ruff与diff检查通过，本轮文档本地链接检查通过；分类器最后调整后另跑2项定向回归并重算全部回执，分类不变。独立审查确认报告结论与原始记录一致。工程测试通过不等于场景推导或生成质量提高。
