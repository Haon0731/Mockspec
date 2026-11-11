import python
import semmle.python.dataflow.new.DataFlow
import semmle.python.dataflow.new.internal.Attributes
import semmle.python.types.Exceptions
import DemandRoots

/** A method call on an injected function parameter whose body is unavailable here. */
predicate opaqueBoundaryCall(DataFlow::MethodCallNode call) {
  exists(Function f, int i |
    f = call.getScope() and
    call.getObject().getALocalSource() = DataFlow::parameterNode(f.getArg(i))
  )
}

predicate sameRegion(DataFlow::Node left, DataFlow::Node right) {
  left.getALocalSource() = right.getALocalSource()
}

predicate uniqueSameRegion(DataFlow::Node left, DataFlow::Node right) {
  sameRegion(left, right) and
  count(DataFlow::LocalSourceNode source | source = left.getALocalSource()) = 1 and
  count(DataFlow::LocalSourceNode source | source = right.getALocalSource()) = 1
}

predicate allocationRegion(DataFlow::Node node) {
  exists(Call allocation, Class visibleClass |
    node.getALocalSource() = DataFlow::exprNode(allocation) and
    allocation.getFunc().(Name).getId() = visibleClass.getName() and
    allocation.getLocation().getFile() = visibleClass.getLocation().getFile() and
    allocation.getLocation().getFile().getRelativePath() = "mechanism_probe.py" and
    visibleClass.getName() = "Box" and
    not exists(visibleClass.getABase()) and
    not exists(visibleClass.getMetaClass()) and
    not exists(Function special |
      special.getScope() = visibleClass and special.getName() in ["__new__", "__init__"]
    )
  )
}

predicate between(DataFlow::Node first, DataFlow::Node middle, DataFlow::Node last) {
  first.asCfgNode().strictlyReaches(middle.asCfgNode()) and
  middle.asCfgNode().strictlyReaches(last.asCfgNode())
}

predicate definiteFieldKill(
  DataFlow::MethodCallNode call, DataFlow::Node argument, DataFlow::AttrRead read,
  DataFlow::AttrWrite write
) {
  uniqueSameRegion(argument, write.getObject()) and
  uniqueSameRegion(argument, read.getObject()) and
  write.getAttributeName() = read.getAttributeName() and
  call.asCfgNode().strictlyReaches(write.asCfgNode()) and
  not write.asCfgNode().strictlyReaches(call.asCfgNode()) and
  write.asCfgNode().dominates(read.asCfgNode())
}

from string relation, string caseKind, Function function, DataFlow::MethodCallNode call,
  DataFlow::Node middle, DataFlow::AttrRead observed, string fieldName,
  string regionKey, string exitKind
where
  opaqueBoundaryCall(call) and function = call.getScope() and
  DemandRoots::selectedStateObservation(function, observed.asCfgNode().getNode()) and
  fieldName = observed.getAttributeName() and
  (
    exists(int i |
      middle = call.getArg(i) and sameRegion(middle, observed.getObject()) and
      regionKey = middle.getALocalSource().toString() and exitKind = "state" and
      call.asCfgNode().strictlyReaches(observed.asCfgNode()) and
      not exists(DataFlow::AttrWrite kill |
        definiteFieldKill(call, middle, observed, kill)
      ) and
      relation = "unknown_may_write_to_read" and caseKind = "positive"
    )
    or
    exists(int i, DataFlow::AttrWrite write |
      middle = write and
      (DataFlow::localFlow(call, write.getObject()) or
        write.getObject().getALocalSource() = call) and
      sameRegion(call.getArg(i), observed.getObject()) and
      write.getAttributeName() = observed.getAttributeName() and
      regionKey = call.getArg(i).getALocalSource().toString() and exitKind = "state" and
      between(call, write, observed) and
      not exists(DataFlow::AttrWrite laterWrite |
        uniqueSameRegion(call.getArg(i), laterWrite.getObject()) and
        laterWrite.getAttributeName() = observed.getAttributeName() and
        write.asCfgNode().strictlyReaches(laterWrite.asCfgNode()) and
        laterWrite.asCfgNode().dominates(observed.asCfgNode())
      ) and
      relation = "return_alias_write_to_argument_read" and caseKind = "positive"
    )
    or
    exists(int i |
      middle = call.getArg(i) and allocationRegion(middle) and
      regionKey = middle.getALocalSource().toString() and exitKind = "excluded" and
      call.asCfgNode().strictlyReaches(observed.asCfgNode()) and
      not sameRegion(middle, observed.getObject()) and
      relation = "trusted_fresh_argument_region_disjoint" and caseKind = "negative"
    )
    or
    exists(int i, DataFlow::AttrWrite write |
      middle = write and allocationRegion(call.getArg(i)) and
      regionKey = call.getArg(i).getALocalSource().toString() and exitKind = "excluded" and
      (DataFlow::localFlow(call, write.getObject()) or
        write.getObject().getALocalSource() = call) and
      write.asCfgNode().strictlyReaches(observed.asCfgNode()) and
      not sameRegion(call.getArg(i), observed.getObject()) and
      relation = "trusted_fresh_return_alias_channel_disjoint" and caseKind = "negative"
    )
    or
    exists(int i, DataFlow::AttrWrite kill |
      middle = kill and definiteFieldKill(call, call.getArg(i), observed, kill) and
      regionKey = call.getArg(i).getALocalSource().toString() and exitKind = "state" and
      relation = "unknown_state_killed_by_definite_write" and caseKind = "kill"
    )
    or
    exists(int i, DataFlow::AttrWrite conditionalWrite |
      middle = conditionalWrite and
      sameRegion(call.getArg(i), conditionalWrite.getObject()) and
      sameRegion(call.getArg(i), observed.getObject()) and
      conditionalWrite.getAttributeName() = observed.getAttributeName() and
      between(call, conditionalWrite, observed) and
      not conditionalWrite.asCfgNode().dominates(observed.asCfgNode()) and
      regionKey = call.getArg(i).getALocalSource().toString() and exitKind = "state" and
      relation = "conditional_write_not_a_kill" and caseKind = "negative"
    )
    or
    exists(int i, DataFlow::AttrWrite write |
      middle = write and
      DataFlow::localFlow(call, write.getObject()) and
      sameRegion(call.getArg(i), observed.getObject()) and
      write.getAttributeName() != observed.getAttributeName() and
      between(call, write, observed) and
      regionKey = call.getArg(i).getALocalSource().toString() and exitKind = "state" and
      relation = "return_alias_other_field_excluded" and caseKind = "negative"
    )
    or
    exists(int i, DataFlow::AttrWrite kill |
      middle = kill and definiteFieldKill(call, call.getArg(i), observed, kill) and
      regionKey = call.getArg(i).getALocalSource().toString() and
      (
        exists(call.asCfgNode().getANormalSuccessor()) and
        caseKind = "normal" and exitKind = "normal"
        or
        call.asCfgNode().(RaisingNode).raisesUnknownType() and
        caseKind = "exception" and exitKind = "exception"
      ) and
      relation = "execution_effect_after_state_kill"
    )
  )
select relation, caseKind, function.getName() as functionName,
  function.getName() + ":" + call.getLocation().getStartLine().toString() + ":" +
    observed.getLocation().getStartLine().toString() as pathId,
  fieldName, regionKey, exitKind, call, middle, observed,
  call.getLocation().getStartLine() as callLine,
  middle.getLocation().getStartLine() as middleLine,
  observed.getLocation().getStartLine() as observedLine
