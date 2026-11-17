# flask_301：从原观察到依赖返回条件

本记录面向本次首例实现及独立验收，人工参照在读取允许的 masked test 与 MUT 后编写；不读取 gold、原始未遮罩测试或历史生成 Mock。它不是分析器输入，也不是完整路径真值。范围只覆盖正常返回的局部值链。

## 身份和证据

使用 `FrozenDataset` 从主 registry `developer-realistic-registry-max` 校验加载，未使用 fix23 或归档模板。

- case：`flask_301`；dataset：`python-benchmark-new`。
- inventory：`0759c27f3d4cc7257d78d9ac2ced70089aedc60fb8d0f562b63cc8f750d7e8dc`。
- snapshot：`c453c24d83b12ba90fa4ca4f38b149166d417b1ad04fcd778ff6ad93fe764c44`。
- test：`tests/test_301make_response.py`，SHA256 `9f0ef26744df86420e7e435d2a11dd1eaaa92e823f0f10a28fa3a75919f440e3`。
- product：`src/src/flask/helpers.py`，SHA256 `3939be44fe5455d5c10b0400038d89f17fb5fdbaed67ac35d250a60399d603c9`。

可见 test 第 11–36 行为四组字面量参数；第 89 行 `result = make_response(*args_input)`。
第 92–94 行仅断言 `result is not None`、`hasattr(result, 'headers')`、`hasattr(result, 'status_code')`。
MUT 第 138 行定义 `make_response(*args)`；180–181 行空参直接返回 `current_app.response_class()`；
182–183 行单参将 `args` 改为 `args[0]`；184 行返回 `current_app.make_response(args)`。

## 人工推导参照

| 固定测试行 | 函数入口 args | 正常执行条件 | 返回位置／依赖表达式 | 传给依赖的实参 |
| --- | --- | --- | --- | --- |
| empty_args（12–17） | `()` | `not args` 为真，提前返回 | 181：`current_app.response_class()` | 无 |
| single_string_arg（18–23） | `('Hello World',)` | `not args` 为假；`len(args) == 1` 为真，执行归一化 | 184：`current_app.make_response(args)` | 单个字符串 `'Hello World'` |
| single_tuple_arg（24–29） | `(('Success', 200),)` | `not args` 为假；`len(args) == 1` 为真，执行归一化 | 184：`current_app.make_response(args)` | 单个元组 `('Success', 200)` |
| multiple_args（30–35） | `('Not Found', 404)` | `not args` 为假；`len(args) == 1` 为假 | 184：`current_app.make_response(args)` | 单个元组 `('Not Found', 404)` |

在入口绑定正确、可见 setup 正常完成、依赖表达式求值和调用正常完成的前提下，MUT 直接返回该依赖调用的结果，测试随后未覆盖 `result`。
因此每行所选依赖返回值必须满足同一组原观察：非 None，`hasattr(..., 'headers')` 为真，`hasattr(..., 'status_code')` 为真。
这里只推导条件性正常返回约束；不证明运行时依赖可用、返回对象可构造或整个 masked test 可执行。
`hasattr` 保留 Python 原谓词语义；不假设属性一定物理存储于实例，也不执行属性访问。

## 不应推导的要求

- 不要求返回值是 `mock_response`：可见代码没有建立这个身份连接，相关 slot 是空的。
- 不要求状态码为 200/404、正文为 Hello World 等：第 61–63 行固定 setup 和参数标签不是这些返回值的断言。
- 不从 `expected_calls` 或空 verify slot 补出 `assert_called_once`、调用次数或参数验证要求。
- 不认定 `current_app` 必须用某种 patch/MOCK/SPY 替换；源码依赖边界不是替换授权。
- 不把两个返回点混成同一行测试的可任选响应；参数行已决定受限局部控制分支。
- 不宣称异常、descriptor、全局代理解析、隐式内部交互或所有合法路径已覆盖。

## 与旧结果的首个差距

比较对象为 `python-preserved-resume-r7/results.json` 中相同 snapshot 的 flask_301，而非早期 formal r3 或 fix23。
旧结果已包含观察身份和 product return/guard 候选：181 与 184 均关联到三个观察；180 的真假方向存在于 detail 中。
因此首个缺口不是“未绑定 MUT”，也不是“后端完全没有关系”，而是没有将可见参数行、分支选择、183 行赋值和依赖实际参数组织成观察约束。

本次只实现字面量参数化、明确任务提供的入口、受限局部 if/assignment/return 与直接返回值观察传递。
不扩展 WALA/CodeQL、不调用模型、不执行测试；未知表达式、覆盖或不支持结构必须显式阻塞。

## 验收

实现结果应保留四行身份和三项观察，选择 181/184/184/184；依赖参数必须区分单字符串和单元组。
每行的返回要求仅含非空与两个 hasattr；不添加 status/body/identity/cardinality。
正例之外必须验证提前返回、确定覆盖、观察变量重绑定、未知条件、内置函数遮蔽、多个目标调用和不支持控制结构。
完整规格仍为 partial，不将此项规则验收扩大为 165 例恢复率或 Mock 生成效果。
