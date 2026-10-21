# Python flask_308 — AI辅助语义审查（待人工确认）

真实原始结果：`out/live_inference/step4/python/attempt-2/result.json`；原始候选JSON：同目录`raw-contract.json`。
该候选不是已验证Contract：Schema通过，Contract Validator实际失败（evidence_error）。
重新执行同一Validator并逐项比对11项引用：10项通过，EV_MOCK_SLOT失败。详见`validation-recheck.json`。未修正候选、未重新调用模型。
不使用Reference Mock、Ground Truth或历史评分。索引0-based，归因A/B/C/D只是假说，不是受控归因实验。

## test_objectives[0]

**Agent原始结论**
```json
{
  "statement": "The parameterized test expects `abort(code, *args, **kwargs)` to raise an `Exception` for every visible row.",
  "status": "required",
  "scenario_ids": [
    "app_ctx_404",
    "app_ctx_500_msg",
    "no_ctx_403",
    "no_ctx_400_args",
    "app_ctx_418_custom"
  ],
  "condition": "when the test invokes `abort` with the row's `code`, `args`, and `kwargs` inside `pytest.raises`.",
  "evidence_ids": [
    "EV_RAISE_CALL",
    "EV_PARAMS"
  ]
}
```
**源码证据**：EV_RAISE_CALL → test:102–103；EV_PARAMS → test:11–47。
**审查判断**：通过，场景关联可完善。
**依据**：test:102–103对每个参数行要求Exception；替代app_ctx_aborter_returns也应关联该目标，当前scenario_ids仅关联五个主场景。
**错误类型/能力缺口与归因**：目标内容正确；场景关联覆盖小缺口D/C。

## test_objectives[1]

**Agent原始结论**
```json
{
  "statement": "For app-context rows, the caught exception string must contain `Aborted with {code}`.",
  "status": "required",
  "scenario_ids": [
    "app_ctx_404",
    "app_ctx_500_msg",
    "app_ctx_418_custom"
  ],
  "condition": "when `has_app_context` is true for the parameter row.",
  "evidence_ids": [
    "EV_IF_APP",
    "EV_ASSERT_APP",
    "EV_PARAMS"
  ]
}
```
**源码证据**：EV_IF_APP → test:111–111；EV_ASSERT_APP → test:120–120；EV_PARAMS → test:11–47。
**审查判断**：通过，场景关联可完善。
**依据**：test:111与120要求true行的消息子串，不约束异常必须来自aborter；应把已列出的app_ctx_aborter_returns也加入关联。
**错误类型/能力缺口与归因**：目标内容正确；关联覆盖小缺口D/C。

## test_objectives[2]

**Agent原始结论**
```json
{
  "statement": "For no-app-context rows, the caught exception string must contain `Werkzeug abort {code}`.",
  "status": "required",
  "scenario_ids": [
    "no_ctx_403",
    "no_ctx_400_args"
  ],
  "condition": "when `has_app_context` is false for the parameter row.",
  "evidence_ids": [
    "EV_ELSE",
    "EV_ASSERT_NOCTX",
    "EV_PARAMS"
  ]
}
```
**源码证据**：EV_ELSE → test:121–121；EV_ASSERT_NOCTX → test:126–126；EV_PARAMS → test:11–47。
**审查判断**：通过。
**依据**：test:121与126明确false行消息子串。
**错误类型/能力缺口与归因**：未发现错误。

## scenario_hypotheses[0]

**Agent原始结论**
```json
{
  "id": "app_ctx_404",
  "description": "For `app_context_exists_404`, a plausible path is that `_cv_app.get(None)` returns a mock context, `ctx.app.aborter` raises an exception containing `Aborted with 404`, and `abort` propagates it before `_wz_abort`.",
  "condition": "`has_app_context` is true, `code=404`, `args=()`, `kwargs={}`, and the masked aborter is configured to raise.",
  "status": "candidate",
  "evidence_ids": [
    "EV_PARAMS",
    "EV_MOCK_ASSIGN",
    "EV_ASSERT_APP",
    "EV_MUT_BRANCH"
  ]
}
```
**源码证据**：EV_PARAMS → test:11–47；EV_MOCK_ASSIGN → test:88–89；EV_ASSERT_APP → test:120–120；EV_MUT_BRANCH → mut:282–285。
**审查判断**：合理候选。
**依据**：test:13–17、88–89、120与mut:282–285支持404上下文及aborter抛出路径。candidate没有把隐藏配置当成已知事实。
**错误类型/能力缺口与归因**：未发现错误。

## scenario_hypotheses[1]

**Agent原始结论**
```json
{
  "id": "app_ctx_500_msg",
  "description": "For `app_context_exists_500_with_message`, a plausible path is that the mock context's aborter receives `500` and `(\"Internal Server Error\",)` and raises an exception containing `Aborted with 500`.",
  "condition": "`has_app_context` is true, `code=500`, `args=(\"Internal Server Error\",)`, `kwargs={}`, and the masked aborter is configured to raise.",
  "status": "candidate",
  "evidence_ids": [
    "EV_PARAMS",
    "EV_MOCK_ASSIGN",
    "EV_ASSERT_APP",
    "EV_MUT_BRANCH"
  ]
}
```
**源码证据**：EV_PARAMS → test:11–47；EV_MOCK_ASSIGN → test:88–89；EV_ASSERT_APP → test:120–120；EV_MUT_BRANCH → mut:282–285。
**审查判断**：合理候选。
**依据**：test:20–24提供500和位置参数，mut:283透传参数；异常来自aborter是条件化候选。
**错误类型/能力缺口与归因**：未发现错误。

## scenario_hypotheses[2]

**Agent原始结论**
```json
{
  "id": "no_ctx_403",
  "description": "For `no_app_context_403`, a plausible path is that `_cv_app.get(None)` returns `None`, so `abort` skips the aborter and `_wz_abort(403)` raises an exception containing `Werkzeug abort 403`.",
  "condition": "`has_app_context` is false, `code=403`, `args=()`, `kwargs={}`, and the masked `_wz_abort` raises the asserted message.",
  "status": "candidate",
  "evidence_ids": [
    "EV_PARAMS",
    "EV_ASSERT_NOCTX",
    "EV_MUT_BRANCH",
    "EV_IMPORTS"
  ]
}
```
**源码证据**：EV_PARAMS → test:11–47；EV_ASSERT_NOCTX → test:126–126；EV_MUT_BRANCH → mut:282–285；EV_IMPORTS → imports:12–16。
**审查判断**：合理候选。
**依据**：test:27–31、126及mut:282–285支持None跳过上下文调用、再由_wz_abort抛出所需消息。
**错误类型/能力缺口与归因**：未发现错误。

## scenario_hypotheses[3]

**Agent原始结论**
```json
{
  "id": "no_ctx_400_args",
  "description": "For `no_app_context_with_args_kwargs`, a plausible path is that `_cv_app.get(None)` returns `None` and `_wz_abort` is called with `400`, `(\"Bad Request\",)`, and `{\"description\": \"Invalid input\"}` before raising `Werkzeug abort 400`.",
  "condition": "`has_app_context` is false, `code=400`, `args=(\"Bad Request\",)`, `kwargs={\"description\": \"Invalid input\"}`, and the masked `_wz_abort` raises the asserted message.",
  "status": "candidate",
  "evidence_ids": [
    "EV_PARAMS",
    "EV_ASSERT_NOCTX",
    "EV_MUT_BRANCH",
    "EV_IMPORTS"
  ]
}
```
**源码证据**：EV_PARAMS → test:11–47；EV_ASSERT_NOCTX → test:126–126；EV_MUT_BRANCH → mut:282–285；EV_IMPORTS → imports:12–16。
**审查判断**：合理候选。
**依据**：test:34–38明确400、位置参数和description；mut:285转发原参数。
**错误类型/能力缺口与归因**：未发现错误。

## scenario_hypotheses[4]

**Agent原始结论**
```json
{
  "id": "app_ctx_418_custom",
  "description": "For `app_context_custom_code`, a plausible path is that the mock context's aborter receives `418` with `kwargs={\"description\": \"I'm a teapot\"}` and raises an exception containing `Aborted with 418`.",
  "condition": "`has_app_context` is true, `code=418`, `args=()`, `kwargs={\"description\": \"I'm a teapot\"}`, and the masked aborter is configured to raise.",
  "status": "candidate",
  "evidence_ids": [
    "EV_PARAMS",
    "EV_MOCK_ASSIGN",
    "EV_ASSERT_APP",
    "EV_MUT_BRANCH"
  ]
}
```
**源码证据**：EV_PARAMS → test:11–47；EV_MOCK_ASSIGN → test:88–89；EV_ASSERT_APP → test:120–120；EV_MUT_BRANCH → mut:282–285。
**审查判断**：合理候选。
**依据**：test:41–45提供418和description，test:120给出消息目标；未把HTTP状态语义扩展成不可见要求。
**错误类型/能力缺口与归因**：未发现错误。

## scenario_hypotheses[5]

**Agent原始结论**
```json
{
  "id": "app_ctx_aborter_returns",
  "description": "For an app-context row, an alternative legal path is that `ctx.app.aborter` returns normally, so `abort` falls through to `_wz_abort`; the visible `Aborted with {code}` assertion could then pass only if `_wz_abort` raises such a message.",
  "condition": "`has_app_context` is true and `ctx.app.aborter` does not raise.",
  "status": "candidate",
  "evidence_ids": [
    "EV_MUT_BRANCH",
    "EV_ASSERT_APP",
    "EV_PARAMS"
  ]
}
```
**源码证据**：EV_MUT_BRANCH → mut:282–285；EV_ASSERT_APP → test:120–120；EV_PARAMS → test:11–47。
**审查判断**：重要的正确替代场景。
**依据**：mut:283之后没有return/else；若aborter正常返回则执行285，且test:120只约束最终异常消息。模型没有被docstring的otherwise误导。
**错误类型/能力缺口与归因**：正确识别异常传播歧义；不是推理错误。

## dependency_constraints[0]

**Agent原始结论**
```json
{
  "statement": "In app-context rows, `_cv_app.get(None)` should yield a non-None context whose `app.aborter` is the assigned `mock_aborter`.",
  "status": "candidate",
  "scenario_ids": [
    "app_ctx_404",
    "app_ctx_500_msg",
    "app_ctx_418_custom",
    "app_ctx_aborter_returns"
  ],
  "condition": "when `has_app_context` is true and the masked setup patches `_cv_app.get`.",
  "evidence_ids": [
    "EV_MOCK_ASSIGN",
    "EV_MUT_BRANCH"
  ]
}
```
**源码证据**：EV_MOCK_ASSIGN → test:88–89；EV_MUT_BRANCH → mut:282–285。
**审查判断**：合理候选。
**依据**：test:88–89显示对象连接，mut:282读取context；但has_app_context并不直接进入MUT，实际get返回值仍由masked设置决定。模型保持candidate。
**错误类型/能力缺口与归因**：未把测试参数当成已知运行时绑定；B仍限制具体补丁定位。

## dependency_constraints[1]

**Agent原始结论**
```json
{
  "statement": "In no-app-context rows, `_cv_app.get(None)` should yield `None` so the aborter branch is skipped and `_wz_abort` is called.",
  "status": "candidate",
  "scenario_ids": [
    "no_ctx_403",
    "no_ctx_400_args"
  ],
  "condition": "when `has_app_context` is false and the masked setup makes `_cv_app.get` return `None`.",
  "evidence_ids": [
    "EV_MUT_BRANCH",
    "EV_PARAMS"
  ]
}
```
**源码证据**：EV_MUT_BRANCH → mut:282–285；EV_PARAMS → test:11–47。
**审查判断**：合理候选。
**依据**：None选择mut:285直接路径，条件明确masked设置该返回值。
**错误类型/能力缺口与归因**：未发现错误。

## dependency_constraints[2]

**Agent原始结论**
```json
{
  "statement": "A plausible setup for app-context rows is that `ctx.app.aborter` raises an exception containing `Aborted with {code}` before execution reaches `_wz_abort`.",
  "status": "candidate",
  "scenario_ids": [
    "app_ctx_404",
    "app_ctx_500_msg",
    "app_ctx_418_custom"
  ],
  "condition": "when `has_app_context` is true and the aborter is configured with that side effect.",
  "evidence_ids": [
    "EV_MUT_BRANCH",
    "EV_ASSERT_APP"
  ]
}
```
**源码证据**：EV_MUT_BRANCH → mut:282–285；EV_ASSERT_APP → test:120–120。
**审查判断**：合理候选而非强制条件。
**依据**：test:120约束消息；aborter抛出满足它但不是唯一来源，模型另列正常返回替代。
**错误类型/能力缺口与归因**：未发现错误。

## dependency_constraints[3]

**Agent原始结论**
```json
{
  "statement": "For no-context rows, satisfying the visible assertion plausibly requires `_wz_abort` to raise an exception containing `Werkzeug abort {code}`.",
  "status": "candidate",
  "scenario_ids": [
    "no_ctx_403",
    "no_ctx_400_args"
  ],
  "condition": "when `has_app_context` is false and `_wz_abort` is the invoked abort dependency.",
  "evidence_ids": [
    "EV_MUT_BRANCH",
    "EV_ASSERT_NOCTX",
    "EV_IMPORTS"
  ]
}
```
**源码证据**：EV_MUT_BRANCH → mut:282–285；EV_ASSERT_NOCTX → test:126–126；EV_IMPORTS → imports:12–16。
**审查判断**：合理候选。
**依据**：在无上下文且正常到达285时，_wz_abort抛所需消息满足test:126；未凭空构造HTTPException种类。
**错误类型/能力缺口与归因**：未发现错误。

## dependency_constraints[4]

**Agent原始结论**
```json
{
  "statement": "Whether app-context rows ever reach `_wz_abort` is not determined because the masked aborter may either raise or return.",
  "status": "unknown",
  "scenario_ids": [
    "app_ctx_404",
    "app_ctx_500_msg",
    "app_ctx_418_custom",
    "app_ctx_aborter_returns"
  ],
  "condition": "when `has_app_context` is true.",
  "evidence_ids": [
    "EV_MUT_BRANCH",
    "EV_ASSERT_APP"
  ]
}
```
**源码证据**：EV_MUT_BRANCH → mut:282–285；EV_ASSERT_APP → test:120–120。
**审查判断**：通过。
**依据**：mut:283没有保证抛出，masked设置不可见，因此是否到达285确实未知。
**错误类型/能力缺口与归因**：Unknown使用正确。

## expected_interactions[0]

**Agent原始结论**
```json
{
  "statement": "`abort` evaluates `_cv_app.get(None)` as the condition for the aborter branch.",
  "status": "required",
  "scenario_ids": [
    "app_ctx_404",
    "app_ctx_500_msg",
    "no_ctx_403",
    "no_ctx_400_args",
    "app_ctx_418_custom",
    "app_ctx_aborter_returns"
  ],
  "condition": "whenever `abort` is called.",
  "evidence_ids": [
    "EV_MUT_BRANCH"
  ],
  "assertion_policy": "not_implied"
}
```
**源码证据**：EV_MUT_BRANCH → mut:282–285。
**审查判断**：通过。
**依据**：mut:282在函数体起始求值_cv_app.get(None)；此声明不要求getter本身一定返回。
**错误类型/能力缺口与归因**：not_implied保持了交互/验证分离。

## expected_interactions[1]

**Agent原始结论**
```json
{
  "statement": "When `_cv_app.get(None)` returns a non-None context, `abort` calls `ctx.app.aborter(code, *args, **kwargs)`.",
  "status": "required",
  "scenario_ids": [
    "app_ctx_404",
    "app_ctx_500_msg",
    "app_ctx_418_custom",
    "app_ctx_aborter_returns"
  ],
  "condition": "when the get result is non-None.",
  "evidence_ids": [
    "EV_MUT_BRANCH",
    "EV_MOCK_ASSIGN"
  ],
  "assertion_policy": "not_implied"
}
```
**源码证据**：EV_MUT_BRANCH → mut:282–285；EV_MOCK_ASSIGN → test:88–89。
**审查判断**：在所述候选对象结构下成立。
**依据**：mut:283支持调用，test:88–89提供app/aborter连接；仅非None的一般对象不必拥有可调用app.aborter。结合已描述的mock context场景合理，通用required条件可补接收者属性访问正常。
**错误类型/能力缺口与归因**：条件精度改进，不计作确定语义错误；B/C，中低置信度。

## expected_interactions[2]

**Agent原始结论**
```json
{
  "statement": "When `_cv_app.get(None)` returns None, `abort` skips the aborter branch and calls `_wz_abort(code, *args, **kwargs)`.",
  "status": "required",
  "scenario_ids": [
    "no_ctx_403",
    "no_ctx_400_args"
  ],
  "condition": "when the get result is None.",
  "evidence_ids": [
    "EV_MUT_BRANCH"
  ],
  "assertion_policy": "not_implied"
}
```
**源码证据**：EV_MUT_BRANCH → mut:282–285。
**审查判断**：通过。
**依据**：mut:282–285中None跳过if体，随后调用_wz_abort，参数来自可见测试。
**错误类型/能力缺口与归因**：not_implied正确。

## expected_interactions[3]

**Agent原始结论**
```json
{
  "statement": "When an app-context aborter returns normally, `abort` then calls `_wz_abort(code, *args, **kwargs)`.",
  "status": "required",
  "scenario_ids": [
    "app_ctx_aborter_returns"
  ],
  "condition": "when `ctx.app.aborter` does not raise.",
  "evidence_ids": [
    "EV_MUT_BRANCH"
  ],
  "assertion_policy": "not_implied"
}
```
**源码证据**：EV_MUT_BRANCH → mut:282–285。
**审查判断**：通过。
**依据**：mut:283正常返回后执行285，没有return/else；明确仅对app_ctx_aborter_returns成立。
**错误类型/能力缺口与归因**：not_implied正确，没有把fallback推广到aborter抛出情形。

## verification_candidates[0]

**Agent原始结论**
```json
{
  "statement": "Consider verifying `mock_aborter` was called with `(code, *args, **kwargs)` in app-context rows.",
  "status": "candidate",
  "scenario_ids": [
    "app_ctx_404",
    "app_ctx_500_msg",
    "app_ctx_418_custom"
  ],
  "condition": "when `has_app_context` is true and the aborter is invoked.",
  "evidence_ids": [
    "EV_MUT_BRANCH",
    "EV_MOCK_ASSIGN",
    "EV_ASSERT_APP"
  ]
}
```
**源码证据**：EV_MUT_BRANCH → mut:282–285；EV_MOCK_ASSIGN → test:88–89；EV_ASSERT_APP → test:120–120。
**审查判断**：有程序依据的可选候选。
**依据**：mut:283参数透传、test:88–89对象赋值、参数表含非空args/kwargs；消息断言未充分检验参数转发，故候选有潜在独立价值。不是现有测试强制要求。
**错误类型/能力缺口与归因**：未发现强制Mock Assertion；模型可更明确说明额外验证价值。

## verification_candidates[1]

**Agent原始结论**
```json
{
  "statement": "Consider verifying `_wz_abort` was called with `(code, *args, **kwargs)` in no-context rows.",
  "status": "candidate",
  "scenario_ids": [
    "no_ctx_403",
    "no_ctx_400_args"
  ],
  "condition": "when `has_app_context` is false and `_cv_app.get(None)` returns None.",
  "evidence_ids": [
    "EV_MUT_BRANCH",
    "EV_ASSERT_NOCTX"
  ]
}
```
**源码证据**：EV_MUT_BRANCH → mut:282–285；EV_ASSERT_NOCTX → test:126–126。
**审查判断**：有程序依据的可选候选。
**依据**：mut:285与含args/kwargs的无上下文行支持检查参数转发；不应将候选推广至所有实现。
**错误类型/能力缺口与归因**：未发现强制Mock Assertion。

## verification_candidates[2]

**Agent原始结论**
```json
{
  "statement": "Consider verifying `ctx.app.aborter` was not called when `_cv_app.get(None)` returns None.",
  "status": "candidate",
  "scenario_ids": [
    "no_ctx_403",
    "no_ctx_400_args"
  ],
  "condition": "when the get result is None.",
  "evidence_ids": [
    "EV_MUT_BRANCH"
  ]
}
```
**源码证据**：EV_MUT_BRANCH → mut:282–285。
**审查判断**：有条件的可选候选。
**依据**：None时跳过283在源码中明确；可用于分支隔离检查，但测试当前只观察异常，是否需要新增交互断言须研究者决定。
**错误类型/能力缺口与归因**：非错误要求；存在实现耦合取舍。

## verification_candidates[3]

**Agent原始结论**
```json
{
  "statement": "Consider verifying `_wz_abort` was not called on app-context rows where `ctx.app.aborter` raises.",
  "status": "candidate",
  "scenario_ids": [
    "app_ctx_404",
    "app_ctx_500_msg",
    "app_ctx_418_custom"
  ],
  "condition": "when `ctx.app.aborter` raises before the fallback line.",
  "evidence_ids": [
    "EV_MUT_BRANCH",
    "EV_ASSERT_APP"
  ]
}
```
**源码证据**：EV_MUT_BRANCH → mut:282–285；EV_ASSERT_APP → test:120–120。
**审查判断**：有条件的可选候选。
**依据**：aborter抛出时传播退出函数，285不可达；条件未错误泛化到所有有上下文行。
**错误类型/能力缺口与归因**：未发现强制Mock Assertion。

## verification_candidates[4]

**Agent原始结论**
```json
{
  "statement": "Consider verifying `_cv_app.get` was called with `None`.",
  "status": "candidate",
  "scenario_ids": [
    "app_ctx_404",
    "app_ctx_500_msg",
    "no_ctx_403",
    "no_ctx_400_args",
    "app_ctx_418_custom"
  ],
  "condition": "whenever `abort` is called.",
  "evidence_ids": [
    "EV_MUT_BRANCH"
  ]
}
```
**源码证据**：EV_MUT_BRANCH → mut:282–285。
**审查判断**：依据较弱的可选候选。
**依据**：mut:282确有get(None)，但为何需要验证这个实现细节而不只验证行为未说明；candidate不等于错误的强制要求。
**错误类型/能力缺口与归因**：A的价值门槛不明确或D论证不足，中置信度；不据此判作语义失败。

## uncertainty[0]

**Agent原始结论**
```json
{
  "statement": "The exact mock patches, return values, side effects, and verification assertions are hidden in the `mock_stub_*` and `verify_*` slots.",
  "status": "unknown",
  "scenario_ids": [
    "app_ctx_404",
    "app_ctx_500_msg",
    "no_ctx_403",
    "no_ctx_400_args",
    "app_ctx_418_custom",
    "app_ctx_aborter_returns"
  ],
  "condition": null,
  "evidence_ids": [
    "EV_MOCK_SLOT",
    "EV_VERIFY_SLOT"
  ]
}
```
**源码证据**：EV_MOCK_SLOT → test:61–63；EV_VERIFY_SLOT → test:106–108。
**审查判断**：语义合理，但引用证据错误。
**依据**：masked内容确实不可见；EV_MOCK_SLOT却把66–68的文本引用到61–63（空白行）。EV_VERIFY_SLOT正确。不得修补原始响应后宣称通过。
**错误类型/能力缺口与归因**：源码定位/精确引用错误；D执行失误或C缺确定性定位工具，高置信度；非B，正确原文已在输入中，非Prompt缺少规则。

## uncertainty[1]

**Agent原始结论**
```json
{
  "statement": "Whether `Aborted with {code}` is raised by `ctx.app.aborter` before `_wz_abort` or by `_wz_abort` after an aborter return is not determined by the visible text.",
  "status": "unknown",
  "scenario_ids": [
    "app_ctx_404",
    "app_ctx_500_msg",
    "app_ctx_418_custom",
    "app_ctx_aborter_returns"
  ],
  "condition": "when `has_app_context` is true.",
  "evidence_ids": [
    "EV_ASSERT_APP",
    "EV_MUT_BRANCH"
  ]
}
```
**源码证据**：EV_ASSERT_APP → test:120–120；EV_MUT_BRANCH → mut:282–285。
**审查判断**：通过。
**依据**：正确识别test:120不决定异常来源，mut:283正常返回仍可到285。
**错误类型/能力缺口与归因**：Unknown正确，没有假造唯一场景。

## uncertainty[2]

**Agent原始结论**
```json
{
  "statement": "The exact patch or side effect of `_wz_abort` for no-context rows is not visible; only the expected message is asserted.",
  "status": "unknown",
  "scenario_ids": [
    "no_ctx_403",
    "no_ctx_400_args"
  ],
  "condition": "when `has_app_context` is false.",
  "evidence_ids": [
    "EV_ASSERT_NOCTX",
    "EV_MUT_BRANCH",
    "EV_IMPORTS"
  ]
}
```
**源码证据**：EV_ASSERT_NOCTX → test:126–126；EV_MUT_BRANCH → mut:282–285；EV_IMPORTS → imports:12–16。
**审查判断**：通过。
**依据**：test:126只约束消息，具体patch和side_effect均不可见。
**错误类型/能力缺口与归因**：B输入范围限制，而非模型错误。

## uncertainty[3]

**Agent原始结论**
```json
{
  "statement": "Whether `_cv_app.get` is explicitly patched to return `None` versus context absence arising otherwise is not visible.",
  "status": "unknown",
  "scenario_ids": [
    "no_ctx_403",
    "no_ctx_400_args"
  ],
  "condition": null,
  "evidence_ids": [
    "EV_MUT_BRANCH",
    "EV_PARAMS",
    "EV_MOCK_SLOT"
  ]
}
```
**源码证据**：EV_MUT_BRANCH → mut:282–285；EV_PARAMS → test:11–47；EV_MOCK_SLOT → test:61–63。
**审查判断**：语义合理，但部分证据错误。
**依据**：masked区域未展示get如何得到None，显式patch与自然缺少上下文不能区分；EV_MOCK_SLOT行号错误，其他mut/params引用正确。
**错误类型/能力缺口与归因**：同一EV_MOCK_SLOT引用错误影响第二条，不重复计作独立根因；D/C。

## 遗漏与总体判断

模型正确覆盖五个参数行并补充aborter正常返回的关键替代路径，异常传播推理明显优于“有上下文则必不调用fallback”的单路径解读。
它没有穷举任意被替换的get本身抛出目标消息等场景；宽松pytest.raises理论上容许更多异常来源，但不能因此强制枚举所有任意mock行为。应明确候选集合非穷尽，并检查测试标志到运行时context绑定仍未知。
主要已证实失败是源码行号引用错误，而不是异常传播理解错误；应优先考虑确定性源码定位/引用工具，不凭两例结果主张加入AST即可解决全部问题。

## 原始引文与实际源码对照

### EV_MUT_BRANCH — mut:282–285
精确匹配：True
模型原引文：
```
    if (ctx := _cv_app.get(None)) is not None:
        ctx.app.aborter(code, *args, **kwargs)

    _wz_abort(code, *args, **kwargs)
```

### EV_IMPORTS — imports:12–16
精确匹配：True
模型原引文：
```
from werkzeug.exceptions import abort as _wz_abort
from werkzeug.utils import redirect as _wz_redirect
from werkzeug.wrappers import Response as BaseResponse

from .globals import _cv_app
```

### EV_PARAMS — test:11–47
精确匹配：True
模型原引文：
```
@pytest.mark.parametrize("scenario,has_app_context,code,args,kwargs", [
    (
        "app_context_exists_404",
        True,
        404,
        (),
        {}
    ),
    (
        "app_context_exists_500_with_message",
        True,
        500,
        ("Internal Server Error",),
        {}
    ),
    (
        "no_app_context_403",
        False,
        403,
        (),
        {}
    ),
    (
        "no_app_context_with_args_kwargs",
        False,
        400,
        ("Bad Request",),
        {"description": "Invalid input"}
    ),
    (
        "app_context_custom_code",
        True,
        418,
        (),
        {"description": "I'm a teapot"}
    ),
])
```

### EV_MOCK_ASSIGN — test:88–89
精确匹配：True
模型原引文：
```
        mock_ctx.app = mock_app
        mock_app.aborter = mock_aborter
```

### EV_RAISE_CALL — test:102–103
精确匹配：True
模型原引文：
```
    with pytest.raises(Exception) as exc_info:
        abort(code, *args, **kwargs)
```

### EV_IF_APP — test:111–111
精确匹配：True
模型原引文：
```
    if has_app_context:
```

### EV_ASSERT_APP — test:120–120
精确匹配：True
模型原引文：
```
        assert f"Aborted with {code}" in str(exc_info.value)
```

### EV_ELSE — test:121–121
精确匹配：True
模型原引文：
```
    else:
```

### EV_ASSERT_NOCTX — test:126–126
精确匹配：True
模型原引文：
```
        assert f"Werkzeug abort {code}" in str(exc_info.value)
```

### EV_MOCK_SLOT — test:61–63
精确匹配：False
模型原引文：
```
    # === PY-SLOT-START:mock_stub_001 ===
    pass
    # === PY-SLOT-END:mock_stub_001 ===
```
实际源行（JSON表示，以显示空白）：
```json
"                                     \n                                    \n       "
```
对应slot原文实际位于test:66–68。只在审查中指出，不修改生成结果。

### EV_VERIFY_SLOT — test:106–108
精确匹配：True
模型原引文：
```
    # === PY-SLOT-START:verify_001 ===
    pass
    # === PY-SLOT-END:verify_001 ===
```
