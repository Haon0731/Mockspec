# Step 4 — 固定验证协议（2026-10-20）

## 范围与隔离

本轮用户明确授权在现有 V0.1 上做两个审核案例的真实推理验证，后续指定模型
`deepseek-flash`，并授权只读复用 Stub-Tester 的 DeepSeek API Key。
此授权仅用于宿主认证，不把凭据或旧项目配置加入 UnifiedTask。不修改旧项目、
准入记录、Prompt；不引入分析工具、Mock Generation、Behavioral Feedback 或评分。
仓库中原有未提交文件保持原状，不自动提交或改写 AGENTS.md。

模型只接收原版本 System Prompt、SDK 输出 Schema、审核 Adapter 的序列化 UnifiedTask。
没有工具、handoff、文件访问或执行能力；不接收本协议、语义审查、synthetic 示例、
Reference Mock、Ground Truth 或历史评分。SDK tracing 关闭，store=False。

## 固定配置

- Prompt：scenario-inference/v0.1，文件 `src/mock_spec/prompts/scenario_v0.1.txt`。
- Prompt SHA256：`e7bf55a8bb78b52701ab20bbee894513931642b772bc1a08bda8a79a5cb61e6d`。
- Task Schema：mock-spec.task/v1；Contract Schema：mock-spec.scenario/v0.1。
- 模型：deepseek-flash；端点：https://api.deepseek.com；API：Responses。
- 官方文档称该别名当前由 DeepSeek-V4.1-Flash-1017 提供；这是文档标称，
  不是响应提供的不可变部署标识。SDK ModelResponse 未暴露 resolved version，记录 null。
- 每例一次应用层调用，max_turns=1，总超时300秒；SDK默认传输重试，
  不按输出好坏重试，不修正回答。网络/协议故障恢复若必需，另存 attempt 并说明。
- 不覆盖采样、推理强度或输出 Token 参数，沿用 SDK/服务端默认；
  因此这些默认可能随服务变化，不声称可逐字复现。Token 用量按响应记录，
  不是费用账单；本入口不提供硬性美元费用上限。
- 输入身份：使用 `docs/input-audit.md` 的两个 Case ID 和冻结 Snapshot ID；
  每次结果另记 task_sha256、snapshot_id、包版本与实际参数。
- 新输出目录 `out/live_inference/step4/<language>/attempt-1/result.json`；
  目录已存在即拒绝调用，失败保留记录。原始响应为 SDK 返回的 output items，
  保留模型文本及归一化用量，不是 HTTP headers 或完整 wire dump。

## 自动验证与审查

1. model_call_failure：没有可验证回答的调用/超时错误；不是推理能力错误。
2. structure_error：SDK结构输出解析或 Contract Schema 失败。
3. evidence_error：案例身份、引用 source_id、范围或精确源码引文不符。
4. schema_and_evidence_locations_passed：仅结构和源码位置通过，不代表语义正确。

语义审查从原始回答展开，不能只审查成功 Contract。每条 objective、hypothesis、
constraint、interaction、verification candidate、uncertainty 均有索引，记录原文、
证据位置、判断、理由、错误类型、A/B/C/D归因及不确定性；同时检查遗漏场景。
AI所做审查标记为“AI辅助语义审查，待人工确认”，不冒称人工研究者意见。
不开展参考答案评估。两例只提供案例级结果，不估计总体准确率。

## 认证与调用

官方文档：
- https://api-docs.deepseek.com/quick_start/agent_integrations/
- https://api-docs.deepseek.com/guides/responses_api/
- https://developers.openai.com/api/docs/quickstart

密钥通过宿主环境 `OPENAI_API_KEY` 注入；不写入本仓库文件、shell 命令参数或日志。
本轮只解析被用户授权的 `.env` 中 DEEPSEEK_API_KEY 一项，不执行/source该文件，
不导入旧仓库模块；使用明确的官方端点和用户指定模型，避免沿用旧模型配置。

通用入口（须先由安全环境注入密钥）：

```bash
export OPENAI_BASE_URL=https://api.deepseek.com
export MOCKSPEC_MODEL=deepseek-flash
PYTHONPATH=src .venv/bin/python -m mock_spec.live \
  --language python \
  --snapshot /home/haon/projects/Stub-Tester/.omx/developer-realistic-registry-max-fix23/formal-snapshots/python-benchmark-new/241ef457f8d071c23e6acd658b684b11b54f13fa955e097d6bebe35dc755f47d \
  --case-id flask_308 --timeout 300 \
  --output-dir out/live_inference/step4/python/attempt-1
PYTHONPATH=src .venv/bin/python -m mock_spec.live \
  --language java \
  --snapshot /home/haon/projects/Stub-Tester/.omx/developer-realistic-registry-max/formal-snapshots/java-bechmark-strong/5b93995c794af0152854a01ddda2fe77948b5ab828b05282b00728f7729f64d4 \
  --case-id 'com.google.cloud.tools.jib.cli.jar.JarFilesTest#testToJibContainerBuilder_explodedStandard_basicInfo@69ae66df' \
  --timeout 300 --output-dir out/live_inference/step4/java/attempt-1
```

当前入口的可运行性测试使用离线 transport doubles，与真实推理结果严格区分。
本轮完成后停止，研究者审核后再决定 V0.2；不自动修改 Prompt。

## 运行故障与恢复记录

首次沙箱内批次的网络检查失败（无凭据 curl exit 7 / HTTP 000）。Java attempt-1
最终写出 model_call_failure / validation=not_run / raw_responses=[]，实际墙钟约600秒，
超过配置的300秒（实际出现asyncio executor线程join额外等待300秒的RuntimeWarning；配置不构成硬墙钟保证）。shell中断未立即终止子进程，
该子进程还创建了 Python attempt-1；随后核验 PID、命令、cwd 后已明确终止。
这些尝试的服务端接收及计费状态未知，不能把未留响应写成零成本或语义失败。

网络外恢复只变更执行权限，不改 Prompt/Task/模型/超时。Java attempt-2完成；恢复批次
对已占用 Python attempt-1 在调用前拒绝，之后用 Python attempt-2独立执行。
`interruption.json` 是宿主恢复记录，不是模型输出。Java attempt-1中早期中断观察
由实际 result.json 与此处说明补充：原子进程确实曾继续执行到Python。

复核还修复了 SDK Usage 的 JSON 序列化缺陷（TypeAdapter JSON mode）；真实成功调用
使用修复后的代码。新增端到端离线持久化测试，未改变推理内容。
