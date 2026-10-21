# Step 6 Live：两案例、两版本真实推理对照

2026-10-21。四次正式推理已完成，无技术重试，无合成响应，无质量重跑。
**真实自主工具调用已验证；本轮不支持进入下游 Mock Generation。**
两个 V0.1 均产生有效 Contract，两个 V0.2 均因 required claim 缺少条件而失败。
Python 无效候选有局部行为推导改善，Java 无效候选则出现替代路径退化。

以下为 **AI 辅助语义审查，待人工确认**，不是人类研究者已完成的审查或参考答案评分。
逐项审查见 [Python](python-review.md) 和 [Java](java-review.md)。

## 四次正式结果

| 案例 / 版本 | 最终状态 | HTTP 模型请求 | 实际工具调用 | 输入 Token | 输出 Token（含 reasoning） | 其中 reasoning | 总 Token | 保守估费 USD |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Python V0.1 | 有效 Contract | 1 | 0 | 3,884 | 14,148 | 10,644 | 18,032 | 0.0181428 |
| Python V0.2 | structure_error | 3 | 4 | 27,093 | 11,350 | 7,551 | 38,443 | 0.0217479 |
| Java V0.1 | 有效 Contract | 1 | 0 | 5,653 | 21,498 | 17,149 | 27,151 | 0.0274935 |
| Java V0.2 | structure_error | 3 | 4 | 33,645 | 15,032 | 8,611 | 48,677 | 0.0281319 |
| 合计 | 2 有效 / 2 无效 | 8 | 8 | 70,275 | 62,028 | 43,955 | 132,303 | **0.0955161** |

四次是四个正式推理运行；V0.2 各自三次 HTTP 请求属于工具循环，不是重试。
八个 HTTP 响应均为 200、provider status=completed；无输出上限截断，无未知费用。
“provider 完成”不等于 Contract 合法。两个 V0.2 的 contract 字段均为 null。

| 开销 | V0.1 | V0.2 | 差异 |
| --- | ---: | ---: | --- |
| Python 模型/工具运行墙钟 | 53.744 秒 | 42.996 秒 | 本次 V0.2 较短；不据单次测量推断性能提升 |
| Java 模型/工具运行墙钟 | 82.248 秒 | 59.781 秒 | 同上 |
| Python 工具累计耗时 | 0 | 0.153 秒 | 4 次工具之和 |
| Java 工具累计耗时 | 0 | 2.089 秒 | 4 次工具之和 |
| Python 总 Token 增量 | — | +20,411 | 约 +113.2%，重复输入/工具历史增加 |
| Java 总 Token 增量 | — | +21,526 | 约 +79.3% |
| Python 保守费用增量 | — | +US$0.0036051 | 约 +19.9% |
| Java 保守费用增量 | — | +US$0.0006384 | 约 +2.3% |

费用统一按峰时未缓存输入 US$0.30/M、输出 US$1.20/M 计算；未减缓存及非峰时优惠，
不是账户账单。全轮远低于 US$5 目标，但门禁不是远端硬上限。

## 预算与模型可比性

首个正式请求前已冻结 [协议](protocol.md)。官方 [Responses 参数](https://api-docs.deepseek.com/api/create-response/)
说明 max_output_tokens 包含 reasoning 与可见输出，所以 V0.2 单次上限预先设为 16,384，
累计 160,000 Token、6 轮、8 次工具、600 秒、16,000 字符工具结果、15 秒分析超时。
单次预留 US$0.75，共用 US$5 账本。没有根据本轮回答改变限额或 Prompt。
本轮 V0.1 的输出量分别为 14,148 和 21,498，说明 4096 不适合作为充分输出预算的假设；
这仅是事后佐证，调整依据在正式运行前已经记录。

两版本均使用 deepseek-flash、同一官方 Responses 端点、SDK 默认采样/推理设置。
模型响应仅回报 deepseek-flash，未获得不可变部署版本；官方标称版本不作为实际部署证明。
同案例两版本的 task_sha256 相同；四次 configuration_sha256 相同，代码、Prompt、SDK、
解析器身份由账本冻结。V0.1 保留无显式输出/累计 Token 上限与 SDK 默认传输重试，
V0.2 显式限制输出并禁用传输重试。实际未出现任何传输重试。
因此这不是严格隔离“工具”单变量的因果实验：Prompt、循环和预算存在已记录差异。

## 自主工具与程序证据

**Agent 是否自主调用了程序分析工具？是，两案例均有。**
未使用强制 tool_choice、宿主预调用或回放。请求正文显示 V0.2 有三个函数工具，
provider 自行返回 function_call，调用名称/参数与实际轨迹逐项一致。

- Python：inspect_source(mut, 265–285)、inspect_source(test, 1–126)、
  inspect_program_structure(mut)、inspect_dependency_usage(mut)，四次均 ok。
- Java：inspect_source(mut, 58–95)、inspect_source(test, 30–104)、
  inspect_source(processor, 50–61) 均 ok；inspect_dependency_usage(mut) 返回 incomplete。

**是否获得 V0.1 没有可靠获取的程序证据？获得了独立、机器提取的语法证据，未获得新增源码。**
Python AST 明确区分 guarded aborter 与无 lexical guard 的 `_wz_abort`，补强 V0.1 未完整表达的
fallback 条件。Java Spoon 返回 from 分支、Java version 条件、entrypoint 三元分支及局部声明引用；
V0.1 已从相同源码识别主要替代路径，因此 Java 的增量主要是可核对的结构证据而非新行为发现。
两者都不提供跨过程行为、路径可满足性、完整类型解析或外部 builder 默认值证明。

**工具事实是否实际改变 Scenario Contract？**
没有有效 V0.2 Contract 可以声称已被改善。原始候选中，Python 新增的 fallback 场景、
交互与不确定性与 AST 事实对应；Java 虽列出分支事实，仍错误排除了合法替代路径。
这些是观察到的对应关系，无法证明工具导致改变或退化。

## 语义与结构对照

| 维度 | Python | Java |
| --- | --- | --- |
| 场景约束准确性 | V0.1 五行参数与消息目标较完整；V0.2 部分 required 条件缺失，slot 归属有无证据推测 | V0.1 保留镜像/entrypoint 多种来源；V0.2 将 from 必为空升级为 required，证据不支持 |
| 条件完整性 | V0.2 加入 aborter 抛出/返回之分，但 D2、I0 的 condition=null | V0.2 5 条 required interaction 的 condition=null；镜像断言不能决定 from 是否 present |
| 替代路径 | 原始候选改善：aborter 正常返回后可继续 `_wz_abort`；应允许 fallback 产生符合断言的消息 | 退化：显式 from 可产生相同镜像，自定义 entrypoint 可等于断言列表，不能排除 |
| 不确定性 | V0.2 保留 masked setup 和 fallback；但部分槽位意图猜测过度 | V0.2 承认外部默认值未知，却又说 present-from 未被断言路径使用，内部不一致；未充分披露工具截断 |
| 行为推导结论 | **原始候选局部改善；有效产出退化** | **原始候选与有效产出均退化** |

Python V0.2 的结构失败位于 dependency_constraints[2]、expected_interactions[0]。
Java V0.2 位于 expected_interactions[0,3,4,8,9]。均为 required claim 的 condition=null；
没有在本轮补字段、修复候选或修改 Prompt 后重跑。
四份输出中的 58 条 Evidence 引用全部精确匹配可见源码，但引用正确不能修复结构或语义错误。

## Java incomplete 的实际影响

依赖工具触发 output_limit，只返回 **31 / 58** 条事实。
最后一条 setProgramArguments 事实另因 Java helper 的单条 quote 400 字符限制没有 quote，
与整包 16000 字符截断是两层限制。这些均不是模型输出 Token 截断。
工具明确提示不能从省略项推断行为不存在。

已返回事实覆盖 from 分支、Java version 阈值、createLayers 调用、自定义 entrypoint 条件
和部分 builder 调用；更多配置 getter、Optional ifPresent 等没有获得完整独立分析确认。
这些源码仍在初始 UnifiedTask 和 inspect_source(mut, 58–95) 中可见；
因此“工具未返回”不意味着“Agent 没有源码”，也不意味着相关调用不存在。
缺失项会削弱对局部变量使用、可选设置条件及后续调用关系的独立交叉核验，不能把工具列表当穷尽事实。
外部 builder 行为、完整绑定、跨过程传播即使没有截断也仍超出工具能力。

V0.2 候选未充分显式保留工具截断这一限制。不过关键退化的 from/entrypoint 替代路径所需
分支事实已经返回，不能把这些错误归因于被截断部分。更稳妥的判断是模型仍发生了
“从最终断言反推唯一依赖配置”的过度推导，具体因果无法由两例一次运行确定。

## 下游决策与证据入口

**当前结果不支持将 V0.2 推进下游 Mock Generation 验证。**
虽然真实自主工具链已通过观察验证，但两份 V0.2 都没有可消费的有效 Contract，
Java 还出现明确语义退化。V0.1 的合法结构也不证明其场景约束已足够完整。
本轮在报告交付后停止；没有新增 Agent 功能、扩展数据集、生成 Mock 或执行目标测试。
研究者仍需完成实际人工审查；本轮 AI 审查不替代这一环节。

留证目录：[完整账本](../../out/live_inference/step6-live/ledger.json)、[独立核验](verification.json)。

| 运行 | 实际结果与轨迹 | 提取的最终 JSON |
| --- | --- | --- |
| Python V0.1 | [result.json](../../out/live_inference/step6-live/python-v0.1/attempt-1/result.json) | [有效 Contract](../../out/live_inference/step6-live/python-v0.1/attempt-1/contract.valid.json) |
| Python V0.2 | [result.json](../../out/live_inference/step6-live/python-v0.2/attempt-1/result.json) | [无效候选](../../out/live_inference/step6-live/python-v0.2/attempt-1/candidate.invalid.json) |
| Java V0.1 | [result.json](../../out/live_inference/step6-live/java-v0.1/attempt-1/result.json) | [有效 Contract](../../out/live_inference/step6-live/java-v0.1/attempt-1/contract.valid.json) |
| Java V0.2 | [result.json](../../out/live_inference/step6-live/java-v0.2/attempt-1/result.json) | [无效候选](../../out/live_inference/step6-live/java-v0.2/attempt-1/candidate.invalid.json) |

各同级 attempt-1-http/ 下保存 request-NN.json、response-NN.json、launcher.json；
请求/响应正文保留实际模型内容和 usage，不含认证头。候选提取仅改变 JSON 排版，未更正内容。
宿主 [运行脚本](../../scripts/run_step6_live.py) 留存安全注入与采集步骤，已有目录拒绝重跑。
逐项核验确认密钥值不在本轮产物中；未写入或打印凭据。

验证：189 项回归测试通过；compileall 与 git diff --check 通过；
与开始提交 7afb0fb5ec1b9aecc36582cfc7ec818669a9e2c0 比较，src、历史协议/结果/Prompt 均无修改。
新增文件仅为本轮宿主脚本、协议/报告/审查与真实推理产物。
