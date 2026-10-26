# M2-CITATION-1 工程交付与案例门禁

日期：2026-10-26。需求：[PRD 第 9 节](../../../docs/workspace-exploration-prd.md#9-17-增量-prd可靠引用与真实探索验收)。
基线：`8ce56e3`。本记录用于区分研究工程交付和真实 M2 验收。

## 范围与实现

研究模型响应采用 v0.2 引用选择协议，只提交 source_id、闭合行范围和其负责的语义字段。
同一运行的 SourceStore 提供已可见原文，宿主生成标准阶段对象和正式 Evidence；
正式 ScenarioContract 与原逐字验证器保持不变。

EXTRACT、QUERY 停止说明、CONSTRUCT、REVISE、DELIVER 使用同一引用边界。
CONTRAST 继续使用已有证据 ID。新响应不允许旧 quote 字段；模型原始选择与宿主生成
结果分别留在 `wire_result` 和 `result`，记录版本/策略/Prompt 为研究 v0.2，
`citation_mode=source_locator_host_materialized_quote/v1`。模型调用原始响应仍按原方式留证。
内部标准阶段对象可保留原类型形状，这不是接受旧模型协议或历史产物的回退路径。

引用生成不增加文件读取，不移动坐标、不模糊匹配，也不把过去的错误响应转换为成功。
固定脚本 fixtures 显式生成新协议响应，仅供离线机制测试；不会把真实旧产物送入转换器。
历史复现仍使用原提交和原产物。生产代码、冻结 V0.1、正式 Schema 和准入记录的
保护指纹见 [baseline.json](baseline.json)。

## G2：案例适用性未通过

[独立案例核对](case-assessment.md)及[机械摘要](case-assessment.json)显示：
本次限定审查未在现有两例中建立一个合格的新增上下文验收案例。

- flask_308 的主要分支和依赖要求已由初始 docstring、实现与普通断言表达；
  新增 context/aborter 生命周期材料没有建立影响主要约束的证据缺口。
- Java JarFiles basicInfo 的 MUT 和完整断言已表达需要的返回；其他 processor
  变体不能证明被遮蔽 StandardExplodedProcessor 的具体行为。

这是有限审查结论，不是对所有潜在问题或全仓内容的穷尽证明。
不完整搜索没有被用作无结果证明。没有恢复隐藏实现、读取 raw/archive/gold、
扫描全量数据或自动批准新输入。

因此 `G2=blocked_no_suitable_reviewed_case`。本轮未加载本地 API 配置或发起模型请求，
没有新增远端用量；G3 请求预检和 G4 真实验收均未启动。
未扩展 run.py 或 Adapter 路由，也未启动 #18。

## 复现与验收范围

在仓库根目录使用新的输出目录执行现有离线入口：

```bash
PYTHONPATH=src .venv/bin/python experiments/staged-inference/run.py \
  --mode synthetic_replay --variant C --output-dir /tmp/mockspec-citation-v02-replay
.venv/bin/python -B -m pytest -q -p no:cacheprovider -m 'not real_data'
.venv/bin/python -m ruff check src tests scripts experiments/staged-inference
```

脚本回放能证明阶段、证据边界与失败留证，不能证明 deepseek-flash 自主探索或语义质量。
新协议在真实供应商上的完整运行仍未验证；历史 QUERY HTTP 200 只适用于当时的请求 Schema。
最终检查结果和门禁状态在本记录末尾列出。

## 剩余工作

#17 保持开放：AC-17-09 的合格案例条件尚不满足，AC-17-10 至 AC-17-12 的完整真实验收待执行。
需要准备侧给出有限候选并审核源码、masking、冻结身份与范围，建立“初始材料缺少实质事实，
允许冻结材料存在相关补充”的依据。新增准入不在本次批准范围，不能靠放宽入口、
强制查询或重复采样绕过该门禁。合格案例及工程门禁通过后，再独立预登记真实运行。

## 最终工程验证与状态

- G0/G1：研究工程交付完成。`pytest-final.txt`：533 passed、13 real_data deselected；
  `ruff.txt`：规定范围 src/tests/scripts/研究目录检查通过。正式 Schema 同步由原测试覆盖。
- 独立 C 变体 CLI 回放输出 SELECTED，产物位于 `c-replay/`。输入、记录、契约哈希匹配
  manifest；正式契约经原 validator 再验证通过；阶段与模型调用序号关联已核对。
- `verification.json`：六个保护文件指纹保持基线一致，生产 src 未修改。
- `mypy-production.txt` 保留 18 项既有诊断；`mypy-research.txt` 的 16 项均属于前者，
  无研究文件新增诊断。不宣称全项目类型检查零错误。
- 独立代码审查 APPROVE，架构复审 CLEAR，摘要与审查代码身份见 `reviews.json`。
  已修正审查发现的 DELIVER 解析失败误触格式修复路径，并补齐实际 wire 类型严格性、
  失败字段位置与调用关联验证。
- G2：blocked_no_suitable_reviewed_case；G3/G4：未启动。AC-17-01 至 AC-17-08 的
  工程要求已验证；案例及真实验收要求仍未满足。#17 保持开放。

保留旧标准阶段对象是为了复用宿主语义校验；新模型响应只接受 v0.2 选择协议。
此次没有新入口、依赖或通用框架，没有修改生产协议或冻结材料。
