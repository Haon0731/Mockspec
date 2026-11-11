import python
import semmle.python.dataflow.new.DataFlow
import semmle.python.dataflow.new.internal.Attributes
import semmle.python.types.Exceptions

predicate inFunction(DataFlow::Node node, string name) {
  node.getScope().(Function).getName() = name
}

predicate unknownFill(DataFlow::MethodCallNode call) {
  call.getMethodName() = "fill" and
  call.getScope().(Function).getName() in ["caught_and_finally", "unknown_write"]
}

predicate sameLocalOrigin(DataFlow::Node left, DataFlow::Node right) {
  left.getALocalSource() = right.getALocalSource()
}

from string relation, string modality, string functionName, DataFlow::Node evidence,
  DataFlow::Node observed
where
  exists(DataFlow::AttrWrite write, DataFlow::AttrRead read |
    write.getAttributeName() = "ready" and read.getAttributeName() = "ready" and
    inFunction(write, functionName) and inFunction(read, functionName) and
    functionName in ["visible_write", "proven_fresh_write"] and
    sameLocalOrigin(write.getObject(), read.getObject()) and
    write.asCfgNode().strictlyReaches(read.asCfgNode()) and
    relation = "visible_write_to_read" and modality = "backend_may" and
    evidence = write and observed = read
  )
  or
  exists(DataFlow::MethodCallNode call, DataFlow::AttrRead read |
    unknownFill(call) and read.getAttributeName() = "ready" and
    inFunction(read, functionName) and call.getScope().(Function).getName() = functionName and
    sameLocalOrigin(call.getArg(0), read.getObject()) and
    call.asCfgNode().strictlyReaches(read.asCfgNode()) and
    relation = "unknown_call_may_write_observed_object" and
    modality = "conservative_unknown" and evidence = call and observed = read
  )
  or
  exists(DataFlow::MethodCallNode call |
    unknownFill(call) and functionName = call.getScope().(Function).getName() and
    relation = "unknown_return_may_alias_argument" and
    modality = "conservative_unknown" and evidence = call and observed = call.getArg(0)
  )
  or
  exists(DataFlow::MethodCallNode call |
    unknownFill(call) and call.asCfgNode().(RaisingNode).raisesUnknownType() and
    functionName = call.getScope().(Function).getName() and
    relation = "unknown_call_may_raise" and modality = "backend_may" and
    evidence = call and observed = call
  )
  or
  exists(DataFlow::MethodCallNode call, Name returnedUse |
    unknownFill(call) and call.getScope().(Function).getName() = "unknown_write" and
    returnedUse.getId() = "returned" and returnedUse.getCtx() instanceof Load and
    returnedUse.getScope() = call.getScope() and
    DataFlow::localFlow(call, DataFlow::exprNode(returnedUse)) and
    relation = "unknown_return_value_reaches_return_use" and modality = "backend_may" and
    functionName = "unknown_write" and evidence = call and
    observed = DataFlow::exprNode(returnedUse)
  )
  or
  exists(DataFlow::MethodCallNode call, DataFlow::AttrRead read |
    unknownFill(call) and call.asCfgNode().(RaisingNode).raisesUnknownType() and
    read.getAttributeName() = "ready" and
    inFunction(read, functionName) and call.getScope().(Function).getName() = functionName and
    sameLocalOrigin(call.getArg(0), read.getObject()) and
    call.asCfgNode().strictlyReaches(read.asCfgNode()) and
    relation = "unknown_call_may_write_before_raise" and
    modality = "conservative_unknown" and evidence = call and observed = read
  )
select relation, modality, functionName, evidence, observed,
  evidence.getLocation().getStartLine(), observed.getLocation().getStartLine()
