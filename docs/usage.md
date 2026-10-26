# 运行与开发

[返回项目入口](../README.md) · [先看 Agent 结构](architecture.md)

普通推断与回放使用 `mock_spec.cli`；需要完整运行证据时使用 `mock_spec.audited_run`。
`mock_spec.live` 保留早期 V0.1 留证行为。以下命令是使用说明，不授权新实验或付费请求。

## 安装与测试

需要 Python 3.10+；运行依赖为 Pydantic 和 OpenAI Agents SDK。
`test` extra 提供 pytest；`dev` extra 另含固定版本的 Ruff 和 mypy。
推荐在项目内 `.venv` 安装，避免修改系统 Python 或旧项目环境。

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/python -B -m pytest -q -p no:cacheprovider -m "not real_data"
.venv/bin/python -m ruff check src tests scripts
.venv/bin/python -m mypy
```

离线且已有项目依赖时：

```bash
PYTHONPATH=src .venv/bin/python -B -m pytest -q -p no:cacheprovider -m "not real_data"
```

Ruff 使用 `pyproject.toml` 中的基础错误与未使用导入规则，不执行全仓格式化。
mypy 按 Python 3.10 的声明下限检查生产包，目前仍有既有类型诊断，会返回非零退出码；
它用于观察类型问题，不是当前合并门禁。修改代码时需核对诊断变化，不能将新增错误归入旧基线。
契约测试同时检查 `docs/scenario-contract.schema.json` 与当前输出模型一致。

[CI 配置](../.github/workflows/ci.yml) 在 Python 3.10 / 3.12 运行基础离线回归，
明确使用不存在的 Spoon 路径并报告 skip，不依赖本机工具、模型凭据或外部 snapshot。
静态检查单独运行，Ruff 必须通过；mypy 的实际结果保留在日志和 job summary 中。
基础 CI 通过不代表真实 Spoon 验收通过；后者见下方工具准备说明。

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

以下 `/path/to/reviewed` 是已审核 snapshot 的存放根目录占位符，按实际位置替换；
保留示例中的 dataset 目录名、snapshot ID 和 Case ID。示例不会下载或生成输入。

Java：

```python
from pathlib import Path
from mock_spec.adapters.java import load_task
from mock_spec.validation import SourceStore

root = Path(
    "/path/to/reviewed/java-bechmark-strong/"
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
    "/path/to/reviewed/python-benchmark-new/"
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
初始输入仍是完整可见 UnifiedTask；默认分析初始片段，仓库模式还可分析已发现的文件。

Python 使用标准库 AST；Java 使用已固定的 Spoon 11.2.1 工具 JAR 和 Java 17+
工具链（含 javac）。宿主可配置 `MOCKSPEC_SPOON_JAR` 和 `MOCKSPEC_JAVA`；
默认查找本机 Step 5B 的独立 `/tmp/mockspec-step5b-tools`，不自动下载。
JAR 必须符合 Step 5B 已记录身份。javac 只编译随包提供的可信 helper，不编译或执行目标源码。
helper 先用宿主 JDK 的内存语法解析检查错误，再交给 Spoon 提取事实；关闭注解处理，
不做目标源码的类型分析或代码生成。此预检用于避免 Spoon 容错解析将语法错误报告为空结果。
工具不可用、分析失败、unsupported、unresolved 和输出截断均显式记录。
语法嵌套、词法分支和局部声明引用不是数据流或可执行路径证明。

两个分析工具共用 SDK/研究查询参数：`{"source_id":"发现返回的 ID 或相对路径","symbol":"可选名称"}`。
省略 `symbol` 返回文件支持的结构/使用事实；指定简单名称或限定语法名称时精确筛选，
例如 `dependency` 不匹配 `dependency_extra`，但同名语法匹配仍可能来自不同对象。
初始 source_id 保留原片段语义；发现文件由宿主在已有大小、身份和时间限制内完整读取后交给 AST/Spoon。
解析只传文本，不导入目标模块、不运行项目脚本；Java helper 编译和解析临时文件均位于宿主临时目录。

`match_precision` 区分 `text_match`、`syntax_match`、`resolved_target` 和 `unresolved`；
本文件可定位声明不等于完整跨文件解析。结果保留原始行位置、解析状态及局限，不能证明路径可行性。
仓库模式的分析结果不提供可直接引用的 `quote`，内部读取不登记 observed；
先按返回位置调用 `inspect_source` 读取完整原文行，才能用于最终契约引用。
错误、不可解析、后端不可用、超时、取消和截断均保留在结果或调用轨迹中。

### Java 工具准备与单独验收

项目包不分发 JAR，也没有自动获取它的安装步骤。已有的受信任工具环境可通过下列变量指定；
`MOCKSPEC_JAVA` 指向 `java` 可执行文件，同目录必须有 `javac`。
必须使用 [Step 5B 工具记录](step5b/environment-lock.json) 中已留证的同一 JAR，
其 SHA256 为 `52ec4e8b874cb3f2b5a87a65ee7bf9da992a5175dcc66f539387dbb2c2053c7c`。
任意同版本 Maven JAR 不因此获得兼容保证；分析入口会核对这个固定身份。

```bash
export MOCKSPEC_SPOON_JAR=/path/to/reviewed-tools/spoon.jar
export MOCKSPEC_JAVA=/path/to/jdk/bin/java
.venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_java_analysis.py -m "not real_data" -rs
```

检查结果中的真实 Spoon 解析用例必须实际运行，不能用 skip 宣称完整验收。
工具缺失或身份不符时返回 `backend_unavailable`；基础开发回归可以继续，Java 真实解析覆盖则未完成。
历史 `docs/step5b/poc/setup_tools.py` 同时准备 Joern 并从旧项目复制工具，
不属于当前生产安装流程，不要为了运行基础测试执行整套历史实验准备命令。

### 预算与运行证据

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

## 分阶段研究回放（M1-1 / M1-4）

[任务 #7](https://github.com/Haon0731/Mockspec/issues/7) 与
[任务 #8](https://github.com/Haon0731/Mockspec/issues/8)、
[任务 #9](https://github.com/Haon0731/Mockspec/issues/9) 与
[任务 #10](https://github.com/Haon0731/Mockspec/issues/10) 的独立研究入口使用
Python 合成任务和固定脚本模型，经真实 SDK Runner 执行 B/C 变体。
在仓库根目录运行，无需模型凭据、外部数据或真实测试执行：

```bash
PYTHONPATH=src .venv/bin/python experiments/staged-inference/run.py \
  --mode synthetic_replay --variant B --output-dir /tmp/mockspec-m1-b-replay
.venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_staged_inference.py tests/test_staged_cancellation.py tests/test_staged_contrast.py tests/test_staged_format_repair.py
.venv/bin/python -m ruff check src tests scripts experiments/staged-inference
MYPYPATH=src .venv/bin/python -m mypy --follow-untyped-imports \
  experiments/staged-inference/controller.py experiments/staged-inference/run.py \
  experiments/staged-inference/scripted.py experiments/staged-inference/query.py \
  experiments/staged-inference/acceptance.py
```

研究代码类型检查显式使用本仓 `src`，避免误读环境中其他安装版本；
它仍会报告所引用生产模块的既有诊断，应与上方生产基线比较。

输出目录必须不存在。有效语义结果（SELECTED、AMBIGUOUS、INCONCLUSIVE）退出码为 0，
ERROR 为 1，命令或无法完成的产物写入错误为 2；
重复运行需指定新目录，不覆盖原证据。入口默认配置为工程回放参数，不是正式实验预算：
最多 10 次模型请求，每次最多 4096 输出 Token，总计 60000 Token，
总时限 120 秒、阶段时限 15 秒，工具结果上限配置为 16000 字符。
所有阶段共享一个用量账本、模型计数与绝对截止时间，关闭技术重试。
六次查询额度仅用于旧片段回放；仓库模式不按模型请求数、SDK turn 或工具总次数截断。
阶段切换或重建 SDK 对象不会重置预算；SDK `max_turns=1` 仅约束单阶段请求。
用 `--query-count 0|1|2|3|4|5|6|7` 选择固定查询回放（默认零次），六次轮流调用三个既有
只读工具，第七次演示拒绝，产物区分 `observed_attempts=7` 与 `budgeted_attempts=6`。
每次请求绑定疑问、候选和原始参数；畸形 JSON、缺字段、类型错误、未知工具/来源、
越界和重复请求均先计数再校验。回执保留事实/诊断，后续模型评估支持、削弱或无新增信息，
未完成判断保留 `assessment_status=incomplete`。三种工具只在 QUERY 分派。

仓库模式正常停止必须提供 `stop_explanation`，理由为 `evidence_sufficient`、
`unknowns_nonblocking` 或 `no_further_evidence`，附原文引用、明确依据、剩余未知及其影响。
无更多有效证据不能当作证据充分；阻塞未知会阻止 SELECTED。
`--repetition-threshold`（默认 4）配置连续重复且无进展的保护阈值；
新分页、新位置或新增证据使探索继续，有目的复核可保留在问题和回执评估中。
记录保存重复检测策略、触发依据、每次耗时与整次 Token 消耗；`termination.initiator`
区分模型主动结束、控制器保护和调用者取消。生产 `InferenceBudget.max_repeated_no_progress`
控制 SDK 的对应保护阈值。研究侧字段映射见[输出契约](scenario-inference.md#五步研究记录的字段映射)。

查询回执的 `semantic_category` 统一父规格分类，原 `status/error_code/limit_reason` 保留：
参数及游标错误为 invalid_request；路径/权限为 path_violation/access_denied；完整范围无结果为
not_found；解析不支持/不确定为 unparseable/unresolved；单次限制为 timeout/scope_limit/output_limit；
整次保护为 budget_exhausted/run_timeout/repeated_exploration；身份变化为
content_changed/identity_mismatch；后端或畸形结果为 backend_error/analysis_error；取消为 cancelled。
失败响应不证明行为不存在。身份变化立即终止，不发布契约。


可显式配置 `--max-model-calls`、`--max-output-tokens`、`--max-total-tokens`、
`--max-tool-output-chars`、`--timeout-seconds` 和 `--parsing-timeout-seconds`。
配置与请求前估算的局限写入 manifest；Token 估算不是远端费用硬上限。
未知用量保留 null/不完整并阻止继续请求；总时限到期后取消并回收解析进程，
不额外请求模型收尾，仅执行本地验证和留证。
这些 Token 用量来自脚本模型的固定回执，不是远端计费证据。

目录包含以下文件：

| 文件 | 内容 |
| --- | --- |
| `input_snapshot.json` | 完整可见任务的确定性 UTF-8 JSON；排序键、紧凑分隔，保持源码与普通 oracle 字符串 |
| `scenario_contract.json` | 通过正式 v0.1 校验的契约；无效输出不发布此文件 |
| `inference_record.json` | 阶段结果与可见模型响应、选择、关键行为、允许变化、错误；B 对照固定为 `disabled_by_variant/not_executed` |
| `run_manifest.json` | 宿主身份、配置、版本、预算和用量、终态，以及前三份文件实际字节的 SHA256；最后写入且不哈希自身 |

`controller.execute_staged_run` 是研究控制器及统一产物边界，`scripted.py` 提供可替换的
合成输入和 SDK 模型，`run.py` 负责显式回放或受限真实检查的命令组装。契约不增加研究状态字段；
记录与清单通过任务身份和契约文件哈希关联正式契约。失败尝试保留可得阶段、用量与错误，
无法写入时以非零退出码和 stderr 报告，不声称成功留证。

可用 `--replay-case selected|ambiguous|inconclusive|error` 分别复现四种结果，默认 selected。
每次指定不同的输出目录；error 回放在 DELIVER 注入身份篡改，验证已完成草案留证但不发布最终契约。

| 结果 | 最终契约与下游含义 |
| --- | --- |
| SELECTED | 必须有有效契约与当前候选 ID；1–3 项关键行为且无阻断性未决，可作为后续生成的输入依据 |
| AMBIGUOUS | 必须有有效契约、恰好两个候选及分歧理由；选择 ID 为 null，不能自动择一生成 |
| INCONCLUSIVE | 选择 ID 为 null，记录缺失绑定/材料及影响；完整有效契约仅供诊断，不可下发生成，无完整契约则不创建文件 |
| ERROR | 不发布最终契约；记录错误阶段、分类及已完成阶段，中间草案只供诊断 |

退出码 0 仅说明完成了有效结果交付，下游仍须读取 `result_status`。
`stop_reason` 独立记录 completed、evidence_exhausted、budget_exhausted 等停止原因；
预算耗尽、run_timeout、timeout 或 repeated_exploration 均返回保护性 INCONCLUSIVE，
已有构造仅保留为诊断，不发布最终契约。取消保存 `cancelled` 后向调用者传播。
协议违规为 output_invalid，模型运行失败为 model_error，不伪装成语义未知。
`unresolved` 保存阻断性未决，`nonblocking_unknowns` 保存不妨碍 SELECTED 的非关键未知。
可恢复的工具错误留在查询日志，模型可评估回执并继续；不会自动把工具失败当作无行为证据。

C 与 B 复用输入、控制器、输出及总预算；B 仅关闭对照并记录
`disabled_by_variant/not_executed`。C 的 SELECTED 和已有可比较候选的 AMBIGUOUS
进行一次静态 CONTRAST；提前材料或预算不足记录 `unavailable`，完成尝试但无有据替代
记录 `none_identified`。`proposed` 必须包含替代 Mock 方案、绕过行为、依据和最小区分观察。
所有状态 `execution_status=not_executed`，不代表替代已执行或通过原测试。

对照可请求一次 REVISE；`revision_log` 保留完整前后快照、准确修改字段和证据原因。
合法替代可替换正式候选，但正式候选仍最多两个；原目标内容与证据不变，
仅允许将被替换候选的绑定重指向新候选。修订后直接交付，不能重开查询。
第二次语义修订请求返回 ERROR/output_invalid，不发布契约，保留中间记录。
已要求修订但共享预算耗尽时返回 INCONCLUSIVE，未修订草案仅供诊断，不发布契约。
原目标保持在 `test_objectives`；新增观察建议进入 `verification_candidates`，
`expected_interactions.assertion_policy` 保持 `not_implied`。

最终格式修复单独记录在 `format_repair_log`，最多一次且消耗同一预算。
仅当损坏输出能建立完整语义快照且与已验证构造完全一致时才尝试；当前可修复
版本/封装字段及十进制字符串行号。引用的来源、规范化范围、原文必须完全一致；
不允许补造缺失语义、改变选择、条件、约束、关键行为或允许变化。
无法建立快照、修复篡改语义/证据、修复仍无效或无修复预算均 ERROR/output_invalid。

```bash
PYTHONPATH=src .venv/bin/python experiments/staged-inference/run.py \
  --mode synthetic_replay --variant C --replay-case cache_miss \
  --output-dir /tmp/mockspec-m1-c-cache-miss
```

固定样例另有 `cache_unspecified`（移除任务说明，保留 AMBIGUOUS）、
`legal_variation`（不锁定无关字段和构造形式，C 修订保留合法替代）和
`second_revision`（拒绝第二次修订，预期退出码 1）。相同样例可选 `--variant B`，
差异由关闭对照决定；B/C 不获额外预算。这些脚本只检验静态表示和控制流程，
不证明模型会自主发现退化或接受合法变化。

`--task-json` 仅支持显式合成回放输入，不能作为真实 Adapter 准入证明。
宿主在加载和校验前登记 run_id 并创建独立目录；可用 `--requested-case-id` 标明请求身份。
读取、JSON 解析或任务校验失败仍保存 record/manifest，有效 case_id/input_hash 为 null，
保留请求身份和拒绝原因；缺失产物哈希为 null，已有文件按实际字节哈希。
输出目录不可创建或写入失败时明确非成功退出，不保证存储故障下产物齐全。

当前完成范围是 EXTRACT → QUERY（片段模式零至六次，仓库模式按证据与资源终止）→ CONSTRUCT →
CONTRAST（仅 C）→ REVISE（可选一次）→ DELIVER → FORMAT_REPAIR（可选一次）
的 B/C 四状态回放，不是整个 M1。synthetic_replay 分支不审核真实 Adapter 输入；
live_inference 分支见下方 M1-5 说明。两者均不生成 Mock、不执行目标测试。
脚本回放通过仅证明控制器和校验边界，不证明真实模型的场景选择或语义推导质量。

## flask_308 与归档模板

已核实：冻结 snapshot 的 10 个 slot 为空；旧 run 中名为 `masked_templates` 的文件
含生成后的 mock 行，与冻结 `mask_sha256` 不一致。Java 选定案例也存在这一归档现象。
两种 Adapter 都拒绝用归档文件替代冻结输入。详见
[输入审查记录](input-audit.md)。

本项目保留选定输入的 masking 与上下文，不声称裁剪后的 Task 与旧完整 workspace
在信息量上完全等价；没有改变旧实验数据、协议或评分。当前批准两个案例，
不意味着全量 Java 59 / Python 106 已通过接入审查。

## M1-5 集成验收

[验收记录与需求映射](../experiments/staged-inference/acceptance-report.md)将工程回放、
真实模型检查和研究效果分开报告。复现固定入口清单时使用新的输出目录：

```bash
PYTHONPATH=src .venv/bin/python experiments/staged-inference/acceptance.py \
  --output-dir /tmp/mockspec-m1-acceptance
```

该命令使用合成任务和固定 SDK 模型，逐次保存输入、记录、有效契约与 manifest，
再核对身份、版本、产物字节哈希、结果状态和停止原因。预期 ERROR 也必须保留；
已有输出目录拒绝覆盖。汇总 `acceptance_report.json` 不是正式 A/B/C 实验账本。
畸形参数、未知用量、修订修复等完整覆盖还需运行上方离线 pytest 回归，
不能把这份固定清单视为整个 M1 或质量验收。

真实检查仍使用同一个 `run.py`，显式选择 `--mode live_inference --variant C`，
提供 `--snapshot`（已审核冻结 Python snapshot 根）、`--case-id flask_308` 与新输出目录。
模型来自 `--model` 或 `MOCKSPEC_MODEL`，凭据来自 `OPENAI_API_KEY`；不在日志中保存凭据。
本地配置可保存在 Git 忽略的 `.env.local` 中（权限 `0600`），运行前在同一 shell 加载：

```bash
source .env.local
```

入口不会自动读取该文件。使用兼容服务时，`OPENAI_BASE_URL` 应为 API 根地址
（例如以 `/v1` 结尾，不包含 `/chat/completions`）；服务与模型必须支持当前入口的
Responses API。仅迁移配置不代表接口已验证，也不替代下面的价格和费用预算参数。
本地配置使用 `OPENAI_AGENTS_DISABLE_TRACING=1` 关闭 SDK tracing。
该分支拒绝 `--task-json`，由现有 Python Adapter 核验输入，不能通过设置模式取得准入。
运行前明确传入 `--max-model-calls`、`--max-output-tokens`、`--max-total-tokens`、
`--timeout-seconds` 和 `--parsing-timeout-seconds`；实际配置及用量保存在 manifest。
运行前必须同时提供 `--input-cost-per-million`、`--output-cost-per-million`，
以及 `--max-cost-usd`；价格或费用预算缺失时不发起真实请求，记录为真实检查未完成。
这些参数复用生产预算门禁，不是远端计费硬上限。技术重试与按质量重试均不开启。

2026-10-23 的[历史预检](../out/staged-inference/m1-validation/live-preflight.json)
记录了当时缺少模型和凭据。2026-10-26 已用 `deepseek-flash` 完成一次真实检查，
官方服务地址为 `https://api.deepseek.com`，支持现有 Responses 入口；配置、预算、
产物及评阅见[真实检查记录](../out/staged-inference/m1-live-20261026/review.md)。
本次在 QUERY 阶段失败，保留已完成 EXTRACT 与未知用量；没有最终契约或工具调用。
#11/M1 的工程与真实检查交付条件已满足，真实推断成功、场景质量及自主工具使用
尚未验证；M2/M3 不在本次交付范围。

后续行动链排障采用新的阶段口径：M1 工程基础、M2 真实流程、M3 语义评阅、M4 方法实验。
不要与上述历史议题的 M2/M3 编号混用。QUERY 的无类型 Schema 分支导致 DeepSeek
HTTP 400，已修复；逐调用诊断位于 `inference_record.json.model_calls`，可关联
`model_responses`、`query_log` 和 `stages` 复核输入、回执与失败层。
这不会开启云端 tracing 或技术重试。定向工具协议检查已通过三个只读工具，
但最新完整运行在 EXTRACT 引用原文校验失败，五步真实验收仍未通过；没有为取得
成功结果重跑或修改语义规则。证据见[排障记录](../out/staged-inference/m2-query-diagnosis-20261026/report.md)。

## 冻结项目读取与搜索（#13 / #14）

生产 V0.2 的普通 CLI 与完整留证入口均可显式添加 `--repository-read`：

```bash
PYTHONPATH=src .venv/bin/python -m mock_spec.audited_run \
  --agent-version v0.2 --language python --snapshot "$SNAPSHOT" \
  --case-id flask_308 --model "$MOCKSPEC_MODEL" --standalone \
  --repository-read --output-dir /tmp/mockspec-repository-run
```

其余凭据、超时、Token 与费用参数沿用上方运行约定；此示例不会自动配置模型或价格。
研究入口 `experiments/staged-inference/run.py` 的 `--mode live_inference` 同样支持
`--repository-read`，仍要求显式配置该入口的共享预算和价格。默认省略此参数时保留旧片段模式。
V0.1 拒绝该选项；研究 synthetic_replay 的文件输入不能自行取得真实仓库准入。

宿主 API 使用 Adapter 的 `load_task_with_repository` 返回 `(task, repository)`，
再创建 `SourceStore(task, repository=repository)`，作为 `source_store` 传给 V0.2 推断。
同一运行的工具与验证器必须共用该 Store；不同运行创建新 Store，用完关闭 repository。
仅 `task.to_dict()` 进入初始模型输入，宿主绑定不会序列化。

`inspect_source` 默认 `operation="read"`，原三个参数仍兼容；`source_id` 可使用初始 ID、
初始逻辑路径或本次返回的动态 ID。显式 `path` 可读取本次发现路径，与 `source_id` 二选一。
扩展读取返回动态 `source_id`，契约应使用回执中的 ID、行号与精确原文；相邻读取可合并引用，
不能引用未返回的空洞。单次输出超限时返回省略回执，该片段不登记为已观察。
两个语法工具仍分析可见片段或已观察且连续的文本，不隐式读取未返回源码。

仓库模式不以 `max-turns`、`max-tool-calls` 或研究 `max-model-calls` 截断探索，
仍共享整次运行超时、Token/费用账本与每次输出上限；记录中的有效次数上限为 null。
SDK 轨迹包含原始参数、解析错误、调用 ID 与观察；研究记录保存查询回执和累计观察。
路径、身份或分页内容变化失败也保留，失败不构成“没有相关行为”的证据。
搜索操作通过同一个 `inspect_source` 请求（研究入口放在 `arguments_json` 内）：

```json
{"operation":"search","path_pattern":"src/*.py","text":"dependency","cursor":null}
```

`path_pattern` 与 `text` 至少提供一个；同时提供时取交集。路径匹配采用大小写敏感 glob，
`*` 可匹配 `/`；文本为单行字面子串，不接受用户正则或 shell。仅路径匹配的结果不含原文。
从 `matches` 取得 `logical_path` 与 `source_id` 后，可读取上下文：

```json
{"operation":"read","path":"src/dependency.py","start_line":1,"end_line":20}
```

SDK 的严格 Schema 将不适用字段填为 null；研究 JSON 可省略这些字段。
每个匹配带完整行及行号；搜索响应带 `complete`、`limit_reason`、`next_cursor`、`usage` 与 `limits`。
续页重复原查询并传回游标，是一次新的调用；不能改查询或跨运行复用游标。
`incomplete` 不代表无匹配，`next_cursor=null` 也不单独证明搜索完成。
输出不足以容纳整行时不返回半行，也不登记为可引用证据；可缩小查询或按发现路径读取。

默认单次搜索最多遍历 2000 个条目、读取 2 MiB、单文件 256 KiB、50 个匹配、2 秒，
输出使用运行配置 `max_tool_output_chars`（默认 16000 字符）。单个搜索条件最多 4096 字符，
每个绑定最多保留 128 个游标，旧游标过期后明确返回 `invalid_cursor`。宿主可用
`ReviewedRepository(..., search_limits=SearchLimits(...))` 配置搜索限制；模型不能提升上限。
被策略跳过的条目也计入遍历消耗并记录排除数；遇到禁读对象、不可解码文本或扫描上限时
保留不完整原因。遍历上限留下的未扫描范围不保证可由当前游标继续，不能作为不存在行为的证据。
最终引用仅接受初始材料或当前 Store 实际返回的完整行，检测到内容变化后整个运行证据失效。
