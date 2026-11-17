# 宿主机械维护改造后的单次真实复验

2026-11-02。**本次未完成：QUERY因额外字段违反Schema终止，未进入CONSTRUCT；没有触发120秒或整体超时。**

## 配置

按用户新指令取消单阶段120秒上限。复用CLI，timeout_seconds=600、parsing_timeout_seconds=600，因此每次模型调用实际等待上限为整次剩余时间；总token200,000、单次输出24,576、费用1美元、默认思考模式均保留。共享parsing参数也将源码分析超时设为600，既有搜索/读取访问控制和单次资源边界保留。未启用取消全部预算的诊断开关。

使用C变体、deepseek-flash、官方Responses端点、repository_read=true。同一审核CloudFoundry案例和snapshot `ff6812732f1e2779e09005d721f464cebed6b587925130e6a2555a154097214a`，Adapter通过身份/masking核对，输入与上一轮一致。实际代码为HEAD f028a059加当前工作区补丁，记录/policy/prompt v0.6；[preflight.json](preflight.json)保存完整参数和源码摘要，[code.patch](code.patch)保存补丁。

预检38项通过，见[precheck-tests.txt](precheck-tests.txt)。费率沿用同日已核验的[官方价格](https://api-docs.deepseek.com/quick_start/pricing/)：峰值cache-miss输入$0.30/M、输出$1.20/M。仅一次真实运行，无重采样；新目录不覆盖历史。

## 实际过程

EXTRACT正常完成。QUERY依次执行：

1. 搜索DefaultApplicationFactory.java，找到允许读取的路径；因审核排除项，搜索状态为incomplete。
2. 请求父类1–240行，超出文件可用范围，返回invalid_source_or_range。
3. 搜索PathMappedEndpoints，取得引用匹配。
4. 读取父类40–130行，成功取得createApplication及部分URL逻辑。
5. 请求115–240行，再次范围错误。
6. 搜索父类getHealthUrl，定位后续读取位置。
7. 拟读取130–185行，但该次模型响应未通过协议校验，读取没有执行，记录为protocol_error。

8次模型请求均HTTP200、provider completed，完整留证；第1次为EXTRACT，其余为QUERY。最终 `ERROR / output_invalid / QUERY`，用时107.690秒。CONSTRUCT/CONTRAST/DELIVER均未进入，无最终contract/spec。

## 直接失败原因

第8次原始输出是有效JSON，但在 `previous_receipt_assessment` 中加入非空 `evidence` 数组。该对象只允许impact/rationale。

```text
extra_forbidden: previous_receipt_assessment.evidence
QUERY output is outside the bounded repair policy
```

SDK给出泛称“Invalid JSON when parsing model output”，不能因此认定JSON语法损坏。用当前QuerySelection只读验证原响应，唯一错误如上，详见[schema-errors.json](schema-errors.json)。在内存中仅删除该字段后可通过wire Schema，这只定位语法契约错误，不证明后续Controller校验或行为推断能够通过，没有改写原响应或继续真实运行。

已有问题更新的question/goal_indexes/impact为null符合当前wire，非本次错误。前述即时进度曾将这些null字段也列为异常，现以实际验证结果更正。

现行受限格式纠正只允许assessment中未知null字段；此次额外evidence是非空数组，不符合纠正范围，故按策略终止。这不是此前nonblocking/acquisition矛盾，也不是stable-ref转换失败。

## 已有能力与质量限制

模型自主发现父类、读取部分源码，并在范围失败后改用搜索定位，说明仓库探索仍有实际发生。但两次把终止行设为240导致拒绝，增加了探索成本；最终尚未取得完整health/status分支正文，不能评阅充分停止或完整条件规格。

其查询仍使用“hidden stub应满足什么”表述，部分候选已把尚未读完的health路径来源作推断。必须继续区分工作假设和已观察行为，不能将本次部分读取等同于有效规格。

本次实际未到CONSTRUCT，因而**无法验证取消120秒限制是否解决构造超时，也无法验证新的CONSTRUCT/CONTRAST/宿主DELIVER在真实模型链路中的交付效果**。机械维护改造的离线证据仍成立，本次暴露的是模型扩展严格Schema字段而恢复策略不接受该类型的协议问题。

## 用量与核验

输入75,110，输出18,117，总计93,227 tokens，用量完整；按登记费率估计 **$0.0442734**，非账户账单。所有响应completed，无截断证据，运行早于总时限终止。请求前预算门禁均允许。

[verification.json](verification.json)核验8份响应用量总和、代码摘要与最终产物摘要一致。http目录留实际请求和完整响应，calls目录留阶段Schema/输入/异常，progress.jsonl另记录收到响应头的时间。[run/inference_record.json](run/inference_record.json)保存查询回执及原始输出，[run/run_manifest.json](run/run_manifest.json)保存预算和终态。

本轮没有修改核心实现，没有补入案例答案，没有执行目标测试或生成Mock，也没有自动扩大容错后再次采样。下一步问题已具体定位为QUERY回执评估的输出边界及非空额外字段处理；不能把它归因于五阶段架构整体或输出时间预算。
