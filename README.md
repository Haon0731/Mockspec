# Mock Spec

从已审核的测试和源码推导 **Scenario Contract：有条件、有证据的依赖行为约束**。
Scenario Agent 输出约束，不生成 Mock 代码、不执行目标测试、不评分。

## 先看哪里

- **想理解 Agent**：[架构导读](docs/architecture.md)，按一次输入走完四个核心文件。
- **想运行或开发**：[运行与开发](docs/usage.md)，区分普通推断、离线回放和完整留证。
- **想理解输出**：[Scenario Contract](docs/scenario-inference.md)，说明约束字段和验证范围。
- **想查研究及历史**：[文档目录](docs/README.md)，区分当前能力、研究验证与阶段记录。

## 当前主链路

```text
审核 Adapter → UnifiedTask → Scenario Agent → ScenarioContract 校验 → JSON
                                │
                                └─ V0.2 可查询可见源码及 AST/Spoon 语法事实
```

Agent 的推断由模型按照 Prompt 完成；宿主控制输入、工具、预算和结果验证。
结构与源码引用校验不证明推断语义正确，也不证明真实测试通过。

| 路径 | 职责 |
| --- | --- |
| `src/mock_spec/` | 生产输入、Agent、契约、工具与运行管理；详见架构导读中的文件地图 |
| `tests/` | 生产行为与隔离回归 |
| `experiments/` | 独立研究：人工模型、局部 cvc5 检查、人工 Mock 见证；未接入生产 Agent |
| `scripts/` | 固定阶段的验证与运行命令，按相应复现文档使用 |
| `docs/` | 当前指南与保留的历史协议、报告、证据 |
| `examples/scenarios/` | 合成契约与回放示例，不是模型成功证据 |
| `out/` | 运行产物，不是 Agent 输入或实现 |

V0.1 为冻结的无工具基线；V0.2 使用三个只读工具，两版共享正式契约。
CLI 默认仍为 V0.1，显式传 `--agent-version v0.2` 选择 V0.2。
完整留证入口 `python -m mock_spec.audited_run` 支持两版并要求明确选择版本。

## 信任边界

1. **准备侧**维护已审查输入记录：案例身份、冻结 ref/runtime/injector 身份、允许源码、
   来源范围与 masking 约定。这些私有记录不属于 Agent payload。
2. **Adapter**只读核对有限的已选文件。只校验所选源码和控制文件，不扫描或哈希 JAR、
   site-packages、依赖树，也不执行 Stub-Tester 的 loader、injector 或其他脚本。
3. **Masking 检查**要求 Java 双区域为空、Python slot 集合和顺序一致且内容仅为 `pass`，
   同时拒绝本策略下的残留 mock 配置与验证。文件身份检查覆盖区域外的上下文。
4. **Agent 边界**初始仅传递 `task.to_dict()`；验证器和 V0.2 工具只访问当前任务的内存 `SourceStore.get(source_id)`。
   Store 不持有物理根目录或文件读取函数，不能以路径访问旧仓库。

Schema 合法不等于 masking 已审核。不能用 `UnifiedTask.from_dict()` 自行组装任意源码，
再宣称它通过 Adapter 审核。内容检查也不是通用泄漏检测器：准入依据是已审查的固定
输入身份加内容检查，而不是正则或文件名。

Agent 在宿主 SDK 中创建。V0.1 没有工具；V0.2 仅有三个受限的源码/语法分析工具，
没有任意路径读取、目标程序执行、handoffs 或 MCP，不接收 Adapter、宿主 review 配置或旧数据目录。关闭 SDK tracing，
模型请求设置 `store=False`。这不是 OS sandbox，也不是对服务方数据保留政策的保证。
调用 Python API 的宿主仍须先通过 Adapter 审核输入；手工构造 UnifiedTask 不获得准入。

## 开发验证

安装与环境要求见 [运行与开发](docs/usage.md)。已安装项目依赖时：

```bash
.venv/bin/python -B -m pytest -q -p no:cacheprovider -m "not real_data"
```

此命令不发起付费模型请求，排除真实输入与归档对照测试。
`experiments/` 有各自的验证命令，不属于默认 pytest 测试集。
