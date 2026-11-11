import python

predicate inFunction(AstNode node, string name) {
  node.getScope().(Function).getName() = name
}

predicate afterTerminalTarget(Call call, string functionName) {
  call.getFunc().(Name).getId() = "observe" and
  inFunction(call, functionName) and
  functionName in ["early_exit", "explicit_raise"]
}

from string relation, string functionName, ControlFlowNode source, ControlFlowNode target
where
  (
    relation = "terminal_source" and
    source.getNode() instanceof Return and inFunction(source.getNode(), functionName) and
    functionName = "early_exit" and target = source
    or
    relation = "terminal_source" and
    source.getNode() instanceof Raise and inFunction(source.getNode(), functionName) and
    functionName = "explicit_raise" and target = source
    or
    relation = "target_after_terminal" and
    afterTerminalTarget(target.getNode().(Call), functionName) and source = target
    or
    relation = "reachable_after_terminal" and
    (
      source.getNode() instanceof Return and functionName = "early_exit"
      or
      source.getNode() instanceof Raise and functionName = "explicit_raise"
    ) and
    inFunction(source.getNode(), functionName) and
    afterTerminalTarget(target.getNode().(Call), functionName) and
    source.strictlyReaches(target)
  )
select relation, functionName, source, target,
  source.getLocation().getStartLine(), target.getLocation().getStartLine()
