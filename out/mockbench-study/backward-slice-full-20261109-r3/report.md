# 全量目标导向后向候选切片：首轮实现与结果

日期：2026-11-09。最终运行目录为本目录（r3）；全部165例均已尝试，无未选择项或超时。
研究入口：[batch_slice.py](../../../experiments/backward-slicing/batch_slice.py)。
本次交付是“目标定位→观察准则→有界选源→后向候选图→逐例证据/失败”的可运行链路，
不是完整SDG、精确Mock规格或生成质量验证。没有执行目标测试、模型生成或旧Stub-Tester实验。

## 固定输入与方法

复用既有冻结清单：Java59、Python106，全部通过FrozenDataset实际加载。
[清单](inventory.json)与[实现/配置摘要](run-manifest.json)记录原Case ID、snapshot和来源身份。
不复制数据集，不读取隐藏Mock答案，不扫描或补算依赖实体哈希。

方法是测试目标导向的源码候选切片：Python标准库AST＋已有固定Spoon解析器，
观察起点限定在selector对应测试函数/方法。默认最多8文件、200000字节选定源码、一层
导入扩展、每例后端30秒；Java堆768MiB、图上限2000节点/10000边，Python上限10000节点/
50000边。单个原子源码读取可超字节预算一次，随后停止；读取不计入后端时限。
Python批处理关闭普通语句前驱边，避免“前面执行过”被当成数据依赖；保留赋值、词法条件、
候选返回/调用事件、后向闭包。Java保留候选参数/返回关系。两者均无精确heap/别名、完整
异常控制流、动态派发或路径可行性分析，也不声称识别出真正的Mock集合。

## 全量结果

| 指标 | Java | Python | 总计 |
| --- | ---: | ---: | ---: |
| 输入加载与目标范围定位 | 59 | 106 | 165 |
| 生成候选图（partial） | 49 | 106 | 155 |
| 构图失败 | 10 | 0 | 10 |
| 闭包触及某个产品源码候选 | 21 | 55 | 76 |
| 已构图但未触及产品源码 | 28 | 51 | 79 |
| 未选择到产品源码 | 2 | 38 | 40 |
| 文件/字节选择预算不足 | 26 | 10 | 36 |
| 只有候选事件、无规则识别的显式观察 | 3 | 6 | 9 |

共540条准则记录，含同一行不同种类的观察，不等于540个独立测试场景。
各类缺口可重叠，不可相加作为失败总数。构图失败10例中，9例达到节点上限，1例重复类型。
5例空支持模块被单独留证并省略，身份/哈希拒绝仍会失败。

155个候选图中，152例含数据候选边、87例含词法控制边、17例含Java参数绑定候选边。
这些计数只说明边存在：Python的名称候选调用也可能只连到测试辅助函数；不代表跨过程
恢复正确，更不能把76/165称为MUT或Mock恢复成功率。146例包含未解析调用，里面也包括
断言框架与标准库调用，不能解释为146例Mock分析失败。

## 真实案例揭示的断点

- [testQueryConfigAsJson](b683204eee44c1e69b82.json)：4个断言闭包均到达`ConfigFileController.java`
  （source:3），图保留局部数据、词法条件及跨过程候选。未知边界仍在，未证明派发或路径。
- [should_use_application_uri](e25ef6aedd4e3605bbe0.json)：5个断言闭包到达Application、
  CloudFoundryApplicationFactory与DefaultApplicationFactory；存在候选参数/返回与条件边。
  这说明能提供跨文件证据，不能据此推导唯一正确URL、调用次数或Mock值。
- [explodedStandard_basicInfo](2a37d7a1edfad4e10a7d.json)：19个断言中只有第87行闭包到达
  `CommonCliOptions.java`（source:1），没有到达选入的目标`JarFiles.java`（source:4）。
  它是“触及产品源码”却未连接目标方法的具体反例，因此必须保持逐准则映射。
- [langflow_189](5b83af29ea4ed3c9d4ce.json)与[crawl4ai_164](2998a0df0d25e226e025.json)：可见测试通过Path拼接、read_text、
  exec/compile载入函数；当前定位器不支持该路径表达式/加载器模式。输入中有线索，
  不是数据集不存在源码，也不是已证明需要符号执行。
- [tqsdk-python_447](b4bf666be309166ca36d.json)：可见字面加载路径已匹配到api.py，但文件超过200KB，
  因预算被原子省略。应研究需求驱动的输入范围或后端规模策略，不把省略当无依赖。
- [testLoadConfigWithGrayRelease](ebfc12956ec49b532a6e.json)：同名产品类型有多条候选路径，Spoon报告
  `already defined`。当前明确记`duplicate_type_input`，没有静默猜一条路径。
- [getDefaultPort_custom](1a3f94450a223c35f671.json)、Hadoop、Druid等9例：当前探针在完成切片前达到
  图节点上限。它们是当前全文件构图策略的规模负结果，不是程序不可分析的结论。
- [flask_313](27029ce43168067c6ab9.json)：没有保留显式oracle，目标调用与辅助enumerate都被列为
  candidate_event。起点技术绑定现已成功，但候选事件筛选仍需语义研究，不能把它们
  全部视为测试目标，更不能恢复隐藏验证内容。

## 已修复与保留的问题

本轮修复了嵌套函数同名变量污染外层数据边、同行Java条件/断言错选、准则身份丢失、
多个未知边界调用相互覆盖、循环变量误作seed及空支持模块阻断。回归覆盖这些行为。
占位方法内部节点不参加传播；抽查与全图机械检查均未发现它们成为图节点。
这不证明所有Mock替换点都已识别，窄模式以外的边界仍未解决。

6例先导后，首次全量在20例时中断修复；r2完成165例（149 partial/16 failed），
随后修复上述空模块与循环seed问题并重新全量运行r3。前两轮原始材料保留，
[中断记录](../backward-slice-full-20261109/interrupted.json)和
[r2后续记录](../backward-slice-full-20261109-r2/followup.json)解释替代原因。
这些开发重跑不是独立留出实验，不能用改进幅度声称泛化收益。

## 后续技术选择依据

1. 先解决目标绑定：识别可见加载器协议与Path表达式、区分被测调用和辅助调用，并显式报告
   唯一/歧义/未知。修复重复类型入口时依据来源证据，不能按结果好坏挑路径。
2. 解决规模问题：比较按目标需求展开与全文件建图，保留未展开部分为unknown；先检查是否
   因源码选择策略造成上限，再评估成熟后端是否改善。禁止静默放大预算后混报本轮数字。
3. 在入口已可靠的代表案例上，对参数返回、对象状态、异常和控制依赖建立人工核对关系，
   再比较成熟分析后端。只有关系可靠而路径相容性仍是主要断点时，局部求解才有依据。
4. 关系证据尚未达到可验证语义规格，本轮不启动Agent生成或缺陷检出对照。

## 验证与复现

33项目标/选择/图/批处理回归通过，Ruff和diff检查通过。
[verification.json](verification.json)核对165个唯一身份、当前实现摘要、155个图的边端点、
source_id、逐准则绑定及opaque内部节点排除；这属于结构验证，不是语义正确率。
原始证据：[summary.json](summary.json)、[results.json](results.json)、
[observations.json](observations.json)。

```bash
PYTHONPATH=src .venv/bin/python experiments/backward-slicing/batch_slice.py \
  --inventory out/mockbench-study/intake-20261109-inventory.json \
  --output /tmp/mockbench-backward-slice-reproduction
```

结果目录必须不存在，具体预算见运行manifest。生产Agent、冻结V0.1 Prompt与输入准入策略未改。
