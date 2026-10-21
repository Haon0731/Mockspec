# Step 6 GitHub 任务

发布时仅 #1 标为 ready-for-agent。原生 blocked_by 已配置；#1 完成后开放 #2/#3/#4，三者完成后开放 #5。不修改其他父议题。

| 任务 | GitHub Issue | 阻塞项 |
| --- | --- | --- |
| 01 核心循环、版本路由、源码查询、基本限制 | https://github.com/Haon0731/Mockspec/issues/1 | 无 |
| 02 Python 程序分析事实 | https://github.com/Haon0731/Mockspec/issues/2 | #1 |
| 03 Java Spoon 程序分析事实 | https://github.com/Haon0731/Mockspec/issues/3 | #1 |
| 04 全局预算、费用、失败留证、重试 | https://github.com/Haon0731/Mockspec/issues/4 | #1 |
| 05 对照验证与交付 | https://github.com/Haon0731/Mockspec/issues/5 | #2、#3、#4 |

所有实现具有案例通用性，冻结 V0.1 并保留输入隔离。02/03 显式区分 unresolved、unsupported 和分析失败。05 将工程验收与场景约束准确性、完整性、证据增量和推导错误分别报告，不能以 JSON 通过率代替研究评价。

本目录 ticket-drafts 保留发布草案；实时任务状态和领取门禁以 GitHub 为准。
