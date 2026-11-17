# CloudFoundry：取消宿主运行资源上限后的真实诊断

2026-10-28。**诊断已定位停止原因；真实规格推导未完成，没有最终 spec。**
本轮按用户新指令暂停四项一致性改造，先执行一次真实流程。以下结论为机器评阅，
包括独立 verifier 复核；没有人工评分快照，也没有执行目标测试或生成 Mock。

## 真实运行结果

| 项目 | 结果 |
| --- | --- |
| 案例 | 已审核 CloudFoundry `should_use_application_uri@d0085edf` |
| 代码 | `f028a0597c3290adddce3e97bb5208242e0f0fed` 加本目录冻结的 `code.patch` |
| 模型 | `deepseek-flash`，官方 Responses 端点；仅记录实际返回的模型标识，不声称固定部署版本 |
| 路径 | C 变体，repository-read；现有五阶段 Prompt 与调查/规格校验逻辑 |
| 用时 | 179.33 秒 |
| 模型请求 | 8 次，8 次均 HTTP 200、provider completed、完整留证 |
| 工具查询 | 6 次：2 次搜索、4 次读取；2 次读取成功，2 次请求范围无效 |
| 用量 | 输入 80,250；输出 32,490；合计 112,740 Token，用量完整 |
| 估费 | US$0.0630630，按预登记输入 0.30/M、输出 1.20/M 计算，非账户账单 |
| 终态 | `ERROR / output_invalid / QUERY`，退出码 1 |
| 最终产物 | 无 `scenario_contract.json`，无 `dependency_spec.json` |

本轮取消累计 Token、估算费用、模型/查询总次数、总时限、模型等待和 HTTP 超时。
单次输出显式使用服务商文档最高值 393,216 Token（包括推理 Token），不是“无限输出”。
没有自动技术重试或质量重跑。单次源码工具输出/解析边界、访问控制、协议校验、
无进展保护、一次格式修复规则和未知用量保护保留，均在预登记中声明。
所有实际预算 gate 均允许请求，未发生超时、截断或无进展终止。
第 4、8 次调用分别输出 9,620、8,242 Token，已越过历史 8,192 上限并完整返回。
由于代码和采样结果不同，本次不能作为单一预算变量的因果对照。

## 直接失败位置

第 8 次模型调用是 QUERY 的 `finish_queries`。JSON 已完成解析；失败发生在
`InvestigationLedger.apply_updates` 的跨字段来源关联检查，错误为：

```text
question evidence must be associated with its observed query receipt
```

该响应的 `question_updates[q1]` 引用父类源码 `110–118` 与 `196–205`，
却只声明 `attempted_sequences=[1]`。查询 1 只观察到类声明所在的第 44 行；
`110–118` 在查询 2 的 `1–200` 读取中，`196–205` 在查询 6 的 `190–222` 读取中。
源码已经真实取得，但该问题更新没有关联提供这些行的回执，因此被拒绝。
失败发生于问题更新，尚未到最终 spec 构建；不能归因为最终 spec 的未知处理问题。

同一响应还有下一层状态不一致：q1、q2、q4 被标为 `nonblocking`，
但 `acquisition` 分别是 `unavailable`、`unavailable`、`available`；
现有协议要求非阻塞问题使用 `not_needed`。这两类检查都未被取消资源门禁绕过。

## 离线因果验证：不作为真实模型成功证据

`reproduce_failure.py` 读取此次保留的 8 个模型输出，使用 FixedScriptModel 和同一审核输入回放，
不发起模型请求、不修改原运行。已有回放会核验所保存的输出与声明的对照一致。

| 回放 | 唯一调整范围 | 结果 |
| --- | --- | --- |
| `exact-response-replay` | 无 | 重现同一 QUERY 来源关联错误 |
| `receipt-link-counterfactual` | q1 的回执集合 `[1]` → `[1,2,6]` | 首错消失；暴露 `nonblocking question must explain why acquisition is not needed` |
| `state-field-counterfactual` | 再将 q1/q2/q4 的 acquisition 对齐为 `not_needed` | 到达 CONSTRUCT 请求；因未提供第 9 个脚本响应而停止 |

最后一项只证明这些元数据检查挡住了后续阶段，不证明模型能完成 CONSTRUCT，
也不证明修正后的停止理由语义充分。没有添加构造答案，没有产出伪装成真实结果的 spec。
机器可读结果见 `offline-diagnosis.json`。

## 已观察到的能力与限制

- 模型自主识别父类 URL 组合逻辑是重要缺口，搜索 `DefaultApplicationFactory`，
  使用返回的 source_id 读取实现；没有接收预设文件路径或查询计划。
- 模型读到健康端点路径的分支后继续调查尾部逻辑。两次越界读取失败后，
  自行搜索端口赋值位置并将读取范围收窄到 `190–222`，取得新的行为证据。
- 中间推导已把 CloudFoundry 首 URI、父类 service/management/health URL 组合、
  health/status 回退连接起来；最终规格的必要条件、合法变化、替换边界仍未交付。
- EXTRACT 的原目标坐标和问题关联被保留，但坐标覆盖不能证明目标含义完整。
  尚不能评阅完整 spec 的强弱、目标覆盖或消费可用性。
- 引用位置仍有语义质量问题：最终 QUERY 用 `145–147` 支持 `webEndpoint.getBasePath()`，
  而已观察源码的相关方法实际在 `143–145`；所选范围主要是闭括号和下一方法声明。
  引用可访问并不等于支持相应结论。
- 两次搜索的局部命中伴随访问范围未遍历完整的诊断；模型一度将其理解为父类无法读取，
  后续直接读取成功。两次范围错误也增加了查询；这些是次要可用性线索，未触发最终停止。
- 隐藏 MOCK-STUB/VERIFY 始终未读取，符合输入边界。后续目标应是推导合法依赖关系，
  不能转为寻找或恢复原 Mock。

下一步已有证据支持检查“问题更新中的证据回执关联及状态一致性”的表达/纠正机制。
本轮没有修改这些校验，没有继续一致性改造，也没有第二次真实请求来挑选成功样本。

## 可复查材料与工程范围

- `preflight.json`：明确调用授权来自本次用户指令；模型、输入、访问范围、有效限制、价格和版本。
- `code.patch`：相对基线的运行时完整补丁；清单另存生产与研究文件哈希。
- `launch.py`：只从本地私有环境加载凭据，用既有 CLI 启动一次运行；响应和调用事件即时写盘。
  留证子类不修改模型请求或响应。拒绝覆盖/重跑已有运行。
- `http/`、`calls/`、`progress.jsonl`：脱敏 HTTP 正文、阶段调用输入/Schema/输出关联和时间线。
- `run/`：审核输入快照、完整调查记录、最终清单；`verification.json` 为产物与用量核验。
- `reproduce_failure.py`、三个离线回放目录：只验证控制流与错误因果。

必要工程改动只有可选累计 Token 上限、研究诊断开关、可空 deadline、完整响应留存及文档/回归。
默认运行限制不变，原四项一致性问题未修，模型 Prompt 未改，冻结输入/准入未改。
相关离线测试 62 passed；沙箱外响应留证与带查询诊断测试 12 passed；最终等价参数整理后定向 3 passed。
沙箱内 SDK 传输夹具挂起，已中断并在沙箱外通过，未把该现象计作模型失败。
运行后将 SDK timeout 的字典展开改为等价显式参数，消除了新出现的类型诊断；
真实运行版本仍以 `code.patch` 为准。最终 mypy 保留 16 条所引用生产代码的既有诊断，
修改的研究模块无诊断；Ruff 与 diff 检查通过。类型检查不宣称全绿。
