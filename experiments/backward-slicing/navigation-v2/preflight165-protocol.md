# MockBench-165 配对实验 · Issue #37

## 配对与冻结

以执行清单中的 dataset、case_id 和生成快照 snapshot_id 为配对键。首次体检使用 #36 r2 的全部快照。经批准对齐后，7 个 Python 案例采用 fix23 评估模板重新生成的视图，其余 158 例沿用 r2。Java 59 例、Python 106 例。第一轮每例、每组各一次，共 330 个新上下文。正式执行须另获批量调用授权。按案例交替两组的执行先后，冻结运行时模型标识、生成参数、权限、输入摘要和评估器版本。两组使用同一遮蔽测试、获准源码、代码槽位与输出预留。v2 组额外接收同一生成快照的有界事实视图。生成侧只能接收审核通过的可见输入；参考测试、变异体、评分信息及执行反馈保留在评估侧。

每次调用前校验已冻结的输入摘要和完整请求窗口。发生模型、分词器、消息模板、预算或输入漂移时重新做离线体检。严格解析有序代码块，保留空槽位和源码缩进。完整保存格式失败、超时、编译失败和原程序失败记录。两个候选冻结后再向隔离评估器交付。

## 预登记指标

| 指标 | 计算与分母 |
|---|---|
| 可执行性 | 原程序上候选通过所有指定测试行的案例数 / 165。另报交付、编译、测试失败和基础设施缺测数。 |
| 对象、Stub、验证忠实度 | 隔离提取候选和参考的三类行为项，用既有 RQ3 匹配器按组件一对一匹配，分别汇总 TP、FP、FN 并计算 2TP/(2TP+FP+FN)。保留有效候选数，另报将无效候选的参考项计作 FN 的结果；分母为零记 N/A。 |
| 过度断言率 | 打乱候选并隐藏组别。逐项判断可见测试目标是否支持接收者、实参、次数、顺序和分支限制。对重复的同一限制去重。缺乏目标支持的项 / 可判定的交互限制项，另列不可判定数、零验证案例数，并交叉列出参考匹配与目标支持。仅匹配到源码中一次调用不足以支持次数限制。 |
| 交互变异检出率 | 每对使用同一预登记变异 ID 和补丁。候选和参考均需在干净原程序上通过。每例报告有效变异中被候选检出的个数 / 可评估个数。另列普通断言已检出与移除交互验证后存活的变异，以差分统计交互验证的独立贡献。基础设施错误、编译无效变异及空分母单列。 |

两组均报告分子、分母及按语言的分布。报告共同可评估案例上的配对差值，单列仅一组成功与双组均失败的案例。变异、断言与参数行均保留所属案例；不把它们视为独立的生成样本。

评分依据见本目录的 rubric.md、上游 Stub-Tester 的 docs/rq3_metadata_extraction_and_metrics.md 和 docs/issue_2_rq4_interaction_contract_closeout_20260909.md。上游记录的镜像、变异库和运行回执须先与当前冻结案例对齐，才可计入本次准备情况。

## 首次体检的离线复核

从本仓库根目录运行。OUT 使用新的输出目录，PROMPTS 用于暂存完整请求，交付清单只保留身份、摘要及计数。PY 指向能导入本项目和 tokenizers 的 Python。TOKENIZER 与 ENCODER 指向窗口回执所列官方修订的 tokenizer.json 和聊天模板 Python 文件。控制器回执来自冻结迁移包的 `launch.sh check`，其工作目录与历史实验只读挂载分离。

```bash
D=experiments/backward-slicing/navigation-v2
SLICE=out/mockbench-study/navigation-v2-health165-slice-20261119-r2-host
R2=out/mockbench-study/navigation-v2-health165-20261119-r2
OUT=$(mktemp -d)
PROMPTS="$OUT/temporary-prompts"
export PYTHONPATH=src
"$PY" "$D/prepare_issue37_eval_manifest.py" --output-root "$OUT"
"$PY" "$D/preflight165.py" --inventory "$SLICE/inventory.json" --results "$SLICE/results.json" --r2-dir "$R2" --output "$OUT/prompt-package" --prompt-dir "$PROMPTS"
"$PY" "$D/window_preflight.py" --manifest "$OUT/prompt-package/prompt-manifest.json" --prompt-root "$PROMPTS" --tokenizer-json "$TOKENIZER" --encoding-py "$ENCODER" --output-dir "$OUT"
cp out/mockbench-study/navigation-v2-preflight165-issue37/controller-check.json "$OUT/controller-check.json"
"$PY" "$D/preflight_report.py" "$OUT"
```
