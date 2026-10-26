# #17 合格案例寻找与独立准入审核

日期：2026-10-26。用户在上一轮 G2 阻塞后明确要求寻找合格审核案例；本次因此包括
有限新候选审查和必要的准入接入。此前报告保持原样，不把新结论追记为旧运行结果。

## 选择结果

选用 `de.codecentric.boot.admin.client.registration.CloudFoundryApplicationFactoryTest#should_use_application_uri@d0085edf`，
冻结 snapshot `ff6812732f1e2779e09005d721f464cebed6b587925130e6a2555a154097214a`。
独立内容审核通过，最终边界验证结果见文末。

完整初始输入保留 62 行 masked test 和 51 行 `CloudFoundryApplicationFactory` 类。
测试实例化这个类并调用继承的 `createApplication()`；没有删除正常测试上下文或截断类。
初始对象不含父类源码、接口答案、评阅问题、答案路径或预设工具顺序。

测试的最终 URL 断言无法说明 health 路径由哪个依赖方法、哪个参数产生，或缺少 health
时如何处理。子类只包含构造器和 service base URL 覆盖。允许发现的冻结父类
`DefaultApplicationFactory.java` 的 69–77、109–154、196–205 行补足了调用链：
`PathMappedEndpoints.getPath(EndpointId.of("health"))` 优先；无文本时查询 `status`；
两者均无文本则抛异常。它决定依赖调用参数、返回值约束和失败前提，不只是重复最终断言。
该结论仅属于宿主适用性审核，真实模型必须自行发现证据。

## 准入与范围

ref、runtime、injector、test 与 MUT 身份均核对；Java 双空区域通过现行 masking
验证，区域外未发现 mock 配置/验证调用。已有 runtime 为 openjdk-javac 11.0.31，
只核对控制记录，不扫描或补算 JAR 身份。

manifest 显示 repo 顶层仅有 `src` 和五项准备/依赖/构建材料。为此新增宿主审核字段
`repository_excluded_paths`，对本案例排除 `out`、`libs`、`baseline_info.txt`、`info.yml`、
`metadata.yml`。这些材料未读取。排除前缀不进入 Agent 输入；搜索、读取和分析均须遵守。
其他现有案例保持原审核记录。

排除后仅有 17 个产品 Java 源文件与 1 个 masked test。独立审核了这 18 个有限文件，
产品源码共 54,164 bytes；选中文件均与冻结 manifest 一致。没有发现答案、残留 mock、
宿主凭据或伪造空实现；这不是声称内容扫描能证明任意项目完全脱敏。
[身份及内容审查记录](selected-case-review.json) 留存范围与摘要，不复制数据集。

## 候选筛选与排除

首次元数据发现实际读取了 Python 12 个、Java 12 个 ref（共 24 个）。原委派上限为
总计 12，执行者误按每类 12 执行；发现后停止扩展。只列举这批冻结元数据，不作全数据集
搜索，不使用模型运行结果筛选案例。

- 现有 `flask_308` 复审：`_cv_app` 与 aborter/fallback 约束已在初始材料中表达，仍不选。
- 7 个 Python 候选：完整 test 与目标函数已表达主依赖行为；没有建立需要补充源码才能
  确认的实质缺口。见 [Python 筛选记录](python-screening.json)。没有自动新增准入。
- Dubbo `GenericImplFilterTest#testInvokeWith$Invoke`：本次额外接口材料未建立缺失约束。
- Neo4j `OperationsTest#shouldAcquireTxStateBeforeAllocatingSchemaId`：区域外仍有
  `inOrder(...)`，现行 mask validator 拒绝；没有为采用案例而放宽规则。
- `DefaultApplicationFactoryTest#test_all_baseUrls`：方法级输入下存在相似缺口，但
  CloudFoundry 案例可保留完整目标类，因此只准入后者，不同时扩充更多案例。

## 验收边界

本次证明案例适用性与准入接入，不证明模型已自主探索或真实 M2 已通过。
没有模型请求、目标测试、旧项目实验、gold/归档读取、依赖哈希扫描或冻结文件修改。
离线准备侧观察与真实运行 Store 分离；真实运行不得复用本次观察或审查问题作为提示。
后续应按 [PRD 第 9 节](../../../docs/workspace-exploration-prd.md)
完成配置与预算预检，再预登记一次完整运行。#17 在真实验收通过前保持开放。

## 最终验证

- G2 **通过**：独立内容审核 APPROVE；范围隔离和最终准入/入口代码审查 APPROVE。
- 完整离线回归：548 passed、14 real_data deselected，见 `pytest.txt`。
- 三个正式审核输入的只读加载与不变性测试：3 passed，见 `adapter-identity-tests.txt`。
  这些不是目标测试或模型实验；没有运行读取历史归档的测试。
- `adapter-tool-checks.json`：新冻结案例经正式 Java Adapter 加载；5 个排除项的搜索/读取
  共 10 次拒绝检查通过；允许的父类可发现并读取。准备侧 Store 随后关闭，不复用到真实运行。
- Ruff 通过；mypy 仍为 18 项既有生产诊断，研究导入的 16 项为其子集，无新增诊断。
- 首次全量回归在收集阶段发现旧测试固定两个参数 ID，记录保留于
  `pytest-initial-collection-failure.txt`。已修复为按案例身份命名；固定场景回放仅选择与其
  fixture case ID 匹配的审核项，避免把旧 Java 答案应用到新案例。没有生成新案例答案。
- 首次宿主工具探针误用了 SourceAnalysis 构造参数，在任何工具或模型请求前失败；
  已改为显式 `source_store` 并完成上述检查，错误事实保留于工具检查记录。
- 原两个准入项逐字段不变；冻结 V0.1、正式契约、validator 与正式 Schema 不变。
  新增一项审核记录和宿主排除能力是本次明确授权的新案例准入，不是原文件哈希自动批准。
- 身份与摘要见 [验证摘要](verification.json)。G3/G4 仍待真实运行；#17 保持开放。

复核命令：

```bash
.venv/bin/python -B -m pytest -q -p no:cacheprovider -m 'not real_data'
.venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_real_data.py::test_real_frozen_case_loads_without_changing_sources
.venv/bin/python -m ruff check src tests scripts experiments/staged-inference
```
