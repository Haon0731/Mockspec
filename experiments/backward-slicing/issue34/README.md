# Flask 单案例三组一次生成对照 · #34

后续[第二轮对照与逐行结果](round2/README.md)采用重新校准的预算。下文记录第一轮。

2026-11-18，`flask_308`。三个独立上下文各生成一次。仅 C 交付了可注入的十个槽位；A、B 最后一轮均返回 `incomplete`，输出正文为空，各 8,192 个输出 token 全部计为 reasoning。没有重新生成。三组分母中保留这两次生成失败。

| 指标 | A 自主探索 | B 完整切片 | C 精简导航 |
|---|---:|---:|---:|
| 初始提示字节数 | 7,368 | 26,364 | 10,862 |
| API 轮数 / 源码工具调用数 | 4 / 5 | 2 / 2 | 2 / 1 |
| 生成耗时（秒） | 53.16 | 45.19 | 52.53 |
| 累计输入 / 输出 token（含推理） | 17,276 / 9,319 | 16,554 / 8,527 | 15,080 / 9,879 |
| 完整候选数 / 尝试数 | 0/1 | 0/1 | 1/1 |
| 原程序通过参数行数 / 5 | 无候选 | 无候选 | 缺测，容器超时 |
| 行为忠实度（完整匹配 / 可判定参考项） | 无候选 | 无候选 | 11/12 |
| 主指标（检出交互变异数 / 3） | 无资格 | 无资格 | 无资格，缺原程序 5/5 基线 |

C 提供了两处依赖替换、查询结果和异常 Stub，以及查询次数、实参转发和回退函数未调用的交互断言。独立评阅者预先冻结 12 项参考，仅接收匿名槽位，按注入后的执行次序核对。C 在无应用上下文时也创建了一个局部 Mock，而开发者参考将局部变量设为 None；两者均让查询返回 None，额外创建的对象在该路径上没有被使用。其余 11 项等价，无额外断言限制、完全遗漏或无法判定项。初步定位参考的人员曾看到历史候选的一行说明；正式冻结和评分均由另一独立上下文完成。[冻结清单](review/frozen.md) · [匿名评阅](review/specimen-714.md)

## 参数行与变异

A、B 的五行在全部四个版本均因生成失败未运行。C 共启动四次容器，每个版本一次。`缺测` 表示 60 秒内未收到 pytest 逐行记录。清理时间另计。两次完成的运行均通过 MUT 身份核对，pytest 退出状态与五行结果一致。

| C 参数行 | 原程序 | wrong_code | drop_kwargs | duplicate_lookup |
|---|---|---|---|---|
| app_context_exists_404 | 缺测 | 缺测 | 通过 | 断言失败 |
| app_context_exists_500_with_message | 缺测 | 缺测 | 通过 | 断言失败 |
| no_app_context_403 | 缺测 | 缺测 | 通过 | 断言失败 |
| no_app_context_with_args_kwargs | 缺测 | 缺测 | 断言失败 | 断言失败 |
| app_context_custom_code | 缺测 | 缺测 | 断言失败 | 断言失败 |

移除关键字参数时，两个传入非空 kwargs 的参数行分别在生成测试第 107、101 行报实参不匹配；其他三行的 kwargs 为空。重复查询时，五行均在第 96 行报告期望一次、实际两次。原程序超时使上述失败均无法与同一行原程序通过配对。执行验证与全仓工程检查的运行时段部分重叠，超时原因未确定。单例、每组一次及缺测情况不支持方法排序。[注入后测试](evaluation/eval-c/candidate.py) · [逐行回执与运行状态](evaluation/generation-metrics.json)

## 输入和隔离

使用 fix23 审核的生成快照 `241ef457…755f47d`。冻结分析来自另一快照 `723639be…1be69b78c`；逐文件核对确认遮蔽测试与 helpers.py 的字节数和 SHA-256 相同。B 保留全部 13 条可提供的关系及关联源码行，C 使用同一分析，按三个观察点整理守卫、可能影响与 Unknown。A 的附加证据为空。三组的普通测试、MUT、提示模板和工具相同。[输入、身份与摘要](inputs/manifest.json)

生成通过项目已配置的 deepseek-flash Responses 接口发起，各组维护独立对话；提供方默认推理设置，禁截断，每组 600 秒、最多 10 次源码调用，初始提示上限 65,536 字节，每次响应上限 8,192 token。服务端上下文窗口未报告。工具仅能读/搜两份获准文件；模型不能运行测试、访问宿主文件或读取变异结果。保存了完整提示、源码调用与返回、回复正文和用量。三次终态先冻结，再对唯一候选运行容器。使用评估器指定镜像，冻结源码与依赖只读，断网、1 CPU、768 MiB，60 秒/次。[生成设置](generation/protocol.json) · [三组冻结摘要](generation/freeze.json)

## 复现入口

在 MockSpec 根目录执行。需要审核快照、冻结依赖和本地固定镜像。重放评估仅消费冻结候选和失败回执，每次使用新输出目录。

```bash
study=experiments/backward-slicing/issue34
run_dir=$(mktemp -d)
.venv/bin/python "$study/prepare_inputs.py" --old-root ../Stub-Tester --output "$run_dir/inputs"
diff -r "$study/inputs" "$run_dir/inputs"
.venv/bin/python experiments/backward-slicing/evaluate.py \
  --old-root /home/haon/projects/Stub-Tester --candidate-dir "$study/generation" \
  --output-dir "$run_dir/evaluation"
```

生成入口为 `generate.py --old-root ../Stub-Tester --output-dir <新目录>`，读取项目配置的 OPENAI_API_KEY 与 OPENAI_BASE_URL。再次调用模型将形成新观测。历史 A/B 可用原 evaluate.py 入口省略候选目录，并用 `--output-dir <新目录>` 保存独立复现证据。
