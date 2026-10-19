# 任务跟踪：GitHub

本项目的任务和规格保存在 Haon0731/Mockspec 的 GitHub Issues。
使用 gh CLI 操作；仓库尚未关联时，显式指定
--repo Haon0731/Mockspec。

## 常用操作

- 创建：gh issue create --title "标题" --body-file <正文文件>
- 读取：gh issue view <编号> --json number,title,body,labels,comments
- 列出：gh issue list --state open
- 评论：gh issue comment <编号> --body-file <正文文件>
- 添加标签：gh issue edit <编号> --add-label "<标签>"
- 移除标签：gh issue edit <编号> --remove-label "<标签>"
- 关闭：gh issue close <编号> --comment "原因"

技能要求“发布到任务跟踪器”时，创建 GitHub Issue。
技能要求“读取相关任务”时，读取对应 GitHub Issue。

## 父子任务与依赖

优先使用 GitHub 原生子议题和依赖关系。
不支持时，在子任务正文注明 Part of #<父任务编号>，
在父任务中维护任务清单，并用 Blocked by: #<编号> 记录依赖。
所有阻塞任务关闭后，任务才可开始。

## Wayfinder

地图使用一个带 wayfinder:map 标签的议题，
正文保存笔记、已做决策和待澄清问题。
子任务关联到地图，使用 wayfinder:<type> 标签；
type 为 research、prototype、grilling 或 task。
按地图顺序领取无阻塞且无人负责的任务，并指派给自己。
完成后评论结果、关闭任务，并将结论和链接更新到地图。

## Pull requests as a triage surface

**PRs as a request surface: no.**
