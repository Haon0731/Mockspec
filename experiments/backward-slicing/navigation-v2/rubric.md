# Navigation v2 匿名评阅冻结规则

冻结日期为 2026-11-18。本规则在读取本轮匿名候选前制定。评阅者只读取匿名槽位、下列冻结材料和获准源码，不读取本轮提示、组别映射、模型理由、工具调用或执行结果。

## 冻结依据

- Jib 开发者参考测试 SHA-256 为 `497c965cfdab5570b7c33030dcd1ee8daa81624a119d4f35e857ef356398161a`。
- Jib 遮蔽测试 SHA-256 为 `19e800bf8967238ef3b7772278e476186e509051e5021067b1f7234de0f68ed0`。
- Jib 既有七项 Stub 判据来自 `issue35/java_evaluation_criteria.json`，该文件 SHA-256 为 `d97ee85ef2df820bb307a05b2593ed8a03f9df8d01ef88682aa04ee321947374`。
- Flask 开发者参考测试 SHA-256 为 `a2d2bbc07bd45affd43cefc5255bfd71317a8331a5d4e494404502b4fcb32149`。
- Flask 遮蔽测试 SHA-256 为 `e3fcbb5818788a9956e388cdd65a1beca1a6dc8ce408ac9903709921affdd5e5`。
- Flask 既有十二项参考语义来自 `issue34/review/frozen.md`，该文件 SHA-256 为 `27e2fa1bff819e8ac1abfcaf83239214770de195aab2329c8f28785f2800ebce`。

变量名和等价 Mock 写法不影响判定。只根据槽位表达的语义评分，不以候选是否执行成功替代语义评阅。

## 两种依据必须分开

`reference_match` 表示候选与开发者参考中的交互断言相符。它回答“开发者原来是否这样写”。

`visible_goal_support` 表示可见输入、调用、普通断言和获准源码独立支持把某个交互本身作为测试要求。它回答“即使看不到开发者参考，当前可见测试目标是否足以要求该交互”。

数据流表明某个调用结果影响普通断言时，只支持为该调用配置相应 Stub。它不自动支持验证调用次数、顺序或查询是否发生。源码中出现调用也不自动产生 `visible_goal_support`。

## Jib 判据

七项 Stub 判据 J1–J7 沿用冻结文件。每项记 `matched`、`partial`、`missed` 或 `undecidable`。

开发者参考包含两项交互断言。

| ID | 参考语义 | `reference_match` 的完整条件 | 独立可见目标支持 |
|---|---|---|---|
| JI1 | `processor.createLayers()` | 正确接收者、无参数、默认 Mockito 次数一次 | 无。层结果和提取路径支持 `createLayers()` 的 Stub 返回值，不独立要求验证调用次数或调用本身。 |
| JI2 | `processor.computeEntrypoint(anyList())` | 正确接收者、列表参数、默认 Mockito 次数一次 | 无。四项入口点普通断言支持 Stub 返回值，不独立要求验证调用次数；可见测试也没有固定 JVM flags 的具体内容。 |

以下内容逐条记为 `extra_getter_verify`，不并入 JI1–JI2。

- 对 `getJavaVersion`、`getJvmFlags`、`getFrom`、`getEntrypoint`、`getEnvironment`、`getLabels`、`getVolumes`、`getExposedPorts`、`getUser`、`getWorkingDirectory`、`getCreationTime`、`getFormat` 或同义配置查询的调用验证。
- 对上述查询增加 `times(n)`、恰好一次、从不调用或顺序约束时，仍按一个独立交互项计数，并在明细中保存强度。
- 同一方法出现互相独立的不同参数、次数或分支限制时分别计数；完全重复的同一断言只计一次。

候选对 JI1 或 JI2 使用比参考更严格的参数、次数或顺序约束时，先按是否仍包含参考语义判断 `reference_match`，再把新增强度记为 `extra_constraint`。例如把 `anyList()` 收紧到具体 JVM flags 列表，匹配“列表参数”的参考部分，同时产生一项额外参数限制。

## Flask 判据

Stub 与对象声明沿用 R01–R08。交互断言采用下表，不把 Stub 配置当成交互匹配。

| ID | 参考语义 | `reference_match` 的完整条件 | `visible_goal_support` |
|---|---|---|---|
| FI1 | `_cv_app.get(None)` 恰好一次 | 正确对象、参数 `None`、次数一次，适用全部场景 | `reference_only`。可见的上下文分支支持配置其返回值，不独立要求验证内部上下文查询。 |
| FI2 | 有上下文时 aborter 恰好一次接收 `code, *args, **kwargs` | 接收者、条件、次数及全部实参转发正确 | `supported`。一次可见 Act、不同异常观察和实参输入共同约束这一委托效果。 |
| FI3 | 有上下文时回退 abort 不调用 | 正确回退对象、否定极性和分支条件 | `supported`。两个分支有不同的可见异常观察，源码分支将应用 aborter 与回退互斥。 |
| FI4 | 无上下文时回退 abort 恰好一次接收 `code, *args, **kwargs` | 接收者、条件、次数及全部实参转发正确 | `supported`。一次可见 Act、回退异常观察和实参输入共同约束这一委托效果。 |

只验证“被调用”而缺少次数、分支或完整参数转发时可记 `partial`。遗漏 `kwargs` 会失去 `no_app_context_with_args_kwargs` 和 `app_context_custom_code` 的目标信息，不能记完整匹配。

FI1 同时记入 `internal_getter_verifies`。它匹配开发者参考，但不记入独立支持交互。FI2–FI4 计入 `supported_interactions_matched`。候选新增的内部上下文查询次数、属性 getter 次数或其他与四项参考无关的精确交互，逐条记入 `extra_getter_verifies` 或 `extra_constraints`。

## 每个匿名候选的输出

每条记录必须包含以下字段。

- `blind_id` 和 `case`。
- `status`，取 `reviewed` 或 `not_delivered`。
- `stub_items`，保存 J1–J7 或 R01–R08 的逐项状态与槽位证据。
- `reference_interaction_items`，保存 JI1–JI2 或 FI1–FI4 的逐项 `reference_match`、`visible_goal_support`、槽位证据和理由。
- `reference_interactions_matched` 与 `reference_interactions_total`。
- `supported_interactions_matched` 与 `supported_interactions_total`。Jib 的分母为 0，Flask 的分母为 3。
- `reference_only_interactions_matched`。Jib 最多 2，Flask 最多 1。
- `extra_getter_verifies` 和 `extra_constraints`，均为非负整数，并提供逐项明细。
- `notes`，记录路径等价、缺少条件或无法判定之处。

未交付候选的所有计数字段为 `null`，并写明 `missing_reason`。评阅结果不得依据本轮组名解释，也不得从候选文本猜测其来源。
