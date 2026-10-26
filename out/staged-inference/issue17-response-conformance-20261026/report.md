# #17 Responses 结构化输出一致性诊断

日期：2026-10-26。结论：**唯一一次真实合成探针合规，未复现原上下文中的违规；不能宣称已修复，M2 仍未通过。**

## 问题与范围

前次 CloudFoundry M2 在第三个模型响应中收到禁止字段 `attempt_sequence_note`，
宿主严格拒绝，未派发后续读取。原记录见
[真实验收报告](../issue17-cloudfoundry-m2-20261026/report.md)。用户继续要求推进后，
本轮独立检查实际 SDK 到 HTTP 的 Schema 序列化及供应商输出，不重跑该案例。

使用 diagnosing-bugs 的离线复现流程，但不采用重复付费采样。先构建无数据集内容的
最小失败形状，保留完整 `QuerySelection` wire Schema 以控制结构差异。
探针为一次性诊断脚本，不是生产入口，也不是新增推断阶段或协议修复。

## 假设与结果

| 假设 | 可检验信号 | 本轮证据与界限 |
| --- | --- | --- |
| SDK 序列化丢失严格约束 | 实际 HTTP body 缺少 strict 或嵌套禁止字段规则 | 本轮否定：捕获的 body 包含 strict=true、完整 Schema、additionalProperties=false |
| 供应商在最小输入也返回禁止字段 | 单次合成请求产生同类额外字段 | 本轮未复现；不能证明供应商所有请求都严格合规 |
| 故障与特定上下文或生成波动有关 | 缩小输入后不再出现违规 | 与观察相容，但一次请求不能区分具体触发因素或确认因果 |

没有证据支持本轮修改控制器、SDK、正式 Schema 或输入准入，也没有把删除额外字段当作修复。

## 诊断设计与身份

[preflight.json](preflight.json) 在真实调用前登记了一次请求、零技术/质量重试：
`deepseek-flash`、Responses、20,000 总 token、4,096 单次输出、150 秒整体、120 秒调用，
US$0.05 估算费用门禁。探针、控制器、Schema、payload、Python 与四个 SDK 版本
均绑定并在调用前核对。独立 code-reviewer 审核通过后才执行。

合成输入只有空回执，无案例、源码、答案或准备侧观察。它故意请求在回执评估中增加
`attempt_sequence_note: null`，而系统指令与 Schema 禁止额外字段。这是针对严格约束的
**对抗检查**，不是正常任务成功率测试；不能把它与原复杂任务的失败直接当作对照实验。

`probe.py --mode offline` 经实际 SDK HTTP 序列化与 MockTransport 注入最小违规响应。
本地严格验证必须产生该字段的 `extra_forbidden`，否则探针失败。
`--mode live` 只允许一条 HTTP 请求；重复请求在发送前拒绝，输出目录禁止覆盖。
不记录 HTTP 请求头；仅保存 JSON 请求体、响应体、状态码和可得请求身份，真实 key
若异常出现在文本中先脱敏。SDK tracing 关闭，实际 wire `store=false`。

## 观察结果

- 离线：`offline-final/` 精确抓到目标错误；完整 JSON Schema 在 `anyOf` 分支内因
  `additionalProperties` 拒绝该字段。其用量为模拟值，不能计为真实费用。
- 真实：`live/` 只有一次 HTTP 200。模型没有添加禁止字段，并在说明中明确指出该字段
  不属于严格 assessment Schema。原始 HTTP 输出 JSON 与 SDK 解析后的 wire result 相同。
- 完整 JSON Schema 验证通过，而不仅是 `previous_receipt_assessment` 的局部验证。
- 供应商响应 ID：`33e3c8d4-3d9c-4baa-85ca-8709d3ff021c`；返回模型名为 `deepseek-flash`。
  如响应头没有 request ID，记录 null，不用 response ID 冒充请求 ID。
- 输入 1,758、输出 480，总计 **2,238 token**；已知用量完整，峰值费率估算
  **US$0.0011034**。没有追加第二条真实请求或完整 M2 运行。

原始证据：[请求](live/wire-request.json)、[响应](live/wire-response.json)、
[SDK/用量记录](live/result.json)、[独立核验](verification.json)。

价格沿用当日独立核对的 [官方峰值费率](https://api-docs.deepseek.com/quick_start/pricing/)：
输入 US$0.30/M、输出 US$1.20/M，不计缓存优惠。这是估算而非账单或远端硬收费上限。
官方 [Responses 参考](https://api-docs.deepseek.com/api/create-response/)描述输出应符合
指定 JSON Schema，但没有解释前次违规成因；本次合规也不消除原失败证据。

## 审查与本地验证

首次离线 harness 配置遗漏 `run_mode=live_inference`，被本地配置校验拒绝，尚未发出
任何请求；修正后 `offline-verified/` 通过。独立审查随后要求绑定控制器与 SDK 身份、
修复动态 kwargs 的类型诊断，并明确单次检查的局限；修正后的 `offline-final/` 再次通过。
以上均是诊断脚本完善，不是发现或修复了原 M2 工程缺陷。

Ruff、编译及探针离线验证通过；mypy 无探针新增诊断，导入的生产代码仍有 16 项既有诊断。
全 Schema 核验脚本第一次将 `additionalProperties` 误当顶层错误；实际错误位于 `anyOf`
分支，修正报告层级后通过，两次都是离线检查，没有额外网络请求。
核验确认预登记身份、原始输出与解析结果一致、真实 key 未进入留存材料。

复现入口（从仓库根执行，必须使用未存在的输出目录）：

```bash
PYTHONPATH=src .venv/bin/python out/staged-inference/issue17-response-conformance-20261026/probe.py \
  --mode offline --output-dir /tmp/mockspec-receipt-offline-review
```

live 模式同时要求与预登记的输出目录和代码身份完全一致；本次目录已存在，不能直接
再次执行。未来新真实诊断需独立登记，不能改原登记或覆盖本次证据。

## 当前状态与下一步

G2/G3 保持通过，G4 仍失败；#17 保持开放，#18 不启动。本次没有修复可支持“修复后
再运行”的工程问题，不以探针合规为理由重跑同一案例。

下一步可进行**离线协议与候选表达设计审查**：明确未知 slot 只是假设，不是待恢复的
隐藏答案；审查协议是否需要消除冗余自由字段或重新划分模型/宿主职责。任何设计都必须
保留严格拒绝、原始响应和失败留证，不能通过忽略额外字段过关。
若要评估供应商在复杂上下文中的可靠性，应先制定独立的固定样本与预算协议，公开计入
所有失败；不能用不断追加单次请求来挑选成功结果。本轮到此结束付费诊断。
