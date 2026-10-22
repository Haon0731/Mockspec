# 问题与复用决策（阶段 A）

基线与当前 HEAD 均为 0cbfb3d。保留任务包、out/architecture-review 和 ledger lock 等已有未跟踪工作。本轮只新增研究目录，不修改生产契约或历史产物。

问题是单个可见测试下可选择的依赖行为，不是对所有输入成立的库规格。Oracle 是测试要求；选定 guard 下的响应关系是场景内义务；在关系中选一个具体值是实现选择。比如默认镜像分支的非空 Integer <=8，不是全局 version=8，也不能推出 getFrom 必为空。

| 来源/实现 | 复用接口或思想 | 差异与边界 | 本轮落点 |
|---|---|---|---|
| S1 Maximal Specification Synthesis | 调用方目标驱动、避免过强规格 | 不移植完整程序验证；无最弱性保证 | 局部充分性与全局必要性分别检查 |
| S2 Elrond | 反例、弱化、假反例解释 | 无不可见库采样权限 | 外部未知关系不能成为可控 H |
| S3 StubCoder | receiver/method/arguments → Return/Throw/sequence | 隐藏 Mock Assertions 未恢复；不复用搜索器 | JSON 调用绑定和反应元数据 |
| S4 cvc5 | Term、Grammar、getAbduct/Next、checkSat、InputParser | 实验 API；不是源码翻译器 | 隔离环境实测，失败独立记录 |
| S5 Mock Assertions / Spock | 区分响应配置、运行预期和交互验证 | 不从调用存在推导 verify 必需 | 验证建议单列理由 |
| adapters/java.py、python.py | load_task | 仅两个审核 snapshot；禁止原始测试回退 | 原样调用 |
| validation.py | SourceStore、decode_json | 源引用合法不表示语义蕴含 | 草案复用，源码只从 store 读取 |
| program_tools.py、python_analysis.py、java_analysis.py | 已有 AST/Spoon 结构接口 | 词法事实、partial/unresolved/truncation 不能消失 | 沿用未来协议，不再造 CFG/DDG |
| scenario.py、inference*.py | 生产契约/SDK 调度及输入隔离经验 | 旧 required 维度混合；本轮不升级 | 仅独立 Research Draft |
| pytest | 现有测试设施 | 不运行目标程序或模型请求 | 独立研究测试和安全的离线回归 |

P0 逐项核查及精确链接见 sources.md（并行核查记录）；本表中的采用决定不代表接口已实测。阶段 B 先完成两份人工样例和公式，再做阶段 C 的环境/API/求解实验，阶段 D 加负例，阶段 E 综合 RFC 和决策。SMT-LIB 为唯一约束持久化表示，JSON 只做绑定/作用域/证据；不写表达式 parser。

停止条件：两份人工样例可验证引用，原生 abduction 与 candidate checking 分开留证，必需负例有结果，覆盖/未知/不可消费原因明确。结束于 Limited-Go/No-Go 等研究结论，不进入生产集成。

初步 P0 核对已返回：S1 的目标是调用方验证，S2 依赖行为采样且承认漏样本过拟合，S3 §3.2 确认调用匹配及 Return/Throw/次序，S4 确认 experimental/produce-abducts 以及下一解仅语法不同，S5/Spock 确认次数、对象、方法、参数与测试作用域。正式逐项链接与许可证追踪见 sources.md。GitHub open issues 只读查询成功、当前为空；未新建/关闭议题，也未将设计发布为完成任务。
