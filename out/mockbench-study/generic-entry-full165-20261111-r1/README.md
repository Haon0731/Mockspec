# 通用165例运行证据清单

结果与限制见[研究报告](report.md)。原运行目录为
`out/mockbench-study/backward-slice-generic165-20261111-r1`；本目录发布其无损归档及可直接阅读的汇总。
原始本地证据未删除。两份分片按编号拼接，完整gzip SHA256为
`968083e62b8dc048fe98629390f4b54a6191f353e9390eae1196b0a3892b33ab`。
[归档清单](archive-manifest.json)列出各分片以及全部2,576个原文件的大小和SHA256。

在仓库根目录恢复到不存在的新目录（已有原运行时不要覆盖）：

```bash
set -euo pipefail
archive=out/mockbench-study/generic-entry-full165-20261111-r1
restored=out/mockbench-study/backward-slice-generic165-restored-new
expected=968083e62b8dc048fe98629390f4b54a6191f353e9390eae1196b0a3892b33ab
actual=$(cat "$archive"/run-evidence.tar.gz.part000 "$archive"/run-evidence.tar.gz.part001 | sha256sum)
test "${actual%% *}" = "$expected"
mkdir "$restored"
cat "$archive"/run-evidence.tar.gz.part000 "$archive"/run-evidence.tar.gz.part001 | tar -xz -C "$restored"
```

恢复内容包括 `results.json`、165个逐例JSON、`semantic/` 的请求／结果／日志／选择事实／逐观察WALA证据，
以及两组 `comparison-*/comparison.json`。不包含数据库、临时源码、应用字节码或依赖副本。
归档工具已流式解压每个成员并与原文件摘要比较；分片只是存储分割，不改变归档内容。

独立复核（输出目录必须为新路径）：

```bash
.venv/bin/python out/mockbench-study/generic-entry-20261111-r1/verification/scripts/full-cohort-audit.py \
  --run out/mockbench-study/backward-slice-generic165-restored-new \
  --output /tmp/mockspec-generic-audit-new
.venv/bin/python out/mockbench-study/generic-entry-20261111-r1/verification/scripts/python-taxonomy.py \
  --run out/mockbench-study/backward-slice-generic165-restored-new \
  --output /tmp/mockspec-generic-python-taxonomy-new
.venv/bin/python out/mockbench-study/generic-entry-20261111-r1/verification/scripts/final-java-taxonomy.py \
  --run-dir out/mockbench-study/backward-slice-generic165-restored-new \
  --output-dir /tmp/mockspec-generic-java-taxonomy-new --expected-total 165 --expected-java 59
```

完整运行命令（需要原审核registry及记录版本的WALA／CodeQL；新输出目录）：

```bash
PYTHONPATH=src .venv/bin/python -u experiments/backward-slicing/batch_slice.py \
  --inventory out/mockbench-study/backward-slice-full-20261109-r3/inventory.json \
  --baseline out/mockbench-study/backward-slice-full-20261109-r3/results.json \
  --semantic-gate out/mockbench-study/issue30-final-gate-20261111-r1/acceptance.json \
  --codeql /tmp/mockspec-issue24-tools/codeql-v2.27.4/codeql/codeql \
  --backend-mode generic --workers 3 --semantic-seconds 1200 \
  --output out/mockbench-study/backward-slice-generic165-new
```

本轮验收、脚本、定向结果、失败历史及回归日志位于
[generic-entry-20261111-r1](../generic-entry-20261111-r1/README.md)。独立审计只检验接入与证据一致性，
不把 `not_evaluated` 关系升级为正确切片。重新运行会受机器、缓存和预算影响，不能用旧收据替新结果背书。
