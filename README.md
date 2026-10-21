# Mock Spec

独立的 MockBench 场景分析项目。提供严格 Task Schema、两个只读 Adapter、
基于 `source_id` 的内存访问接口，以及 Scenario Inference Agent V0.1/V0.2。
Agent 只分析依赖行为约束，不生成 Mock、不执行目标测试、不评分。

## 安装与测试

需要 Python 3.10+；运行依赖为 Pydantic 和 OpenAI Agents SDK，开发依赖为 pytest。
推荐在项目内 `.venv` 安装，避免修改系统 Python 或旧项目环境。

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e '.[test]'
.venv/bin/python -m pytest -q
```

离线且已有项目依赖时：

```bash
PYTHONPATH=src .venv/bin/python -B -m pytest -q -p no:cacheprovider
```

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

## 信任边界

1. **准备侧**维护已审查输入记录：案例身份、冻结 ref/runtime/injector 身份、允许源码、
   来源范围与 masking 约定。这些私有记录不属于 Agent payload。
2. **Adapter**只读核对有限的已选文件。只校验所选源码和控制文件，不扫描或哈希 JAR、
   site-packages、依赖树，也不执行 Stub-Tester 的 loader、injector 或其他脚本。
3. **Masking 检查**要求 Java 双区域为空、Python slot 集合和顺序一致且内容仅为 `pass`，
   同时拒绝本策略下的残留 mock 配置与验证。文件身份检查覆盖区域外的上下文。
4. **Agent 边界**初始仅传递 `task.to_dict()`；验证器和 V0.2 工具只访问当前任务的内存 `SourceStore.get(source_id)`。
   Store 不持有物理根目录或文件读取函数，不能以路径访问旧仓库。

Schema 合法不等于 masking 已审核。不能用 `UnifiedTask.from_dict()` 自行组装任意源码，
再宣称它通过 Adapter 审核。内容检查也不是通用泄漏检测器：准入依据是已审查的固定
输入身份加内容检查，而不是正则或文件名。

Agent 在宿主 SDK 中创建。V0.1 没有工具；V0.2 仅有三个受限的源码/语法分析工具，
没有任意路径读取、目标程序执行、handoffs 或 MCP，不接收 Adapter、宿主 review 配置或旧数据目录。关闭 SDK tracing，
模型请求设置 `store=False`。这不是 OS sandbox，也不是对服务方数据保留政策的保证。
调用 Python API 的宿主仍须先通过 Adapter 审核输入；手工构造 UnifiedTask 不获得准入。

## Scenario Inference V0.1

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
详见 [运行示例与证据解释](examples/scenarios/README.md)、
[契约与验证边界](docs/scenario-inference.md)。

结构验证只证明字段、引用和源码位置合法；不证明推导语义正确，也不能保证模型从不
虚构需求。`required` 必须引用场景并声明条件；调用预期不等于必须生成 Mock Assertion。

真实推理留证使用 `python -m mock_spec.live`：仅支持 live 调用，使用
`--output-dir` 指定不存在的目录，保留 SDK 原始输出项、Token 用量、版本身份和
结构/证据验证失败类型。它不修改 V0.1 Prompt，也不自动重试修正回答。
两例验证配置与命令见 [Step 4 固定协议](docs/step4/protocol.md)。

## Scenario Inference V0.2

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

完整留证入口为 `python -m mock_spec.live_v02`（同时支持两个版本）。
正式对照要求共用 `--experiment-ledger`，并提供 `--attempt-id`、`--run-key`、
`--max-cost-usd` 和宿主核实的输入/输出每百万 Token 价格。
账本限制四个首轮运行、最多两次技术故障重试和全轮 US$5 目标；未知费用或未完成尝试
阻止后续运行。单独诊断必须显式选择 `--standalone`，不冒充正式对照。
V0.1 核心未改，原有 Token 上限与传输重试差异会在产物中注明。

两版本均不自动按质量重试。结果目录必须不存在；失败保存轨迹与可得用量，
不伪造 Contract。详情见 [Step 6 规格](docs/step6/spec.md) 和
[任务依赖](docs/step6/issues.md)。实际验收与未覆盖项见 [交付报告](docs/step6/report.md)。

## flask_308 与归档模板

已核实：冻结 snapshot 的 10 个 slot 为空；旧 run 中名为 `masked_templates` 的文件
含生成后的 mock 行，与冻结 `mask_sha256` 不一致。Java 选定案例也存在这一归档现象。
两种 Adapter 都拒绝用归档文件替代冻结输入。详见
[输入审查记录](docs/input-audit.md)。

本项目保留选定输入的 masking 与上下文，不声称裁剪后的 Task 与旧完整 workspace
在信息量上完全等价；没有改变旧实验数据、协议或评分。当前批准两个案例，
不意味着全量 Java 59 / Python 106 已通过接入审查。
