# Step 4 真实推理验证报告

日期：2026-10-20。状态：**两例真实模型输出已取得；一例验证通过，一例证据验证失败。**
本轮结束，待研究方法与人工语义审查；不自动推进 V0.2。

## 1. 固定条件与可信边界

模型按用户指定为 `deepseek-flash`，使用被授权的 Stub-Tester DeepSeek Key，仅在
宿主进程内读取并注入环境；未将密钥复制到代码、配置、日志或报告。密钥配置的旧模型
名称不沿用。官方 DeepSeek Responses 端点支持现有 OpenAI Agents SDK 路径，
未切换到其他模型、未使用synthetic生成本轮结果。

- Prompt：scenario-inference/v0.1；SHA256：
  `e7bf55a8bb78b52701ab20bbee894513931642b772bc1a08bda8a79a5cb61e6d`，前后相同。
- Task Schema：mock-spec.task/v1；Scenario Contract Schema：mock-spec.scenario/v0.1。
- SDK：openai-agents 0.24.0；openai 3.35.1；pydantic 2.14.1。
- 端点：https://api.deepseek.com；Responses API；max_turns=1，timeout=300秒；
  tracing disabled，store=False，无工具/handoff/MCP/文件读取；采样与推理强度使用默认值。
- 官方文档标称别名对应 DeepSeek-V4.1-Flash-1017；SDK ModelResponse未暴露实际解析版本，
  结果中的 resolved_model_version=null。**文档标称不等于响应确认的不可变版本。**
- 输入仍为两个已审核冻结snapshot，只发送UnifiedTask及原Prompt/输出Schema。
  Agent未得到synthetic输出、旧mock、Ground Truth、评分或预先发现的歧义。

端点/别名依据：[DeepSeek首次调用](https://api-docs.deepseek.com/quick_start/agent_integrations/)、
[Responses兼容说明](https://api-docs.deepseek.com/guides/responses_api/)。
完整复现协议及环境命令见 [protocol.md](protocol.md)。

## 2. 实际运行结果

| 项目 | Java | Python |
|---|---|---|
| Case ID | com.google.cloud.tools.jib.cli.jar.JarFilesTest#testToJibContainerBuilder_explodedStandard_basicInfo@69ae66df | flask_308 |
| Snapshot ID | 5b93995c794af0152854a01ddda2fe77948b5ab828b05282b00728f7729f64d4 | 241ef457f8d071c23e6acd658b684b11b54f13fa955e097d6bebe35dc755f47d |
| 返回回答的尝试 | java/attempt-2 | python/attempt-2 |
| 模型调用 | 成功，真实响应已留存 | 成功，真实响应已留存 |
| Contract Schema | 通过 | 通过 |
| Contract Validator | 通过 | evidence_error |
| 精确源码引用 | 11/11匹配 | 10/11匹配 |
| input_tokens | 5,653 | 3,884 |
| output_tokens | 22,835 | 30,041 |
| 其中 reasoning_tokens | 17,135 | 25,847 |
| total_tokens | 28,488 | 33,925 |
| cached input tokens | 0 | 2,048 |
| UTC起止 | 15:44:03–15:45:28 | 15:46:40–15:48:28 |

两份成功返回响应的用量合计 **62,413 Token**（9537输入、52876输出；推理Token已含在输出中，
不重复相加）。这是SDK归一化用量，不是费用账单，不含没有返回用量的网络阻塞尝试。

结果根目录：[out/live_inference/step4](../../out/live_inference/step4/)。每个attempt-2包含：

- `result.json`：live_inference标记、版本/输入hash/参数、原始SDK输出项、用量、实际验证状态；
- `raw-contract.json`：模型原始JSON文本，无人工修正；Python这份是**未通过证据验证的候选**；
- `validation-recheck.json`：重新调用同一Validator的结果，以及每项引用与实际源码的逐项比对。

初次沙箱网络不可达。Java attempt-1最终记录model_call_failure，未捕获响应；
Python attempt-1来自同一阻塞批次，子进程已终止，保存interruption记录。
沙箱外恢复批次对已存在Python目录拒绝调用，之后独立使用attempt-2。
这不是按回答质量重试。取消/SDK清理导致初次Java墙钟约600秒，说明配置timeout不是硬进程期限。
不能推断这些无响应尝试未被服务端接收或零计费。详见协议中的恢复时间线。

## 3. 自动验证发现

Python唯一精确引用失败是 `EV_MOCK_SLOT`：模型将`mock_stub_001`三行原文标为
`test:61–63`，实际该范围为空白，正确位置是`test:66–68`。
源码与行号都已提供给模型，故这是定位/引用执行错误，不是缺少该段输入。
它影响uncertainty[0]、uncertainty[3]的引用可接受性；不能通过修正行号后冒称原回答通过。

Java验证通过只说明字段、ID引用、源码范围和引文合法，不证明其条件推导正确。

## 4. AI辅助逐条语义审查（待人工确认）

已覆盖**59条**结论：Java32条、Python27条，包括目标、假设、约束、交互、候选验证和Unknown。
每条保留Agent原始结论、源码证据、判断、依据和错误/能力归因；附引用源码对照：

- [Java逐条审查](java-semantic-review.md)
- [Python逐条审查](python-semantic-review.md)

这不是人工研究者签字，也不是参考答案评估。没有查询Ground Truth。

### 重要正向证据

- Java识别了计算entrypoint与直接提供等价自定义entrypoint两条路径；没有声称Java版本必须恰为8。
- Python覆盖五个参数行，并正确区分aborter抛出导致fallback不可达、aborter正常返回后继续调用
  `_wz_abort`；没有把docstring的otherwise误解为代码中的else。
- 两例所有ExpectedInteraction均为not_implied，没有升级为强制Mock Assertion。
- 多处Unknown正确保留隐藏配置和库实现不可见导致的不确定性。

### 具体问题与归因假说

| 发现 | 可见依据 | 更可能原因 | 置信度/限制 |
|---|---|---|---|
| Java的base分支与entrypoint分支没有完整交叉关联：部分目标和下游调用只关联S1/S2，未覆盖S3正常返回的情况 | mut:60–91；objectives、interactions的scenario_ids | D的覆盖不足或C缺组合建模证据；B限制S3 helper结果判断 | 中；已列出的required在其条件内成立，不是选错目标分支 |
| Java dependency_constraints[0]将default分支称作“asserted by the test” | test:71只断言base输出；mut:60–72 | D的措辞/论证不精确 | 中；S1/S2中empty-from条件本身正确，不是全局过度约束 |
| Python原始源码定位错行 | EV_MOCK_SLOT；test:61–68 | D执行失误或C缺确定性定位支持 | 高；输入和Prompt精确引用规则均已存在，B/A解释较弱 |
| Python替代异常来源场景未与三个app-context行的目标完整关联 | app_ctx_aborter_returns与objectives的scenario_ids | D/C组合表达覆盖不足 | 中低；异常传播语义本身正确 |

独立审查复核纠正了首轮审查中的过度判错：S4是源码支持的冲突候选，Prompt允许保留；
S3的库实现不可见，不应要求模型猜测；返回测试构造layer是合法candidate而非唯一要求；
正常成功场景内的调用不必逐次重复所有前缀不抛异常。上述均不计作模型推理错误。
verification candidates明确可选且有调用源码依据，额外回归价值/过拟合风险可以讨论，
但没有独立判定门槛时，不把审查偏好计作A/D缺陷。当前没有充分证据认定Prompt指令不足。

没有观察到足以支持“Python模型未理解异常传播”的证据。不能把失败的精确引用
替换解释成语义推理失败，也不能从这两例估计总体成功率或因果归因比例。

## 5. V0.2工具候选优先级（仅建议，本轮未实现）

1. **受限源码定位/引用工具**：在现有SourceStore边界内通过source_id返回精确行号和原文，
   或定位引用文本；不允许任意路径。直接对应Python已观测错误，成本较低，不需要先引入AST。
   若未来采用自动补正，应单独标记补正前/后，不能隐藏原始错误。
2. **路径条件—可见断言关联证据**：标明分支条件、替代路径以及与测试目标的冲突/未知，
   优先补充Java base×entrypoint的交叉关联；区分可达路径、冲突路径和满足目标仍未知的路径。隐藏helper实现继续标unknown。
3. **值流与异常边界摘要**：可辅助参数传递、顺序链和抛出后不可达的系统检查。
   本轮未证实对象身份或异常传播推理错误，故此项是低优先级候选，不是失败驱动的必需新增。
4. **交互候选理由门槛**：先作为研究者统一的审查规则，说明额外回归价值和适用条件，避免把个人偏好误判成模型错误；未见证据表明
   需要立刻新增Mock Generation或Behavioral Feedback机制。

A/B/C/D只能据现象提出假说；下一阶段需对照实验才能判断工具是否真正提升推理。
不通过现在修改Prompt来掩盖基线问题。

## 6. 实现与验证

本轮新增`src/mock_spec/live.py`留证入口；`inference.py`增加宿主响应捕获、JSON-safe用量
和结构/证据/调用错误分类，原`infer_scenario`默认调用接口仍兼容；新增`tests/test_live.py`。
README补充入口说明。没有新增依赖、修改Prompt/审核记录/旧仓库，未复制数据集。

全套自动测试：**101 passed**；真实输入集成测试包含所选文件不变校验；源码AST解析、
compileall与git diff --check通过。独立代码复核在修复Usage序列化问题后APPROVE；语义复核反馈已用于收紧Java报告，避免夸大模型错误，闭环复核APPROVE。新增/修改产物的密钥不泄漏检查通过。
当前环境未安装Ruff/Mypy/Pyright，且项目未配置这些检查，故未声称lint/typecheck已通过。
