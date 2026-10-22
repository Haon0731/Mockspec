# 人工最小模型（阶段 B）

所有模型 authoring_mode=handcrafted_from_visible_input；没有自动源码翻译。源码位置最终由 examples 的 source_id/quote 绑定。SMT 符号是下述局部抽象，不是对真实库实现的定义。

Java image：绑定 mut:60 的 from1_present 和 mut:63 的 from2_present，分别出现，不默认稳定。mut:69 的 version 是有符号32位整数载荷，另设 version_null、version_throws；默认路径调用确实发生且正常非空才能比较。局部 O 是执行到选中 Java8 镜像的语句（mut:70），不是最终 buildPlan.getBaseImage()。未形式化 Jib.from、setter、toContainerBuildPlan；不能将目标镜像输出设为可控变量。显式分支保留未解释 external_image_matches，该符号不可进入 abduction grammar；其有利赋值只能证明抽象未排除该路径，不能证明可消费。

Java entrypoint：mut:75–79 分别抽象 custom_empty、custom_matches、computed_matches（后两者表示响应列表等于可见四项列表）。custom_empty ⇒ ¬custom_matches，因为可见目标列表非空。选定值匹配为 ite(custom_empty,computed_matches,custom_matches)。两条分支可满足，不能声称所有镜像×entrypoint 组合真实可行。列表/getter null、抛异常及后续 builder 失败列为未知前提；无 whole-test 证明。

Python：mut:282–285 以正常 lookup、ctx.app.aborter 可访问为 G 前提。ctx_none 表示 is None，独立于 truthiness。call_aborter = ¬ctx_none；call_fallback = ctx_none ∨ ¬abort_throws。反应只在调用发生时生效，正常返回与抛出互斥。观测目标 O = (call_aborter ∧ abort_throws ∧ abort_message_matches) ∨ (call_fallback ∧ fallback_throws ∧ fallback_message_matches)，throw 在此仅表示 pytest.raises(Exception) 能捕获的异常，消息匹配当前参数行的子串。未捕获 BaseException、lookup/属性访问异常不在覆盖范围。两种来源均可产生相同消息；异常消息不是来源证明。按五个参数行分别关联 code,args,kwargs 和 O，参数透传仅凭可见调用语法，不自动证明动态绑定/运行时参数协议。

候选 H 只能引用审核响应符号；derived reach/output、外部库语义、整个 O 不允许进入 grammar。候选检查需分别 SAT(T∧G∧H)、SAT(T∧G∧H∧O)、UNSAT(T∧G∧H∧¬O)。不一致 H 绝不称为充分；跳过观测不称成功。第二候选需做异或可满足性检查，UNSAT 代表语义等价。
