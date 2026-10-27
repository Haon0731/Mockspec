# #17 受限 QUERY 格式纠正与真实 M2 验收

日期：2026-10-27。需求：`docs/workspace-exploration-prd.md` 第 10 节 M2-QUERY-RECOVERY-1。

## 当前结论

**R1 工程通过；R2 预检和单次真实运行完成；R3/M2 未通过。** 工程提交 `cbe04c0` 已推送。
QUERY v0.3 本次合规且未触发纠正，但模型零查询直接停止；CONSTRUCT 因输出上限截断。
#17 保持开放，#18 不启动。本次不证明真实 QUERY 纠正效果或全链路可用。

## 工程交付

- QUERY wire/policy/prompt/record 升至 v0.3；wire assessment 仅 impact/rationale，内部 v0.1 回执序号由宿主绑定。
- SDK 严格解析失败仍保留原响应、无效 stage 与符合登记谓词的 attempt。原无效 attempt 保持 protocol_error；纠正后的请求使用新 sequence。
- 仅允许 assessment 直接子字段中未知 null 这一类错误。完整重发必须与比较快照、pending receipt 身份/摘要精确一致；宿主投影不作为成功结果或工具输入。
- QUERY 与 DELIVER 共用一次不可退回的名额；预算拒绝、失败、取消不恢复名额。原始失败、纠正调用和新尝试在 format_repair_log 中关联。
- query.py 提供同一无副作用参数验证器。已登记的离散仓库搜索命中可支持下一次新行读取的身份检查；纠正资格检查不预读。已有证据引用仍要求可见且逐字一致。
- 通用指令要求条件假设和 unresolved；四个合格/不合格语义样例通过独立评阅，没有 CloudFoundry 专用答案。

## 验证

- 最终冻结实现：全离线 **606 passed, 14 deselected**，见 [pytest.txt](pytest.txt)。三个已审核冻结输入的只读加载/身份检查另行通过（3 passed）。
- 最终恢复与仓库专项 **91 passed**；独立代码审查 APPROVE，无剩余 Critical/High/Medium，见 [code-review.json](code-review.json)。
- Ruff 通过。研究类型检查仍有 16 条未修改生产依赖的既有诊断，修改的研究文件无新增；生产基线为18条/6文件，不把它们写成类型检查全绿。
- 最终 C 回放：[c-replay-reviewed](c-replay-reviewed/)，SELECTED，契约重验及全部产物/当前代码摘要匹配，见 [replay-verification.json](replay-verification.json)。较早的 c-replay 与 c-replay-final 保留实施过程中的离线快照，不作为最终代码证据。
- 原生产、正式 Schema、准入和历史失败材料全部摘要不变，见 [scope-verification.json](scope-verification.json)。无新依赖、目标测试、Mock 生成/注入或全量数据扫描。
- 固定语义样例评阅见 [semantic-review.json](semantic-review.json)；它不证明真实模型输出质量。

## 真实验收约束

使用已经审核的 CloudFoundry 案例和原冻结 snapshot，保留完整62行 masked test、51行目标类及原 Case ID。没有预先给模型父类路径、查询顺序或审核答案。
一次完整 C 变体；200000总token、8192单次输出、600秒整体/120秒阶段、估算费用US$1。DeepSeek官方峰值cache-miss输入US$0.30/M、输出US$1.20/M于2026-10-27独立核验；不采用缓存/非峰值折扣，估算不等于账单或远端硬上限。
官方来源：https://api-docs.deepseek.com/quick_start/pricing/ 。
真实运行结果不利时保留失败，不以质量重采样通过；仅全部AC-17-10～12与工程门禁通过才关闭#17。

## 单次真实运行结果

[预登记](live/preflight.json)与[复现命令](live/reproduce.sh)固定工程提交、原审核输入和预算。运行耗时111.00秒：EXTRACT → QUERY（finish_queries，零工具）→ CONSTRUCT失败；未进入CONTRAST/DELIVER，没有最终场景卡。

- QUERY没有额外字段协议错误。模型误把“父类未在初始sources里”解释为“无法进一步读取”，尽管请求指令明确提供仓库search/read。零查询、零新增证据使AC-17-10独立失败；不能通过强制查询次数替代这一语义问题。
- CONSTRUCT的SDK错误明确为 `response.incomplete`，`IncompleteDetails(reason='max_output_tokens')`，本次上限8192。未保存该次完整响应或用量；不能据此判断未返回草案的语义质量。
- 3次请求开始，2次完成留证；已知小计22156 token、按登记费率估算US$0.0170058。第三次用量缺失，**整次token/费用未知**，不能把小计当总额。
- 格式纠正调用0次，名额仍available；受限纠正仅有离线恢复证据，真实运行没有触发它。
- [产物核验](live/artifact-checks.json)确认输入/代码/响应关联/摘要一致，实际凭据未出现在产物中。[诊断](live/diagnosis.json)区分观察事实与未确定原因。独立M2判定另见live/acceptance.json。

后续缺陷是仓库可探索能力的理解与停止判断，以及CONSTRUCT输出上限/不完整响应留证；需另行限定修改和新运行协议。当前未扩大修复范围、提高额度或自动重跑。
