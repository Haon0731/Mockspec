# 复现与环境

在仓库根目录运行。只读 Adapter 需要本机两个已审核 snapshot；路径定义在 experiments/scenario-research/inputs.py，缺失时失败，不回退。模型/API 测试可不加载 snapshot；完整样例与绑定验证需要它们。没有目标程序执行、付费模型调用或数据集扩展。

```bash
uv venv --python .venv/bin/python /tmp/mockspec-contract-cvc5
uv pip install --python /tmp/mockspec-contract-cvc5/bin/python -r experiments/scenario-research/requirements.txt
PYTHONPATH=src /tmp/mockspec-contract-cvc5/bin/python experiments/scenario-research/build_examples.py
/tmp/mockspec-contract-cvc5/bin/python experiments/scenario-research/poc.py
/tmp/mockspec-contract-cvc5/bin/python experiments/scenario-research/negative_checks.py
PYTHONPATH=src /tmp/mockspec-contract-cvc5/bin/python experiments/scenario-research/bind_results.py
PYTHONPATH=src /tmp/mockspec-contract-cvc5/bin/python -m pytest -q experiments/scenario-research/tests
```

脚本会覆盖**本轮独立研究目录**下的生成样例/结果；需要保留旧运行时先另存 results。不会修改既有实验或生产输出。PoC 是固定可信 Python 代码，不读取或执行外部 Agent 脚本。InputParser 只处理本地生成 SMT-LIB，调用前限制命令为 set-logic / declare-fun / assert；这不是面向任意上传 SMT 的生产沙箱。

## 实测环境身份

固定 cvc5==1.3.1 / pytest==8.4.2；传递依赖也固定在 requirements.txt。CPython 3.12.13，Linux x86_64；官方 Python wheel 的 Solver.getVersion() 实际返回 `1.3.1-modified`，不能说 wheel 是未经修改的源码构建。源码参考点为 cvc5-1.3.1 / ea1b484fa54bfe56c0f8b3ac90a6e3e2f46441e7；固定文档与许可出处见 sources.md。

results/environment.json 保存实际 wheel tag、模块路径、本地 native 扩展 SHA256、分发版本及打包许可文件身份。源码 modified BSD 与 wheel 附带的 GPL/LGPL/GCC exception/minisat/winpthreads/pythonic 许可文件分别记录；本轮没有完成二进制链接组成或再分发合规审计，不能将其说成只有 BSD 的完整依赖闭包。

初次 uv 命令被沙箱的用户缓存只读限制阻止，授权重试后成功安装到 /tmp；没有修改项目 pyproject.toml 或原 .venv。安装需网络，后续实验离线。GitHub open issues 读取也在沙箱网络失败后获准重试，结果为空；未写入 tracker。

## API 与结果解释

使用 base API：TermManager、mkTerm、mkGrammar、getAbduct、getAbductNext、checkSat 和 InputParser。produce-abducts=true，incremental=true，tlimit-per=3000ms；每个 abduction 模型额外有12秒子进程墙钟上限。候选在独立 Solver 中复核。

实际 getAbductNext 返回 Term（可为 null），不能照搬该版本 Python docstring 中残留的 bool/output 参数描述。null term 没有提供清晰的资源耗尽/枚举结束区分；记录为 next unavailable，不能据墙钟接近3秒就伪称已确认 timeout。负例中的 rlimit=1 实际得到 unknown/RESOURCEOUT；微秒级 subprocess deadline 是刻意触发的超时处理测试，不是正常求解负载性能证据。

results/solver-results.json 保存 API 调用、grammar atoms、背景/guard/goal、每次耗时、原始返回项、独立复核与等价查询。*.smt2 来自 solver term 序列化，并通过 cvc5 官方 InputParser 做 SAT/UNSAT round-trip。results/bound-results.json 将契约哈希、源身份、符号映射、公式引用和逐行检查绑定；formula parsing 只证明语法/类型，semantic_check 仍为 not_run。

## 回归范围

```bash
PYTHONPATH=src .venv/bin/python -B -m pytest -q -p no:cacheprovider tests --ignore=tests/test_real_data.py
```

该次190项通过。显式不执行 test_real_data.py，因为其中含归档生成后 mock 文件对照；本轮禁止读取这类答案材料。新的契约测试仍通过审核 Adapter 加载冻结输入并核对引文。仓库没有配置独立 lint/typecheck；本轮执行 Python 编译检查、pytest 和差异空白检查，不宣称运行了不存在的静态分析配置。

## 实际失败与隔离修复

负面测试首次在宿主进程调用 InputParser 解析未声明符号，pytest 退出139（segmentation fault）。复现实验将同一错误放入子进程后，得到 RuntimeError 的未声明变量信息，子进程最终退出 -11，宿主存活；见 results/parser-error.json。当前契约公式解析全部在10秒子进程边界内进行；解析错误/崩溃均拒绝输入，不转换为 UNSAT。该措施隔离故障，**没有修复 cvc5 本身的错误路径**，亦未证明其他版本没有此问题。将来的生产集成必须保留 native 隔离并另行核实此缺陷。

最终独立研究测试38项通过；已有项目回归190项通过。没有运行项目 .venv 的 cvc5 测试，因为它刻意不安装研究依赖。
