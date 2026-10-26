# M2 案例适用性核对

日期：2026-10-26。范围：现有两个已审核冻结案例，独立于模型生成过程。
结论：**本次限定审查未建立合格案例，G2 = blocked_no_suitable_reviewed_case。**
这不是对整个仓库所有可能问题的穷尽证明。引用协议与离线工程继续，真实 M2 不启动。

| 案例 | 核对问题与新增材料 | 判定 |
| --- | --- | --- |
| flask_308 | 初始 helper、docstring、普通断言已表达上下文存在/不存在的两条依赖路径。新增读取的 sansio/app.py 150–165、315–325、495–505 与 ctx.py 416–430 解释 aborter 构造及 context push，没有建立改变主要约束的材料缺口 | 不选为本次探索验收案例；不能据此声称该案例永远无需探索 |
| Java JarFiles basicInfo | MUT 与完整断言给出 Java-version 分支、layer 和 entrypoint 返回需求。额外接口重复已知签名，其他 processor 的实现不能证明被冻结为空方法体的 StandardExplodedProcessor 的具体行为 | 不选；不恢复隐藏实现，不把其他变体当成该依赖的证据 |

[机械摘要](case-assessment.json) 保存 case/snapshot 身份、Adapter 核验结果、精确查询参数、
完成状态、资源统计、已选原文或原文摘要及判断。由只读 analyst 的工具回执摘要持久化，
完整原始回执未另存文件；没有为补齐日志重复扫描。已保存的三条 Java 原文与其摘要逐字核对一致。

两案均经现有 `load_task_with_repository` 做身份与 masking 核验，再通过
`SourceStore`、`SourceAnalysis.inspect_source` 有界访问。Python 初轮宽搜索因
`content_unavailable` 不完整；Java 初轮宽搜索触发 byte limit。这些结果没有被解释为
“没有相关内容”；结论采用后续指定路径、完整结束的有限搜索与读取。
JSON 中的 observation text SHA256 用于标识本次观察，不构成重新审核或自动批准输入。

本次没有模型请求、目标测试、新案例准入、raw/archive/gold 读取、全量数据扫描或依赖哈希扫描。
这些评阅问题、答案位置和结论不进入后续模型输入，也不注入为模型本次已观察材料。

若继续真实验收，需要一个有限候选集合：初始材料缺少影响依赖约束的事实，允许的冻结仓库
存在补充材料，且源码、masking、冻结身份与范围已明确审核。新案例准入属于后续独立任务；
不能直接删除 flask_308 限制、开放任意目录或反复运行直到模型调用工具。
