# Scenario Agent / Contract Research Draft RFC

状态：研究草案；不替代 mock-spec.scenario/v0.1。Agent V0.2 与 Contract 版本不是同一版本线。目标是单个测试内可配置的依赖行为，而非普遍库契约。问题与复用矩阵见 approach.md、sources.md；最小公式的含义见 model-scope.md。

## 三种命题

|角色|例子|消费者权限|
|---|---|---|
|Oracle requirement|Java base image 等于 adoptopenjdk:8-jre；Python 捕获 Exception 且包含行特定消息|固定，任何场景都不得修改|
|Scenario-local obligation|默认镜像分支被选中且版本调用正常返回时，非空 Integer <=8 才选择 Java8|仅对所指 guard、调用点、参数行成立|
|Implementation choice|把版本具体设为8，或明确选择 getter 两次稳定返回|只是一个见证；保留更弱的允许关系|

scope、logical_role、evidence basis、check status、coverage 分别记录。空条件不能隐式 true：explicit_unconditional、conditional、unknown 是不同状态；漏字段拒绝。未知语义不是 solver unknown；unsupported 表示编码/接口不支持，timeout 表示资源终止，not_run 表示未执行，error 表示失败，SAT/UNSAT 仅代表给定模型查询结果。

## 推导协议（设计；本轮未自动实现）

1. 宿主通过现有 Adapter 审核 UnifiedTask，冻结测试输入和 oracle，建立内容身份；Agent 只得到任务及 source_id 工具。
2. 现有 AST/Spoon 提供位置、局部词法结构和声明引用。保留 unresolved、incomplete/truncated、工具错误；不得当作路径证明。占位 implementation-hidden 不可做行为公理。
3. Agent 提议场景与可控调用绑定；每次调用按位置/出现序号区分。只可配置允许 Mock 的响应、异常和属性，不能配置 MUT 输出、oracle 或不可模拟库语义。
4. 宿主将**已支持且经审核的**片段映射到现成 solver terms。本轮是人工 terms，不存在通用 Java/Python 翻译器。无法翻译的原表达式留下 not_formalized 和原因。
5. 宿主按符号权限限制 grammar，先查一致性，再查观测可达和局部充分性；反例查询用于反驳过强必要性，等价查询用于合并语法不同的候选。每次在独立 Solver 上查询。
6. 宿主输出实际 solver 版本、耗时、输入公式、状态、模型和限制。Agent 无权填写 verified、伪造检查结果或通过添加有利外部假设提升证据等级。
7. 有明确支持的局部方案则输出 partial；若重要外部语义未知，则阻止自动生成承诺。资源预算耗尽/不支持/无法构成一致候选则输出无可消费方案及原因。未找到 abduct 不是场景不存在。

## Contract 消费语义

JSON 是证据/绑定/场景薄封装，表达式采用 solver term 和标准 SMT-LIB；不新增可执行 DSL，不执行 Agent 生成脚本。PoC 只解析自己导出的受控公式；未来接受外部 SMT 文件需要独立命令白名单、声明/符号校验、大小/时间/内存限额，当前不能直接开启上传执行。

两份完整样例在 ../../experiments/scenario-research/examples/。oracles 固定所有可见结果要求；scenarios 表示局部备选；response_rules 是对象/方法/参数匹配→Return/Throw，顺序反应以 occurrence 明确。runtime_expectations 是控制流前提下预期；verification_candidates 只表示建议检查的行为、保护理由及依据，不能直接转 mandatory verify。

Java 消费：可先选默认镜像方案，版本载荷仍可在有界非空 <=8 中选择；可选空 custom list 后 computed list 为目标，或非空 custom list 自身等于目标。显式 from 路径没有被排除，但 builder 语义未知，不能自动承诺它满足最终镜像断言。两次 getFrom 不能合并；稳定返回是显式选择。createLayers 与所有其余 getter 的普通结果断言保留；Jib builder 默认行为未见，整个测试不可宣称已验证。

Python 消费：每个参数行独立绑定 code,args,kwargs 和消息。正常 lookup 返回 None 后 fallback；非 None 后 aborter 抛出则 fallback 不发生；aborter 正常返回则 fallback 发生。两条上下文候选均保留，消息不确定来源。不因 scenario 名或 slot 名恢复 patch 内容或隐藏 verify。依赖可替换位置、动态目标解析与异常匹配协议需确认后才允许自动生成；本轮只给人工 partial 方案。

准入决策：`partial` 可供人审阅或后续受控研究选择，**不可作为“保证通过测试”的自动 Mock Generation 输入**。源码语义翻译审核、依赖绑定权限和关键外部行为尚未解决时，自动消费必须阻塞。非关键未覆盖信息可作为附带 unknown，但这里 builder 语义/动态补丁均属关键。

## 能力边界

可靠挡住（给定忠实公式）：矛盾候选、不可达/跳过观察的空洞成功、局部充分性反例、受控 grammar 中的非许可符号、逻辑等价重复候选。结构检查挡住：漏条件、失效引用、丢失参数行、将 verify 候选改为必需。

仍依赖人或未来受审核翻译：引用是否支持结论、真实 Java/Python 异常/别名/对象模型、外部库可行性、所有路径是否覆盖。正确引用并不证明语义；SAT 反例可能是抽象假反例；没有最弱规格/完整性/真实性保证。两个开发案例不能估计总体效果。
