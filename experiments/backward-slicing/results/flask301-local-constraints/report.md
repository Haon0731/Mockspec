# 首例局部约束闭环：flask_301

范围：真实冻结输入的局部正常返回约束；未生成 Mock、执行目标测试、调用 LLM 或运行全量案例。
读者：判断下一轮分析实现投入的维护者。本记录不是完整分析器验收或研究效果结论。

## 发现与修复

首个真实缺口不是调用入口：r7 已将测试调用绑定到 MUT，并保留 181/184 两个返回候选到三个观察的关系。
缺少的是把固定测试参数代入局部逻辑，将不同参数行、分支和赋值结果组织成条件化约束。
新规则使用 AST 完成这个局部投影，不需要重建 CodeQL/WALA，也不以模型猜测补边。
入口仍由任务明确提供；这一结果不增加自动调用绑定能力。

| 项目 | r7 已有结果 | 本次增量 |
| --- | --- | --- |
| 输入和观察 | 同一冻结身份；三个观察 | 保留 snapshot、选中文件摘要及 criterion:0/1/2 |
| 返回候选 | 181/184 各关联三个观察 | 四个参数行独立选择 181/184/184/184 |
| 分支条件 | 180 真假方向的候选关系 | 保留原表达式、位置、行内求值方向，并处理 182 的长度条件 |
| 实参 | 缺少按参数行组织的归一化说明 | 183 的单参解包；输出零参／字符串／单个元组／单个元组，保留类型 |
| 约束 | 关系原语，没有该局部约束组合 | 将非空和两个 hasattr 原观察沿直接返回传递到对应依赖结果 |
| 成立范围 | 路径可行性未评阅 | 局部条件性结果；完整解释、运行时代理和外部效果仍未解决 |

## 一个重要限制：原观察本身很弱

实际断言仅要求返回值非空并具有 headers/status_code，不验证返回对象的身份、具体状态码或正文。
固定 setup 中的 `mock_response`、`expected_calls` 和空 verify slot 不能补出这些要求。
所以分析器不应“更聪明地”产出 status=200、body=Hello World 或 assert_called_once。
依赖实际参数是当前实现导出的事实，不是新增测试 oracle。今后若要保证这些业务属性或检测相应缺陷，需要另有授权测试要求；仅增强切片不能创造这样的依据。

人工参照及完整允许/禁止推导项见 [manual-reference.md](manual-reference.md)。它先于实现验收编写并经独立源码核对，不作为分析器输入。

## 产物和实现

- [result.json](result.json)：同一真实 snapshot 的实际输出、旧返回候选、源码摘要、成本及实现指纹。
- [verification.json](verification.json)：[独立人工参照检查器](check_reference.py)的机器核对结果；不证明运行时可行性。
- [local_constraints.py](../../python-backend-pilot/local_constraints.py)：纯 AST 受限规则；字面量参数行、局部赋值/if/return、直接返回的原观察要求。
- [run_local_constraints.py](../../python-backend-pilot/run_local_constraints.py)：复用 FrozenDataset，核对单案例与基线身份；从原观察位置恢复稳定 ID。
- [test_local_constraints.py](../../python-backend-pilot/test_local_constraints.py)：独立合成正反机制测试，非真实数据集成功数。

新分析模块是独立研究层，既有后端只输出关系、没有这项组合职责，因此未把它塞入 generic_backend 或生产 Agent。
输入选择、后端、原 v1 结果、历史运行和 WIP Java 草案均保持原样。

## 复现

在 `Mockspec-entry-repair` 工作区根运行。下面依赖已有本地 r7/r5 材料及同一只读冻结 registry；不读取 fix23 或生成后归档。
输出路径必须不存在；命令示例使用 `/tmp`，不会覆盖本目录的留证结果。

```bash
.venv/bin/python experiments/backward-slicing/python-backend-pilot/run_local_constraints.py \
  --inventory out/mockbench-study/entry-repair-20261111/full165-r5/inventory.json \
  --baseline out/mockbench-study/entry-repair-20261111/python-preserved-resume-r7/results.json \
  --case-id flask_301 \
  --product-path src/src/flask/helpers.py --product-function make_response --product-line 138 \
  --test-function-line 37 --call-line 89 \
  --output /tmp/flask301-local-constraints-replay.json
.venv/bin/python experiments/backward-slicing/results/flask301-local-constraints/check_reference.py \
  /tmp/flask301-local-constraints-replay.json
.venv/bin/python experiments/backward-slicing/python-backend-pilot/run_slice_projection.py \
  --inventory out/mockbench-study/entry-repair-20261111/full165-r5/inventory.json \
  --local-result /tmp/flask301-local-constraints-replay.json \
  --observation-receipt \
    out/mockbench-study/entry-repair-20261111/python-preserved-resume-r7/results.json \
  --output /tmp/flask301.slice.json
.venv/bin/python -m pytest -q \
  experiments/backward-slicing/python-backend-pilot/test_local_constraints.py \
  experiments/backward-slicing/python-backend-pilot/test_generic_backend.py \
  experiments/backward-slicing/test_result_contract.py
```

## 验收范围与后续

最终留证与当前实现摘要一致；人工参照检查通过，28 项新增机制测试与 41 项既有回归合计 **69 项通过**。
Ruff、编译及 diff 检查通过，独立审查批准上述受限范围。

本次仅验收四个可见参数行的局部推导。已加入确定覆盖、提前返回、未知条件、内置遮蔽、参数别名/修改、多目标调用、调用后副作用、不可达观察、生成器及非法参数绑定等反例。
已知字面量与断言冲突不得作为可选依赖规格；缺少语义依据的情况保持 blocked。

实际输出保持 `partial / conditional / conditional_only`，共同约束为空，替换授权未建立。
耗时只覆盖单案例准入、两个文件核对、局部 AST 规则及旧结果比对；不能和含完整数据库构建的 r7 耗时比较并宣称加速。

下一步先换一个未参与规则实现的真实案例检验迁移，优先定位该案例所需而当前不支持的表达式/值链；不直接扩到全量，也不继续按案例名加答案。
