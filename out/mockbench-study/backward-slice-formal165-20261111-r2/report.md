# 正式165例 r2：保留的进程环境失败

165例全部有终态，耗时161.03秒；候选155例构图、10例失败。成熟后端Jib完成、162例不支持、Flask与langflow两例建库失败，最终门禁不通过。

本轮已正确定位CodeQL2.27.4可执行文件并冻结其SHA256。新失败位于真实批处理调用边界：宿主`analysis_process.communicate`仅传LANG/LC_ALL，删除PATH。CodeQL以`python3`启动其采集器；解释器在缺少PATH时得到空`sys.executable`，multiprocessing无法启动resource_tracker，产生BrokenPipeError。最小宿主探针复现相同错误；仅加入受信`PATH=os.defpath`后，解释器路径恢复为`/bin/python3`、Queue创建成功。

这不是数据集错误，也不应归因于Python3.14本身不支持。此前桥接smoke直接从shell启动，没有覆盖实际受限进程边界。修复限定在研究Python桥，为可信工具明确最小运行环境；不放宽生产进程清理、不继承凭证环境、不执行目标测试或loader。后续必须通过相同communicate入口的两个实际后端smoke，再完整执行r3。

[原始结果](results.json)、[预登记](pre-registration.json)、[运行结论](run-outcome.json)、[r3逐例对照](r3-comparison/comparison.json)及两个driver的stdout/stderr完整保留。Python桥修改前源码按预登记哈希保存到implementation-snapshot。本轮不计作#31通过。
