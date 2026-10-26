# #17 CloudFoundry 真实 M2 验收

日期：2026-10-26。结论：**NOT PASS；G3 已通过，G4 未通过，#17 保持开放，#18 不启动。**
这是一次真实、完整流程的失败尝试，不是完成了全部阶段的成功运行。

## 运行与结果

在提交 `98b8aca0cd3f7f7b5096300fbde815267ea63fd0` 上，使用已独立审核的
`CloudFoundryApplicationFactoryTest#should_use_application_uri@d0085edf`，
snapshot `ff6812732f1e2779e09005d721f464cebed6b587925130e6a2555a154097214a`。
完整 case ID、输入摘要、配置、代码摘要和复现命令见 [预登记](preflight.json)。
初始输入仅完整 test 和目标类；无父类答案、评阅问题、工具顺序或准备侧观察。

单次 variant C、`deepseek-flash`、Responses API、仓库读取模式；200,000 总 token、
8,192 单次输出 token、600 秒整体、120 秒单次阶段调用、US$1 估算费用门禁。
没有固定工具次数目标，没有自动技术重试或按质量重跑。实际用时约 47.29 秒。

| 步骤 | 实际证据 | 判定 |
| --- | --- | --- |
| EXTRACT | 有效 locator，经宿主物化为精确原文；提出两个候选 | 引用工程通过；候选含未经证据确认的隐藏内容描述 |
| QUERY 1 | 模型自主提出父类如何使用 PathMappedEndpoints 的问题，执行一次搜索 | 新增父类第 29、48、58 行，但只是 import、字段和构造参数 |
| 搜索回执 | 5 个审核排除项被跳过，状态为 incomplete/access_denied，返回可见匹配 | 不能推断未返回内容不存在；没有取得关键行为证据 |
| QUERY 2 | 模型拟读取父类 50–170 行，但上一回执评估对象含禁止的额外字段 | 在派发读取前失败；第二条仅为已登记的拒绝尝试 |
| 后续阶段 | 未进入 CONSTRUCT、CONTRAST、DELIVER，无停止说明和场景卡 | 五步验收未完成 |

运行结果为 `ERROR / output_invalid / QUERY`。完整记录和请求关联位于
[inference_record.json](run/inference_record.json)，用量和身份位于
[run_manifest.json](run/run_manifest.json)。没有发布 `scenario_contract.json`。

## 原因与独立复核

第三个模型响应在 `previous_receipt_assessment` 中多出 `attempt_sequence_note: null`。
实际请求 Schema 明确设置 `additionalProperties: false`，只允许 `attempt_sequence`、
`impact`、`rationale`。用原 `QueryAssessment` 离线复现，唯一错误为
`extra_forbidden`，详见 [诊断](diagnosis.json)。请求 Schema 与当前代码一致。

外层保留原始对象是为了先登记畸形工具尝试，后续严格验证仍拒绝执行；这次没有复现
宿主与 Schema 不一致。不会删除额外字段、放宽验证器或把旧响应改写为成功。
分类为返回输出违反已声明协议；其背后的模型/供应商实现原因尚未确定，不能据此
宣称已修复工程故障并自动重跑。

DeepSeek 官方 [Responses 参考](https://api-docs.deepseek.com/api/create-response/)
说明 `text.format.json_schema` 的输出应符合指定 Schema；
[指南](https://api-docs.deepseek.com/guides/responses_api/)列明支持 `text.format`。
当前观察与该契约不一致，但官方资料未说明这次额外字段的成因。
另行文档中的 `/beta` strict 模式针对 function 工具参数，不足以推断它能修复本次
Responses 最终 JSON 输出。不能用未经证实的 thinking-mode 假说解释失败。

独立 verifier 结论为 NOT PASS，并指出两项语义问题：

- EXTRACT 把未知的空 slot 内容描述为确定的 declare/stub/verify 行为，甚至以 imports
  推断“恰为此目的”，但同一输出又承认 slot 原内容未知。候选必须保留假设身份。
- 回执评估文字称未确认也未反驳候选，却标记 `supports`，影响判断不一致。

这些是模型结果的局限，不能把结构校验或自主调用发生视为场景推导质量通过。

## 逐项验收

| 标准 | 结论 | 理由 |
| --- | --- | --- |
| AC-17-09 / G2 | 保持通过 | 已审核案例与输入范围未变 |
| G3 | 通过 | 代码/输入身份、凭据存在性、模型、费率和预算均预检并预登记 |
| AC-17-10 | 未通过 | 有自主问题和一次真实搜索，但未取得关键行为证据、完成判断更新或自主停止 |
| AC-17-11 | 未通过 | 后三阶段未执行，无严格有效最终契约 |
| AC-17-12 | 记录完成 | 命令、身份、全部尝试、诊断和用量已留存；在 #17 同步未通过判定 |

## 用量与验证

3 次请求均完成，已知输入 18,469 token、输出 8,698 token，总计 **27,167 token**，
用量完整。按峰值缓存未命中输入 US$0.30/M、输出 US$1.20/M 估算 **US$0.0159783**。
第三次请求报告 3,200 cached input tokens，但为保守估算不减去缓存优惠；这不是账单。
费率由独立 researcher 于当日核对 [官方价格](https://api-docs.deepseek.com/quick_start/pricing/)。

[artifact-checks.json](artifact-checks.json) 核对了：产物 SHA256、预登记输入/提交、
全部生产与研究代码摘要、模型 payload 摘要和响应关联、三条观察原文及冻结文件身份。
真实凭据未出现在留存文件中。manifest 的 `dirty=true` 包含未提交的本次证据及原有
未跟踪锁文件；代码摘要逐项与预检一致，本轮没有代码或输入准入修改。

前一交付的 548 项离线回归仍作为代码门禁证据；本轮未声称重新运行它们。
本轮新增验证为严格校验离线复现、产物/来源核验和独立真实轨迹评阅。
没有执行目标测试、旧项目实验、Mock 注入、gold/归档读取或依赖扫描。

## 后续方案与复现限制

下一步先解决协议一致性和假设表述问题，不对同一完整案例连续抽样：

1. 如继续外部接口诊断，单独登记最小合成回执的 schema-conformance 检查，明确请求数、
   token/费用预算，并保存实际 `text.format`、返回 JSON、响应身份；不携带案例答案。
   其结果只能用于定位接口行为，不能计作 M2 通过。
2. 只有发现可复现的宿主/SDK/协议构造缺陷，才做离线修复与回归，再独立登记修复后运行。
   若仍是供应商返回不合规，保留失败并处理接口契约问题，不能靠删除未知字段通过。
3. 独立复核通用候选表达，要求将未知 slot 行为写为条件假设；不能恢复隐藏实现，
   也不能把本案例已知答案写入提示。该改动需单独设计验证，本轮未修改 Prompt。

[reproduce.sh](reproduce.sh) 会发起付费请求，并因现有输出目录而拒绝覆盖。
如以后获准复验，应另行预登记并使用新输出目录；本轮未执行第二次完整运行或额外接口探针。
