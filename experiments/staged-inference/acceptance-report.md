# M1-5 验收记录（#11）

日期：2026-10-23。读者：M1 验收者与后续 M2/M3 维护者。
研究问题：同一分阶段入口能否在受限预算下交付可核验的结果及失败证据？
本记录是验收证据索引；运行方法归属 [usage](../../docs/usage.md)，规格归属
[PRD](../../docs/PRD.md)，不另建当前指南。

## 验收范围与判读

工程验证覆盖合成输入、固定 SDK 模型和受控错误注入；它验证流程与宿主约束，
不证明自然语言结论正确、普遍防退化或真实模型自主工具使用。
AC-11 仅覆盖单次失败留证及验收回放的完整性，不验收正式批量实验。
AC-09/10、FR-11 属于 M2；AC-12、FR-12 完整批量及十例/九十次实验属于 M3，均未实施。

## 需求到证据

下列测试均在 `tests/` 中；同名参数化测试包括其全部参数。

| 需求 | 固定测试/观察 | 可证明范围 |
| --- | --- | --- |
| AC-01 / FR-01 | `test_staged_inference.py::test_invalid_model_claims_leave_error_evidence_without_contract`、`test_variant_b_replay_preserves_identity_stages_and_artifact_hashes` | 身份、原文及行范围拒绝，输入快照保持；不声称能证明任意目标转述等价 |
| AC-02 / FR-02、04 | `test_four_results_through_cli`、`test_result_invariants_reject_model_forgery`、`test_staged_contrast.py::test_removing_explicit_cache_requirement_preserves_ambiguity` | 两候选无选择 ID，移除明确说明保留歧义；选择偏好仅为固定样例观察 |
| AC-03 / FR-03、10 | `test_query_limits_through_cli`、`test_failed_retries_and_renamed_tools_share_six_attempts` | 六次共享额度，第七次拒绝；失败与重试计数 |
| AC-04 / FR-03、09 | `test_invalid_query_attempt_is_counted_and_receipt_reaches_model`、`test_invalid_model_claims_leave_error_evidence_without_contract` | 畸形参数、未知来源、越界、伪造原文均可追踪 |
| AC-05 / FR-05、08 | `test_staged_contrast.py::test_one_revision_preserves_snapshots_and_legal_variations`、`test_legal_alternative_can_replace_candidate_without_changing_original_goal` | 固定关系表达及合法变化，不将全部对象字段锁死；不证明普遍接受合法替代 |
| AC-06 / FR-06、07 | `test_cache_miss_contrast_keeps_original_goal_and_marks_checks_not_implied` | 原目标与新增检查分离，表达绕过回源回填的静态风险 |
| AC-07 / FR-07 | `test_completed_contrast_can_report_no_supported_alternative`、`test_budget_before_contrast_records_unavailable_without_extra_request`、`test_b_and_c_share_input_and_only_c_runs_static_contrast` | proposed/none_identified/unavailable/disabled_by_variant 均未执行 |
| AC-08 / FR-09、10 | `test_second_semantic_revision_request_is_rejected_without_contract`、`test_staged_format_repair.py`、`test_unknown_model_usage_blocks_next_request_and_query`、`test_staged_cancellation.py` | 第二次修订、语义越界修复、未知用量及取消留证，无无限收尾请求 |
| FR-09 | `test_scenario.py::test_documented_json_schema_matches_contract_model`、`test_staged_inference.py::test_result_invariants_reject_model_forgery` | 正式 Schema 兼容，选择状态另存，不证明语义正确 |
| FR-10 / AC-11 部分 | 四状态入口、`test_model_transport_failure_is_error_not_semantic_unknown`、`test_artifact_write_failure_is_visible_and_manifest_never_claims_complete` | 失败、未决和运行错误分别留证；存储失败明确非成功 |

原断言由不可执行的只读任务快照保留；模型没有修改原任务文件的工具。
来源核验不能自动发现所有“引用正确但解读错误”的情况，该类质量需独立评阅。

## 真实检查与研究效果

真实检查仅允许现有审核 Python `flask_308`，不接受合成任务充当审核输入。
模型、输入位置及预算缺口见 `out/staged-inference/m1-validation/live-preflight.json`。
配置不具备时不发起请求，不按质量重试，不沿用旧正式实验账本。
无真实产物时，可读性评阅和场景推导质量均为未完成；不能用固定回放替代。

## 本次改动与排除项

- `acceptance.py` 与 `test_staged_acceptance.py`：同一研究入口的 15 次验收回放，
  逐项核对身份、版本、输入/产物哈希、状态及停止原因；补上后端不可用入口证据。
- `scripted.py`：修正 C/INCONCLUSIVE 固定脚本无可对照契约时仍插入 CONTRAST
  响应的问题，避免下一阶段误读；预期保持 INCONCLUSIVE/evidence_exhausted。
- `run.py`、`controller.py` 与 `test_staged_live.py`：增加显式真实检查模式，
  使用已有 Python Adapter 与预算组件；合成输入或任意 loader 不能冒充审核输入。
- 架构、用法、材料索引原处更新；旧入口和冻结材料保留其复现用途。
- 未修改生产 Agent、正式 Schema、输入准入记录、旧 Stub-Tester 或历史账本；
  未生成 Mock、执行目标测试或启动正式 A/B/C 实验。

`out/staged-inference/m1-acceptance/` 是开发过程中生成的预备证据，不作为最终
代码身份依据；最终证据使用独立目录，不覆盖任何已有产物。

## 最终判定与验证记录

**离线工程验收通过；真实模型检查未完成；研究效果尚未验证。#11 与整个 M1
保持未全部完成。** 本次没有提交关闭议题的操作。

- 完整非 `real_data` 回归：399 passed、13 deselected；无 skip。
  随后真实入口费用门禁修改另运行定向回归；日志见下方。
- Ruff 通过；`git diff --check` 通过。
- 生产 mypy 仍为 31 条/9 文件。研究检查从 16 条/4 个生产文件变为
  25 条/5 个生产文件：新增引用 Adapter 导致 `_frozen.py` 的既有 9 条进入检查，
  逐条与生产基线对应；研究文件无新增诊断。mypy 非零退出不是通过，差异已解释。
- V0.1 核心与 Prompt、V0.2 入口、正式契约模型和 JSON Schema 均逐字匹配
  `0bd0b4a778110a644785673c5219b695cfbcb5fc`；契约 Schema 同步测试通过。
- 审核 Adapter 已只读加载 `flask_308` 成功，保留原 Case ID 与冻结 snapshot 身份。
  当前进程未提供模型 ID、`OPENAI_API_KEY` 和价格/费用配置，真实请求为 0。
  可读性评阅、失败模型产物检查和自主工具使用观察均未执行，不以合成结果替代。

证据位于仓库 `out/staged-inference/`：

| 文件 | 内容 |
| --- | --- |
| [最终回放清单](../../out/staged-inference/m1-acceptance-final/acceptance_report.json) | 15 次入口结果、身份、版本和逐产物 SHA256；每次子目录保留产物 |
| [全套回归](../../out/staged-inference/m1-validation/pytest.txt) | 完整非真实数据回归 |
| [最终真实入口离线测试](../../out/staged-inference/m1-validation/pytest-live-final.txt) | 真实入口配置/准入/价格预算门禁，不调用真实模型 |
| [静态检查](../../out/staged-inference/m1-validation/ruff.txt) | Ruff 结果 |
| [类型差异](../../out/staged-inference/m1-validation/type-comparison.json) | 原基线与本次诊断的逐项对应 |
| [兼容身份](../../out/staged-inference/m1-validation/compatibility.json) | 冻结核心、接口及 Schema 的字节身份 |
| [真实检查预检](../../out/staged-inference/m1-validation/live-preflight.json) | 已核验输入、配置缺口、建议预算、零请求记录 |

最终回放包括 B/C 各自的四状态、0/6/7 查询，及原断言引用篡改、后端不可用、
跨阶段总时限边界。`backend_unavailable` 原始错误保留在回执、后续模型输入及产物；
跨阶段用受控时钟验证共享总期限，不声称每个阶段都做过真实延时实验。
固定回放含 ERROR 与 INCONCLUSIVE，均计入清单；15 次不是正式实验计划的分母。

继续真实检查所缺的是明确模型 ID、安全注入的凭据及可核实价格/费用预算。
取得配置后只运行预先约定的小规模检查，保存所有产物和失败并人工评阅，
不按质量重试。M2 生成接入、M3 十例/九十次实验仍未实施。
