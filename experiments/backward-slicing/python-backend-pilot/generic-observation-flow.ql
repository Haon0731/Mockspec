/** Generic frozen-case observation query.
 *
 * The backend supplies authenticated path, function and line facts through
 * CodeQL external predicates. The query contains no case identity, expected
 * value, or fixed benchmark line, and remains compile-stable across cases.
 */
import python
import semmle.python.GuardedControlFlow
import semmle.python.types.FunctionObject
import semmle.python.dataflow.new.DataFlow

external string selectedTestPathFact();
external string selectedTestFunctionFact();
external int selectedObservationLineFact();
external predicate selectedObservationExprFact(int originalLine, int expressionLine, int column, string kind);
external predicate visibleLoaderBindingFact(int callLine, string name, string productPath,
                                            int functionLine, string functionName, int functionEnd);

predicate selectedTestPath(string path) { path = selectedTestPathFact() }
predicate selectedTestFunction(string name) { name = selectedTestFunctionFact() }
predicate selectedObservationLine(int line) { line = selectedObservationLineFact() }

predicate inSelectedTest(AstNode node) {
  selectedTestPath(node.getLocation().getFile().getRelativePath()) and
  selectedTestFunction(node.getScope().(Function).getName())
}

predicate selectedObservationExpr(Expr expr, int line) {
  selectedObservationLine(line) and
  inSelectedTest(expr) and
  exists(string kind |
    selectedObservationExprFact(line, expr.getLocation().getStartLine(),
                                expr.getLocation().getStartColumn(), kind) and
    (kind = "assert" and exists(Assert assertion | assertion.getTest() = expr)
     or kind = "call" and expr instanceof Call)
  )
}

predicate visibleLoaderCall(Call call, Function function) {
  inSelectedTest(call) and
  visibleLoaderBindingFact(call.getLocation().getStartLine(), call.getFunc().(Name).getId(),
                          function.getLocation().getFile().getRelativePath(),
                          function.getLocation().getStartLine(), function.getName(),
                          functionEndLine(function))
}

predicate resolvedProductCall(Call call, Function function) {
  inSelectedTest(call) and
  exists(FunctionObject callee, ControlFlowNode callNode |
    callee.getACall() = callNode and callNode.getNode() = call and
    function = callee.getFunction() and
    not selectedTestPath(function.getLocation().getFile().getRelativePath())
  )
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
    exists(Call call, Function function, int index |
      resolvedProductCall(call, function) and
      source = DataFlow::exprNode(call.getArg(index))
    )
  }

  predicate isSink(DataFlow::Node sink) {
    exists(Call call, Function function, int index |
      resolvedProductCall(call, function) and
      sink = DataFlow::parameterNode(function.getArg(index))
    )
  }
}

module ActualFormalFlow = DataFlow::Global<ActualFormalConfig>;

from string relation, string sourcePath, int sourceLine, int sourceEndLine,
  string targetPath, int targetLine, int targetEndLine, int observationLine,
  int argumentIndex, string detail
where
  (
    exists(Expr observation |
      selectedObservationExpr(observation, observationLine) and
      relation = "observation" and
      sourcePath = observation.getLocation().getFile().getRelativePath() and
      sourceLine = observation.getLocation().getStartLine() and
      sourceEndLine = observation.getLocation().getEndLine() and
      targetPath = sourcePath and targetLine = sourceLine and targetEndLine = sourceEndLine and
      argumentIndex = -1 and detail = "selected_observation_expression"
    )
    or
    exists(Call call, Function function |
      (resolvedProductCall(call, function) and relation = "resolved_test_product_call"
       or visibleLoaderCall(call, function) and relation = "visible_loader_product_call") and
      sourcePath = call.getLocation().getFile().getRelativePath() and
      sourceLine = call.getLocation().getStartLine() and
      sourceEndLine = call.getLocation().getEndLine() and
      targetPath = function.getLocation().getFile().getRelativePath() and
      targetLine = function.getLocation().getStartLine() and targetEndLine = functionEndLine(function) and
      observationLine = 0 and argumentIndex = -1 and detail = function.getName()
    )
    or
    exists(Call call, Function function, int index |
      resolvedProductCall(call, function) and
      ActualFormalFlow::flow(DataFlow::exprNode(call.getArg(index)),
                             DataFlow::parameterNode(function.getArg(index))) and
      relation = "actual_to_formal" and
      sourcePath = call.getLocation().getFile().getRelativePath() and
      sourceLine = call.getArg(index).getLocation().getStartLine() and
      sourceEndLine = call.getArg(index).getLocation().getEndLine() and
      targetPath = function.getLocation().getFile().getRelativePath() and
      targetLine = function.getArg(index).getLocation().getStartLine() and
      targetEndLine = function.getArg(index).getLocation().getEndLine() and
      observationLine = 0 and argumentIndex = index and detail = function.getName()
    )
    or
    exists(Call call, Expr observation |
      inSelectedTest(call) and selectedObservationExpr(observation, observationLine) and
      DataFlow::localFlow(DataFlow::exprNode(call), DataFlow::exprNode(observation)) and
      relation = "call_result_to_observation" and
      sourcePath = call.getLocation().getFile().getRelativePath() and
      sourceLine = call.getLocation().getStartLine() and
      sourceEndLine = call.getLocation().getEndLine() and
      targetPath = observation.getLocation().getFile().getRelativePath() and
      targetLine = observation.getLocation().getStartLine() and
      targetEndLine = observation.getLocation().getEndLine() and
      argumentIndex = -1 and detail = "localFlow"
    )
    or
    exists(Call call, Function function, Expr observation, Expr observedUse |
      (resolvedProductCall(call, function) and relation = "resolved_product_result_to_observation"
       or visibleLoaderCall(call, function) and relation = "loader_product_result_to_observation") and
      selectedObservationExpr(observation, observationLine) and observation.contains(observedUse) and
      DataFlow::localFlow(DataFlow::exprNode(call), DataFlow::exprNode(observedUse)) and
      sourcePath = function.getLocation().getFile().getRelativePath() and
      sourceLine = function.getLocation().getStartLine() and
      sourceEndLine = functionEndLine(function) and
      targetPath = observation.getLocation().getFile().getRelativePath() and
      targetLine = observation.getLocation().getStartLine() and
      targetEndLine = observation.getLocation().getEndLine() and
      argumentIndex = -1 and detail = function.getName()
    )
    or
    exists(If guard, Expr observation, ControlFlowNode guardNode,
      ControlFlowNode observationNode, boolean truth |
      inSelectedTest(guard) and selectedObservationExpr(observation, observationLine) and
      guardNode.getNode() = guard.getTest() and observationNode.getNode() = observation and
      guardNode.getBasicBlock().(ConditionBlock).controls(observationNode.getBasicBlock(), truth) and
      relation = "guard_controls_observation" and
      sourcePath = guard.getLocation().getFile().getRelativePath() and
      sourceLine = guard.getLocation().getStartLine() and
      sourceEndLine = guard.getLocation().getEndLine() and
      targetPath = observation.getLocation().getFile().getRelativePath() and
      targetLine = observation.getLocation().getStartLine() and
      targetEndLine = observation.getLocation().getEndLine() and
      argumentIndex = -1 and detail = truth.toString()
    )
  )
select relation, sourcePath, sourceLine, sourceEndLine, targetPath, targetLine,
  targetEndLine, observationLine, argumentIndex, detail
