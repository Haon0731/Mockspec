import python
import semmle.python.types.Exceptions
import DemandRoots

predicate selectedCall(Call call, string label) {
  DemandRoots::selectedProductFunction(call.getScope().(Function)) and
  (
    call.getFunc().(Attribute).getName() = label and label = "aborter"
    or
    call.getFunc().(Name).getId() = label and label = "_wz_abort"
  )
}

from RaisingNode node, Call call, string label, string relation
where
  node.getNode() = call and selectedCall(call, label) and
  (
    relation = "raises_unknown_type" and node.raisesUnknownType()
    or
    relation = "exceptional_exit" and node.isExceptionalExit(call.getScope())
  )
select relation, label, call, call.getLocation().getFile().getRelativePath(),
  call.getLocation().getStartLine()
