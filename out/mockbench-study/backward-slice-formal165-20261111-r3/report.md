# 后向分析修复：正式165例与原r3对照（2026-11-11）

本轮完整尝试Java59/Python106，主分母165不变，批处理耗时277.27秒。成熟后端执行状态为`{'unsupported': 162, 'complete': 3}`；语义状态为`{'not_evaluated': 162, 'verified_incomplete': 3}`。

完整语义恢复成功数为0。已核验案例仍是带假设与未知边界的部分结果；其它案例没有被候选图或工具启动成功冒充语义成功。

| 已实际执行的成熟后端案例 | 原观察数 | 类型化关系行数 | 绑定/核验 |
| --- | ---: | ---: | --- |
| [Jib JarFiles](2a37d7a1edfad4e10a7d.json) | 19 | 173 | automatic/unique; verified_incomplete |
| [flask_308](e42b25e4f92f782cac37.json) | 3 | 13 | automatic/unique; verified_incomplete |
| [langflow_189](5b83af29ea4ed3c9d4ce.json) | 2 | 31 | automatic/unique; verified_incomplete |

Jib关系覆盖正确五参数MUT、参数返回、builder到buildPlan及字段观察；Optional只保留条件式may影响。Flask保留原3个观察的分支、正常继续和异常影响。langflow把可见路径、loader返回、调用变量和实际CodeQL关系连接到原2个观察。未知依赖具体返回、异常、路径可满足性及支持域外状态影响仍未知。合成状态/递归探针未增加数据集案例数。

## 本轮实现与真实限制

修复了Java静态接收者/签名绑定、Python静态加载来源；接入真实WALA和CodeQL值/控制/状态关系，并保留未知调用潜在影响。Java依赖图改为按相关方法展开，Python按观察根限定查询；缓存、递归与预算前沿已单独验收。Java调用图/指针分析与全局ModRef、Python数据库提取仍有前置成本；真实Jib未证明加速。

成熟后端的输入准备和观察映射目前只有三个已验收适配器，另外162例明确不支持。这个瓶颈属于分析器适配能力，不能通过删除难例、改masking或给数据人工补答案解决。下一步应按完整结果里的输入/观察类型扩展通用适配，再增加真实案例验收；目前没有证据要求转向完整符号执行或替换整个数据集。

## 与11月9日原r3逐例对照

候选触及产品源码从76例变为110例；这是候选覆盖变化，不是已正确绑定MUT的数量。候选终态迁移：`{('partial', 'partial'): 155, ('failed', 'failed'): 10}`。所有入口、观察、类型化关系、未知、失败和三层成本见[r3逐例对照](r3-comparison/comparison.json)。原r3没有记录的语义指标保留为未记录，不补造历史正确率。

候选构图仍有10例失败，均保留在主分母和[证据索引](evidence-index.json)中：

- [com.ctrip.framework.apollo.configservice.service.config.DefaultConfigServiceTest#testLoadConfigWithGrayRelease@67a78efb](ebfc12956ec49b532a6e.json)：duplicate_type_input。
- [io.grpc.internal.ManagedChannelImplBuilderTest#getDefaultPort_custom@ef784649](1a3f94450a223c35f671.json)：source_budget, resource_limit。
- [org.apache.druid.server.coordinator.duty.KillCompactionConfigTest#testRunDoNothingIfCurrentConfigIsEmpty@e5633d78](9191e502652e552e0288.json)：source_budget, resource_limit。
- [org.apache.hadoop.fs.azurebfs.services.TestAbfsOutputStream#verifyShortWriteRequest@b612c310](3d83a681774c1109f1c4.json)：source_budget, inferred_criterion_only, resource_limit。
- [org.apache.hadoop.fs.azurebfs.services.TestAbfsOutputStream#verifyWriteRequest@b612c310](df050e9668f9bb5dd9b3.json)：source_budget, resource_limit。
- [org.apache.hadoop.fs.azurebfs.services.TestAbfsOutputStream#verifyWriteRequestOfBufferSize@35570e41](a3930300616939fa159e.json)：source_budget, inferred_criterion_only, resource_limit。
- [org.apache.hadoop.fs.azurebfs.services.TestAbfsOutputStream#verifyWriteRequestOfBufferSizeWithAppendBlob@c1fde4fe](ed9e312045b51d254b0b.json)：source_budget, inferred_criterion_only, resource_limit。
- [org.apache.hadoop.mapreduce.TestJob#testJobToString@6e11461e](a1006999c3e6e087fff4.json)：source_budget, resource_limit。
- [org.apache.zookeeper.server.FinalRequestProcessorTest#testACLDigestHashHiding_NoAuth_WorldCanRead@777f0c15](8c19ed536fb1209901fe.json)：source_budget, resource_limit。
- [org.neo4j.kernel.impl.newapi.OperationsTest#shouldAcquireTxStateBeforeAllocatingSchemaId@4ec55bf8](31cd85f9d038778ca86e.json)：source_budget, resource_limit。

这些候选失败与162例后端支持缺口有交集，不能相加为失败总数。重复类型冲突保留，不任择定义；资源或选源预算限制不解释为没有依赖。

## 成本、失败运行与复现

本轮3路候选任务、最多2路成熟后端并发；每个支持案例使用新结果缓存。CodeQL2.27.4，Jib复用已核验结构应用包；此前准备成本明确排除。Python Flask驱动包含机制资格检查，其总成本不能当作纯Flask成本。OS/工具内部缓存未隔离。与原r3的后端、预算边界和并发不同，耗时列保留但标记不可比较，不计算虚假加速。

正式[r1](../backward-slice-formal165-20261111-r1/report.md)因CodeQL目录误作可执行文件失败；正式[r2](../backward-slice-formal165-20261111-r2/report.md)因受限环境缺PATH导致CodeQL多进程启动失败。两轮均完整保留165例及各自r3对照。本轮在工具前置拒绝和同一受限进程入口的双案例并发smoke通过后，重新完整运行165例，没有选择性替换两例。

输入、代码和配置见[预登记](pre-registration.json)与[运行manifest](run-manifest.json)，原始逐例数据见[results.json](results.json)，汇总见[summary.json](summary.json)。旧数据集、masking和原r3未改；未执行目标测试或loader，未开展Mock生成、评分或付费模型实验。

复现命令见仓库[运行说明](../../../docs/usage.md)。大体积JSON通过无损gzip发布，恢复方法与SHA见[发布清单](../backward-analysis-publication-20261111-r1/README.md)；本地raw及所有失败运行均保留。最终独立审核另见acceptance.json。
