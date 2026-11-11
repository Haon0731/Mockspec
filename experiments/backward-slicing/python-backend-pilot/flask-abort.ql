import python
import semmle.python.types.FunctionObject
import DemandRoots

predicate helperAbort(FunctionObject callee) {
  DemandRoots::selectedProductFunction(callee.getFunction())
}

predicate namedCall(Call call, string name) {
  call.getFunc().(Name).getId() = name
  or
  call.getFunc().(Attribute).getName() = name
}

from string relation, AstNode source, AstNode target, string detail
where
  exists(FunctionObject callee, ControlFlowNode callNode, Call call |
    helperAbort(callee) and callee.getACall() = callNode and callNode.getNode() = call and
    DemandRoots::selectedTestCall(call) and
    namedCall(call, "abort") and
    relation = "test_call_resolves_to_helper_abort" and source = call and
    target = callee.getFunction() and detail = callee.descriptiveString()
  )
  or
  exists(Call aborter, Call fallback, ControlFlowNode aborterNode, ControlFlowNode fallbackNode |
    namedCall(aborter, "aborter") and namedCall(fallback, "_wz_abort") and
    aborter.getScope().(Function).getName() = "abort" and
    fallback.getScope().(Function).getName() = "abort" and
    DemandRoots::selectedProductFunction(aborter.getScope().(Function)) and
    aborterNode.getNode() = aborter and fallbackNode.getNode() = fallback and
    aborterNode.strictlyReaches(fallbackNode) and
    relation = "aborter_normal_path_reaches_fallback" and source = aborter and target = fallback and
    detail = "normal CFG reachability"
  )
select relation, source, target, detail,
  source.getLocation().getFile().getRelativePath(), source.getLocation().getStartLine(),
  target.getLocation().getFile().getRelativePath(), target.getLocation().getStartLine()
