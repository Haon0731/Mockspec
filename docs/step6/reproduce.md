# Step 6 复现

从仓库根目录运行。已有 `.venv` 提供本轮使用的 openai-agents 0.24.0、openai 3.35.1、pydantic 2.14.1、pytest；源码运行需 `PYTHONPATH=src`。Java 需要 Step 5B 的固定 Spoon JAR 与含 javac 的工具链，环境变量和哈希约束见 README。不存在时工具明确失败，不自动下载。

## 回归

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src scripts
git diff --check
git diff f699554 -- src/mock_spec/inference.py src/mock_spec/prompts/scenario_v0.1.txt docs/step4 docs/step5b examples/scenarios
```

最后一条应无差异；完整基线 diff 也不应出现历史记录修改。

## 两个真实输入的离线 SDK/工具验证

以下脚本仅用宿主脚本模型驱动 SDK，实际执行程序分析。它不请求 provider、不证明自主选择、不生成真实模型 Contract。输出目录必须不存在；重新检查时使用新目录，不覆盖已有证据。

```bash
PYTHONPATH=src .venv/bin/python -B scripts/validate_step6_offline.py \
  --language python \
  --snapshot /home/haon/projects/Stub-Tester/.omx/developer-realistic-registry-max-fix23/formal-snapshots/python-benchmark-new/241ef457f8d071c23e6acd658b684b11b54f13fa955e097d6bebe35dc755f47d \
  --case-id flask_308 \
  --synthetic-contract examples/scenarios/python.synthetic.json \
  --output-dir /tmp/mockspec-step6-recheck-python

PYTHONPATH=src .venv/bin/python -B scripts/validate_step6_offline.py \
  --language java \
  --snapshot /home/haon/projects/Stub-Tester/.omx/developer-realistic-registry-max/formal-snapshots/java-bechmark-strong/5b93995c794af0152854a01ddda2fe77948b5ab828b05282b00728f7729f64d4 \
  --case-id 'com.google.cloud.tools.jib.cli.jar.JarFilesTest#testToJibContainerBuilder_explodedStandard_basicInfo@69ae66df' \
  --synthetic-contract examples/scenarios/java.synthetic.json \
  --output-dir /tmp/mockspec-step6-recheck-java
```

Adapter 仍校验冻结身份与准入记录，不得通过改 manifest 或回退旧测试来绕过失败。两个输入只读，不扫描或补算整个旧数据集。

## 后续 live 矩阵的入口（本轮未运行）

先通过环境注入 `OPENAI_API_KEY`、`MOCKSPEC_MODEL`，核实所选模型的计费配置。不要把凭据写进命令、报告或 Git。入口帮助：

```bash
PYTHONPATH=src .venv/bin/python -m mock_spec.live_v02 --help
```

四个槽位为 `python-v0.1`、`python-v0.2`、`java-v0.1`、`java-v0.2`，各一次，共用同一新的 `--experiment-ledger`。每次使用唯一 `--attempt-id`、不存在的 `--output-dir`，提供 `--agent-version`、`--language`、对应 snapshot/case-id、`--max-cost-usd`、`--input-cost-per-million` 和 `--output-cost-per-million`。价格和共同配置在首轮前固定；不能随结果改变。

账本固定 US$5 全轮目标和最多两次技术重试。只有合格技术故障可使用 `--retry-of` 指向失败尝试；未知费用、未完成运行、质量重跑和更换配置会阻止继续。`--standalone` 只用于独立诊断，不能用来绕过正式矩阵预算；任何另行批准的 live 诊断也应计入总体费用。

每个结果保留原始响应、工具轨迹、身份、用量、费率估算和失败原因。全局门禁触发后无额外收尾请求。V0.1 冻结的默认重试与输出上限差异、未知实际模型版本及估算不构成硬预算保证的限制必须保留在比较报告中。
