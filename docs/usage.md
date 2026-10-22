# 运行与开发

[返回项目入口](../README.md) · [先看 Agent 结构](architecture.md)

普通推断与回放使用 `mock_spec.cli`；需要完整运行证据时使用 `mock_spec.audited_run`。
`mock_spec.live` 保留早期 V0.1 留证行为。以下命令是使用说明，不授权新实验或付费请求。

## 安装与测试

需要 Python 3.10+；运行依赖为 Pydantic 和 OpenAI Agents SDK，开发依赖为 pytest。
推荐在项目内 `.venv` 安装，避免修改系统 Python 或旧项目环境。

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e '.[test]'
.venv/bin/python -B -m pytest -q -p no:cacheprovider -m "not real_data"
```

离线且已有项目依赖时：

```bash
PYTHONPATH=src .venv/bin/python -B -m pytest -q -p no:cacheprovider -m "not real_data"
```

上面的命令排除 `real_data` 测试；Java 合成测试仍可能使用本机固定的 Spoon/JDK，缺失时按测试约定 skip。
需要读取已审核冻结输入的集成测试时，应先阅读 [输入审核记录](input-audit.md)；
`tests/test_real_data.py` 还包含归档对照，不属于普通离线回归。

真实数据测试直接读取本机已有冻结 snapshot，不复制数据、不运行 benchmark。
没有这些外部文件时对应测试会明确 skip。设置 `MOCKSPEC_STUB_TESTER` 可指定旧仓库根；
显式配置后缺少 snapshot 会失败。归档对照测试只在已记录的 home-ts 路径存在时运行。

## 数据接入

Adapter 接受 **snapshot 目录和原 Case ID**，不接受原始测试、gold 目录、评分目录
或任意源码文件。当前只批准 `src/mock_spec/reviewed_inputs.json` 中的两个案例。
未审核案例报错；扩展案例需要人工/受信任代码审查后更新这份宿主侧白名单，
不能由后续 Agent 填写一个 `verified=true` 来取得授权。

Java：

```python
from pathlib import Path
from mock_spec.adapters.java import load_task
from mock_spec.validation import SourceStore

root = Path(
    "/home/haon/projects/Stub-Tester/.omx/developer-realistic-registry-max/"
    "formal-snapshots/java-bechmark-strong/"
    "5b93995c794af0152854a01ddda2fe77948b5ab828b05282b00728f7729f64d4"
)
task = load_task(
    root,
    case_id=(
        "com.google.cloud.tools.jib.cli.jar.JarFilesTest#"
        "testToJibContainerBuilder_explodedStandard_basicInfo@69ae66df"
    ),
)
agent_payload = task.to_dict()
source_store = SourceStore(task)
assert source_store.get("mut") == task.mut_source
```

Python：

```python
from pathlib import Path
from mock_spec.adapters.python import load_task

root = Path(
    "/home/haon/projects/Stub-Tester/.omx/developer-realistic-registry-max-fix23/"
    "formal-snapshots/python-benchmark-new/"
    "241ef457f8d071c23e6acd658b684b11b54f13fa955e097d6bebe35dc755f47d"
)
task = load_task(root, case_id="flask_308")
agent_payload = task.to_dict()
```

没有写出文件、修改旧仓库或自动回退来源的行为。可迁移 snapshot 的物理位置，
但目录的 dataset/snapshot ID、所选控制文件和源码内容必须与已审核记录一致。

## Schema

`UnifiedTask` 使用不可变 dataclass；`from_dict()` 对每一层实施严格字段白名单、
类型、枚举、源码范围、source ID 和引用一致性验证。未知字段、缺失字段、
布尔值充当行号、越界引用和宿主绝对路径均被拒绝。`decode_json()` 还拒绝重复 JSON 字段。

包含 `language`、`case_id`、`mut_source`、`masked_test`、`permitted_interfaces`、
`supporting_sources`、`execution_context`、`input_policy`、`sources` 和版本号。

- 源码行号是所选冻结文件中的 1-based 行号；MUT 切片保留原位置。
- `symbol`、`resolution_status`、`target_precision` 区分符号定位与解析精度。
- Java 当前保留完整 `JarFiles` 类，定位精度为 class；未声称完成类型绑定。
- Java 接口列表包含冻结 `StandardExplodedProcessor` 的三个声明，保留其原有
  implementation-hidden 源码，不从原仓库恢复方法体。
- Python MUT 为 `abort` 函数，并保留必要导入上下文。动态依赖签名尚未解析，
  `permitted_interfaces` 为空，不推测参数或调用约束。
- 测试框架版本未核实，用 `null`；不读取环境变量、凭据、依赖目录或结果文件来补齐。

## 普通推断与离线回放（默认 V0.1）

单案例流程：审核 Adapter → UnifiedTask → 共享 Agent → 契约验证器 → JSON。
Java/Python 共用同一个 Prompt、SDK Agent 配置和验证器，只在宿主选择不同 Adapter。

```bash
# OPENAI_API_KEY 由运行环境安全注入，不写入仓库。
export MOCKSPEC_MODEL='你的可用模型 ID'
PYTHONPATH=src .venv/bin/python -m mock_spec.cli \
  --language python --snapshot /path/to/reviewed/snapshot \
  --case-id flask_308 --output scenario.json
```

`--model` 可覆盖 `MOCKSPEC_MODEL`；默认总超时 120 秒，可用 `--timeout` 调整。
输出文件必须不存在。失败返回非零退出码，stderr 不回显模型响应或凭据。
缺少 API Key 不会自动伪造结果。

离线回放显式提供 `--response-json examples/scenarios/python.synthetic.json`，
仍通过原 Adapter 核验真实输入，输出会标记 `synthetic_replay`，不调用模型。
详见 [运行示例与证据解释](../examples/scenarios/README.md)、
[契约与验证边界](scenario-inference.md)。

结构验证只证明字段、引用和源码位置合法；不证明推导语义正确，也不能保证模型从不
虚构需求。`required` 必须引用场景并声明条件；调用预期不等于必须生成 Mock Assertion。

真实推理留证使用 `python -m mock_spec.live`：仅支持 live 调用，使用
`--output-dir` 指定不存在的目录，保留 SDK 原始输出项、Token 用量、版本身份和
结构/证据验证失败类型。它不修改 V0.1 Prompt，也不自动重试修正回答。
两例验证配置与命令见 [Step 4 固定协议](step4/protocol.md)。

## V0.2 工具与完整留证

默认仍为 V0.1。使用 `mock-spec-scenario --agent-version v0.2` 显式选择 V0.2，
其独立 Prompt 和 SDK Agent 可自主调用 `inspect_source`、
`inspect_program_structure`、`inspect_dependency_usage`，也可以零次调用。
初始输入仍是完整可见 UnifiedTask；工具分析不增加源码可见范围。

Python 使用标准库 AST；Java 使用已固定的 Spoon 11.2.1 工具 JAR 和 Java 17+
工具链（含 javac）。宿主可配置 `MOCKSPEC_SPOON_JAR` 和 `MOCKSPEC_JAVA`；
默认查找本机 Step 5B 的独立 `/tmp/mockspec-step5b-tools`，不自动下载。
JAR 必须符合 Step 5B 已记录身份。javac 只编译随包提供的可信 helper，不编译或执行目标源码。
工具不可用、分析失败、unsupported、unresolved 和输出截断均显式记录。
语法嵌套、词法分支和局部声明引用不是数据流或可执行路径证明。

默认 V0.2 上限为 6 轮、8 次工具调用、每次最多 4096 输出 Token、
估算及累计 24000 Token、16000 字符工具结果、120 秒总超时。
Python 分析受源码/节点规模限制；Python/Java 均在受限环境的可信解析子进程中运行，
超时或取消时终止并回收子进程，不执行目标源码。
Token/费用门禁采用请求前估算和响应后累计，不能保证远端费用硬上限。

完整留证入口为 `python -m mock_spec.audited_run`（同时支持两个版本）。
正式对照要求共用 `--experiment-ledger`，并提供 `--attempt-id`、`--run-key`、
`--max-cost-usd` 和宿主核实的输入/输出每百万 Token 价格。
账本限制四个首轮运行、最多两次技术故障重试和全轮 US$5 目标；未知费用或未完成尝试
阻止后续运行。单独诊断必须显式选择 `--standalone`，不冒充正式对照。
V0.1 核心保持冻结，原有 Token 上限与传输重试差异会在产物中注明。

两版本均不自动按质量重试。结果目录必须不存在；失败保存轨迹与可得用量，
不伪造 Contract。详情见 [Step 6 规格](step6/spec.md) 和
[任务依赖](step6/issues.md)。实际验收与未覆盖项见 [交付报告](step6/report.md)。

## flask_308 与归档模板

已核实：冻结 snapshot 的 10 个 slot 为空；旧 run 中名为 `masked_templates` 的文件
含生成后的 mock 行，与冻结 `mask_sha256` 不一致。Java 选定案例也存在这一归档现象。
两种 Adapter 都拒绝用归档文件替代冻结输入。详见
[输入审查记录](input-audit.md)。

本项目保留选定输入的 masking 与上下文，不声称裁剪后的 Task 与旧完整 workspace
在信息量上完全等价；没有改变旧实验数据、协议或评分。当前批准两个案例，
不意味着全量 Java 59 / Python 106 已通过接入审查。
