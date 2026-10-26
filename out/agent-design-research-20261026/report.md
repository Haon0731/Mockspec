# Scenario Agent 的 SDK 复用与 M2 排障调研

检索日期：2026-10-26。范围：OpenAI Agents SDK Python `0.24.0`（本项目固定版本）
及其 OpenAI Python 传输层。本文不修改五步方法、工具权限或研究结论；其作用是决定
M2 应优先复用什么，以及如何把下一次真实失败变成可归因证据。

本文采用用户新定义：M1 工程基础；M2 真实五步流程；M3 语义有效性；M4 方法对照实验。
现有 PRD 中旧版 M2/M3 的编号含义不同，本轮不改写 PRD，也未修改 GitHub 任务。

## 结论

保持单 Agent、五个宿主阶段及现有受限 `SourceStore` 边界。没有必要为了 M2 引入新
Agent、通用浏览器、MCP 或另一个编排框架。SDK 已可复用来做严格函数工具、参数 schema
校验、工具超时、运行/工具事件、原始响应和结构化 final output；研究特有的查询尝试
计数、冻结输入、证据分类、跨阶段预算及 Scenario Card 语义验证仍应由宿主保留。

当前真实运行的 `QUERY/model_error` 不能推出“模型没有主动探索”。现有记录只说明：
QUERY 的第二次模型请求没有产生已解析的 `QueryResult`，所以没有被宿主登记的工具尝试。
泛化错误不足以区分传输、服务端/API 或其他宿主异常。当前已知的结构化解析异常另映射为
`output_invalid`，因此不能无证据把本次 `model_error` 归因于模型 JSON。先补齐诊断，
才可判断失败类别并测量主动探索。

## 已有 SDK 能力，可直接采用

| 能力 | 官方/上游证据 | M2 可采用的用途 | 不能替代的研究语义 |
| --- | --- | --- | --- |
| 函数工具 | [Tools](https://openai.github.io/openai-agents-python/tools/) 说明 Python 函数可自动生成 JSON schema，并以 Pydantic 校验；`@function_tool` 还支持超时和错误格式化。见 `openai/openai-agents-python@17d994bd3045dae71acda676a4ef4762ed8ecd94:src/agents/tool.py:L514-L555`。 | 将三个已有只读工具以其实际参数类型暴露给 QUERY，保留白名单与行范围验证。 | 不产生“该查询有助于哪个候选”的判断，也不保证模型会调用。 |
| 工具故障与未知工具 | [Runner 配置](https://openai.github.io/openai-agents-python/running_agents/) 规定未知函数工具默认抛 `ModelBehaviorError`，可选择将未知工具错误返回模型；可以限制本地工具并发，错误格式可定制。 | M2 可为未知工具、参数错误与超时留下模型可见回执，同时保留宿主审计回执。 | 不能取代项目“参数解析前登记一次尝试、重试也计数、上限六次”的不变量。 |
| 结构化终点 | [Results](https://openai.github.io/openai-agents-python/results/) 规定设置 `output_type` 后，`final_output` 是该类型对象；若尚未产生终点则可为 `None`。 | 用 Pydantic/JSON Schema 验证每阶段结构和最终 Scenario Card。 | 结构有效不等于依赖契约有证据或场景正确。 |
| 完整运行轨迹 | [Runner 生命周期](https://openai.github.io/openai-agents-python/running_agents/) 说明模型输出工具调用后 Runner 执行工具、追加结果并继续；[Results](https://openai.github.io/openai-agents-python/results/) 提供 `new_items`、`raw_responses`、`to_input_list()`。流式运行还提供 `stream_events()`。 | 每次真实运行保存阶段输入摘要、`new_items` 的工具 call/result 元数据、`raw_responses` 的 request ID/usage，以及宿主收据。 | 原始输出不能成为唯一审计记录，且不得把隐藏推理当作研究证据。 |
| 生命周期 hook | `openai/openai-agents-python@17d994bd3045dae71acda676a4ef4762ed8ecd94:src/agents/lifecycle.py:L13-L45,L209-L230` 提供 `on_llm_start`/`on_llm_end`。 | 在 LLM 调用开始时登记 stage、序号、deadline、输入摘要；返回后记录响应 ID、模型版本和 usage。 | `on_llm_end` 只会在模型调用返回后触发，失败请求仍需外层异常路径留证。 |
| 请求 ID / HTTP 错误 | [openai-python README](https://github.com/openai/openai-python) 说明成功对象有公开 `_request_id`；`APIStatusError` 有 `status_code`、`response` 与 `request_id`。源码见 `openai/openai-python@5d72315876ffd8b72b958623ef4db26da3fb1e61:src/openai/_exceptions.py`。 | 捕获并持久化错误类别、HTTP 状态、provider request ID、可安全保存的错误类别与根因链。 | 连接错误或无响应时本来就可能没有 request ID；不可虚构。 |
| 本地追踪 | [Tracing](https://openai.github.io/openai-agents-python/tracing/) 自动给 Runner、模型 turn、函数工具各建 span；`set_trace_processors()` 可替换默认 exporter。 | M2 可用应用自有的本地 processor 写不含秘密/敏感原文的事件轨迹；或以运行文件为唯一轨迹。 | 不能假设 `add_trace_processor()` 已停止默认外传；官方明确说它仍保留默认 exporter。 |

## M2 排障的最小可复用设计

1. **先保留项目自己的阶段控制。** QUERY 需要的不是任意 tool loop，而是“问题—候选—
   工具参数—收据—影响判断”的协议。SDK 做函数调用与 schema；宿主做状态机和总预算。
2. **把失败拆成可观察事件。** 每个模型请求预先写 `started` 事件；成功时补 request ID、
   model、usage 与结构化结果；异常时写异常类型、HTTP status、request ID（若有）、
   `__cause__` 的类型和安全摘要。不得用 `except Exception: ... from None` 抹掉原因。
3. **QUERY 不强制调用工具。** SDK 的正常 loop 是模型提出函数调用后再继续；但研究假设
   要测“按需探索”。对于初始可见材料已充分的案例，零查询是有效结果。为验证主动探索，
   应选一个确实需要获准工具提供结构/依赖分析结果的问题，而不是要求每次都调用工具。
   由于源码文本已在初始任务中，不能声称工具首次揭示了此前不可见的仓库文件。
4. **只给只读工具最小 JSON schema。** 参数应为 `source_id` 与受限行号/无参数结构分析；
   每次调用都经现有 SourceStore 与范围校验。SDK schema 是传输与验证机制，不是权限机制。
5. **超时按层记录。** 函数工具可设置 per-call timeout；整体运行仍由宿主 deadline 控制。
   不把 API 调用 timeout、解析 timeout 和工具 timeout 合并为一个“失败”。
6. **上游问题只能作为排查线索。** [SDK issue #4901](https://github.com/openai/openai-agents-python/issues/4901)
   讨论了失败/截断响应中供应商已报告的 usage 丢失问题，涉及旧版本及不同服务入口。
   Issue 已关闭不代表已修复，也不能证明本次失败属于该问题。应保存终止状态、
   `incomplete_details` 和实际收到的 usage，再判断能否匹配上游问题；缺失用量保持未知。

## 当前实现对应的边界（本地只读核对）

- `experiments/staged-inference/controller.py:289` 给各阶段建 Agent 时固定 `tools=[]`；
  QUERY 是“模型先输出 QueryResult，宿主再注册并执行 QueryExecutor”的专用协议，
  并非 SDK 原生函数工具 loop。
- `controller.py:776` 仅在 `_call_stage` 返回 `QueryResult` 后才登记/执行查询。真实运行
  只有 EXTRACT，`query_log=[]`，因此“实际查询为 0”只证明没有完成一个可登记的 QUERY
  请求，不能判断模型的工具选择能力。
- `src/mock_spec/source_tools.py` 和 `src/mock_spec/inference_v02.py:93` 已有
  `@function_tool` 与工具组装先例。若 M2 将 QUERY 改为原生工具，必须保留
  `QueryExecutor` 的“先登记、再参数验证”计数规则，不能盲目替换。
- `_call_stage` 在 `controller.py:337-347` 将广泛异常转换为 `StagedInferenceError` 并
  `from None`，而 `BudgetHooks.on_llm_end` 只能保存已返回的响应；这解释了当前第二个
  请求没有 status/request ID/根因的留证缺口。
- `UnifiedTask` 初始已经带入全部获准 `SourceStore` 材料。这里的“探索”是对冻结材料的
  定向读取/解析，不是遍历新仓库文件；M2 不应扩大该边界。

## 追踪与隐私

默认 tracing 会记录 generation 和 function span；官方说明这些可含输入/输出敏感数据，
而 `trace_include_sensitive_data=False` 可省略它们。若研究记录需含任务文本或工具原文，
应继续由项目的受控本地产物承担；若使用 SDK trace，应在运行前用
`set_trace_processors()` 置换默认 exporter，并将脱敏与导出放在同一个应用拥有的 exporter
中。官方明确警告仅 `add_trace_processor()` 不会移除默认 exporter，独立 redactor 失败也
不能阻止其他 exporter 收到原始数据。

证据：[Tracing 的敏感数据与 processor 说明](https://openai.github.io/openai-agents-python/tracing/#sensitive-data)
及[redaction-before-export](https://openai.github.io/openai-agents-python/tracing/#redaction-before-export)。

## 版本与未决项

- 本项目安装 `openai-agents==0.24.0`；对应官方 tag 为
  `17d994bd3045dae71acda676a4ef4762ed8ecd94`，发布于 2026-10-15。
- OpenAI Python 的异常接口经常随版本调整；引用的 `main` SHA
  `5d72315876ffd8b72b958623ef4db26da3fb1e61` 仅说明当前上游行为。实施前应读取本地
  安装的 `openai` 版本和异常类，不应凭本文升级依赖。本次本地核对为 `3.35.1`，
  `APIConnectionError`、`APIStatusError`、`APITimeoutError` 均存在。
- 本文没有证明 DeepSeek 在每一种失败情形都返回 OpenAI 兼容 `x-request-id` 或完整错误体。
  M2 的首要工作正是把真实失败分类；服务端兼容性是待验证事实。
- 不把自动 schema、结构化解析、成功退出码或字段完整度解释为语义正确。M3 仍需逐条区分
  接口/依赖行为证据、MUT 路径可达性证据和当前测试目标证据；反事实候选必须标记为预测，
  除非独立执行实际确认 PASS。

## 采用清单

M2 可以采用：已有单 Agent、冻结输入、三个只读工具、SDK Pydantic schema、函数工具超时、
`new_items`/`raw_responses`、带根因的异常留证及本地脱敏追踪。

M2 不应采用：扩大读取权限、为了“证明主动探索”强制工具调用、自动重试掩盖失败、把 SDK
trace 当成唯一证据、或把结构化 Scenario Card 当作语义验证。

## GitHub 参考实现：可借鉴的边界，而非待引入依赖

| 项目 | 可借鉴的单点 | 本轮决定 |
| --- | --- | --- |
| [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai) | [函数工具文档](https://pydantic.dev/docs/ai/tools-toolsets/tools/)同样以类型注解生成工具 schema。 | 这是“参数必须可验证”的参考，不构成替换已固定 Agents SDK 的理由。 |
| [huggingface/smolagents](https://github.com/huggingface/smolagents) | [Agent API](https://huggingface.co/docs/smolagents/reference/agents)有逐步执行和 `step_callbacks`；其文档同时标为 experimental。 | 可参考“逐步观察”的产品形态，但不引入代码执行型 Agent，也不扩大工具权限。 |
| [langfuse/langfuse](https://github.com/langfuse/langfuse) | [Scores 文档](https://langfuse.com/docs/evaluation/scores/overview)把人工、程序或 LLM 评价都作为附着在 trace/observation/dataset-run 上的 score。 | M3 可采用“独立评阅与运行轨迹关联”的数据模型思想；目前仍以仓库本地审计产物为准，不新增服务或依赖。 |

这三者是设计参照，均未在本仓库安装、接入或替代现有实现。它们不能提供 Scenario Card
语义、冻结输入权限或“反事实预测/实际 PASS”之间的证据区分。

## 仍需由项目定义的语义与验收

- 接口/依赖行为证据、MUT 路径可达性证据、当前测试目标证据分开评阅。
  `if (user == null)` 只能支持路径条件，不能单独支持 `repository.find` 的合法返回值契约。
  未取得接口依据时应记录假设或未知，而非升级为依赖保证。
- Scenario Card 优先作为现有 `scenario_contract.json` 与 `inference_record.json` 的
  可读表达，不另起一套竞争 Schema。卡片中的每条主张应能回到已有证据与推断状态。
- 反事实预测 PASS、未知、独立实际运行确认 PASS 分别标注；语义评阅本身不是测试执行。
  当前生产与推断入口不执行目标测试，M3 不自动取得测试执行权限。
- 三类真实案例的评阅需要对应的已审核输入。目前仅 `flask_308` 是获准 Python 案例；
  参数化行、合成回放和同一案例的多次运行不能替代三个代表性真实案例。本轮未新增准入。

本轮仅新增此调研文件；未实现修复、安装依赖、启动模型请求或修改研究方法。

## 补充：Agent 设计指导与当前阻塞（2026-10-26）

一手指导材料：

- [Anthropic: Building effective agents](https://www.anthropic.com/research/building-effective-agents)：
  区分预定义工作流与模型动态决策；从简单设计开始，用真实工具反馈和停止条件约束循环。
  这是设计原则参考，不能据此推断本项目故障根因。
- [OpenAI: A practical guide to building agents](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/)：
  模型、工具和明确指令共同组成系统；先建立可评测的单 Agent 基线，再按实际需求增加复杂度。
- [Anthropic: Writing effective tools for agents](https://www.anthropic.com/research/writing-tools-for-agents)：
  工具要围绕实际任务设计，返回有助于后续决策的上下文；评测不能过度限定唯一调用路径。

对本项目的判断：

1. 五步外层工作流与 QUERY 内部的模型决策可以共存。固定外层阶段不是不合格 Agent
   的证据，也没有必要为了名称而改成自由规划或多 Agent。
2. 首要工程阻塞是 QUERY 内的模型请求失败，同时异常被泛化，无法定位提供商/传输/API
   或宿主原因。真实运行没有完成“决策—工具执行—观察—更新判断”循环。
3. 辅助设计风险：`controller.py:229` 的阶段指令覆盖 QUERY、CONSTRUCT、CONTRAST、REVISE，
   EXTRACT 仅收到通用阶段/结构/宿主边界要求与 Schema，缺少专用的候选语义说明。
   这支持进一步检查“竞争解释与互补分支如何区分”，不证明它导致 QUERY 失败。
4. 工程机制已有离线验证；真实工具适用性、卡片语义与方法效果是不同验收层。
   固定响应测试不能评测模型选工具、解释回执或识别关键行为的能力。
5. 后续成功证据应包含待解问题、获准动作、实际工具回执、候选判断变化及明确终点。
   需要的是可观察决策与依据，不是索取或依赖模型隐藏推理。

本次补充仍为只读调研与文档记录，没有修改实现或重跑模型。
