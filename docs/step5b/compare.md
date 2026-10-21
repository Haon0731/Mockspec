# Step 5B — AST/Spoon 与 Joern 实测对照

## 结论

**当前将 Joern 作为 Java/Python 统一分析后端：No-Go。**

Joern 在审核后的 Python 输入和不含测试目录的 Java 输入上，确实输出了 AST 之外的
`CFG`、`CDG`、`REACHING_DEF` 图边，并由固定查询返回了候选 data-flow paths。这证明
其图分析能力真实存在。但 Java 的默认成功构建 `java-build-2` 排除了 `src/test`；显式
加入 `--no-default-exclude` 的完整输入构建 `java-build-3` 又在生成 DDG 时失败。因此
`java-query-2`、`java-export-1` 只能证明生产源码子集的能力，不能证明测试断言到 MUT 的
完整分析链路。Python 的 CPG 可用；深度 4 的 `joern-slice` 能完成，但过滤/输出参数疑似
未被 CLI 应用，深度 20 又在 180 秒超时；固定查询所得 6 条路径也没有到达测试断言。

现阶段主路径应继续采用 **Python AST + Spoon + 小型适配层**。Joern 可保留为隔离的
实验辅助后端；在 Java 完整输入构建缺陷解决、切片范围和耗时可控之前，不接入 Scenario
Agent，也不把不完整 CPG 的结果提升为场景结论。

## 证据分层

下文严格区分三类证据：

1. **语法事实**：AST 节点、源码位置、词法祖先和 true/false 分支；不等于 CFG、控制依赖
   或数据流。
2. **图边事实**：Joern CPG 中实际存在的 `CFG`、`CDG`、`REACHING_DEF` 等边；它们仍不
   等于可执行路径或路径可满足性证明。
3. **未解析关系**：缺失调用声明、`<unresolvedSignature>`、缺失测试节点、构建/切片失败；
   空结果不得解释为“无依赖”。

## Java JarFiles

### Spoon：完整审核输入上的语法基线

`spoon-1` 成功解析允许的 MUT、测试和 processor 文件，输出 5 个方法、109 个调用、
1 个 `if`、2 个三元表达式和 56 个变量读取。

- MUT `JarFiles.toJibContainerBuilder` 位于 `JarFiles.java:50-94`；测试方法位于
  `JarFilesTest.java:48-102`，测试在第 63 行调用 MUT。
- 19 个断言位于测试第 71–88、97 行。
- 条件事实完整：`if` 位于 60–72 行；69–71 行三元表达式位于该 `if` 的 false 分支；
  77–79 行三元表达式区分 `customEntrypoint.isEmpty()` 的 true/false 分支。
- 关键依赖调用均返回位置：69 行 `getJavaVersion()`，74 行 `createLayers()`，75 行
  `getEntrypoint()`，78 行 `getJvmFlags()` 和 `computeEntrypoint(...)`。
- 56 个变量读取中 49 个链接到输入内参数或局部声明。例如 `processor` 的 69/74/78 行
  读取链接到 51 行参数，`customEntrypoint` 的 77/79 行读取链接到 75 行局部声明。
- 7 个变量读取未解析；109 个调用声明全部未解析。即使 MUT 和部分候选实现同在输入中，
  Spoon 本次也没有形成可用的调用声明边。

这些是可靠的源码位置、词法包含和局部引用事实。Spoon 没有输出 CFG、跨语句 def-use、
backward slice 或测试结果到依赖响应的数据流。

### Joern：不完整 Java CPG 上的真实图事实

`java-build-2` 命令成功，但使用 Java frontend 默认排除规则；其 `java-export-1` 的
`file_nodes` 只有 `JarFiles.java`、`StandardExplodedProcessor.java` 和 `<unknown>`，没有
`JarFilesTest.java`。因此这不是完整审核输入上的成功。

在这个生产源码子集上：

| 事实 | 实测结果 |
| --- | --- |
| 图规模 | 545 nodes / 2,352 edges |
| CFG | 158 edges |
| CDG | 29 edges |
| REACHING_DEF | 504 edges |
| CALL | 55 edges |
| 固定查询候选 data-flow paths | 19 条，`paths_truncated=false` |
| 未解析调用签名 | `java-query-2` 中 30 个 `methodFullName` 含 unresolved |

Joern 的增量价值可直接观察到：第 60 行条件通过 `CDG` 控制 61–69 行节点；第 69 行
比较又控制 Java 8/11 两个三元分支。固定查询还返回了例如
`getEntrypoint@75 → customEntrypoint@75/77 → conditional@77 → entrypoint@76/82 →
setEntrypoint@81` 的 `REACHING_DEF` 路径。它比 Spoon 的词法祖先更接近 Scenario Agent
所需的控制/数据依赖事实。

但这些路径只能称为 **CPG 图路径**：查询本身已声明不证明 path feasibility；部分路径把
多个调用参数和 builder 链连接在一起，缺失依赖使其语义精度无法从当前输入确认。更关键
的是图中没有测试、断言或测试到 MUT 的调用链，不能从这些路径反推测试场景。

第 69 行 `getJavaVersion()` 和数值比较 `<operator>.lessEqualsThan` 都出现在 CPG/slice
节点中，但深度 4 slice 没有给出从该调用到数值比较的直接 `REACHING_DEF` 边。因此只能
报告二者的语法嵌套和控制分支用途，不能宣称已实测“依赖返回值流入数值比较”。

### 完整 Java 输入失败

`java-build-3` 加入 `--no-default-exclude` 后，命令退出 1。失败发生在默认 overlays 的
`ReachingDefPass`：

```text
NoSuchElementException: next on empty iterator
TrackedTypeRef.equals
UsageAnalyzer.isAlias
```

因此其残留 `cpg.bin` 不能作为完整 DDG 成功产物，也不能用 `java-build-2` 替代后宣称
Java 案例通过。现有证据无法确定：

- 完整 Java 测试加入后应生成哪些 CFG/CDG/REACHING_DEF 边；
- 断言是否能经 `buildPlan`、MUT 返回值追踪到 69/74/75/78 行依赖响应；
- `ArtifactProcessor` 调用是否能链接到审核输入中的 processor 方法；
- Spoon 与 Joern 对 `StandardExplodedProcessor` 方法位置相差一行的根因和哪一方更准确。

## Python flask_308

### CPython AST：完整、低成本的语法基线

`python-ast-1` 成功解析两个允许文件，并准确给出：

- MUT `abort`：`helpers.py:265-285`；
- 外部状态读取 `_cv_app.get(None)`：282 行；
- true 分支调用 `ctx.app.aborter`：283 行；fallback `_wz_abort`：285 行；
- 测试方法：`test_308abort.py:48-126`；`pytest.raises` 在 102 行，MUT 调用在 103 行；
- 断言分别位于 120 行 true 分支和 126 行 false 分支。

AST 的 guard polarity 能表达词法分支，但没有 CFG、def-use、异常控制流或调用解析。

### Joern：完整 CPG 上的图事实

`python-build-2` 成功，`python-export-1` 同时包含 `helpers.py` 和
`test_308abort.py`：

| 事实 | 实测结果 |
| --- | --- |
| 图规模 | 656 nodes / 3,879 edges |
| CFG | 338 edges |
| CDG | 59 edges |
| REACHING_DEF | 777 edges |
| CALL | 126 edges |
| 固定查询候选 data-flow paths | 6 条，`paths_truncated=false` |
| 未解析调用签名 | `python-query-2` 中 19 个 `methodFullName` 含 unresolved |

Joern 补出了 AST 没有的真实图边。固定查询显示 `_cv_app.get(None)@282 → ctx@282`，并
进一步到达 282 行条件或 283 行 `ctx.app`；另有路径继续经过 frontend 临时变量、
`code` 和 `*args`。这些可用于说明外部状态读取参与了条件和 true 分支调用，但它们仍是
静态 `REACHING_DEF` 候选，不证明哪条异常出口在具体参数下执行。

当前 6 条路径均未到达测试文件、`pytest.raises` 或 120/126 行断言，所以仍不能回答
“哪个依赖响应导致哪个测试结果”。测试与 MUT 的语法节点虽同时存在于 CPG，跨调用的
场景关联仍需 Mock Spec 自己建模。

### Joern Slice

首次 Java/Python slice 因工具把输入解释为 `/work/cpg.bin` 而文件未复制到该位置失败，
不计为能力结论。修正输入位置后有两组不同结论：

- `python-slice-2` 使用深度 20，在 180 秒超时并退出 124，没有生成切片；峰值聚合 RSS
  为 756,036 KiB，cgroup `memory.peak` 为 739,520,512 bytes。
- 深度 4 的 `java-slice-3` 在 11.079 秒完成，生成 97 nodes / 125 edges、77,053 bytes；
  但其来源仍是排除测试的 `java-build-2` CPG。
- 深度 4 的 `python-slice-4` 在 9.728 秒完成，生成 237 nodes / 226 edges、95,145 bytes。

这证明浅层 data-flow slice 在本轮资源上限内可以生成，不能再笼统称 Joern Slice 不可用。
但 CLI 行为没有满足当前固定调用的预期：命令传入 `--out /work/slice`，实际仍写默认
`slices.json`；Python 命令传入 `--method-name-filter abort`，产物的 `parentMethod` 却包含
模块、`abort`、测试模块和 `test_abort_scenarios` 全部 4 个方法范围。因此不能宣称方法
过滤已生效。`--parallelism 2` 是否被解析也没有输出证据；无论该参数是否生效，阶段的
2 CPU 和 256 PID 上限仍由 Docker 约束提供。Java 完整 CPG 未成功，所以浅层 Java slice
也不能补足完整案例验证。

## 成本对照

下表的 RSS 是 50 ms 采样的容器可见进程聚合值；cgroup peak 是整阶段内存峰值，包含
非 RSS 内存，二者不可互换。

| 阶段 | 结果 | 时间 | 聚合 RSS 峰值 | cgroup peak |
| --- | --- | ---: | ---: | ---: |
| Spoon Java | 成功 | 18.295 s | 170,356 KiB | 273,207,296 B |
| CPython AST | 成功 | 0.188 s | 20,628 KiB | 9,519,104 B |
| Joern Java build-2 | 成功但排除测试 | 18.229 s | 301,724 KiB | 357,720,064 B |
| Joern Java build-3 | 完整输入 DDG 失败 | 28.775 s | 306,712 KiB | 388,816,896 B |
| Joern Java query-2 | 仅不完整 CPG | 14.818 s | 507,856 KiB | 461,746,176 B |
| Joern Python build-2 | 成功 | 10.155 s | 256,524 KiB | 252,702,720 B |
| Joern Python query-2 | 成功 | 15.011 s | 507,936 KiB | 460,890,112 B |
| Joern Python slice-2 | 180 s 超时 | 180.027 s | 756,036 KiB | 739,520,512 B |
| Joern Java slice-3 | 深度 4 成功；不含测试 | 11.079 s | 782,064 KiB | 779,939,840 B |
| Joern Python slice-4 | 深度 4 成功；过滤疑似未生效 | 9.728 s | 573,944 KiB | 552,566,784 B |

Joern 固定发行包解压后约 2.16 GB，另需隔离 JDK 21。CPG 文件本身很小（本次约
0.11–0.13 MB），主要成本来自工具环境、JVM 启动、图查询和切片，而非案例存储。

## 选择与适用边界

### 主方案

继续选择 **OpenAI Agents SDK + Python AST + Spoon + 小型固定工具适配层**：

- 两个案例在本次运行中都返回测试、MUT、断言、依赖调用和确定性逻辑位置；
- 对缺失依赖显式标为 unresolved；
- 资源和失败模式更容易隔离；Python AST 的成本尤其低；
- 输出可严格限制为 UnifiedTask 的允许逻辑路径和短文本。

### Joern 的保留位置

Joern 只作为可选、离线、只读的图证据后端。只有满足以下条件后才重新评估 Go：

1. 固定版本能在包含审核测试的 Java 输入上完成 overlays，且失败时不留下可误用的“成功”
   CPG；
2. 固定查询在两种语言上都能从依赖响应到条件，并至少连接到 MUT 输出或测试结果；
3. slice 的输出位置和方法/文件/sink 固定过滤经产物证明生效，并能在目标深度与资源上限
   内结束；
4. unresolved、`<unknown>`、缺测试和 query failure 都成为硬降级信号。

不建议为填补本轮缺口自行实现新的 CFG/DDG 引擎。可行替代是保留 AST/Spoon 语法事实，
将现有 Joern Python 图路径作为带置信边界的辅助证据，并向上游提交 Java
`TrackedTypeRef.equals` / `UsageAnalyzer.isAlias` 的最小复现或评估后续固定版本。

## 仍需 Mock Spec 实现

无论采用哪种后端，以下都不是本轮工具自动给出的结论：

- 将外部依赖调用映射为可 mock 的响应槽位；
- 把条件的合取、析取、替代分支和异常出口组织为 Scenario Contract；
- 区分静态数据流路径、控制依赖与可执行路径，并处理路径可满足性；
- 把测试断言、MUT 输出与依赖响应关联，同时在输入不足时返回“无法确定”；
- 对 masked test、未解析调用和缺失依赖执行保守降级，而不是补猜 Ground Truth。

本报告仅使用 `/tmp/mockspec-step5b-runs` 的隔离运行输出和 `docs/step5b` 记录；没有读取
原始仓库、Reference Mock、评分结果或隐藏任务规格。
