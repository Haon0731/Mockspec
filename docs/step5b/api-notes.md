# Step 5B — Joern v4.0.658 固定制品与查询 API 说明

**状态：本文记录文档/上游源码依据；实际运行结论见 [report.md](report.md)。** 本文固定 PoC
所需的发行制品与公开 API；它不是 Joern 能够正确解析任一不完整项目的保证。

## 固定发行制品（Linux x86_64）

- 版本/tag：`v4.0.658`，发布提交 `f7e618c`，GitHub 于 2026-10-17 发布。
- 下载 URL：
  `https://github.com/joernio/joern/releases/download/v4.0.658/joern-cli-linux-x86_64.zip`
- GitHub release asset SHA-256：
  `afcf8cba64580aa6872987c6111937bdfe39b575280b0cb222f57305d1df1092`
- 对应的上游 release 页面：[v4.0.658](https://github.com/joernio/joern/releases/tag/v4.0.658)。
  运行前必须对下载文件执行 `sha256sum -c`；不能使用 `latest` URL 或安装脚本的
  默认版本分支。上游固定 tag 的安装脚本确实会把非空 `--version` 拼入
  `releases/download/$JOERN_VERSION/...` URL，见
  [`joern-install.sh` L165-L178](https://raw.githubusercontent.com/joernio/joern/v4.0.658/joern-install.sh)。

### JDK 结论

固定 tag 的 README 将 **JDK 21** 列为要求，并说其他版本可能可用但未充分测试：
[`README.md` L24-L27](https://raw.githubusercontent.com/joernio/joern/v4.0.658/README.md)。
因此 Step 5B 应把隔离 JDK 21 的绝对路径置于 `PATH` / `JAVA_HOME`，记录
`java -version`，而不改系统默认 Java。当前在线安装页仍写 JDK 19，和固定 tag
不一致；PoC 以 tag README 的 JDK 21 为准，不能把 JDK 19 当作 v4.0.658 已验证组合。

## 无网络 CPG 构建边界

前端选择是公开且确定的：

```text
joern-parse <staged-input> --language JAVASRC --output <cpg.bin> --frontend-args \
  --no-default-exclude --jdk-path <isolated-jdk-21>

joern-parse <staged-input> --language PYTHONSRC --output <cpg.bin>
```

`JAVASRC` 和 `PYTHONSRC` 是官方列出的语言参数，见
[Frontends](https://docs.joern.io/frontends/)。Java 的 `--fetch-dependencies` 是
“attempts to fetch dependency jars”的**可选** flag，文档列为默认 `false`；PoC 不传
此 flag，也不传 Maven/Gradle 工作区或未审核 jar。若需要类型信息，只能传入审核后
冻结副本中明示的 `--inference-jar-paths`。见
[Java frontend arguments](https://docs.joern.io/frontends/java/)。

### Java `src/test` 的 v4.0.658 默认排除（实测遗漏的根因）

这不是解析成功却丢失测试方法的正常行为。v4.0.658 的 Java frontend 将
`JavaSrc2Cpg.DefaultIgnoredFilesRegex` 初始化为 `JvmDefaultIgnoredFolders`：
[JavaSrc2Cpg.scala](https://github.com/joernio/joern/blob/v4.0.658/joern-cli/frontends/javasrc2cpg/src/main/scala/io/joern/javasrc2cpg/JavaSrc2Cpg.scala)。
后者的上游注释明确说它会在目录遍历阶段剪枝 *test roots*，并把 `test` 列为一个完整
路径分段（同时匹配 `/` 与 `\\`）：
[package.scala](https://github.com/joernio/joern/blob/v4.0.658/joern-cli/frontends/x2cpg/src/main/scala/io/joern/x2cpg/frontendspecific/javasrc2cpg/package.scala)。

正确的显式包含方法是把 hidden frontend flag `--no-default-exclude` 放在
`--frontend-args` 后。v4.0.658 的共享 parser 将它实现为
`withDefaultIgnoredFilesRegex(Nil)`：
[X2Cpg.scala](https://github.com/joernio/joern/blob/v4.0.658/joern-cli/frontends/x2cpg/src/main/scala/io/joern/x2cpg/X2Cpg.scala)。
它故意是 hidden，所以不会出现在通常的 `javasrc2cpg --help` 输出中。`--exclude` 和
`--exclude-regex` 都只能额外排除文件，不能反向 include `test`。此开关保留
`src/test/...` 安全逻辑路径、原 Java 包名、类名和源码内容；不需要合成路径或重写源码。

Python 前端的 `--venvDir`/`--venvDirs` 是可选的本地虚拟环境目录参数；对于本轮
冻结输入，应省略它们（或只传审核副本内的只读 venv），并以网络 namespace 禁用作为
真实的禁止联网控制。官方文档没有声明 `pysrc2cpg` 的无网络 sandbox 保证：临时目录
隔离本身不是安全沙箱。见 [Python frontend arguments](https://docs.joern.io/frontends/python/)。

所有输入目录应仅含复制出的允许源码；不要启用 `--enable-file-content`，因为它会把原始
源文件内容写入 `FILE.CONTENT`，而不是只保留节点的位置/片段。该开关默认关闭，见
[X2CPG arguments](https://docs.joern.io/frontends/x2cpg/)。

## 固定 CPGQL 查询面

固定 `.sc` 脚本可以只接受 CPG 与输出路径参数；不得把 Scala/CPGQL 文本、正则或
任意 traversal 从 Agent 传入。`joern --script ... --param ...` 是官方支持的非交互
执行方式，见 [Interpreter](https://docs.joern.io/interpreter/)。

下列查询是公开、适合纳入白名单的最小集合（筛选目标应在脚本中写死为该审核 case 的
方法/调用名，并将结果投影为 id、label、code、filename、line/column）：

| 所需事实 | 固定 CPGQL traversal | 上游依据 / 解释 |
| --- | --- | --- |
| 方法、调用、断言/MUT/外部调用候选的位置 | `cpg.method`、`cpg.call`；调用参数用 `.argument`，位置字段用 `.filename`、`.lineNumber`、`.columnNumber` | `call` 是 CALL 节点/调用点；节点可带文件和行号，[node-type reference](https://docs.joern.io/cpgql/node-type-steps/)。调用名、代码、参数本身均应记录，避免仅以可能不完整的解析 name 判定。 |
| 条件与嵌套语法事实 | `cpg.controlStructure`（必要时从目标调用 `astParent` 向上取固定层数并过滤 control structure） | `controlStructure` 枚举控制结构，[reference card](https://docs.joern.io/cpgql/reference-card/)；CPG schema 定义 `CONTROL_STRUCTURE` 和 `CONDITION` 关系，[CPG spec](https://cpg.joern.io/)。 |
| 控制依赖 | `<target-CFG-node>.controlledBy`、`<condition-CFG-node>.controls` | 官方定义：`controlledBy` 递归返回控制依赖条件，`controls` 返回该节点控制的节点，[Control-Flow Steps](https://docs.joern.io/cpgql/control-flow-steps/)。这比 AST 父子关系更适合标为“控制依赖”。 |
| 源到条件/断言的 data-flow | `sink.reachableBy(source)`；需要路径证据时 `sink.reachableByFlows(source)` | 官方定义：前者返回可达 source，后者返回 flow paths，[Data-Flow Steps](https://docs.joern.io/cpgql/data-flow-steps/)。**只有返回的 flow 才能标成 Joern data-flow；空结果和未解析调用均不得推断为无数据依赖。** |

`reachableByFlows` 的 source/sink 必须由脚本内固定的审核 case 标识选出，例如一个固定的
外部调用返回值节点作为 source，目标 `if` 条件内的调用/identifier 作为 sink；它不是
“任意 CPGQL”接口。Joern 的调用建模包含算术/比较/赋值等 `<operator>.*` CALL，因而判断
条件时要同时记录其 operator calls，见 [Calls](https://docs.joern.io/cpgql/calls/)。

### CFG、CDG、REACHING_DEF 的证据等级

CPG 规范定义：CFG 用 `CFG` 边；PDG 由 `REACHING_DEF`（DDG）与 `CDG`（控制依赖）
边组成，且 `REACHING_DEF` 带 `VARIABLE` 属性，见 [CPG specification](https://cpg.joern.io/)。
为避免依赖未在版本化公共 CPGQL 文档中承诺的低层 generated-edge traversal 名，Step 5B
应采用两个可验证出口：

1. 对控制依赖使用上述稳定的 `controls` / `controlledBy` API；
2. 导出每个方法的 PDG，并保存原始 DOT 与解析后的事实。官方支持
   `joern-export --repr pdg --out <dir>` 导出 PDG，见
   [Exporting Graphs](https://docs.joern.io/export/)。

运行时再以 v4.0.658 二进制的 `joern-export --help` 和 `joern --script` compile 作为
验收，确认实际输出中是否含 `CFG`/`CDG`/`REACHING_DEF` 标签及边属性。本文**不**将
未实测的低层 `.cfgNext`/`.reachingDef` 方法名写进 PoC 合同。

## joern-slice：限定用途与固定参数

上游 `JOERN_SLICE.md` 定义的 `data-flow` 模式从 call arguments 作后向 data-flow slicing，
输出 JSON；其 shared options 包含 CPG/source 输入、`-o/--out`、`--file-filter`、
`--method-name-filter`，`data-flow` 还支持 `--slice-depth`、`--sink-filter`、
`--end-at-external-method`。来源：
[JOERN_SLICE.md](https://github.com/joernio/joern/blob/master/joern-cli/JOERN_SLICE.md)。

### v4.0.658 CLI 限制：共享全局参数被 `data-flow` 覆盖

**实测已确认，不能把 `joern-slice` 当作本 PoC 的可靠范围/输出隔离器。** 固定 tag 的
parser 把 `data-flow` 定义为 `.action((_, _) => DataFlowConfig())`，无条件构建一个新的
配置对象；它不复制此前累积的 `BaseConfig` 字段。源码见
[JoernSlice.scala](https://github.com/joernio/joern/blob/v4.0.658/joern-cli/src/main/scala/io/joern/joerncli/JoernSlice.scala)。
而 `BaseConfig` 对 `outputSliceFile` 的默认值是 `slices`，对 file/method filters 和
parallelism 的默认值是 `None`；`DataFlowConfig` 的 child options 只定义
`--slice-depth`、`--sink-filter`、`--end-at-external-method`，没有这些共享选项的替代项。
见 [slicing/package.scala](https://github.com/joernio/joern/blob/v4.0.658/dataflowengineoss/src/main/scala/io/joern/dataflowengineoss/slicing/package.scala)。

因此，实际运行
`joern-slice data-flow --out /work/slice --parallelism 2 --method-name-filter abort --slice-depth 4 ...`
却写出默认 `slices.json`、并产生未按 method filter 限定的全局结果，和源码一致。把全局
args 放在子命令前也会被新建 `DataFlowConfig` 覆盖（有的排列还会只打印 usage 并以 0 退出）。
在本固定版本，未能从上游 source 找到一个可同时保留 `--out`、`--parallelism` 和
`--method-name-filter` 的受支持调用顺序。

故本轮应保留该实测限制，不能把 `joern-slice` 的这些 flag 当作资源、路径或 case 范围
控制；将固定 CPGQL 查询和 `joern-export` 作为主要事实出口。切片即使成功也只是 DDG
的后向证据，不是可执行路径、分支可满足性证明或 scenario solver。

## 最小机器可读输出合同

每条事实写为 JSONL，至少含：`case_id`、`language`、`kind`、`evidence_level`
(`syntax`/`control_dependency`/`data_flow`/`unresolved`)、`node_id`、`code`、
`safe_logical_path`、`line`、`column`、`method_full_name`。关系还应有
`src_node_id`、`dst_node_id`、`edge_kind`、`variable`（仅 `REACHING_DEF` 有时可用）。
任何无位置、`<unknown>` 文件、external/methodFullName 未解析或 query compile failure
都必须单独输出诊断，不得伪装为“未发现”。
