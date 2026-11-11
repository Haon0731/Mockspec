import python
import semmle.python.GuardedControlFlow
import semmle.python.types.FunctionObject
import semmle.python.dataflow.new.DataFlow
import DemandRoots

predicate selectedTestFile(File file) {
  DemandRoots::selectedTestPath(file.getRelativePath())
}

predicate resolvedTestProductCall(FunctionObject callee, Call call, ControlFlowNode callNode) {
  callee.getACall() = callNode and callNode.getNode() = call and
  DemandRoots::selectedTestCall(call) and
  not selectedTestFile(callee.getFunction().getLocation().getFile())
}

int functionEndLine(Function function) {
  result = max(int line |
    exists(Stmt statement |
      statement = function.getAStmt() and line = statement.getLocation().getEndLine()
    )
  )
}

private module ActualFormalConfig implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) {
    exists(FunctionObject callee, Call call, ControlFlowNode callNode, int i |
      resolvedTestProductCall(callee, call, callNode) and
      source = DataFlow::exprNode(call.getArg(i))
    )
  }

  predicate isSink(DataFlow::Node sink) {
    exists(FunctionObject callee, Call call, ControlFlowNode callNode, int i |
      resolvedTestProductCall(callee, call, callNode) and
      sink = DataFlow::parameterNode(callee.getFunction().getArg(i))
    )
  }
}

module ActualFormalFlow = DataFlow::Global<ActualFormalConfig>;

from string relation, string observationKind, string sourcePath, int sourceLine,
  string targetPath, int targetLine, int targetEndLine, string branch, int guardLine, int argumentIndex
where
  (
    exists(With context, Call raisesCall |
      (context.getContextExpr() = raisesCall or context.getContextExpr().contains(raisesCall)) and
      raisesCall.getFunc().(Attribute).getName() = "raises" and
      DemandRoots::selectedObservation(context) and
      relation = "observation" and observationKind = "exception_context" and
      sourcePath = context.getLocation().getFile().getRelativePath() and
      sourceLine = context.getLocation().getStartLine() and
      targetPath = "none" and targetLine = 0 and targetEndLine = 0 and branch = "none" and guardLine = 0 and
      argumentIndex = -1
    )
    or
    exists(Assert observation, ControlFlowNode observationNode, If guard,
      ControlFlowNode guardNode, boolean truth |
      DemandRoots::selectedObservation(observation) and DemandRoots::selectedGuard(guard) and
      observationNode.getNode() = observation and guardNode.getNode() = guard.getTest() and
      guardNode.getBasicBlock().(ConditionBlock).controls(observationNode.getBasicBlock(), truth) and
      relation = "observation" and observationKind = "assertion" and
      sourcePath = observation.getLocation().getFile().getRelativePath() and
      sourceLine = observation.getLocation().getStartLine() and
      targetPath = "none" and targetLine = 0 and targetEndLine = 0 and
      (truth = true and branch = "true" or truth = false and branch = "false") and
      guardLine = guard.getLocation().getStartLine() and argumentIndex = -1
    )
    or
    exists(FunctionObject callee, Call call, ControlFlowNode callNode |
      resolvedTestProductCall(callee, call, callNode) and
      relation = "resolved_test_product_call" and observationKind = "none" and
      sourcePath = call.getLocation().getFile().getRelativePath() and
      sourceLine = call.getLocation().getStartLine() and
      targetPath = callee.getFunction().getLocation().getFile().getRelativePath() and
      targetLine = callee.getFunction().getLocation().getStartLine() and
      targetEndLine = functionEndLine(callee.getFunction()) and
      branch = "none" and guardLine = 0 and argumentIndex = -1
    )
    or
    exists(FunctionObject callee, Call call, ControlFlowNode callNode,
      DataFlow::Node actual, DataFlow::Node formal, int i |
      resolvedTestProductCall(callee, call, callNode) and
      actual = DataFlow::exprNode(call.getArg(i)) and
      formal = DataFlow::parameterNode(callee.getFunction().getArg(i)) and
      ActualFormalFlow::flow(actual, formal) and
      relation = "actual_to_formal" and observationKind = "none" and
      sourcePath = call.getLocation().getFile().getRelativePath() and
      sourceLine = call.getLocation().getStartLine() and
      targetPath = callee.getFunction().getLocation().getFile().getRelativePath() and
      targetLine = callee.getFunction().getLocation().getStartLine() and
      targetEndLine = functionEndLine(callee.getFunction()) and
      branch = "none" and guardLine = 0 and argumentIndex = i
    )
  )
select relation, observationKind, sourcePath, sourceLine, targetPath, targetLine, targetEndLine,
  branch, guardLine, argumentIndex
