# Java 方法按需展开机制核验

问题：WALA `CISlicer` 是否确实只构造后向追踪到的方法依赖图？
本目录只核验这一机制，不执行目标方法，不代表真实 Jib 或全量门禁已通过。

## 发现与修复

WALA 1.7.2 的 `SDG` 初始为空，`CISlicer` 构造器不加载方法 PDG。
但第一次 `computeBackwardThinSlice` 使用 numbered DFS，内部调用 `getMaxNumber()`，
进而触发 `SDG.eagerConstruction()` 和 `computeAllPDGs()`。
[r1 异常栈](../issue28-java-method-demand-mechanism-20261111-r1/analysis.stderr)
直接记录了这条路径；仅统计构造器阶段会错误地认为已实现按需展开。

修复保留同一个 WALA `CISDG`、原始 Mod/Ref 区域与 predecessor 关系，只将遍历改为
WALA `SlowDFSFinishTimeIterator`，避免依赖最大节点编号。全图 Mod/Ref 扫描和反向索引
仍提前计算，其成本没有隐藏成零。

## 实际结果

[analysis.json](analysis.json) 的 10 项检查全部通过，运行 exit 0：

- 同一调用图包含 5 个可达方法；按需路径加载 4 个 PDG，完整路径加载 5 个。
- `unrelated` 方法确实可达，但不影响所选返回值，按需路径没有加载其 PDG。
- 调用图包含真实 `left → right → left` 递归，两者都进入相关切片，遍历终止。
- 按需与完整分析产生完全相同的切片语句集合。
- 预算设为 2 个方法时，先完成的 `direct` 切片 7 条证据被保留，后续 `left` 方法明确
  标记为未展开边界；结果为 partial，不解释为无影响。

分析总耗时约 2.70 秒，作用域／调用图／指针分析约 2.64 秒。两种路径共用调用图和 JVM，
不据它们的顺序计时宣称加速。此机制使用 NONE 控制通道；真实执行条件需在 Jib 路径另外验收。
`/usr/bin/time`记录整个Java进程峰值RSS为1,229,398,016字节，包含CG／PA及两种分析路径。
原始证据与测量文件由[独立核验收据](independent-review.json)绑定。

## 复现与历史

源码、固定预算及预期见 [pre-registration.json](pre-registration.json)，实际编译和运行
命令见 [receipt.json](receipt.json)，使用的源码保存在 [source-snapshot/](source-snapshot/)。
机制实现为 `DemandMechanismProbe.java`，输入为 `DemandScopeFixture.java`。
只编译和静态分析输入，不调用 fixture 方法。

r1 是原遍历触发全量 PDG 导致预算异常的真实失败。
r2 已恢复全部所需行为，但完成证据验证器错误依赖 `toString()` 中的标点；
r3 改为使用实际 Statement 的方法身份核验。各轮源码均匹配原预登记 SHA，失败没有覆盖。

结束状态：本机制通过；#28 真实 Jib、缓存、完整成本及独立验收仍由主任务继续完成。
