import python
import semmle.python.types.FunctionObject
import DemandRoots

predicate directCall(Function caller, Function callee, Call call) {
  exists(FunctionObject object, ControlFlowNode node |
    object.getFunction() = callee and object.getACall() = node and node.getNode() = call and
    call.getScope() = caller and DemandRoots::selectedRecursiveFunction(caller) and
    DemandRoots::selectedRecursiveFunction(callee)
  )
}

predicate reaches(Function caller, Function callee) {
  directCall(caller, callee, _)
  or
  exists(Function middle |
    directCall(caller, middle, _) and reaches(middle, callee)
  )
}

from string relation, Function observationFunction, int observationLine,
  Function function, Call call
where
  DemandRoots::selectedRecursiveObservation(observationFunction, observationLine) and
  reaches(observationFunction, function) and reaches(function, function) and
  directCall(function, _, call) and
  relation = "recursive_expansion_boundary"
select relation, function.getName() as functionName,
  call.getLocation().getStartLine() as callLine,
  observationFunction.getName() as observationRoot, observationLine,
  "recursive methods are delegated to the fixed CodeQL query; no custom unbounded expansion"
    as boundary
