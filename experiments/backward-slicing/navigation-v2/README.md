# 纯事实导航 v2

这是后向切片的研究入口。切片描述从遮蔽测试能定位到的调用、守卫和观察。如何使用 Mock、给哪些依赖配置返回值以及如何验证交互，由 Coding Agent 阅读源码后决定。

在仓库根目录运行 `.venv/bin/python -m pytest -q tests/test_navigation_facts.py tests/test_tagged_slots.py` 可用合成输入验证生产投影与槽位解析。

## MockBench-165 对照准备

#37 的执行清单由 158 例 r2 快照和 7 例 fix23 快照组成。指标和分母见[配对实验协议](preflight165-protocol.md)，组合窗口、运行参数和资源估算见 [Issue #37 复检记录](https://github.com/Haon0731/Mockspec/issues/37#issuecomment-7029608578)。

下文的真实案例命令需先恢复获准的冻结 registry，以及 #34、#35 和 navigation-v2 研究归档中的输入清单、源码身份和运行回执。各文件按原相对路径放回对应研究目录；#37 的完整逐例清单在仓库根目录下的 `out/mockbench-study/navigation-v2-preflight165-issue37-fix23/`。恢复后可用 `study.py facts --case flask_308` 或 `study.py facts --case jib` 查看导航 JSON。

## v2 契约

版本标识为 `mock-spec.navigation-facts/v2`。Java 与 Python 均以 `project_navigation_facts(case_result, visible_sources)` 接受已审核、带来源坐标的冻结分析及可见源码。输出字段在两种语言中含义相同。

| 字段 | 内容 |
|---|---|
| `identity`、`entry_binding` | 案例、快照与目标身份，后端报告的入口绑定状态及证据。入口无法定位时保留 `unresolved`。 |
| `observations` | 原测试可见观察的 ID、类型、来源和坐标。 |
| `calls` | 调用的结构角色、源码位置或候选位置、解析精度、守卫和分通道的 `effects`。 |
| `guards`、`relations` | 已恢复的控制依赖和值流等关系，保留模态和观察 ID。同一案例的两条观察不会因共享某个调用而自动共享全部关系。 |
| `endpoints`、`evidence_sources` | 关系引用的端点和来源字典，包括精度与分析假设。 |
| `analysis_boundaries`、`projection` | 未覆盖的效应和区域、输入身份摘要、关系和调用数量。 |

产品端点只有方法符号时保留符号；只到达某个函数区间时，关系停留在该区间。Python 可根据已绑定的产品函数恢复具体调用位置，动态接收者的目标可能仍然未知。所有语义判断限定于输入提供的分析范围。[转换实现](../../../src/mock_spec/navigation_facts.py)

## 读一条调用记录

`calls[].structural_role` 说明调用在程序中的位置。测试调入 MUT 记为 `entry_call`。已定位的外部依赖边界记为 `outbound_call`。产品内部已定位的调用和目标尚未解析的调用分别记为 `internal_call`、`unresolved_call`。每条调用保留定位依据。Java 中一个词法匹配对应多行时列出候选位置；Python 中动态接收者的属性名可以已知，目标实现仍然未知。

`effects` 将返回值和异常分别记录。`reaches_assertion` 为 true 表示分析找到通向可见观察的关系，关系的模态给出是否只在某些路径上可能发生。false 需要肯定的反证，例如 Python 调用作为独立语句丢弃返回值。null 表示当前证据不足。正常返回、控制依赖及状态流分别保留。`guards` 记录有证据的守卫，空列表只表示这个位置未附上守卫证据。

例如 Flask 的 `ctx.app.aborter(code, *args, **kwargs)` 在 `(ctx := _cv_app.get(None)) is not None` 分支中执行。调用丢弃返回值，异常有可能到达测试的异常观察，动态接收者的具体实现未知。可以在Flask 事实 JSON `inputs/flask_308/facts.json`和Jib 事实 JSON `inputs/jib/facts.json`中查看完整关系和来源。

## 七例 Base / v2 主试验

`study.py prepare` 按七个真实 Case ID 生成提示。`study.py run` 首先运行 Jib 完整 v2 三次，交付三份完整候选后运行其余 39 个上下文。Base 与 v2 共用每例的输入、槽位标签和执行工具。模型只返回标签间的源码，运行器按原字段组装 `slots` 和空的 `rationale`。保留每轮实际模型输出及逐槽位内容，可区分标签解析、源码缩进、原程序执行和变异检出的问题。[七例结果](tagged/README.md) · [运行协议](tagged/protocol.md) · 七例清单 `inputs/manifest.json`

投影器和标签接口已迁入生产包，研究入口直接复用生产行为。[生产调用说明](../../../docs/usage.md#v2-事实投影和源码槽位)

复核 #35 时使用冻结的 `inputs/` 和 `tagged/run/`。用当前生产接口准备新输入时，Langflow 的 `get_settings_service` 会标为“同项目绝对导入、目标源码不可见”，取代当时的外部调用分类。该修正保留未知边界。下面的命令使用新目录和当前实现；只有运行生成命令会调用配置的研究模型。

```bash
D=experiments/backward-slicing/navigation-v2
R=$(mktemp -d)
.venv/bin/python "$D/study.py" prepare --inputs "$R/inputs"
set -a
source .env.local
set +a
.venv/bin/python "$D/study.py" run --inputs "$R/inputs" --run-dir "$R/run"
.venv/bin/python "$D/tagged_evaluate.py" --run-root "$R/run" --output "$R/evaluation.json"
```

## 历史两例 JSON 对照

历史两例提示与 manifest 保存在 `legacy-json-inputs/manifest.json`，可从这里回放当时的交付约定。每例使用三种输入。v1 是既有格式，ablation 在 v1 中只删一个候选标签，v2 使用事实地图。每种输入重复三次。三组均允许模型读取同样的源码。生成完成后冻结候选，再做匿名槽位评阅和容器执行。逐轮结果同时保留未交付、参考交互匹配、额外 getter 验证、原程序执行和变异结果。[生成协议](protocol.md) · [盲评准则](rubric.md)

重建提示和复跑使用新目录。run 使用 .env.local 中配置的研究模型，调用会产生费用。

```bash
D=experiments/backward-slicing/navigation-v2
R=$(mktemp -d)
.venv/bin/python "$D/study.py" legacy-prepare --inputs "$R/inputs"
set -a
source .env.local
set +a
.venv/bin/python "$D/study.py" legacy-run --inputs "$R/inputs" --run-dir "$R/run"
.venv/bin/python "$D/study.py" legacy-blind --run-dir "$R/run"
.venv/bin/python "$D/evaluate.py" --run-root "$R/run" --output "$R/evaluation.json"
```

另有两项补充观察。第一项从未交付回复中，用标准 JSON 解码器独立读取完整的 slots 子对象，保留每个字符；这部分只做匿名静态评阅。第二项对 Jib 三组各新增一次原样代码块交付，统一允许空槽位，取消 JSON 信封和理由要求。用 `transport.py run` 和 `transport.py evaluate` 分别生成与执行。补测单独计数。[补测协议](transport-protocol.md)

## 盲评结果

| 案例与条件 | 正式交付 | 每轮额外 getter 验证数 | 每轮匹配的参考交互数 |
|---|---:|---|---|
| Jib · v1 | 2/3 | 4、0、缺测 | 2、2、缺测（每轮共 2 项） |
| Jib · 只删标签 | 3/3 | 0、0、0 | 0、1、2 |
| Jib · v2 | 0/3 | 缺测、缺测、缺测 | 缺测、缺测、缺测 |
| Flask · v1 | 3/3 | 0、0、0 | 4、3、4（每轮共 4 项） |
| Flask · 只删标签 | 2/3 | 0、缺测、0 | 4、缺测、3 |
| Flask · v2 | 3/3 | 0、0、0 | 4、4、4 |

Jib v1 第一轮在两个参考交互以外，逐项验证了 `getJavaVersion()`、`getJvmFlags()`、`getFrom()` 和 `getEntrypoint()`。只删标签的三轮中额外 getter 验证均消失，参考中的 `createLayers()` 和 `computeEntrypoint(...)` 也有不同程度的遗漏。v2 三个主试验回复都未交付规定信封。独立读取其中完整的 slots 子对象后，按轮观察到额外 getter 数 2、0、0，参考交互命中数 2、0、0。后两轮同时漏掉参考中的两项验证。该静态诊断保留了所有原始转义，未将这些片段回填为主试验候选。正式盲评 `review.json` · 失败回复诊断 `diagnostic-review.json`

Flask 的三份 v2 候选均完整验证了应用 aborter 的参数转发、有上下文且 aborter 抛错时回退未被调用，以及无上下文时回退收到的参数。三份也都保留 `_cv_app.get(None)` 恰好一次的检查。最后这一项匹配参考，但可见异常目标本身无法约束内部查找次数，因此单独登记。三种条件中已交付的八份 Flask 候选全部保留三项分支交互，观察不到 v2 独占的改善。

Jib 原样交付补测的三个候选均保留空的 VERIFY 区域。空槽位被明确允许、同时取消理由要求，两处改变可能共同影响结果。三组同方向变化，无法归因于 v2 的导航结构。补测盲评 `transport/review.json`

## 实际执行

下表逐轮报告原程序通过的参数行数和合格候选检出的变异数。Jib 每轮 1 行，Flask 每轮 5 行。两例各有三个固定变异。只有原程序全部通过才计算变异检出数。“缺”是生成失败，“—”是原程序未全通过。

| 案例与条件 | 原程序通过行数（第 1、2、3 轮） | 检出变异数（第 1、2、3 轮） |
|---|---|---|
| Jib · v1 | 1、1、缺 | 3、3、缺 |
| Jib · 只删标签 | 1、0（编译失败）、1 | 2、—、3 |
| Jib · v2 | 缺、缺、缺 | 缺、缺、缺 |
| Flask · v1 | 2、5、5 | —、2、3 |
| Flask · 只删标签 | 5、缺、5 | 3、缺、2 |
| Flask · v2 | 5、5、5 | 3、3、3 |

Jib 的原样代码块补测中，三组各一份，均通过原程序，均检出遗漏 createLayers 和遗漏 computeEntrypoint，均漏检重复 createLayers。Flask 主试验中，v2 每一轮都验证上下文查询次数，也都检出了重复查询变异。这体现了“能检出某个变异”和“可见测试目标要求这个次数”两个评估维度。主试验逐次汇总 `summary.json` · 主试验执行回执 `evaluation.json` · 补测执行回执 `transport/evaluation.json`
