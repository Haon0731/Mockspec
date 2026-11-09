# 目标导向值/效果组合：本轮方法依据

日期：2026-11-04。本文为 `goal-pilot` 的外部方法依据，不实现分析器，也不改变已冻结的 `path-pilot` 输入、候选或结论。

## 结论

本轮最小原型应从**测试的可观察目标**开始，按需组合少量方法路径摘要，输出“该场景要使哪些 Mock 调用正常返回、返回哪个符号值、哪些调用不能发生”的关系包。它不是完整符号执行，也不是把每个调用的同名表达式视为同一值。

对 CloudFoundry，目标不是事先写入 `actuator/health`，而是从普通断言抽取：

```text
goal.health_url = "http://application/Uppercase/actuator/health"
```

再从可见实现反向展开 `app.getHealthUrl()` 的返回表达式。只有当可见路径和受版本约束的 URI 语义共同导出 `goal.health_url` 时，才把 `PathMappedEndpoints.getPath(EndpointId.of("health"))` 的响应约束写为 `"actuator/health"`。若 URI 操作或动态分派未获支持，结果为 `unknown`，不能把 `health`、`/health` 或字符串拼接猜成等价值。

## 本轮可实施的最小算法

输入为：审核后的 masked test、同一审核快照内由切片选出的可见方法、调用解析候选和外部边界。输出为多个候选 `GoalScenario`，每个都带证据位置与未知项。

1. **提取目标，不读取或构造参考 Mock。** 识别支持的普通断言形状，例如 `assertEquals(constant, app.getHealthUrl())`。创建目标符号 `G_health` 与等式 `G_health = constant`，记录 test 的 `source_id` 和行号。未支持的 matcher 保留为未解释目标，不用字符串搜索代替语义。
2. **按需反向展开接收者调用。** 从 `getHealthUrl` 的可见 `return` 表达式开始，仅展开能到达该返回的单一可见候选；每一次调用都创建新调用实例：`call_id = <caller-context, source-position, ordinal>`。其正常结果为新符号 `r_call_id`，异常出口也独立记录。调用文本相同不合并，除非有已验证的纯函数/别名证据。
3. **保持路径为析取，而不是合并 guard。** 对每个返回路径保存 `(guards, normal/raise events, expression)`。例如 health 路径与 status fallback 是两个 scenario；前者不要求 status lookup 未调用，除非从所选路径能证明它不可达。loop、try/catch、未决动态分派、heap 写入和预算截断加入 `unknowns`，不转成否定事实。
4. **组合支持的表达式。** 第一轮只解释字符串常量、局部变量替换、受版本锁定的 `UriComponentsBuilder` 构造链、以及被标记为 opaque 的 Mock 返回符号。把方法参数实际代入被调摘要，保留调用/返回连接；不要把整份方法摘要当作已经跨过程求值。
5. **局部一致性，不默认求解。** 使用常量相等/不等、nullness、布尔极性、简单字符串片段和事件后继做传播。若精确支持的规则得出 `".../health" != G_health`，将该已组合路径标记 `contradiction`；若 URI、异常或分派有未知，标记 `unknown`, 而不是 `contradiction`。本轮不把 SAT 当作可执行 Mock 的证明。
6. **输出紧凑证据包供 Agent 选择。** 一个候选包含：目标等式、guard、每个 `call_id` 的 receiver/实参/结果符号/正常或异常、值构造关系、不可达的后继事件、代码位置和 unknown。Agent 可选择多个不冲突候选中的一个，并把 Stub 和 Mock Assertion 映射回 `call_id`；检查器只拒绝该候选内部的已证明冲突。

这是一种受限的、目标查询式后向组合。它不会证明真实依赖的业务语义，也不会替代Mock可替换性分析。

## 不能偷换的语义

| 事实 | 允许结论 | 不允许结论 |
| --- | --- | --- |
| 目标与受支持构造链发生常量冲突 | 当前路径不满足当前目标 | 整个程序或所有Mock场景不可行 |
| 外部调用返回符号 `r_c1` | 该调用一次正常返回时的未知值 | 相同文本的下一次调用也返回 `r_c1` |
| 路径在异常事件停止 | 该场景中后继事件不发生 | 该依赖在所有场景都必须抛异常 |
| `unknown` URI/dispatch/heap | 不能自动给该关系下结论 | 不存在该调用、值或状态影响 |
| 一份运行见证通过 | 固定测试和该配置下可执行 | Mock行为是唯一真实实现或所有合法实现都应满足 |

## 为什么这比逐方法摘要更接近问题

Demand-driven compositional symbolic execution把函数路径表示为输入前置条件和输出后置条件，并按目标按需扩展不完整摘要；这个“按目标组合”原则正对应本轮缺失的步骤。但该论文依赖一阶公式和SMT来判定可行性；本原型只借用摘要的输入/输出连接和延迟展开，不继承其完备性或求解保证。[Anand, Godefroid, Tillmann, 2008](https://www.microsoft.com/en-us/research/wp-content/uploads/2008/01/fulltext.pdf)

对未知Mock边界，Microsoft 的参数化单测工作明确为每一次 Mock 方法调用创建新符号，并用计数区分多次调用；这直接支持 `call_id`/fresh result，而不是按方法名缓存一个“返回值”。[Unit Tests Reloaded，Microsoft Research Technical Report MSR-TR-2005-153](https://www.microsoft.com/en-us/research/wp-content/uploads/2006/07/UnitTestsReloadedMSR-TR-05-153.pdf)；其后续 mock-behavior 工作也说明可以只从测试中 Mock 的使用推导行为，而不要求真实被替代实现存在。[Mock-object generation with behavior，ASE 2006](https://www.microsoft.com/en-us/research/publication/mock-object-generation-with-behavior/)

## URI 摘要的可信边界

该案例的冻结运行时列出 Spring Framework `5.1.2.RELEASE` 和 Spring Boot `2.1.0.RELEASE`，所以 URI 规则必须绑定这个版本，不能引用当前 Spring 文档替代。

Spring Framework `v5.1.2.RELEASE` 的源代码说明：

- `fromHttpUrl` 解析 HTTP URL 并将已解析路径放入 builder；无 host 的有 scheme URL 会抛 `IllegalArgumentException`。[UriComponentsBuilder.java:238-275](https://raw.githubusercontent.com/spring-projects/spring-framework/v5.1.2.RELEASE/spring-web/src/main/java/org/springframework/web/util/UriComponentsBuilder.java)
- `path(String)` 是**追加**已有路径，不是覆盖；builder 中 `toUriString()` 会走 `encode().build().toUriString()`。[同文件:548-570、410-419](https://raw.githubusercontent.com/spring-projects/spring-framework/v5.1.2.RELEASE/spring-web/src/main/java/org/springframework/web/util/UriComponentsBuilder.java)
- `UriComponents.toUriString()` 根据当前组件做简单连接；但编码、模板展开与规范化调用顺序会改变结果。[UriComponents.java:181-204](https://raw.githubusercontent.com/spring-projects/spring-framework/v5.1.2.RELEASE/spring-web/src/main/java/org/springframework/web/util/UriComponents.java)

因此可实现的第一版库摘要应只覆盖同一版本、无模板变量、无 query/fragment、无编码/规范化调用、且 `fromHttpUrl(base).path(p1)...path(pn).toUriString()` 全部输入为常量或Mock符号的链。它应保留“path追加”和连续双斜杠折叠的版本化规则；对任何其他 builder API、模板、保留字符、相对URL或未知中间值返回unknown。实际库源码展示路径构建阶段会折叠连续 `/`，这也是不能用裸字符串拼接代替库语义的原因。[UriComponentsBuilder.java:930-947](https://raw.githubusercontent.com/spring-projects/spring-framework/v5.1.2.RELEASE/spring-web/src/main/java/org/springframework/web/util/UriComponentsBuilder.java)

## 本轮验收点

原型应满足以下几点，才值得重新做小样本生成：

1. 从 CloudFoundry 的原普通断言和可见实现自动产生 health 直达与 status fallback 两个候选，不依赖写死的 endpoint 字符串。
2. 对返回 `health` 的 health 直达候选，在受支持 URI 子集内报告与目标常量的冲突；对 `actuator/health` 不报告冲突。
3. status fallback 仅在 health 非文本、status 为文本的条件下产生，且两个 `getPath` 调用拥有不同 `call_id`。
4. 让未知 URI/动态接收者保留unknown，不用“没有冲突”写成“可行”。
5. 输出大小明显小于上一轮R的93KB整包摘要，并允许每条关系回链到 `source_id`/行号。

这些是机制验收，不是模型质量提升声明。若目标组合仍不能从审核可见代码抽出路径，应记录缺少的调用/heap/库摘要证据，再判断是否需要WALA或局部SMT。
