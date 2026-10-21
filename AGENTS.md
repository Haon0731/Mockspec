## Agent skills

### Issue tracker

使用 Haon0731/Mockspec 的 GitHub Issues 跟踪任务。
详见 `docs/agents/issue-tracker.md`。

### Triage labels

使用五个默认分诊标签。
详见 `docs/agents/triage-labels.md`。

### Domain docs

采用单一上下文布局：根目录 GLOSSARY.md 和 docs/adr/。
详见 `docs/agents/domain.md`。

## 数据接入边界

维护 Adapter、Task Schema 或输入审核记录时，先读 `README.md` 的信任边界和
`docs/input-audit.md`。仅从明确审核的冻结 snapshot 加载案例；保留原 Case ID、
masking 和普通测试上下文。缺少证据时失败，不自动回退到原始测试或归档候选。

旧 Stub-Tester 及数据只读；不复制数据集，不运行实验，不扫描或补算全量依赖哈希。
`reviewed_inputs.json` 是宿主侧准入记录，更新需审查所选源码、masking 与冻结身份，
不能通过重新计算哈希自动批准变更。Agent 仅接收 UnifiedTask 和 source_id 访问接口。

当前范围止于 Schema、只读 Adapter、输入隔离与测试；不实现 LLM Agent 或评分流程。
