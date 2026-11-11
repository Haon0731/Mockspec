# 正式165例 r1：保留的失败运行

本轮完整尝试Java59/Python106，耗时180.95秒，但最终门禁不通过。

| 维度 | 结果 |
| --- | --- |
| 候选诊断 | 155例获得图，10例失败 |
| 成熟后端 | Jib实际完成；Flask和langflow启动失败；162例输入域不支持 |
| 语义核验状态 | 1例verified_incomplete，164例not_evaluated |
| 主分母 | 165，无筛选、无缺例 |

根因是批入口把CodeQL安装目录作为可执行文件传给Python桥。预登记因`Path.is_file()`为false而留下null executable hash，但当时没有在正式运行前拒绝配置。Python桥准确记录`CodeQL executable does not exist`，没有以旧结果代替本次执行。

修复在批入口增加普通文件与执行权限检查，并新增缺失、目录、不可执行三种负例。正确可执行文件为`/tmp/mockspec-issue24-tools/codeql-v2.27.4/codeql/codeql`，真实版本查询为2.27.4。r2使用新输出与新缓存重新尝试全部165例，未只替换本轮失败的两例。

原始[结果](results.json)、[预登记](pre-registration.json)、[运行结论](run-outcome.json)、[r3逐例对照](r3-comparison/comparison.json)及stdout/stderr保留。当时的batch_slice.py已按原预登记SHA保存到implementation-snapshot，供重建当时版本；它不是新独立入口。本轮不作为#31通过证据。
