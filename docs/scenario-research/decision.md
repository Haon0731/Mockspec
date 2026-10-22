# 决策与验收

**Limited-Go：复用现有输入/工具边界、调用匹配→反应表示，以及 cvc5 的有限检查与受限 abduction；暂不进入生产 Scenario Agent 或 Mock Generation。** 已实际验证原生接口，但自动源码语义翻译、真实 Agent 推导与运行时对应性均未实现。

## 来源与实现

来源—设计矩阵见 [sources.md](sources.md) 和 [approach.md](approach.md)。本轮直接调用：既有 Adapter/SourceStore（inputs.py）、cvc5 Term/Grammar/Abduct/CheckSat/InputParser（models.py、poc.py、bind_results.py）、pytest。S1/S2 的目标驱动/反例/弱化是思想借鉴；S3/S5/Spock 提供行为与交互表达依据，没有引入其运行时或搜索系统。

新增草案及宿主绑定位于 experiments/scenario-research；生产 Agent、Prompt、Schema、Validator、历史报告与实验没有改动。本轮没有隐藏答案、参考 Mock、评分或新模型请求。旧回归中读取归档答案的文件被明确排除。

## 协议与实际输出

[完整协议与消费规则](rfc.md)：固定输入/Oracle → 已有词法工具 → 提议场景/响应 → 宿主受支持公式 → 一致性/非空洞性/反例/等价查询 → 带未知项的 Contract。工具只证明它实际检查的层面；无可消费方案时说明阻塞。

- [Java 人工契约](../../experiments/scenario-research/examples/java.contract.json)：19个原始断言，默认/显式镜像与计算/自定义 entrypoint 备选，两次 getFrom 独立绑定。非空版本<=8是局部关系，8只是具体选择。所有最终 ContainerBuildPlan 断言仍标 not_formalized。
- [Python 人工契约](../../experiments/scenario-research/examples/python.contract.json)：五行输入、Exception 与消息子串保持不变，按行选择 None / aborter throw / return 后 fallback。has_app_context 和场景名称不等于已知隐藏 patch 实现。

两份均为 `handcrafted_from_visible_input`。结构完整并不代表测试通过；只允许人审阅 partial，不准自动承诺通过。调用预期与建议 verify 分开。

## 实际 PoC

见 [原生 API 与独立 checking 结果](../../experiments/scenario-research/results/solver-results.json)、[逐行绑定](../../experiments/scenario-research/results/bound-results.json)、[负面结果](../../experiments/scenario-research/results/negative-results.json)。具体耗时以 JSON 的本次字段为准；机器性能和候选顺序不是稳定 API 保证。

|有限模型|原生 abduction|独立 candidate checking|边界|
|---|---|---|---|
|Java 默认镜像分支|生成非空、正常返回、version<=8；第一项成功，下一项返回 null，合计约3秒|人工候选及自动候选均 SAT / 可观察SAT / 反例UNSAT|只证明选中 mut:70；不是最终镜像正确。Next null 原因无法区分|
|Java entrypoint|两项候选约几十毫秒，含 computed 与 custom alternatives；异或查询 SAT|局部一致性与充分性通过|列表相等是人工抽象谓词；setter/最终 builder 未验证|
|Python 有限控制片段|两项候选约几十毫秒，独立 checking 通过；异或查询 SAT|逐参数行保留响应与消息关系，八条合同内路径分别检查|normal lookup/属性可访问前提；消息谓词绑定仍由人给出|

自动项是 **solver 对人工模型的 abduction**，不是 Agent 从源码自动推断。getAbductNext 仅保证语法不同，因此另查等价；独立负例中双重否定候选的异或为 UNSAT，不能计成不同场景。SMT-LIB 经官方 parser 检查 SAT 和 UNSAT round-trip；契约中的公式单独做声明边界/类型检查，不冒充执行源码。

## 验收矩阵与错误拦截

|要求|本轮证据|不能声称的内容|
|---|---|---|
|A1 原目标独立|oracles 与 response_rules 分离；所有 Java 断言和 Python 五行保留|选择场景可改 oracle|
|A2 两个真实输入人工例|Adapter + 引用逐字验证 + source SHA256|自动生成成效|
|A3 非矛盾/局部充分性|三模型检查及逐行检查真实 SAT/SAT/UNSAT|整测试通过|
|A4 默认版本与显式备选|getters 分开；version7仍充分；显式路径保留 unknown|默认 from 全局必要|
|A5 custom可等于预期|custom_matches 候选 SAT 且局部充分|所有 builder 组合真实可行|
|A6 Python return/throw|throw 阻断、return 到达、None 到达三个反例查询 UNSAT|词法顺序证明动态异常全语义|
|A7 状态独立|语义unknown、unsupported、not_run、actual unknown/RESOURCEOUT、forced process timeout分别记录|unknown/null=UNSAT|
|A8 来源/符号/公式/检查|schema_draft + bind_results 的引用、声明边界和哈希|合法引文蕴含候选|
|A9 grammar边界|生成 atoms 限制、生成结果和人工 H 均检查 free constants|最终输出或有利外部语义可充当响应|
|A10 下游说明|RFC和每份 consumption 给出选择/响应/交互/阻塞|已进入 Mock Generation|

必要负例均有测试：矛盾 H、不观察异常、空值异常出口、漏条件、显式无条件与未知条件区别、动态未知、局部结论越权、行/规则遗漏、合法引文但错误关系、等价候选、verify义务升级、有利外部语义。显式镜像反例是**抽象未排除**而非真实可执行反例；必要性只能降为未证明。源码到公式的错误不能由 SMT 自动挡住。

调用次数/序列另用 synthetic_representation_test 表示两次返回1/2、首调用抛出后第二次不发生；没有实现 Mock 状态机，也不是第三个 Benchmark。

## 未完成与下一轮最小工作

未实现：自动 Java/Python→公式、真实 Scenario Agent 推导、真实依赖/Mock 运行时执行验证、下游生成质量与总体效果评估。没有证据称获得最弱规格或覆盖全部路径；没有完整 wheel 再分发许可审计。

下一轮最多一组受控集成任务：审查并测试**单一有限片段**的源码→term绑定与控制权/外部前提，再在既有 Agent 宿主增加隔离的候选检查回调及证据 sidecar；在这一门槛通过前，不升级正式 Schema，不扩展数据集，不开始 Mock Generation。本轮至此停止。

## 最终验证补充

独立研究测试 **38 passed**；既有回归 **190 passed**（跳过含归档答案读取的 test_real_data.py 文件）。两份契约重新绑定审核输入，八条逐行路径都通过局部 checking。Python 编译检查通过；没有生产文件差异。

一个重要负面结果：cvc5 1.3.1 wheel 的 InputParser 未声明符号错误路径在本机触发了 native segfault。现已把契约解析隔离至子进程，故障被拒绝并记录，宿主继续完成独立任务；见 parser-error.json。此结果加强 **Limited-Go、禁止直接生产集成** 的决定。下一轮的最小集成组必须同时处理 native 进程隔离/资源限制及该 parser 缺陷核实。
