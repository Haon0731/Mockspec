# 方案2五阶段 Scenario Agent：CloudFoundry 单次真实复验

日期：2026-11-02。结论：**未完成五阶段交付；取得了真实自主探索和部分正确推断证据，但尚无可交付的行为规格。现有证据支持继续优化方案2，尚不足以要求引入程序分析机制。**

## 1. 复验配置与边界

- 代码：`f028a0597c3290adddce3e97bb5208242e0f0fed` 加运行前已有工作区修改，完整补丁见 [code.patch](code.patch)，实际模块摘要见 [preflight.json](preflight.json) 与 [运行清单](run/run_manifest.json)。本轮没有修改生产或研究实现、Prompt、准入配置，也没有覆盖历史记录。
- 案例：`de.codecentric.boot.admin.client.registration.CloudFoundryApplicationFactoryTest#should_use_application_uri@d0085edf`。
- 冻结 snapshot：`ff6812732f1e2779e09005d721f464cebed6b587925130e6a2555a154097214a`，复用10月27日路径及已有 reviewed_inputs.json。Adapter 成功核对身份、所选文件及 masking；没有扫描依赖或重新批准输入。
- 输入：完整62行 masked test、51行目标类；[输入比较](input-comparison.json)确认与10月27日 JSON 完全相同，摘要 `5a63819309dc6304478151f2cf57680996437240ba61db907d40fce48a1e9eac`。
- 变体 C，repository_read=true，模型 deepseek-flash，官方 Responses 端点；openai 3.35.1、openai-agents 0.24.0、pydantic 2.14.1。响应模型名仅为 deepseek-flash，不能据此证明服务端部署与历史完全相同。
- 单次输出24,576、总token 200,000、整体600秒、每次阶段调用120秒、估费上限1美元；源码工具单次输出16,000字符。仓库模式的实际 model/query 次数上限为 null，清单里的10/6是兼容配置字段，不是本次生效门禁。
- 不设置 reasoning override，保留服务商默认思考模式。SDK 将 ModelSettings.max_tokens 映射为 max_output_tokens；7份实际请求均为24,576，均省略 reasoning。响应累计报告18,279 reasoning tokens，证明实际发生思考；输出预算包含这部分，并非纯JSON预算。
- 官方费率于执行前核验，保守使用峰值 cache-miss 输入 $0.30/M、输出 $1.20/M，不计缓存与非峰值优惠。来源：[官方价格](https://api-docs.deepseek.com/quick_start/pricing/)、[Responses 参数和用量](https://api-docs.deepseek.com/guides/responses_api/)。核验摘要见 [official-verification.json](official-verification.json)。费用门禁使用宿主估算，不是账户账单或服务端绝对硬限。

离线预检：完整协议、读取、阶段、live入口和响应留证专项 **172 passed**，见 [pytest-host.txt](pytest-host.txt)；另有协议专项52 passed、仓库专项5 passed。沙箱中的混合工具测试曾挂起，保留原日志，沙箱外同一完整专项通过；这不是模型失败。新输出目录不存在旧run，启动器拒绝重复启动。仅执行一次真实运行，无技术重试或质量重采样。

留证元数据修正：预登记复用了历史模板，其 policy/prompt/record/query_wire 字段残留v0.3，command字段仍是历史命令；**实际执行的是 arguments 数组，实际协议/Prompt/记录为v0.4**，以请求正文和清单为准。原预登记保持原样，修正记录见 [verification.json](verification.json)，未事后改写预算或输入。

## 2. 实际推断过程

| 阶段 | 实际行为与结果 |
| --- | --- |
| EXTRACT | 第1次模型调用完成；目标引用test 37–60及mut 43–50，覆盖原测试所有断言。提出健康路径依赖、可能的交互验证两个条件候选，并明确隐藏区域内容未知。 |
| QUERY | 第2–7次模型调用；5次工具查询后尝试finish_queries，宿主拒绝问题状态更新。 |
| CONSTRUCT | 未进入，无依赖规格草案。 |
| CONTRAST | 未进入，替代行为及过强约束未经过模型对比。 |
| DELIVER | 未进入，无scenario_contract.json或dependency_spec.json。 |

QUERY实际工具链（完整参数和回执在 [inference_record.json](run/inference_record.json) 的 query_log）：

1. 按 `**/DefaultApplicationFactory.java` 搜索类声明，发现父类及44行；不是预置路径。搜索因5个审核排除项标记 incomplete/access_denied，但返回的允许范围命中可用。
2. 用动态source_id读取父类1–220行，成功取得 createApplication、URL组合、health/status回退和异常代码；这是初始材料之外的真实新增关键证据。
3. 搜索 Java 中 `EndpointId.of("health")`，仅返回已知父类197行。
4. 搜索 `**/*Test.java` 中 `pathMappedEndpoints`，只返回原masked test构造器调用48行。
5. 搜索 Java 中 `PathMappedEndpoints`，返回自动配置类、父类和原测试的相关行，没有获得隐藏区域内容。

后3次查询逐渐追求类似测试里的精确stub/verify写法，新增行为价值较低。第4次回执被解释成“没有其他测试使用该标识”，但回执明确不完整，该否定超出了搜索证据范围。模型最终承认精确隐藏文本不可见，提出unknowns_nonblocking停止。

直接失败：第7次调用输出完整JSON、HTTP200、provider completed；q2/q3/q6 被标记 `state=nonblocking`，却填入 `acquisition=available`。现行调查协议要求此状态解释为何不必再获取并使用not_needed，宿主抛出：

```
ValueError: nonblocking question must explain why acquisition is not needed
```

终态 **ERROR / output_invalid / QUERY**。这是跨字段状态一致性错误，不是10月26日assessment额外字段错误。受限格式修复只处理已登记的窄格式问题，本次不符合，名额仍available；没有放宽验证器或替模型修正后继续运行。

## 3. 已获得的部分行为结果与独立质量评价

以下是对实际QUERY结果的评阅，**不是模型完成的最终规格**。动态父类source_id为 `repository:f1cbe4e7534b595de231fa4a`，原文已保存在查询2回执中。

| 行为 | 证据与评阅 |
| --- | --- |
| 原测试目标 | test 52–57同时要求management URL、health URL、service URL、name与contributor metadata。EXTRACT引用范围保留了它们，但后续主要聚焦health，尚未逐项形成约束，不能把坐标覆盖当作目标完成。 |
| 应用URI | mut 43–50的覆盖方法使用CloudFoundry URI；test 49、53–55要求保留`application/Uppercase`及对应URL。模型已连接URI与URL构造，但无最终完整规格。 |
| management URL | 父类110–117：显式management URL优先，否则管理基址拼接端点web path；143–145返回webEndpoint.getBasePath()。模型理解大体正确。 |
| health URL | 父类147–154：显式health URL优先，否则管理基址拼接getHealthEndpointPath()。不能对所有配置一概要求调用路径依赖。 |
| 路径返回与异常 | 父类196–205：先查询health；hasText时返回并不查询status；否则查询status，hasText时返回；两者均不满足则抛IllegalStateException。模型识别了回退及throw，但未形成完整返回、异常、交互条件。 |
| 合法替代行为 | 模型反复称health查询“必须返回actuator/health”，过强：在当前可见代码下，health为空而status返回对应路径同样是需保留的替代分支。是否允许该替代应在CONTRAST中分析，不能由imports或隐藏VERIFY猜测排除。 |
| 验证内容 | verify导入和空VERIFY区域不证明具体调用次数、顺序或原验证对象；模型最终仍承认这些未知，却花费查询寻找精确Mockito文本，暴露目标偏移。 |
| 引用质量 | 最终QUERY中用145–147支撑webEndpoint.getBasePath()，实际调用在144行；范围可读但不支持所称结论。较早对175–194的引用也没有覆盖197起的health路径实现。引用工程合法不等于语义正确。 |
| 默认配置 | test只构造WebEndpointProperties并断言/actuator；未读取其外部依赖实现，不能把其默认值来源说成已从源码验证，更不能据此强加额外配置。 |

QUERY停止的实质证据已足以开始构造“条件化路径行为”草案，无须恢复隐藏文本。其停止方向较零查询合理，但“必须health返回某值”的充分性理由遗漏status替代，且原目标其他字段尚未系统解释，因此不能独立认定证据和推断全部充分。

下游Coding Agent可参考父类路径分支及原断言，但当前输出没有完成替代、异常和交互边界整理，也没有经过CONTRAST/DELIVER。**尚不适合作为已验收的下游规格。** 独立只读评阅同样确认状态冲突、过强health要求及引用145–147的问题。

## 4. 工程执行、预算与历史比较

本次133.63秒，7次请求开始/完成；全部HTTP200且provider completed，响应未裁剪。输入64,774，输出23,078，合计87,852 tokens，用量完整；保守估费 **$0.0471258**。第7次输出11,086 tokens（含8,893思考tokens），超过历史8,192限制但完整返回。全部预算门禁允许，未触发总token、输出、时间或费用限制。

| 运行 | 主终止原因 | 本轮比较 |
| --- | --- | --- |
| 10月26日，98b8aca | QUERY assessment多出字段，读取请求未派发 | 宿主绑定assessment及受限格式修复已有离线通过证据；本次未复现此错误。不能宣称任意模型协议错误已被消除。 |
| 10月27日，cbe04c0 | 零查询停止；CONSTRUCT达到8,192输出上限 | 本轮自主查询并取得关键父类；24,576预算下无截断。但本次未进入CONSTRUCT，不能证明它能在新预算内完成。 |
| 10月28日，f028a05加已有补丁 | 无累计上限诊断取得父类代码，QUERY回执关联错误，另有nonblocking状态冲突 | 本轮恢复600秒/200k/$1上限，没有回执关联作为首错，nonblocking状态冲突再次成为终止点。 |

10月27日之后提交包含dependency_spec能力、调查状态及阶段协议拆分、B回放修正；当前工作区另有可选资源上限及响应留证等修改。因此本轮不是单独输出预算的因果对照，不能把查询增加归因于预算提高或五阶段方法收益。完整差异以commit历史及code.patch为准，输入准入摘要保持一致。

归因分开看：

- 工程/协议执行：工具可用，schema解析成功，但调查状态跨字段契约未被模型遵守，宿主按设计拒绝。当前协议可用性/纠错覆盖是直接瓶颈，不意味着校验器实现错误。
- 模型自主探索：具备识别父类、发现路径并读取关键实现的能力；仍出现追寻隐藏答案、对不完整搜索作否定推断等不足。
- 行为推断：取得并理解部分关键分支；存在过强必要条件、目标覆盖不足和引用语义错误。
- 输出预算：本次没有截断证据；后续阶段未执行，其预算是否充足仍未知。

## 5. 研究判断与后续取舍

**支持继续优化方案2，现阶段不以本次失败作为引入后向切片或符号分析的依据。** 模型自主取得了关键行为代码，说明“找不到相关代码”并非本次决定性瓶颈。直接停止来自调查状态表达，而推断质量的主要问题是隐藏文本恢复导向、必要条件过强及证据引用不准。程序分析不能自动解决这些输出契约与目标边界问题。

有针对性的下一步应首先降低调查状态的表达歧义、处理符合明确范围的状态纠错，并让模型围绕可观察目标构造条件与合法替代；这只是研究建议，本轮没有实施或再次采样。若这些问题排除后仍系统性漏掉分支、数据依赖或路径条件，再评估程序分析带来的独立增益。

本次没有成功规格，不能宣称五阶段方法已可用；已有部分能力证据也使“架构无效”的结论不成立。单例、不同代码版本和非确定采样均不足以证明整体有效性或收益。

## 6. 产物与验证范围

- [preflight.json](preflight.json)：预算、冻结输入、实际arguments、版本和摘要；历史模板字段的更正见verification.json。
- [launch.py](launch.py)、[started.json](started.json)、[finished.json](finished.json)、[launch.log](launch.log)、[progress.jsonl](progress.jsonl)：唯一一次启动、退出与事件记录。
- `http/request-*.json`：实际请求正文（无请求头或凭据）；`http/call-*.json`：保留的响应正文；`calls/`：各阶段输入、Schema、关联、用量与异常。
- [run/inference_record.json](run/inference_record.json)：工具调用、回执、可见原文、问题与阶段记录；[run/run_manifest.json](run/run_manifest.json)：结果、预算和完整用量。
- [verification.json](verification.json)：7次响应、实际输出预算、思考用量、产物和代码摘要交叉核验均通过；无最终spec。

本轮只新增本研究目录内的留证、启动器及报告。保留已有用户修改；未生成Mock、未执行目标测试、未运行旧项目实验、未扩展数据集、未引入新工具/分析机制。离线测试验证工程路径，不证明行为推断质量提高。
