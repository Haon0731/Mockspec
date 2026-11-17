# 切片线索 A/B 配对试跑协议

状态：局部语义门禁已通过；第一轮生成接入因偏离Stub-Tester原实验协议而停止，不能用于A/B效果结论。
必须恢复原冻结Agent工作区、依赖／测试反馈、`mock_stub.txt`与`verify.txt`双文件输出及原注入流程，A/B只相差切片输入。
本轮用户已指定 deepseek-v4-pro，每个合格案例每组一次，单次最多600秒，先报告逐例错误，不加入定向变异反馈。

## 配对变量

A 接收审核的 Masked Test、待测函数和同等源码查询权限；B 的唯一区别是额外提供同版本验收通过的 slice-evidence 线索。两组新会话，不共享答案、失败诊断或生成历史。

固定 Agent 框架、明确模型 ID、供应商配置、工具权限、单次600秒预算、生成/修复机会和评价方式。组序按预先固定种子20261117排列；不按结果重跑或择优。模型费用与静态分析成本分开记录；未知usage不得写0。

工具生成器沿用 Stub-Tester 的 DeepSeek 路由和隔离原则。旧项目只读，不在其中写入样本清单或运行产物；不得使用旧 candidate_cli 的默认全量入口。只允许本次清单中的病例，临时工作区和输出位于 MockSpec 的独立实验目录。

## 前置门禁

- 记录所有原局部7个案例的 pass/unknown/fail；失败及未支持案例不删除。
- Python正例、Java正例与Java已知时序反例须独立通过；不能只移除全部关系取得通过。
- 消费物经Pydantic和外部原文身份校验，包含实际源码切片及有证据的关系。
- 每份B输入绑定原snapshot、源码摘要、原观察与当前实现摘要；不得读取人工参照、gold、私有IR、旧生成候选或答案报告。
- 仅对有用且通过的消费物发起A/B；其余案例保留原因而不宣称支持。
- 运行前确定API认证、隔离运行时、实际费用控制能力与输出保护。不得把CLI的JSON事件格式当成模型契约Schema。

## 运行与观察

两组均可自主读取授权源码。若复用Stub-Tester broker的受控shell/测试反馈，应两组一致并在manifest明确，而不是称为只有Read/Grep。候选代码按原masking区域注入；普通测试输入、断言、被测实现及其身份不得改变。

优先把模型输出作为源代码文件处理，宿主执行masking和语法/构建校验；如让LLM生成结构化契约或诊断，必须原生Schema/强制Tool Calling＋Pydantic model_validate_json，不从正文裸json.loads。传输事件和宿主记录不等于LLM正文。

留证：冻结的共同输入和B附加输入摘要、指令、模型/CLI版本、工具轨迹、终止原因、用量/费用或未知、候选差异、构建/测试回执及原区域外不变检查。秘密认证值不得写入产物。

## 评价

逐例配对报告生成有效性、编译/导入失败、替换位置错误、返回/异常配置错误、状态遗漏、参数/交互错误、无依据的过约束，以及无法判定。原测试未覆盖的行为不因为测试通过而判为正确；基础设施失败与Mock语义错误分开。

单次配对仅能展示本次具体变化，不建立统计性效果或因果普遍性。不预设B优于A；结果相同、变差或无法评价均如实记录。本轮不把“杀死更多变异体”作为优化目标。

## 已停止的接入试跑（历史，不能继续使用）

以下实现复用了底层CLI runner，但错误地将原冻结工作区简化为源码、将双槽输出改为完整测试文件。
这不是等价的Stub-Tester实验方式。两例B均超时，Jib仅留下部分文件；A在用户指出偏差后停止。
`out/slice-ab-pilot-20261117/`已标记 `invalid_protocol_deviation`，只保留排障证据，不计入正式配对实验。
`prepare_pair.py`、`run_pair.py`与`evaluate_candidate.py`仅保留解释该次失败尝试，不作为下一轮执行入口。

`prepare_pair.py` 从门禁指定的冻结案例读取全部允许源码，在两个独立工作区生成完全相同的源码；仅B增加切片。
它验证门禁、契约、源码原文和身份，不把人工参照加入输入。当前两例分别有32和42个授权源码文件。

`run_generation.py` 只适配 Stub-Tester 的 `run_feedback_repo_cli_agent_case`；固定 Claude Code + `openai/deepseek-v4-pro`、600秒、单个候选文件写权限。
Agent通过相同broker读取源码、使用受控shell；生成工作区只含源码，没有运行时依赖，因此不将生成中的shell检查称为正式目标测试验证。
没有经确认的硬美元费用上限；用量和费用缺失时记null，不记0。

`run_pair.py` 按固定顺序B→A各运行一次并记录失败，无质量重试；调用 `evaluate_candidate.py` 检查候选标记外固定上下文逐字不变后，
使用冻结注入器和测试runner在隔离Docker内评估。依赖只读挂载，不重新计算依赖树哈希；运行产物全部在MockSpec中。
这里评估的是原masked测试上下文加候选区域，不读取或恢复gold测试。

实际产物为仓库 `out/slice-ab-pilot-20261117/{flask_301,jib}/`，每例包含配对manifest、task、工具轨迹、候选和results。
相同输出目录拒绝覆盖；复现必须指定新目录，并继续使用原门禁绑定的输入。

```bash
PYTHONDONTWRITEBYTECODE=1 /home/haon/data/experiment-registry/rq234-repeat/tools/fair-v1-fix11-runtime/bin/python \
  experiments/backward-slicing/slice-ab-pilot/run_pair.py \
  --gate experiments/backward-slicing/results/frontend-local-reliability/gate.json \
  --pair out/slice-ab-pilot-20261117/flask_301
```

该命令仅展示本轮已准备工作区的启动方式，不应用于重复已登记尝试。
