# Step 6 Live 固定协议 — 2026-10-21

在首个正式模型请求前记录。此次用户授权覆盖早期 AGENTS 的 Agent 非目标限制，
仅执行已有 Step 6 推理与审查，不开发 Agent、不生成 Mock、不扩大输入。

正式顺序：Python V0.1、Python V0.2、Java V0.1、Java V0.2，各一次。
结构、引用、语义失败和零工具调用均保留为结果，不按质量重试。
全轮最多两次技术重试，仅在现有账本允许且此前费用明确时使用；未知费用即停止。
同一模型 deepseek-flash、官方 https://api.deepseek.com Responses API，
使用 Step 4 授权的 Stub-Tester .env 中单个 DEEPSEEK_API_KEY，仅内存注入。
不执行 dotenv、不保存密钥、不记录 HTTP headers。

## 预先确定的预算

DeepSeek 官方 [Responses 参数](https://api-docs.deepseek.com/api/create-response/)
明确 max_output_tokens 同时包含可见输出和 reasoning tokens。默认 4096
可能在最终 JSON 前耗尽。本轮 V0.2 统一提高为 16384，而非根据结果调整。
累计输入加输出限制设为 160000，避免多轮重复完整输入与工具结果时过早阻断；
6 轮、8 次工具调用、每次工具输出 16000 字符、分析超时 15 秒不变。
总超时统一 600 秒。单次费用门禁/账本预留 US$0.75，全轮目标 US$5。
按最高单价保守估计，160000 Token 即使全部按输出计费也仅 US$0.192；
六次这样的尝试为 US$1.152。估计与远端实际计费、V0.1 默认上限不同，非硬上限。

官方 [价格页](https://api-docs.deepseek.com/quick_start/pricing/) 2026-10-21：
Flash 峰时未缓存输入 US$0.30/M，缓存输入 US$0.006/M，输出 US$1.20/M；
非峰时减半。本账本统一按峰时未缓存输入 0.30/M、输出 1.20/M 保守估费，
reasoning 属于输出，不重复相加。保留原始 usage 以供复核缓存/推理细分。
这不是 provider 账单，不能宣称严格美元硬上限。

## 保持基线与比较限制

V0.1 核心、两个 Prompt、历史记录不变。V0.1 仍 max_turns=1，
不设置 max_output_tokens 或累计 Token hook；保留 SDK 默认传输重试。
V0.2 max_retries=0；工具由模型自主选择，未强制工具调用。
两版本均不覆盖采样/推理参数。官方 [模型说明](https://api-docs.deepseek.com/api/list-models/)
标称 Flash 默认 high reasoning，部署对应 V4.1-Flash-1017，输出能力最大 393216。
不把别名或文档版本当不可变部署标识；以原始响应实际字段为证。
不为统一输出上限而修改冻结基线；输出预算、Prompt 与多轮循环差异是混杂因素，
不能将质量差异严格归因于工具。thinking 模式 temperature 不生效，不声称固定温度。

## 留证、审查与停止

宿主脚本 scripts/run_step6_live.py 只注入授权认证和保存实际 HTTP JSON 正文，
调用已有 live_v02 入口，不修改 Agent 请求参数。请求包含 Prompt、Task、工具 Schema、
Contract Schema 和实际历史；响应包含工具请求、最终文本/无效候选、usage 和状态。
原始路径 out/live_inference/step6-live，各目录拒绝覆盖；共用 ledger.json。
首个请求冻结代码/Prompt/SDK/解析器/输入身份；脚本及本协议哈希另存 launcher.json。
HTTP 传输头不留存，body 按 JSON 保存且记录传输原文 SHA256，不声称完整网络抓包。

运行前已有 189 项测试通过；无认证连通性检查在沙箱内失败，提升网络权限后返回 401，
未发送推理请求、未发送凭据。正式请求应在可联网权限下执行。
宿主脚本首次启动在导入 httpx 时失败（安装的 OpenAI 3.35.1 使用 httpx2），
尚未创建结果目录、账本或发起网络请求。首个正式调用前已改用已安装的
httpx2 和 SDK 默认 HTTP client；没有新增依赖，不计为模型技术重试。

按准确性、条件完整性、替代路径、不确定性、行为推导逐条审查原始候选及有效 Contract。
审查明确标注“AI 辅助语义审查，待人工确认”，不冒称人类已完成审查。
特别追踪模型工具请求、实际返回、incomplete 缺失事实及最终结论间的关系。
工具分析不扩大原始可见信息范围，不把额外工具调用等同于新的源码证据或因果改善。
四次结果完成或模型/费用阻塞后产出对照报告并停止，不启动下游生成。
