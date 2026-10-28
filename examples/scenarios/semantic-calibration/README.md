# 首轮 Spec 语义校准参考卡

状态：合成参考草案；真实输入适配部分也是手写参考规约草案。
所有结论均为静态评阅，不是模型运行、人工确认、金标准或目标测试通过证据。
读者是研究者和消费接口评阅者；语义约定唯一归属为
[scenario-inference.md](../../../docs/scenario-inference.md#首轮-spec-语义校准研究草案)。
本目录保存可复核小样例，不复制数据集，也不产生实际 Mock。

起点 HEAD：`3776c00158d6c2487a0c6e01ed52cf13a12636cc`。
开始时已有 `docs/README.md`、`docs/workspace-exploration-prd.md` 修改及
`out/live_inference/step6-live/ledger.json.lock` 未跟踪；均保留，不作历史运行依据。
复用正式 Claim/Evidence、旧 Python 合成契约的分支分析、PRD 缓存示例和
`tests/fixtures/query_recovery_semantic_review.json` 的 import/声明证据边界。
新增内容补足关系多解与必要常量对照、状态历史范围、未知可探索性和消费充分性。

## 阅读与复现

A–F 每卡的可见材料都在下文明确给出；`synthetic.json` 保存对应的微型 UnifiedTask
和正式契约投影（C 有读取前/后两份，共七份），不声称经过真实 Adapter 准入。
`flask.contract.json` 仅引用现有审核任务，检查时重新走 Python Adapter，缺材料即失败。
`check.py` 只调用现有 `UnifiedTask.from_dict` 和 `validate_contract` 做结构、身份、
引用、原文检查，不实现语义验证器，不执行材料中的代码。

```bash
PYTHONPATH=src .venv/bin/python examples/scenarios/semantic-calibration/check.py
PYTHONPATH=src .venv/bin/python examples/scenarios/semantic-calibration/check.py --real
.venv/bin/pytest -q tests/test_scenario.py
```

真实检查根与上级 README 的 Python 根相同；可用 `--snapshot` 显式指定同一审核身份的
冻结根，不能换为归档或修改准入。检查输出不覆盖任何旧运行。

## A — 关系多值（合成参考草案）

- **可见输入/范围 A0**：Python 整数运算；依赖 `left(7)` 和 `right(7)` 返回普通正整数
  （不含 bool、自定义运算对象）；只允许替换这两个调用的响应，无副作用。
  MUT：`p=left(7); q=right(7); return p*q`。测试固定 `assert target()==100`。
- **原目标/条件**：目标为正常返回 100；适用条件为本次固定输入 7 及 A0 替换边界，
  不以结果已满足作为前提，不扩展到其他参数或调用历史。
- **认可规约**：A1：该路径下两个调用参数均为 7、返回正整数 p/q 且 `p*q=100`，无异常。
  依据：参数/乘积是实现事实；100 是测试要求；正整数和替换范围是显式任务前提；
  等式是这些前提的推导。没有附加假设。
- **行为实例**：允许 `(1,100)`、`(4,25)`、`(10,10)` 以及交换组合，属于同一关系。
  排除 `(4,20)`、负数对、异常；排除“p、q 各自任意正整数”这种丢失关联的规约，
  也排除“必须 (10,10)”这种过强规约。
- **未知/影响**：其他参数、额外调用历史没有被判断，不授权任意变化；不阻塞本次交互。
- **交付**：就声明的本次交互而言关键依赖需求覆盖充分，可供参考消费；不是完整 API 规范。
  不新增调用次数 verify，程序出现两个调用不等于测试要求验证两次。

## B — 必要固定值（合成参考草案）

- **可见输入/范围 B0**：沿用 A 的普通正整数/无副作用前提，仅 `left(7)` 可替换；
  MUT：`p=left(7); return p*2`；固定断言 100。
- **原目标/条件**：目标为正常返回 100；适用条件为固定 7 及 B0 替换边界。
- **认可规约**：B1：该条件下 `left(7)` 正常返回 **50**。依据：乘以 2 的实现事实、
  精确 100 的测试要求与整数算术推导；无附加假设。
- **行为实例**：允许返回普通整数 50；排除 49、51、异常，排除仅写“正整数”而漏掉 50。
  内部实现形式只要保持已声明无副作用且返回 50 即不受此规约限制。
- **未知/影响**：其他输入和调用历史不作判断，不阻塞当前使用。
- **交付**：当前交互覆盖充分。与 A 成对检查：关系抽象不能把有据常量抽象掉。

## C — 可继续取证（合成参考草案）

- **可见输入/范围 C0**：`from helper import transform`；MUT `return transform(dep(7))`，
  测试 `assert target()==100`；dep 返回普通正整数，无副作用，仅 dep 可替换。
  宿主明确允许定位并读取 `helper.py`；transform 必须保留真实行为。
- **原目标/条件**：目标为正常返回 100；适用条件为固定 7、仅替换 dep 并保持真实 transform。
- **读取前规约 C1**：dep(7) 正常返回正整数 p；必须满足真实 `transform(p)=100`。
  依据为测试要求、调用/参数实现事实和显式保真边界的推导；并未取得 transform 的行为。
- **读取前未知**：transform 的映射决定可配置 p，阻塞具体消费；授权渠道仍可用。
  仅 import、`def transform(p): ...` 或路径命中均不能消除此未知。
  允许实例尚无法确定；排除替换 transform 为常量、把“未读”说成“不可探索”。
- **读取后新增材料 C2**：实际返回的 helper 源码为 `def transform(p): return p*2`。
  这是独立合成的“已读视图”，不是伪造工具回执；前视图不含该源码。
- **读取后规约 C3**：在同一条件下 dep(7) 返回 50。依据 C1 + C2 的实现事实与算术推导。
  允许 50；排除 49、任意正整数及 helper 被替换。无附加假设。
- **未知/交付**：读取前有价值但不充分，应继续取证；读取后本次交互充分。
  两个视图均不扩展到其他输入/历史；这些范围外未知不阻塞当前使用。

## D — 场景分支（合成参考草案）

- **可见输入/范围 D0**：固定 k=7；MUT `v=cache.get(7); return v if v is not None else repo.fetch(7)`；
  测试只断言返回 100。两处依赖可替换，响应为普通整数或 None，无副作用；未指定必须回源。
- **原目标/条件**：本次返回 100。D-hit 条件 `cache.get(7) != None`；D-miss 条件等于 None。
- **认可规约**：D1：hit 下 get 返回 100，fetch 不在执行路径上；D2：miss 下 get 返回 None，
  fetch(7) 返回 100。依据均为可见分支实现 + 测试目标的推导。无附加假设。
- **行为实例**：允许 hit/100 或 miss/None→100，作为两个替代候选；排除同时要求 get 返回
  None 和 100、miss 下 fetch 返回 99。不能把 repo 必须被调用加到 hit 条件中。
- **未知/影响**：原目标不要求固定命中或未命中；在 D0 已明确两处依赖均可替换、无副作用，
  且两条分支各自有正向实现与测试依据，因此这里是确认允许的替代，不是未消除的场景歧义。
  hit 下没有发生 fetch 不授权任意的未来 fetch 行为，也不自动产生禁止调用的 verify。
- **交付**：当前协议映射为 ALTERNATIVES、selected=null；生成器可选择一条完整分支并遵守其
  全部约束，不能合取两支或只抽取更容易实现的局部要求。早期草案曾因单一选择约定映射为
  AMBIGUOUS；该判断已被当前分支语义取代，历史 JSON 投影仍仅用于结构参考。

## E — 状态关系（合成参考草案）

- **可见输入/范围 E0**：MUT `store.put('k',7); return store.get('k')`，测试断言 7。
  宿主提供 API 语义：本次顺序交互内，put(k,v) 正常完成后、无中间写入时，get(k) 返回 v；
  put 的返回值不使用。两调用可替换，不改 MUT/test；无并发，无其他副作用。
- **原目标/条件**：只针对本次 `put('k',7)`→`get('k')`；不覆盖任意重写、多键或无限历史。
- **认可规约**：E1：put('k',7) 正常完成；随后 get('k') 正常返回对应写入值 7。
  依据：参数与次序是实现事实，读写关系是明确提供的 API 语义，最终 7 是测试要求。
  返回值和写值必须关联，不只是碰巧返回测试常量。无附加假设。
- **行为实例**：在这个固定交互内，用状态存储或预设的交互响应都可满足关系；put 返回 None
  或 0 均可（任务明确不使用且无其他影响）。排除 get 返回 8 或 put 抛出异常。
  不要求字典、真实数据库或特定内部实现。预设响应只在本次交互内认可，不声称一般读写正确。
- **未知/影响**：不同 key、重复写入、并发一致性未判断，不作为允许变化；不阻塞当前范围。
- **交付**：当前交互覆盖充分；路径次序作为关系前提保留，不自动转成 inOrder/次数 verify。

## F — 材料不足（合成参考草案）

- **可见输入/范围 F0**：MUT `return sealed_transform(dep(7))`，测试断言 100；dep 返回普通正整数、
  无副作用，只有 dep 可替换。关键 sealed_transform 必须保留真实行为。
  **宿主明确范围声明**：其实现及行为文档均不在获准材料内，本次禁止额外读取、网络获取或执行取证，
  当前没有获准渠道。该限制不是从初始 sources 缺项推断的。
- **原目标/条件**：目标为正常返回 100；适用条件为固定 7、F0 宿主范围及保留真实转换。
- **认可规约 F1**：dep(7) 返回正整数 p，且真实 `sealed_transform(p)=100`；
  依据为参数/调用实现事实、原断言及保真范围的推导；未声称存在这样的 p。
- **行为实例**：无法给出已知合法具体值；排除把转换替换为常量 100 或跳过转换。
  p=50、p=100 以及是否存在解均无法判断，不以“任意正整数”填空。
- **未知/影响**：缺转换语义，阻塞输入选择与可满足性判断；本次授权范围明确不可继续取证。
- **交付**：保留有价值的关系片段，INCONCLUSIVE；可以停止调查，不代表可下发生成。
  需要研究者另行获得授权材料后才能重新评估，本轮不扩大准入。

## R — flask_308 获准真实输入适配（手写参考草案）

本轮实际经 `mock_spec.adapters.python.load_task` 读取审核冻结根，成功保留原 Case ID、
masking 与完整 126 行测试，只通过 task 的 mut/test/imports source_id 分析。
没有读取隐藏 Mock、归档候选、评分答案或重新批准哈希；没有调用模型或执行目标测试。
复用旧 `python.synthetic.json` 的条件路径，另存本轮草案而不重写旧结果。

- **范围/输入**：五行参数化分别固定 `(True,404,(),{})`、
  `(True,500,('Internal Server Error',),{})`、`(False,403,(),{})`、
  `(False,400,('Bad Request',),{'description':'Invalid input'})`、
  `(True,418,(),{'description':"I'm a teapot"})`，来源 test:11–47。
  已有六个 mock_stub 槽及四个 verify 槽为授权编辑边界；普通 setup、调用、断言及 MUT 不可改。
  本轮仅用初始材料；未读取依赖完整语义不等于宿主禁止继续探索。
- **原目标 R0**：每行 `abort(code,*args,**kwargs)` 抛出 Exception 或其子类的实例；
  True 行消息含 `Aborted with {code}`，False 行含 `Werkzeug abort {code}`。
  来源 test:102–103、120、126；是子串要求而非整条消息相等。
- **R1 条件关系**：若 `_cv_app.get(None)` 返回 None，则调用 `_wz_abort(code,*args,**kwargs)`；
  在无上下文候选且 False 行下，该调用须抛出符合 R0 的异常。依据 mut:282–285 的事实与 R0 推导。
- **R2 条件关系**：若 get 返回 ctx、ctx.app 为可见 mock_app、其 aborter 为 mock_aborter，
  且该 aborter 抛出，则原参数透传、异常传播，不到达 `_wz_abort`；在 True 行下异常满足 R0。
  依据 mut:282–285、test:86–89 和 R0。ctx 返回绑定是候选前提，不冒充隐藏 setup 事实。
- **R3 条件关系**：同样的 ctx 前提下若 aborter 正常返回，则继续 `_wz_abort`；
  在 True 行下该后续调用必须抛出满足 R0 的异常。依据 mut:283–285 与 R0，
  不能因为函数注解 NoReturn 或 import 就删去可见的 fallthrough。
- **依据/假设**：R0 是测试要求；分支和参数传递是实现事实；异常响应关系是推导。
  将 has_app_context 与 lookup 实际结果对应是候选场景假设，mask 未证明这种装配；
  相关调用可在获准槽内被合法替换是消费前提，具体绑定和依赖 API 还未核实。
  这些前提写在条件/uncertainty 中，不仅留在日志。
- **行为实例**：在 R2 的装配前提成立时，404 行返回异常 `Exception('Aborted with 404')`
  或 `ValueError('prefix Aborted with 404 suffix')` 均满足此异常关系；不声称已经构造可运行 Mock。
  排除正常返回到测试、错误 code 消息、丢失 args/kwargs，或强制消息完全相等。
  R2 与 R3 是替代场景，不应同时要求 aborter 抛出且正常返回。
- **未知/影响**：实际 lookup 装配、可替换调用绑定、app aborter 与 fallback 的行为限制尚未确认，
  影响能否实现 R1–R3，属阻塞项。没有额外行为限制时具体异常类型和消息前后缀允许变化，
  但不能扩大为任意异常或任意状态副作用。其他 API/调用历史未判断。
- **交付**：真实材料适配完成，当前只有有用的条件约束，尚不充分支持单一场景生成。
  映射为 INCONCLUSIVE 的评阅判断；不制造运行记录或 SELECTED。
  正式草案可容纳三条路径，当前研究协议也不再限制为两个候选；这些路径仍受绑定和关键依赖
  语义未知阻塞，因此保持 INCONCLUSIVE，而不是为了通过格式删减路径。

## 研究记录投影与消费检查

以下为字段级适配表，不是伪造的 inference_record 运行产物。
每卡 A1/B1/C1/C3/D1/D2/E1/F1/R0–R3 是评阅锚点。合成投影按各卡约束顺序对应；
R0 对应两条 test_objectives，R1 对应 dependency_constraints[0:2]，R2 对应 [2:4]，
R3 复用绑定前提 [2] 并对应后续异常关系 [4]（索引从 0 开始，切片右端不含）。

| 卡 | 正式字段投影 | 需配套的已有研究字段 | 缺口/研究交付判断 |
| --- | --- | --- | --- |
| A | condition + `p*q=100`，证据包含域/测试/MUT | allowed_variations=正因子组合；claim_basis=推导；nonblocking_unknowns=范围外历史 | 关系可表达，当前范围充分；status 不证明算术或覆盖 |
| B | condition + `p=50` | allowed_variations=实现形式；claim_basis=推导 | 不得用“可变值”删 50；当前范围充分 |
| C 前 | condition + transform(p)=100；uncertainty=语义未读 | unresolved=选 p 被阻塞；query/stop 信息=授权可读 | INCONCLUSIVE；结构有效不说明取得行为证据 |
| C 后 | 新增 helper evidence；p=50 | claim_basis=新实现事实支持推导；allowed_variations=实现形式 | 当前范围充分；仍非真实查询轨迹 |
| D | 两个 hypothesis 和各自条件约束 | result_status=ALTERNATIVES，selected=null；每支分别标 allowed 并绑定前提 | 当前条件下可选一条完整分支；历史投影没有新研究语义字段，不冒充当前运行产物 |
| E | condition 限定单次读写；statement 写值关联 | allowed_variations=状态/预设实现、put 返回值；basis=API 事实 | 当前范围充分；不能由文字推断一般状态机完整性 |
| F | sealed_transform(p)=100；uncertainty=映射/可满足性 | unresolved=输入选择阻塞；stop rationale=宿主明确无渠道 | INCONCLUSIVE；没有合法实例，不捏造 allowed_variations |
| R | R0–R3、三条候选与绑定 unknown | claim_basis 区分候选假设；unresolved=绑定/语义；allowed_variations 附前提 | INCONCLUSIVE；阻塞原因是绑定和依赖语义未知，不是候选数量 |

A/B/C后/E 的“充分”是本卡限定输入下的语义评阅，不是一次 SELECTED 运行记录。
本目录没有用 statement 替代 allowed_variations/假设/未知字段的消费责任。

## 逐项语义评阅（作者自查，另经独立自动审阅）

| 卡 | 对准目标 | 条件/关系依据 | 关键遗漏 | 过强/过弱 | 内部冲突 | 未知诚实 | 交付匹配 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | 固定100/7 | 正整数乘积 | 保留乘积关联 | 未固定因子对 | 同关系多解 | 历史未判断 | 当前交互充分 |
| B | 固定100/7 | 乘2反推50 | 精确值保留 | 不放宽50 | 与A有意不同 | 历史未判断 | 当前交互充分 |
| C | 保留transform | 前后证据分开 | 前视图缺映射已明示 | 未读前不猜50 | 不混前后视图 | 可读不等于已读 | 前不足/后充分 |
| D | 未添加回源目标 | 两支均有正向依据 | 不增加调用断言 | 不合取两支 | None/非None分开 | 范围外行为不授权变化 | ALTERNATIVES 可选完整分支 |
| E | 固定读写7 | 显式API语义 | 保留写→读关系 | 不指定内部实现 | 单次历史一致 | 不泛化并发 | 当前交互充分 |
| F | 转换保持真实 | 显式宿主范围 | 缺转换语义已明示 | 不编造解 | 不声称可满足 | 阻塞且无渠道 | 仅部分约束 |
| R | 五行原断言 | 事实/推导/假设分列 | 装配/依赖语义待查 | 子串不收紧为相等 | 抛出/返回分支分开 | 不称不可探索 | 部分规约未充分 |

结构检查与独立自动评阅结果见下方完成记录；均不作为人工确认。

## 仍待研究者决定与下一步

1. 关键需求覆盖应如何显式评阅、哪些假设必须先核实；不能从 required、SELECTED 或 ALTERNATIVES 自动推定。
2. 范围授权摘要、假设与正式 Claim 的绑定是否需要进一步机器化；当前研究规格已有独立字段，正式 v0.1 Schema 保持兼容。
3. flask_308 下一次授权取证应先确认 lookup/替换绑定和关键依赖语义，再判断候选范围；
   本轮并未耗尽授权仓库，也未证明某条分支可实际生成。

后续研究入口已在独立 `dependency_spec.json` 中把关键依赖决定的前提、允许变化、阻塞未知和
目标覆盖显式关联，同时保持正式 v0.1 Schema 不变。本目录早于该消费规格，保存的是正式契约
投影和人工语义参考；不能把这些投影反向冒充新规格或模型运行，也不能声称字段完整能自动证明语义。

新增 check.py 的用途仅是复现本目录的七个合成输入投影及一个审核真实输入投影。
已有 CLI 的真实 Adapter 入口不能加载这些微型合成 task，故使用这个局部薄包装，
核心验证仍全部复用现有 validate_contract；没有增加生产入口或通用语义验证器。


## 本轮检查与修正记录（2026-10-27）

结构检查：`check.py` 七个合成视图 PASS；`check.py --real` 经现有 Adapter 加载
flask_308 并验证本轮草案 PASS（无跳过）。`tests/test_scenario.py` 为 19 passed，
包含非法引用/身份/状态拒绝和冻结 JSON Schema 一致性检查。
新增脚本 Ruff 通过；`MYPYPATH=src .venv/bin/mypy
examples/scenarios/semantic-calibration/check.py --follow-imports=skip` 通过；
`git diff --check` 通过。此次类型检查仅覆盖新增脚本，不宣称全仓类型检查。
新涉及指南/样例的 8 个本地链接路径存在，未自动检查 Markdown 锚点。
这些检查只确认结构、身份与来源，不证明语义充分或目标测试通过。

独立自动审阅（独立 Codex 子代理，只读）提出三项具体分歧，作者接受并修正：

| 问题 | 影响 | 修正 |
| --- | --- | --- |
| A/B 的正常完成、C/F 的返回100写入 JSON condition | 将待保证结果当前提，弱化甚至循环化约束 | condition 只保留输入/分支/替换范围，结果保留在目标/约束；同时清理 D 通用条件 |
| task 被描述为完整承载替换范围 | 槽位位置和空接口表可能被误当调用替换许可 | 消费约定改为宿主明确范围；字段表新增实际缺口，R 保留绑定阻塞 |
| R “Exception 子类”与 Exception 实例措辞不一致 | 合法实例与类型要求读法冲突 | 明确为 Exception 或其子类的实例 |

修正后重新运行七个合成与一个真实草案的结构校验，全部通过。
语义结论仍按当时评阅表限定：A/B/C后/E 在声明范围内充分；C前/F/R 是有用但不充分的部分规约；
D 当时结论为缺少选择依据。该 D 结论后来由当前 Q2/ALTERNATIVES 分支语义取代，见上方现行说明；
此处保留 2026-10-27 复核原貌。独立自动审阅不构成人工确认，后续研究者仍需裁定覆盖规则。
复用的四条 query_recovery_semantic_review 既有材料仍支持 import/声明不构成行为证据的
评阅原则；本轮未把它们包装成新的真实运行或自动语义判定器。

独立自动审阅者随后进行了针对性复核，确认以上三项问题均已消除，
未发现这些修正引入的新矛盾；该复核仍不是人工确认。
