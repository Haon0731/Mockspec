# Step 5B：Joern 真实案例可行性验证

日期：2026-10-21；基线：`a0563fa`。**结论：No-Go，当前不将 Joern v4.0.658 作为 Java/Python 统一 Scenario 分析后端。**

实际运行证明了 Joern 的增量价值：Python 完整审核输入及 Java 生产源码子集均有真实 `CFG`、`CDG`、`REACHING_DEF` 边，固定查询和深度 4 切片能输出机器可读证据。但 Java 默认排除了测试；启用 `--no-default-exclude` 后，完整审核输入在 `ReachingDefPass` 抛出 `NoSuchElementException`。缺测试的成功图不能替代完整案例通过。此结论仅适用于本次固定版本、输入和配置，不是对所有 Joern 版本的否定。

## 交付与证据

- [详细 AST/Spoon 对照](compare.md)：源码位置、语法/真实图边/未解析关系、分支、响应依赖和能力缺口。
- [版本和制品锁](environment-lock.json)、[固定版本官方 API 依据](api-notes.md)。
- [输入清单](input-manifest.json)：允许 source_id、原逻辑路径、行区间和投影哈希。
- [全部尝试、诊断与资源记录](evidence/attempts.json)：包含失败，不只保留成功样本。
- [Java Spoon](evidence/java-spoon.json)、[Python AST](evidence/python-ast.json)。
- [Java Joern 查询](evidence/java-joern.json)、[Python Joern 查询](evidence/python-joern.json)。
- [Java 实际图边](evidence/java-edges.json)、[Python 实际图边](evidence/python-edges.json)。
- [Java 子集切片](evidence/java-slice.json)、[Python 切片](evidence/python-slice.json)。切片短文本限 240 字符，原制品大小和 SHA-256 另存运行记录。
- [复现步骤与工作区前置条件](reproduce.md)。
- [固定 PoC 脚本](poc/)、[交付脚本哈希](evidence/probe-hashes.json)。未接入 Agent，无动态 Scala 查询入口。

## 环境与隔离

实测 Joern **4.0.658**、Temurin **21.0.12.1+1**、Spoon **11.2.1**、CPython **3.12.13**。Joern/JDK 下载制品已逐字节校验 SHA-256；完整值见锁文件。固定 tag README 要求 JDK 21，不能沿用移动文档中的 JDK 19。Spoon 使用相同独立 JDK 21，消除了 Step 5A 的 Java 11 启动问题。没有改系统 Java、旧实验 venv、Stub-Tester 或 Prompt。

使用固定内容 ID 的 Ubuntu 22.04 Docker 镜像，工具安装在 `/tmp/mockspec-step5b-tools`，分析进程只看到独立投影 `/input`。Java 保留包、类和 `src/main/java`、`src/test/java` 路径；Python 仅合并已允许的 imports 与 MUT 区间，其余行为空白。没有挂载完整 Mock Spec/Stub-Tester 仓库，也没有依赖缓存或 Docker socket。

每阶段：`--network none`、只读根、只读绑定、UID/GID 1000、drop ALL capabilities、no-new-privileges，2 CPU、4 GiB 内存且禁止额外 swap、256 PID、JVM heap 2 GiB、命令 180 秒（5 秒强制终止宽限）、宿主轮询 240 秒。每次 Docker 调用另限 30 秒；收集和清理另计时间，不将 240 秒表述为整个宿主脚本的严格总时限。工作 tmpfs 1 GiB，临时 tmpfs 256 MiB；后者允许执行，仅供 Joern zstd JNI 加载。输出文件限 8 MiB；最终脚本收集再限 32 文件/64 MiB。

[`preflight-2` 输出](evidence/preflight.json)验证了无网络路由、旧仓库不可见、输入/根不可写、无 Docker socket 和上述限制。**这是有实际容器隔离的运行，不是把临时目录称作安全沙箱**；仍信任宿主内核和 Docker daemon，不声称可抵抗内核漏洞。下载工具发生在准备阶段；案例分析阶段不能联网，未启用 `--fetch-dependencies`，未运行项目构建或测试。

## 核心结果

| 能力 | Java | Python |
| --- | --- | --- |
| 断言/MUT/依赖位置 | Spoon 完整定位；Joern 默认图漏测试，完整图 DDG 失败 | AST/Joern 都定位 MUT、依赖和断言 |
| 条件与嵌套 | Spoon 词法分支；Joern 子集有 CDG 和 CFG | AST true/false；Joern 有条件 CDG，fallback 不属于 else |
| 响应到条件 | 子集 `getEntrypoint → customEntrypoint → isEmpty/conditional`；不能证明到测试结果 | `_cv_app.get(None) → ctx → isNot` 真实图边；不证明 aborter 的异常行为 |
| 组合/替代/异常 | 可区分词法候选与局部控制关系；不证明可满足性或异常出口 | true 分支调用与后续 fallback 可区分；缺外部实现，无法证明哪次抛异常 |

固定查询 Java 19 条、Python 6 条 data-flow paths 均未截断；Java 只代表不含测试的子集。Python 路径没有到达测试断言。图的过近似、临时变量和未解析调用均保留为限制，**数据流路径不是可执行路径**。不可由无边推断无语义依赖。

深度 20 的 Python Slice 超时；深度 4 后 Java 子集 97 节点/125 边，Python 237 节点/226 边。此调用形态下未观察到部分全局选项生效：指定输出名仍生成默认 `slices.json`，Python 指定 `abort` 过滤仍包含四个方法；本轮不声称方法过滤或工具线程参数生效，CPU/PID 由容器硬限制。参数放在子命令前还会报错但退出 0，因此必须检查产物和诊断。

## 时间、内存、存储

单次观测，不是性能统计或稳定性保证。RSS 为 50ms 采样的进程聚合峰值，可能漏瞬时峰值、重复计算共享页；cgroup memory.peak 包括页缓存等，另存原始记录。Spoon 时间包含编译固定 probe。下表时间为容器内命令时间，不含宿主启动/收集，宿主时间另有记录。

| 阶段 | 秒 | 聚合 RSS MiB | 制品字节 |
| --- | ---: | ---: | ---: |
| Spoon Java | 18.295 | 166.36 | JSON 142593 |
| Python AST | 0.188 | 20.14 | JSON 4704 |
| Java build-2，漏测试 | 18.229 | 294.65 | CPG 114348 |
| Java build-3，完整输入失败 | 28.775 | 299.52 | 残留 75448，不可用 |
| Python build-2 | 10.155 | 250.51 | CPG 131974 |
| Java 子集 query-2 | 14.818 | 495.95 | JSON 54869 |
| Python query-2 | 15.011 | 496.03 | JSON 51259 |
| Java 子集 export-1 | 2.689 | 175.35 | GraphML 603596 |
| Python export-1 | 3.013 | 197.09 | GraphML 865262 |
| Python slice-2，深度20超时 | 180.027 | 738.32 | 无切片 |
| Java 子集 slice-3，深度4 | 11.079 | 763.73 | JSON 77053 |
| Python slice-4，深度4 | 9.728 | 560.49 | JSON 95145 |

Joern 压缩制品 1,788,768,508 B，解压 2,158,404,213 B；JDK 压缩制品 207,473,347 B，Spoon JAR 15,218,742 B。上述工具获取成本不能用百 KB 的 CPG 大小掩盖。临时缓存还含解压 JDK、独立 Python、下载分块等，复用前可清理下载中间文件；本次 `du -sh` 实测工具缓存 4.4 GiB、全部运行记录 3.5 MiB、授权投影目录 128 KiB（均为文件系统占用，含目录/块开销）。无全量数据集副本。

## 失败与输出审查

保留诊断的失败包括：初次 Docker cp 无法读取 tmpfs 输出（改为命名空间内 cat）；离线 hostname 查询告警（固定 hosts）；zstd 因 noexec 临时目录失败（仅隔离 `/tmp` 改为 exec）；固定 Scala probe 的 AstNode.method API 不适用（改用 AST 所属方法定位）；Java 默认排除 test；完整 Java DDG 异常；Slice 默认 CPG 路径和参数顺序问题、退出 0 无产物、深度 20 超时。均未作为成功事实。Python frontend 另报告 `Using order fallback for try statement catch body`，因此异常控制流尤其不能作为精确语义保证。

输出审查扫描了工具 stdout/stderr、GraphML、JSON，未发现宿主 `/home/haon/` 路径。FILE 节点仅允许逻辑输入及 `<unknown>`/`N/A` 哨兵。`/input`、`/work`、`/tools` 是容器路径；Python `/globals.py:...` 是未解析类型符号，不能解释为读取了该文件。脚本中的宿主工具/批准 snapshot 路径属于宿主配置，不传给分析查询。此审查结合挂载边界提供证据，不是任意泄漏形式的完备证明。

## 决策与后续最小任务

暂保留 **OpenAI Agents SDK + Python AST + Spoon + 小型固定适配层** 为主方案。本轮只验证分析工具，不改变现有 Agent。Joern 有真实增量图证据，但完整 Java 失败使跨语言统一后端验收不通过；不自建 CFG/DDG 引擎补洞。

若后续重新立项，最小任务是使用同一冻结输入验证一个修复了该 DDG 问题的固定版本，并先验证“含测试 FILE 节点 + overlays 成功 + 非空相关图边”；再验证 Slice 参数行为。未审核源码不得作为上游复现附件直接发布。没有证据前保留显式 unknown，不扩展输入到旧仓库。

Mock Spec 仍需设计：断言与目标结果映射、可 mock 响应槽位、条件合取/替代与异常出口表示、源位置到 source_id 的校验、证据置信层级与不足信息降级，以及路径约束的场景级推理。LLM 可提出候选解释，但不能把缺失依赖或 Masked Test 信息补猜成事实。

完成 PoC 与 No-Go 决策后停止；未修改 V0.1 Prompt，未接入 V0.2，未生成 Mock，未使用 Reference Mock。

## 交付核验

`poc/verify.py` 已通过：允许路径/行区间、图与切片非空结构、失败状态、脚本哈希、冻结 Prompt SHA-256。Python 语法解析、shell 语法检查和文本空白检查通过。Docker 查询确认本轮命名容器全部清理。未运行旧实验或全量项目测试。

最终门禁补验：未带诊断标志的 `java-query --build-attempt 2` 在启动容器前退出 1；`python-export-2` 成功通过新增 GraphML 非空校验，并记录上游 CPG SHA-256 和清理成功。历史尝试记录保留原样，归档层显式标记“工具退出 0 但无切片”的失败，未用新门禁伪造旧运行状态。
