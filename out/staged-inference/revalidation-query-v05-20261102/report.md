# QUERY v0.5 减负后的单次真实复验

2026-11-02。**状态协议修正通过真实QUERY节点；完整推断仍未交付，CONSTRUCT触发单次120秒超时。** 本次只运行一次，没有因结果不理想重采样。

## 配置与预检

沿用上一轮审核CloudFoundry案例、snapshot `ff6812732f1e2779e09005d721f464cebed6b587925130e6a2555a154097214a`，完整case ID和路径见 [preflight.json](preflight.json)。Adapter再次通过身份和masking检查，输入与上一轮逐对象相同，未读取隐藏答案或扫描依赖。

代码仍为 `f028a0597c3290adddce3e97bb5208242e0f0fed` 加当前工作区补丁 [code.patch](code.patch)，实际代码摘要已保存且运行后核对不变。新增生效变化是QUERY v0.5 open/closed输出类型、宿主投影及通用查询目标指令；保留此前已有工程修改，不能视为仅一个字段的因果实验。模型/Prompt/record实际版本以清单为准。

变体C；deepseek-flash；官方Responses端点；repository_read=true；单次输出24,576，总token200,000，整体600秒，每次阶段调用120秒，估费上限1美元。默认思考模式保持，无reasoning override。SDK openai3.35.1/openai-agents0.24.0/pydantic2.14.1。执行前再次核对[官方价格](https://api-docs.deepseek.com/quick_start/pricing/)，使用峰值cache-miss输入$0.30/M、输出$1.20/M估费，不采用缓存优惠。

启动前重新运行关键Schema与语义循环回归：**30 passed in 5.18s**，见 [pytest.txt](pytest.txt)。此前补丁专项81项通过属于上轮工程验证，本轮不重复计数。新目录启动器拒绝覆盖或重复运行；既有研究报告保持原样。

## 实际过程与工程结果

| 阶段 | 结果 |
| --- | --- |
| EXTRACT | 调用1完成，保留原目标并提出待调查问题。 |
| QUERY | 调用2搜索父类文件；调用3读取父类1–220行；调用4自主停止，宿主校验通过。 |
| CONSTRUCT | 调用5开始，等待120.064秒后host_timeout，未获得模型输出或用量。 |
| CONTRAST | 未进入。 |
| DELIVER | 未进入，无最终规格。 |

两次工具调用分别为 `inspect_source search **/DefaultApplicationFactory.java` 和读取动态source_id `repository:f1cbe4e7534b595de231fa4a` 的1–220行。搜索因审核排除项标记incomplete，但允许范围路径可读；随后读取成功。本次没有继续搜索精确隐藏Mock/VERIFY文本。

最终QUERY的resolved/nonblocking更新均不含acquisition，宿主分别投影为not_needed并留在host_question_updates。原wire_result保持不变，证据/回执/目标关联检查仍执行。这是修正实际通过前次故障点的直接证据，不是把失败记录事后改为成功。

终态为 **INCONCLUSIVE / timeout / CONSTRUCT**，总用时210.025秒。异常为 `TimeoutError`，classification=host_timeout，cause链含CancelledError、SSLWantReadError；只能说明等待响应时被宿主取消，不能由此认定网络故障或供应商故障。没有provider incomplete/max_output_tokens证据，不能称为输出预算截断。

5次请求开始，4份完整provider completed响应；清单requests_completed=5是账本已结束的请求计数，包含失败调用，不能解读为5份模型响应成功。前4次已知小计：输入31,089、输出17,762、合计48,851 tokens，按登记费率$0.0306411。第5次用量缺失，**整次token与费用未知**，远端可能仍产生费用。整体600秒未耗尽，直接门禁是单次120秒；token/费用请求前门禁均允许。

## 部分推断质量

模型已获得关键父类源码并将其用于停止解释：

- createApplication在父类70–77组装health、management、service、metadata及name。
- service URL在83–89使用service base并拼接尾斜线；初始mut43–50从CloudFoundry首URI生成基址。
- 父类109–155说明显式URL优先、管理端口相同或未设置时使用service URL、webEndpoint base path及health路径拼接。
- 父类196–206说明先health后status、两者均无文本则抛IllegalStateException；该范围在停止输出中准确引用。
- 原测试49–57的URI及全部结果断言仍作为停止证据。metadata在解释中得到考虑，比前次仅关注health更完整，但未形成逐项可消费约束。

QUERY的停止理由把精确隐藏声明、stub、verify文本列为非阻塞未知，符合本轮减负目标。已读源码足以开始构造条件行为，继续搜隐藏文本没有必要。

质量仍有缺口：remaining_unknowns的impact称“production getHealthUrl requires pathMappedEndpoints.getPath(health) to return the health path”，没有在此处表达status可回退；这仍可能把一种满足方式说成必要条件。实际代码支持的准确条件是：无显式health URL时，health若hasText则使用；否则status若hasText则使用；两者均无文本才抛异常。下游约束必须保留这个分支差异。停止输出含正确源码引用，也不证明模型已正确处理所有合法替代或默认依赖配置。

没有CONSTRUCT输出，无法评价最终返回值、异常、交互条件和替代分支是否完整，更不能把上述人工评阅内容当作Agent交付规格。

## 比较与判断

上一轮v0.4用5次查询后在状态一致性处终止；本轮v0.5用2次查询后成功进入CONSTRUCT。可以确认：本轮实际没有再要求模型填写闭合问题的机械acquisition字段，宿主投影成功；没有发生追寻隐藏文本的额外搜索。单次采样、Schema与Prompt同时改变，不能把查询减少全部归因于某个因素。

新的直接阻塞是CONSTRUCT等待超时。现有非流式响应记录无法区分模型长时间思考、服务端排队/生成慢或其他响应延迟，也没有部分草案可供评阅。将单次输出从8,192增加至24,576而保留120秒等待，是否足以完成该阶段仍未得到证实；本轮不再自动调整参数重跑。

**继续优化方案2仍有依据：状态责任调整已使真实执行前进一步，但端到端可用性尚未建立。** 当前证据没有显示程序分析是解除此超时的必要机制。下一步应先围绕CONSTRUCT的响应耗时和输出负担做有界诊断，再判断其行为推断能力；不能用超时替代语义质量评价。

## 追溯材料

[preflight.json](preflight.json)、[code.patch](code.patch)、[launch.py](launch.py)、started/finished.json和[progress.jsonl](progress.jsonl)保存配置与唯一启动；http目录保存已获取的实际请求与响应，calls目录保存每次阶段调用输入、Schema、用量和异常。超时阶段未获取的响应正文不虚构。

[运行清单](run/run_manifest.json)、[完整推断记录](run/inference_record.json)保存查询回执、观察源码、原始和宿主投影状态、终止原因。[verification.json](verification.json)确认代码和产物摘要一致、4份完整响应、QUERY投影通过、总用量未知及无最终规格。未修改实现、未执行目标测试、未生成Mock。
