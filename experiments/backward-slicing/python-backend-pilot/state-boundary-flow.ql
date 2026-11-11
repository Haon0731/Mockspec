import python
import semmle.python.dataflow.new.DataFlow
import semmle.python.dataflow.new.internal.Attributes
import DemandRoots

/** A call through an injected dependency parameter whose implementation is unavailable. */
predicate opaqueBoundaryCall(DataFlow::MethodCallNode call) {
  exists(Function f, int i |
    f = call.getScope() and
    call.getObject().getALocalSource() = DataFlow::parameterNode(f.getArg(i))
  )
}

predicate uniqueSameRegion(DataFlow::Node left, DataFlow::Node right) {
  left.getALocalSource() = right.getALocalSource() and
  count(DataFlow::LocalSourceNode source | source = left.getALocalSource()) = 1 and
  count(DataFlow::LocalSourceNode source | source = right.getALocalSource()) = 1
}

predicate sameStateRegion(DataFlow::Node left, DataFlow::Node right) {
  uniqueSameRegion(left, right)
  or
  exists(NameNode leftName, NameNode rightName, GlobalVariable variable |
    left.asCfgNode() = leftName and right.asCfgNode() = rightName and
    leftName.uses(variable) and rightName.uses(variable)
  )
}

predicate selectedRead(Function function, DataFlow::AttrRead read) {
  read.getScope() = function and
  DemandRoots::selectedStateObservation(function, read.asCfgNode().getNode()) and
  exists(Return statement |
    statement.getScope() = function and
    statement.getValue() = read.asCfgNode().getNode()
  )
}

predicate noGlobalRebind(
  DataFlow::MethodCallNode call, DataFlow::Node region, DataFlow::AttrRead observed
) {
  exists(NameNode regionName, GlobalVariable variable |
    region.asCfgNode() = regionName and regionName.uses(variable) and
    not exists(NameNode rebind |
      rebind.defines(variable) and
      call.asCfgNode().strictlyReaches(rebind) and
      rebind.strictlyReaches(observed.asCfgNode())
    )
  )
}

predicate noDefiniteFieldKill(
  DataFlow::MethodCallNode call, DataFlow::Node region, DataFlow::AttrRead observed
) {
  not exists(DataFlow::AttrWrite kill |
    sameStateRegion(region, kill.getObject()) and
    kill.getAttributeName() = observed.getAttributeName() and
    call.asCfgNode().strictlyReaches(kill.asCfgNode()) and
    not kill.asCfgNode().strictlyReaches(call.asCfgNode()) and
    kill.asCfgNode().dominates(observed.asCfgNode())
  )
}

from string relation, string caseKind, string scopeKind, Function function,
  DataFlow::MethodCallNode effectCall, DataFlow::Node regionEvidence,
  DataFlow::AttrRead observed
where
  opaqueBoundaryCall(effectCall) and selectedRead(function, observed) and
  function = effectCall.getScope() and
  effectCall.asCfgNode().strictlyReaches(observed.asCfgNode()) and
  (
    uniqueSameRegion(effectCall.getObject(), observed.getObject()) and
    noDefiniteFieldKill(effectCall, effectCall.getObject(), observed) and
    regionEvidence = effectCall.getObject() and
    relation = "unknown_receiver_write_to_read" and caseKind = "positive" and
    scopeKind = "receiver"
    or
    exists(int i |
      observed.getObject().getALocalSource() = DataFlow::parameterNode(function.getArg(i)) and
      not uniqueSameRegion(effectCall.getObject(), observed.getObject()) and
      noDefiniteFieldKill(effectCall, observed.getObject(), observed) and
      regionEvidence = observed.getObject() and
      relation = "parameter_alias_boundary_unknown" and caseKind = "unknown" and
      scopeKind = "parameter_alias"
    )
    or
    observed.getObject().asCfgNode().(NameNode).isGlobal() and
    noGlobalRebind(effectCall, observed.getObject(), observed) and
    noDefiniteFieldKill(effectCall, observed.getObject(), observed) and
    regionEvidence = observed.getObject() and
    relation = "unknown_global_write_to_read" and caseKind = "positive" and
    scopeKind = "module_global"
    or
    exists(NameNode regionName, NameNode rebind, GlobalVariable variable |
      observed.getObject().asCfgNode() = regionName and regionName.uses(variable) and
      rebind.defines(variable) and
      effectCall.asCfgNode().strictlyReaches(rebind) and
      rebind.strictlyReaches(observed.asCfgNode()) and
      regionEvidence = observed.getObject() and
      relation = "global_rebind_boundary_unknown" and caseKind = "unknown" and
      scopeKind = "module_global_rebind"
    )
    or
    exists(DataFlow::MethodCallNode escapeCall, int i |
      opaqueBoundaryCall(escapeCall) and escapeCall.getScope() = function and
      escapeCall != effectCall and
      uniqueSameRegion(escapeCall.getObject(), effectCall.getObject()) and
      uniqueSameRegion(escapeCall.getArg(i), observed.getObject()) and
      escapeCall.asCfgNode().strictlyReaches(effectCall.asCfgNode()) and
      noDefiniteFieldKill(effectCall, escapeCall.getArg(i), observed) and
      regionEvidence = escapeCall.getArg(i) and
      relation = "unknown_escaped_argument_write_to_read" and caseKind = "positive" and
      scopeKind = "escaped_argument"
    )
  )
select relation, caseKind, scopeKind, function.getName() as functionName,
  function.getName() + ":" + effectCall.getLocation().getStartLine().toString() + ":" +
    observed.getLocation().getStartLine().toString() as pathId,
  observed.getAttributeName() as fieldName,
  regionEvidence.getALocalSource().toString() as regionKey,
  effectCall, regionEvidence, observed,
  effectCall.getLocation().getStartLine() as callLine,
  regionEvidence.getLocation().getStartLine() as regionLine,
  observed.getLocation().getStartLine() as observedLine
