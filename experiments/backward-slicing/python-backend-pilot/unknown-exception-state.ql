import python
import semmle.python.dataflow.new.DataFlow
import semmle.python.dataflow.new.internal.Attributes
import semmle.python.types.Exceptions
import DemandRoots

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

predicate readInHandler(Try statement, DataFlow::AttrRead read) {
  statement.getAHandler().contains(read.asCfgNode().getNode())
}

predicate readInFinally(Try statement, DataFlow::AttrRead read) {
  statement.getFinalbody().contains(read.asCfgNode().getNode())
}

predicate noDefiniteKillOnExceptionalPath(
  DataFlow::MethodCallNode call, DataFlow::Node argument, DataFlow::AttrRead read
) {
  not exists(DataFlow::AttrWrite kill |
    uniqueSameRegion(argument, kill.getObject()) and
    kill.getAttributeName() = read.getAttributeName() and
    call.asCfgNode().strictlyReaches(kill.asCfgNode()) and
    kill.asCfgNode().dominates(read.asCfgNode())
  )
}

from string relation, string caseKind, Function function, Try statement,
  DataFlow::MethodCallNode call, DataFlow::Node argument, DataFlow::AttrRead observed,
  ControlFlowNode exceptionalSuccessor
where
  opaqueBoundaryCall(call) and function = call.getScope() and
  DemandRoots::selectedStateObservation(function, observed.asCfgNode().getNode()) and
  argument = call.getArg(_) and sameRegion(argument, observed.getObject()) and
  statement.getBody().contains(call.asCfgNode().getNode()) and
  noDefiniteKillOnExceptionalPath(call, argument, observed) and
  call.asCfgNode().(RaisingNode).raisesUnknownType() and
  exceptionalSuccessor = call.asCfgNode().getAnExceptionalSuccessor() and
  exceptionalSuccessor.strictlyReaches(observed.asCfgNode()) and
  (
    readInHandler(statement, observed) and caseKind = "handler"
    or
    readInFinally(statement, observed) and caseKind = "finally"
  ) and
  relation = "write_effect_exception_path_to_read"
select relation, caseKind, function.getName() as functionName,
  function.getName() + ":" + call.getLocation().getStartLine().toString() + ":" +
    observed.getLocation().getStartLine().toString() as pathId,
  observed.getAttributeName() as fieldName,
  argument.getALocalSource().toString() as regionKey,
  "exception" as exitKind, call, argument, exceptionalSuccessor, observed,
  call.getLocation().getStartLine() as callLine,
  exceptionalSuccessor.getLocation().getStartLine() as middleLine,
  observed.getLocation().getStartLine() as observedLine
