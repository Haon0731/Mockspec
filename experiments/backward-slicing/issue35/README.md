# #35 六例三组重复生成

后续的七例 Base 与 v2 对照采用统一标签交付，新增 flask_308，各例沿用冻结后端输入和变异判据。本文保留原六例 JSON 交付的 A/B/C 结果。[七例主试验](../navigation-v2/README.md)

[协议](protocol.md)和[设计冻结记录](design-freeze.json)登记了六个案例、各自的分析版本、评估节点和三轮生成。Java 使用 Zipkin 的通用 WALA 结果和 Jib 的已登记投影，Python 使用 Langflow 的动态桥接及 Ansible、Click 的通用 CodeQL 结果。所有输入经全量冻结 Adapter 和逐文件摘要核验。旧仓库与数据只读。

[Jib 导航表示与事实消融](jib-navigation/README.md)使用新的独立生成，对照证据的组织方式和两种被省略的关系字段。

## 复现输入与生成

在 MockSpec 根目录运行。需要本研究登记的主 registry。默认路径在 inputs.py 中，load_case 也接受显式的 registry_root。下列命令在新目录重建 18 份初始提示和 B/C 来源清单；历史运行的提示与清单位于 prompts。

```bash
study=experiments/backward-slicing/issue35
replay=$(mktemp -d)
.venv/bin/python "$study/generate.py" prepare --inputs "$replay/prompts"
diff -qr "$study/prompts" "$replay/prompts"
```

重新运行生成会产生新的收费观测。需要项目配置的 DeepSeek 凭据。

```bash
source .env.local
.venv/bin/python "$study/generate.py" run --inputs "$replay/prompts" --output "$replay/run"
```

[生成冻结清单](run/freeze.json)绑定各次原始响应、工具往返、实际用量与候选。Python 评估侧在生成前冻结参考及具体变异的摘要。之后的注释将普通断言和交互断言分列，并补充变异适用行说明。[冻结版本](python_eval/manifest.json) · [注释版本](python_eval/annotated-manifest.json) · [Java 评分判据](java_evaluation_criteria.json)

容器使用固定镜像，在两种语言之间串行执行。Python 的 Ansible 参考有一处绝对源路径调整为冻结测试中的相对路径；登记中保留原始与调整后摘要。成功原程序校准共 23 行，Java 每例一行，Python 四例分别为 7、6、3、5 行。[Java 校准](java_preflight.json) · [Python 校准](python_eval_calibration/summary.json)

## 导航范围和生成成本

| 案例 | 观察 / 关系 | A / B / C 初始提示字节 | B 三次额外源码调用 | C 三次额外源码调用 |
|---|---:|---:|---|---|
| Zipkin | 2 / 2 | 9,529 / 16,421 / 17,718 | 2, 2, 3 | 0, 3, 0 |
| Jib | 19 / 173 | 10,085 / 332,697 / 322,662 | 5, 2, 5 | 10, 2, 3 |
| Langflow | 2 / 31 | 9,790 / 38,458 / 39,850 | 0, 2, 4 | 2, 2, 2 |
| Ansible | 1 / 0 | 7,548 / 11,351 / 10,952 | 3, 4, 3 | 4, 3, 5 |
| click_423 | 1 / 3 | 34,752 / 41,680 / 41,960 | 3, 5, 4 | 6, 5, 10 |
| click_402 | 1 / 3 | 25,198 / 32,557 / 33,096 | 5, 8, 5 | 2, 3, 4 |

Jib 的 B 和 C 均完整装入模型。C 少 10,035 字节，首轮请求实际输入却从 B 的 94,280 token 增至 102,913 token；字节节省没有对应到首轮 token。三次工具调用也没有一致下降。click_402 的 C 初始提示稍长，但三次源码调用数和累计输入 token 均低于同轮 B。六例中 C 的初始字节数仅在 Jib 和 Ansible 低于 B。

Zipkin 的两条 may 关系只关联 criterion:0，包括 Schema.java:103 到 SchemaTest.java:38 的候选值流。criterion:1 保留自身观察坐标。Jib 的字符串或 SSA 端点保留后端定位信息，无法解析到具体源码行时记录边界。对比 Python 的 click_402，utils.py:407–446 到 test_402format_filename.py:100 有观察关联值流，覆盖的是函数区间，单独这条边不能证明某个替身的返回值。

Langflow 的可见 loader 从 mcp_encryption.py:94–118 提取函数，并把局部异常类放入执行命名空间。桥接记录支持初始函数与异常类型的来源；后续 callable 重绑定、解密值、具体抛出异常仍依赖运行时。Ansible 只映射到测试观察，入口未解析。click_423 已绑定 termui.py:261–290，并有局部及实参值流，但缺少把产品效应连回观察的边。两种弱证据均保留观察，不能据此指定必须配置的返回值或验证调用次数。

## 配对结果

每组每例三次。下表每格的两个数分别是候选交付数和原程序全部行通过数。分母始终是三个独立上下文。参考列是原程序全过后检出的有效变异数。所有原程序全过的生成候选都检出同例 3/3 个变异。

| 案例 | A 交付 / 全过 | B 交付 / 全过 | C 交付 / 全过 | 开发者参考检出 |
|---|---:|---:|---:|---:|
| Zipkin | 3 / 3 | 3 / 3 | 1 / 1 | 3/3 |
| Jib | 3 / 3 | 3 / 3 | 3 / 3 | 3/3 |
| Langflow | 3 / 0 | 3 / 0 | 3 / 0 | 3/3 |
| Ansible | 3 / 1 | 3 / 1 | 3 / 0 | 3/3 |
| click_423 | 3 / 0 | 3 / 0 | 2 / 0 | 3/3 |
| click_402 | 3 / 0 | 2 / 0 | 3 / 0 | 2/3 |

Zipkin 的两个 C、click_423 的一个 C 和 click_402 的一个 B 返回无法解析的 JSON。34 个 Python 候选通过生成接口的槽位校验，其中 32 个按约定加入槽位相对缩进后无法编译。其余两个均来自 Ansible 第三轮，A、B 各一。重复探测调用会耗尽两项 run_command.side_effect，六个适用行触发 StopIteration；这两个候选各有两个变异触发显式断言、一个变异通过 Stub 耗尽暴露。开发者参考在此变异上触发断言。click_402 参考只要求编码查询发生过，重复查询后依然通过。该例三个组均没有可执行候选用于对比参考漏检。

静态盲评比较的是槽位文本表达的配置，执行结果另列。50 个已交付候选合计命中 148/150 个可判定 Stub 项；两个缺项都在 click_423，B、C 各一个。Zipkin 可读的三组候选均符合五项 Stub 判据，Jib 均符合七项。Jib 的 C 仍有 12、2、12 项额外精确调用限制（按轮次见逐候选表）；从路径可见某个 getter 并不能推出应强制验证次数。Langflow 的三个 C 均写出了参考期待的两项配置，但三个均在注入时失败，无法据此判断动态路径的实际保真度。

六例没有显示 C 比 A 更稳定地恢复场景所需 Stub。在 Jib 的三次重复中，B、C 均全过且均检出 3/3；Zipkin 的 B 交付三次而 C 只交付一次，同一轮可配对的合格候选得分相同。Python 大量缩进失败限制了可执行行为比较。各组原始 token、时延、工具调用、逐候选分项、范围和同轮方向见[汇总](summary.json)。[匿名静态评阅](blind-semantic-review.json) · [Java 逐项配对](java_results/paired.json) · [Python 逐行配对](python_results/paired.json)

## 重放评估与扩大样本的条件

下列命令使用已冻结的本次候选，在新目录重放。Java 与 Python 共用容器锁。Python 只运行能注入的候选，并保留各阶段失败。恢复生成或重新抽样均会构成独立观测。

```bash
export PYTHONPATH=src:experiments/backward-slicing
.venv/bin/python "$study/java_batch_evaluate.py" prepare --run-root "$study/run" --output "$replay/java"
.venv/bin/python "$study/java_batch_evaluate.py" execute --run-root "$study/run" --output "$replay/java"
.venv/bin/python "$study/python_batch.py" run --output "$replay/python"
.venv/bin/python "$study/python_batch.py" summarize --output "$replay/python"
.venv/bin/python "$study/summarize.py" --study "$study" --output "$replay/summary.json"
```

向全量 165 例扩展前，还需要为其余案例完成评估侧参考身份与普通测试区比对、可执行节点及依赖校准、逐例有效变异登记，并核对最长 B 包的上下文容量。Python 的槽位表示约定值得在新的、独立预登记的生成实验中先行校准。这批候选保留原输出与原注入规则。执行期间另行启动过一次容忍绝对缩进的诊断，发现偏离预登记规则后已停止；该目录单独保留，主结果只读取 python_results。
