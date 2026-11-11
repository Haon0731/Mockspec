import python

predicate selectedCall(Call call, string label) {
  call.getFunc().(Name).getId() = label and
  label in ["unknown_value", "fill", "observe"]
  or
  call.getFunc().(Attribute).getName() = label and label = "fill"
  or
  call.getFunc().(Attribute).getName() = label and label = "aborter"
  or
  call.getFunc().(Name).getId() = label and label = "_wz_abort"
}

from ControlFlowNode source, ControlFlowNode target, Call call, string callName,
  string edgeKind
where
  source.getNode() = call and selectedCall(call, callName) and
  (
    target = source.getANormalSuccessor() and edgeKind = "normal"
    or
    target = source.getAnExceptionalSuccessor() and edgeKind = "exception"
  )
select callName, edgeKind, call, source.toString(), target.toString(),
  call.getLocation().getFile().getRelativePath(), call.getLocation().getStartLine(),
  target.getLocation().getStartLine()
