# Step 2 输入审查记录

审查日期：2026-10-20。来源主机：home-ts；旧仓库：
`/home/haon/projects/Stub-Tester`。只读审查，无实验、注入或依赖扫描。

## 冻结输入身份

| 项目 | Java | Python |
| --- | --- | --- |
| Dataset | `java-bechmark-strong` | `python-benchmark-new` |
| Case ID | `com.google.cloud.tools.jib.cli.jar.JarFilesTest#testToJibContainerBuilder_explodedStandard_basicInfo@69ae66df` | `flask_308` |
| Registry（相对旧仓库） | `.omx/developer-realistic-registry-max` | `.omx/developer-realistic-registry-max-fix23` |
| Snapshot ID | `5b93995c794af0152854a01ddda2fe77948b5ab828b05282b00728f7729f64d4` | `241ef457f8d071c23e6acd658b684b11b54f13fa955e097d6bebe35dc755f47d` |
| Mask SHA256（已有 ref） | `19e800bf8967238ef3b7772278e476186e509051e5021067b1f7234de0f68ed0` | `e3fcbb5818788a9956e388cdd65a1beca1a6dc8ce408ac9903709921affdd5e5` |
| Runtime lock | `openjdk-javac 11.0.31` | `cpython 3.10` |

完整根目录为 `<旧仓库>/<registry>/formal-snapshots/<dataset>/<snapshot_id>/`。
以上身份已与对应 run 的 `workspace_snapshot_refs` 核对。

两个 ref 均为 `state=frozen`、`protocol_version=developer_realistic_workspace_v1`。
选择的 run 不是 fair-v1 primary。具体文件白名单见包内 `reviewed_inputs.json`；
其中已有源码和 injector 哈希来自冻结 tree manifest，并对实际选中文件做有界校验。
没有重新计算任何 JAR、site-packages 或依赖树哈希。

## Java

目标：`tree/repo/src/test/java/com/google/cloud/tools/jib/cli/jar/JarFilesTest.java`。
完整保留 104 行：`MOCK-STUB` 和 `VERIFY` 两区域为空，普通 setup、目标调用和断言保留。

MUT 为同一 tree 中的 `repo/src/main/java/com/google/cloud/tools/jib/cli/jar/JarFiles.java`。
额外允许 `StandardExplodedProcessor.java`：它的三个方法在冻结输入中已经是
`UnsupportedOperationException` 方法体。保持这个既有可见性，不恢复原始实现；
接口只摘录源码中的方法声明，不提供 mock 响应值。

Java 使用 `java-dual-region-v1` injector 约定，ref 的 `slot_contract_digest` 为 null；
其 `apply_regions.py` 文件身份纳入只读检查，但不执行脚本。

## Python flask_308

目标：`tree/repo/tests/test_308abort.py`，完整保留 126 行。
共有六个 `mock_stub_001..006` 和四个 `verify_001..004` slot，内容均只有 `pass`。
原参数化数据、普通异常断言和非 slot 支持语句原位保留。

`apply_regions.py` 的 `MOCK_EXPECTED` 和 `VERIFY_EXPECTED` 明确给出这些 slot ID；
冻结 ref 的 slot contract digest 为
`81dc375315690c163892b9ad71dc0f011b92cd67815e179e115d52f97ad75b2b`。
准备代码 `scripts/test_support_metadata/developer_realistic/preparation_adapters.py`
以 `python-slot-v1` injector 和 slots 生成 contract；当前准备路径要求
FORMAL_V3 artifact manifest。新项目固定审核后的 ref/injector 身份与 slot 集合，
不读取 private semantic IR，也不重新生成 gold 或 masking。

MUT：同一 tree 的 `repo/src/src/flask/helpers.py` 第 265–285 行。
支持上下文：该文件第 1–24 行导入。依赖动态 API 的完整签名尚未核实；
本阶段不构造推测性接口条目。

## 为什么归档 masked_templates 不可用于加载

归档 run 根：

```text
/home/haon/data/experiment-registry/rq234-repeat/runs/
  java-bechmark-strong/agent_opencode__developer_realistic_workspace_v1/
    deepseek-v4-pro/budget_300s/run_3/
  python-benchmark-new/agent_claudecode__developer_realistic_workspace_v1/
    deepseek-v4-pro/budget_300s/run_1_full_106/
```

Python 对照文件：
`raw/oneshot/feedback_repo_claudecode__developer_realistic_workspace_v1/masked_templates/flask_308.py`。
第 78 行包含 `return_value`，第 85/88 行包含 `side_effect`，第 96–107 行包含调用验证。
其文件身份不匹配上述冻结 mask。Java 对照文件位于
`raw/masked_templates/feedback_repo_opencode__developer_realistic_workspace_v1/`，
文件名为
`com.google.cloud.tools.jib.cli.jar.JarFilesTest_testToJibContainerBuilder_explodedStandard_basicInfo_69ae66df_e89a13fb5a.java`；
其生成区域非空，同样不匹配冻结 mask。

代码链路解释了这种差异：

- `developer_realistic/formal.py` 的 `run_formal_snapshot_generation()` 先从冻结 tree
  clone Agent workspace，再调用 Agent。
- `python/feedback_repo_workflow.py` 在生成完成后从 `generated.agent_workspace` 的目标文件
  读取 `masked_text`，再写入归档 `masked_template`。
- Java `candidate_runner.py` 使用相同的生成后归档方式。

因此这些归档文件和运行结束后的 `raw/workspaces/.../agent/` 都不是不可变的初始输入。
本记录把它们认定为不适合新任务输入的生成后产物；没有据此宣称历史 Agent 初始输入
泄漏 reference，也没有对其每一行与 gold 做归因。

## 可证明范围

本节记录时验证的是两个已审查、固定身份的输入及少量允许源码，不是全量数据集的自动脱敏。
结构检查不证明任意文本没有泄漏；所有新案例仍须做源内容与协议审查。
单测验证私有控制字段不会进入序列化结果、输入变化或 masking 未核验时拒绝加载。
真实集成测试还比较所读取文件的内容、大小与 mtime，确认加载前后不变。

## #13：初始路径扩展读取（2026-10-26，历史增量）

`load_task` 保留历史片段加载；`load_task_with_repository` 在同一身份及 masking 审核后，
另行返回宿主 `ReviewedRepository`。它仅允许初始逻辑路径所对应的完整已审核文件，
未修改 `reviewed_inputs.json`，未将这次功能开发当作新案例或全仓源码准入。
例如 Python 的 `helpers.py` 可按行读取原 MUT 与导入片段以外的内容；其他路径仍拒绝。

宿主通过目录描述符及 `O_NOFOLLOW` 逐层打开，读取前确认普通文件类型与大小边界，
并以已有审核 SHA256 验证实际读取的完整选定文件。读取摘要用于核对和留证，不能自动批准变化。
每次返回的片段在独立运行 Store 登记 snapshot、逻辑路径、source_id、行范围、原文、
摘要与响应关联；输出被省略的片段不登记。相邻观察可合并引用，未观察空洞、错误原文或身份拒绝。
内容变化作为工具失败留证，保留之前获得的观察，不将新旧内容混合。

此增量实现初始路径的读取闭环，当前搜索范围见下方 #14。
验证使用合成冻结树与既有安全回归，不运行旧项目实验，也不改写冻结原件。


## #14：冻结项目内有界搜索（2026-10-26）

仓库模式现在允许发现同一冻结快照 `tree/repo` 内的普通源码、测试、文档和配置。
Adapter 仍先核对既有控制身份、选定文件及 masking，并确认逻辑路径与 `repo/` 映射一致；
控制文件、injector 和其他快照不在绑定根内。此能力采用 #12 已确认的准备侧脱敏前提，
不将历史少量来源审核改称全仓实测证明，不修改 `reviewed_inputs.json` 或补算全仓哈希。

搜索遍历前排除控制/归档/评价目录、宿主凭据和依赖环境；符号链接、非普通文件不读取。
普通隐藏配置并非一律禁读。实际读取的新文件建立运行期内容与文件身份记录，后续读取或
搜索发现变化后使运行证据失效，最终契约不得发布；这不是新增输入的审核批准。
分页游标绑定运行、仓库身份、策略与原查询，不能跨运行导入观察。

仅返回给模型的完整匹配行进入观察记录；路径结果、内部扫描文本及输出裁掉的行不能引用。
单次资源上限、实际消耗、完整性及续页信息随回执留证。验证使用合成冻结树和脚本模型，
不读取旧项目全量数据、不执行目标测试，也不将工程回放当作真实自主探索成功。

## #17：CloudFoundry 新案例准入（2026-10-26）

用户在原两例不满足适用性条件后明确要求寻找合格审核案例。本次新增一个准入项，
原两项身份与范围不变。完整筛选、独立评阅与验证见
[案例准入记录](../out/staged-inference/issue17-case-search-20261026/report.md)。

- Dataset：`java-bechmark-strong`；registry：`.omx/developer-realistic-registry-max`。
- Case ID：`de.codecentric.boot.admin.client.registration.CloudFoundryApplicationFactoryTest#should_use_application_uri@d0085edf`。
- Snapshot：`ff6812732f1e2779e09005d721f464cebed6b587925130e6a2555a154097214a`，状态 frozen，协议 `developer_realistic_workspace_v1`。
- Mask SHA256：`54a6abf96890af556281a72ec3546e21e2eea4efd80f89cf4e891ed32b5dc0f4`。
- Runtime：openjdk-javac 11.0.31；只核对已有控制身份，不重新扫描依赖。

初始输入为完整 62 行 `CloudFoundryApplicationFactoryTest.java` 与完整 51 行
`CloudFoundryApplicationFactory.java`；Java 双区域均为空，普通 setup、调用、断言保留，
区域外无残留 mock 行为。测试实例化该类、调用继承方法，选择完整目标类并非人为裁剪上下文。
不预置父类源码或接口答案；父类作为工具可发现的普通产品源码。

本案例的宿主审核字段 `repository_excluded_paths` 明确排除 `out`、`libs`、
`baseline_info.txt`、`info.yml`、`metadata.yml`，覆盖 repo 下除 src 外全部顶层对象。
这些排除材料未读取。余下 17 个产品 Java 源文件（54,164 bytes）及 1 个 masked test
已做独立内容与选定文件身份核对；未发现答案材料、残留 mock、宿主凭据或伪造空实现。
这是有限案例审核，不是以重新计算哈希自动批准输入，也不是全量脱敏证明。

搜索、直接读取、分析和分页均执行相同排除约束。宿主评阅问题、答案位置、排除配置和
准备侧工具观察不进入模型初始输入或真实运行 Store。准入及适用性通过不等于真实 M2 通过。
