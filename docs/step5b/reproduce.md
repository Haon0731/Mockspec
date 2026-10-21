# 复现说明

适用于本机已审核工作区；需要现有 `.venv`、`src/mock_spec` Adapter 和冻结准入记录。单独 checkout `a0563fa` 不包含这些当时尚未提交的工作区前置文件，不能宣称本交付可从该 commit 独立启动。没有审核输入时应停止，不能回退原仓库或重新批准哈希。

先查看 `environment-lock.json`。只在宿主准备工具时联网，分析容器禁止网络。需要 Docker daemon 权限、已存在的锁定 Ubuntu 镜像及锁定的独立 CPython 路径；若缺少该 Python 发行版，脚本失败，不自动选择其他版本。工具 JAR 只从列明旧工具路径复制，绝不执行旧仓库分析脚本。

以下是首次运行顺序，必须从项目根目录执行。工具脚本可复用下载缓存；输入目录和运行编号已存在时拒绝覆盖。重跑应使用新的 `--attempt` 编号（1–9）并传对应 `--build-attempt`，不要删除原始实验记录。

```bash
.venv/bin/python -B docs/step5b/poc/setup_tools.py
.venv/bin/python -B docs/step5b/poc/prepare.py
.venv/bin/python -B docs/step5b/poc/run_stage.py preflight
.venv/bin/python -B docs/step5b/poc/run_stage.py spoon
.venv/bin/python -B docs/step5b/poc/run_stage.py python-ast
.venv/bin/python -B docs/step5b/poc/run_stage.py java-build
.venv/bin/python -B docs/step5b/poc/run_stage.py python-build
.venv/bin/python -B docs/step5b/poc/run_stage.py python-query
.venv/bin/python -B docs/step5b/poc/run_stage.py python-export
.venv/bin/python -B docs/step5b/poc/run_stage.py python-slice
```

Java 完整构建在本次锁定版本失败是预期观测，不要串联 `&&` 后误以为后面的 Python 阶段已执行。`run_stage.py` 已做产物门禁，不能对失败 CPG 自动继续查询。它输出 JSON 诊断后返回失败退出码；`command_exit` 保存工具本身退出码，避免工具错误返回 0 被掩盖。

仅为重现默认排除测试的**不完整** Java 证据：

```bash
.venv/bin/python -B docs/step5b/poc/run_stage.py java-build --attempt 2 --diagnostic-java-subset
.venv/bin/python -B docs/step5b/poc/run_stage.py java-query --build-attempt 2 --diagnostic-java-subset
.venv/bin/python -B docs/step5b/poc/run_stage.py java-export --build-attempt 2 --diagnostic-java-subset
.venv/bin/python -B docs/step5b/poc/run_stage.py java-slice --build-attempt 2 --diagnostic-java-subset
```

这不是 Java 验收成功；`--diagnostic-java-subset` 明确标记降级复现。最终固定 Slice 命令深度 4；实际 CLI 调用未观察到输出名/方法过滤等全局参数生效的限制见报告。深度 20 的历史超时命令、早期修复前命令都在 `evidence/attempts.json`，不会悄悄替换记录。

已交付证据可离线核验（不调用 Adapter、不读取旧仓库）：

```bash
.venv/bin/python -B docs/step5b/poc/verify.py
```

`collect.py` 是**本次会话归档脚本**，固定对应报告中的尝试编号；新的独立复现若使用不同编号，需要显式调整该映射，而不是把新结果混入旧报告。它输出短文本事实、哈希和诊断，不归档完整 CPG/GraphML/输入树。`graph_facts.py` 仅读取 Joern 导出的边，不构造 CFG/DDG。

运行输出留在 `/tmp/mockspec-step5b-runs`，投影在 `/tmp/mockspec-step5b-inputs`，工具在 `/tmp/mockspec-step5b-tools`；容器在各阶段结束删除。临时文件包含已授权源码或派生物，应按本机留存政策清理，不能提交原始 CPG/输入树。工具缓存约数 GB，可能另有下载分块；不要用这些空间估计每案例持久化成本。
