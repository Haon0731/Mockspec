/** Generic frozen-case observation query.
 *
 * The backend supplies authenticated path, function and line facts through
 * CodeQL external predicates. The query contains no case identity, expected
 * value, or fixed benchmark line, and remains compile-stable across cases.
 */
import python
import semmle.python.GuardedControlFlow
import semmle.python.ApiGraphs
import semmle.python.types.FunctionObject
import semmle.python.types.Exceptions
import semmle.python.dataflow.new.DataFlow
import semmle.python.dataflow.new.internal.Attributes
private import LegacyPointsTo

external string selectedTestPathFact();
external string selectedTestFunctionFact();
external int selectedTestStartLineFact();
external int selectedObservationLineFact();
external predicate selectedSupportPathFact(string path);
external predicate selectedObservationExprFact(int originalLine, int expressionLine, int column, string kind);
external predicate visibleLoaderBindingFact(int callLine, string name, string productPath,
                                            int functionLine, string functionName, int functionEnd,
                                            int resultMode, string runnerName);
external predicate visibleImportBindingFact(int callLine, string name, string productPath,
                                            int functionLine, string functionName, int functionEnd,
                                            int resultMode, string runnerName);

predicate selectedTestPath(string path) { path = selectedTestPathFact() }
predicate selectedTestFunction(string name) { name = selectedTestFunctionFact() }
predicate selectedTestStartLine(int line) { line = selectedTestStartLineFact() }
predicate selectedObservationLine(int line) { line = selectedObservationLineFact() }
predicate selectedSupportPath(string path) { selectedSupportPathFact(path) }

predicate inSelectedTest(AstNode node) {
  selectedTestPath(node.getLocation().getFile().getRelativePath()) and
  selectedTestFunction(node.getScope().(Function).getName()) and
  selectedTestStartLine(node.getScope().(Function).getLocation().getStartLine())
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

predicate visibleLoaderCall(Call call, Function function, int functionEnd, int resultMode,
                            string runnerName) {
  inSelectedTest(call) and
  visibleLoaderBindingFact(call.getLocation().getStartLine(), call.getFunc().(Name).getId(),
                          function.getLocation().getFile().getRelativePath(),
                          function.getLocation().getStartLine(), function.getName(),
                          functionEnd, resultMode, runnerName) and
  not selectedTestPath(function.getLocation().getFile().getRelativePath()) and
  not selectedSupportPath(function.getLocation().getFile().getRelativePath())
}

predicate visibleImportCall(Call call, Function function, int functionEnd, int resultMode,
                            string runnerName) {
  inSelectedTest(call) and
  visibleImportBindingFact(call.getLocation().getStartLine(), call.getFunc().(Name).getId(),
                          function.getLocation().getFile().getRelativePath(),
                          function.getLocation().getStartLine(), function.getName(),
                          functionEnd, resultMode, runnerName) and
  not selectedTestPath(function.getLocation().getFile().getRelativePath()) and
  not selectedSupportPath(function.getLocation().getFile().getRelativePath())
}

predicate visibleFrontendCall(Call call, Function function, int functionEnd, int resultMode,
                              string runnerName, string origin) {
  visibleLoaderCall(call, function, functionEnd, resultMode, runnerName) and origin = "loader"
  or
  visibleImportCall(call, function, functionEnd, resultMode, runnerName) and origin = "import"
}

predicate visibleCallResultExpr(Call call, int resultMode, string runnerName, Expr resultExpr) {
  resultMode = 0 and runnerName = "" and resultExpr = call
  or
  resultMode = 1 and runnerName = "" and
    exists(Await await | await.getValue() = call and resultExpr = await)
  or
  resultMode = 2 and exists(Call runner |
    runner.getArg(0) = call and resultExpr = runner and
    runner.getFunc().(Attribute).getName() = "run" and
    runner.getFunc().(Attribute).getObject().(Name).getId() = runnerName
  )
}

predicate asyncioRun(Call runner) {
  exists(DataFlow::CallCfgNode apiCall |
    apiCall = API::moduleImport("asyncio").getMember("run").getACall() and
    apiCall.asExpr() = runner
  ) and
  runner.getPositionalArgumentCount() = 1 and
  not exists(runner.getANamedArg()) and not exists(runner.getStarArg()) and
  not exists(runner.getKwargs())
}

predicate resolvedProductTarget(Call call, Function function) {
  inSelectedTest(call) and
  exists(FunctionObject callee, ControlFlowNode callNode |
    callee.getACall() = callNode and callNode.getNode() = call and
    function = callee.getFunction() and
    not selectedSupportPath(function.getLocation().getFile().getRelativePath()) and
    not selectedTestPath(function.getLocation().getFile().getRelativePath())
  )
}

predicate nativeCallResultExpr(Call call, Function function, int resultMode,
                               string runnerName, Expr resultExpr) {
  not function.isAsync() and resultMode = 0 and runnerName = "" and resultExpr = call
  or
  function.isAsync() and resultMode = 1 and runnerName = "" and
    exists(Await await | await.getValue() = call and resultExpr = await)
  or
  function.isAsync() and resultMode = 2 and runnerName = "api:asyncio.run" and
    exists(Call runner | runner.getArg(0) = call and asyncioRun(runner) and resultExpr = runner)
}

predicate authenticatedProductCall(Call call, Function function, int visibleEnd,
                                   int resultMode, string runnerName, string origin) {
  resolvedProductTarget(call, function) and visibleEnd = functionEndLine(function) and
  origin = "native" and
  (
    nativeCallResultExpr(call, function, resultMode, runnerName, _)
    or
    function.isAsync() and resultMode = -1 and runnerName = "" and
      not exists(Expr resultExpr, int mode, string runner |
        nativeCallResultExpr(call, function, mode, runner, resultExpr)
      )
  )
  or
  visibleFrontendCall(call, function, visibleEnd, resultMode, runnerName, origin)
}

predicate executedProductCall(Call call, Function function, int visibleEnd,
                              int resultMode, string runnerName, string origin) {
  authenticatedProductCall(call, function, visibleEnd, resultMode, runnerName, origin) and
  resultMode in [0, 1, 2]
}

predicate linkedProductResult(Call call, Function function, Expr resultExpr,
                              Expr observation, int observationLine, string origin) {
  exists(int visibleEnd, int resultMode, string runnerName, Expr observedUse |
    executedProductCall(call, function, visibleEnd, resultMode, runnerName, origin) and
    (origin = "native" and nativeCallResultExpr(call, function, resultMode, runnerName, resultExpr)
     or origin != "native" and
        visibleCallResultExpr(call, resultMode, runnerName, resultExpr)) and
    selectedObservationExpr(observation, observationLine) and
    (observation = observedUse or observation.contains(observedUse)) and
    DataFlow::localFlow(DataFlow::exprNode(resultExpr), DataFlow::exprNode(observedUse))
  )
}

predicate selectedPytestRaisesObservation(Call productCall, Expr observation, int observationLine) {
  selectedObservationExpr(observation, observationLine) and
  exists(With context, Call raisesCall, DataFlow::CallCfgNode apiCall |
    observation = raisesCall and context.getContextExpr() = raisesCall and
    context.getBody().contains(productCall) and
    apiCall = API::moduleImport("pytest").getMember("raises").getACall() and
    apiCall.asExpr() = raisesCall and raisesCall.getPositionalArgumentCount() = 1 and
    forall(DictItem item | item = raisesCall.getANamedArg() |
      item instanceof Keyword and item.(Keyword).getArg() = "match" and
      item.(Keyword).getValue() instanceof ImmutableLiteral
    ) and not exists(raisesCall.getStarArg()) and
    not exists(raisesCall.getKwargs())
  )
}

predicate branchReaches(ControlFlowNode guardNode, ControlFlowNode target, boolean truth) {
  truth = true and
  (guardNode.getATrueSuccessor() = target or guardNode.getATrueSuccessor().strictlyReaches(target))
  or
  truth = false and
  (guardNode.getAFalseSuccessor() = target or guardNode.getAFalseSuccessor().strictlyReaches(target))
}

predicate branchSelectsReturn(ControlFlowNode guardNode, ControlFlowNode returnNode, boolean truth) {
  truth = true and branchReaches(guardNode, returnNode, true) and
  not branchReaches(guardNode, returnNode, false)
  or
  truth = false and branchReaches(guardNode, returnNode, false) and
  not branchReaches(guardNode, returnNode, true)
}

predicate selectedPathStateObservation(Expr observation, int observationLine) {
  selectedObservationExpr(observation, observationLine) and
  exists(Call stateCall, Attribute operation |
    (observation = stateCall or observation.contains(stateCall)) and
    stateCall.getFunc() = operation and
    operation.getName() in ["exists", "read_text", "read_bytes", "glob", "iterdir"]
  )
}

predicate productExternalStateEndpoint(Call effectCall, string category) {
  exists(Attribute operation |
    effectCall.getFunc() = operation and
    operation.getName() in ["unlink", "write", "write_text", "write_bytes", "mkdir",
                            "rmdir", "rename", "replace", "copy", "copy2", "move"] and
    category = "external_state_method_endpoint_region_type_unproven:" + operation.getName()
  )
  or
  effectCall.getFunc() instanceof Name and effectCall.getFunc().(Name).getId() = "open" and
  category = "builtin_open_endpoint_mode_region_unproven"
}

predicate productExecutionPrecedesPathObservation(
  Call productCall, Function function, Expr observation, int observationLine,
  string origin, string route
) {
  exists(int visibleEnd, int resultMode, string runnerName, Expr resultExpr |
    executedProductCall(productCall, function, visibleEnd, resultMode, runnerName, origin) and
    (origin = "native" and nativeCallResultExpr(
       productCall, function, resultMode, runnerName, resultExpr)
     or origin != "native" and visibleCallResultExpr(
       productCall, resultMode, runnerName, resultExpr)) and
    selectedPathStateObservation(observation, observationLine) and
    DataFlow::exprNode(resultExpr).asCfgNode().strictlyReaches(
      DataFlow::exprNode(observation).asCfgNode()
    ) and
    (
      selectedPytestRaisesObservation(productCall, _, _) and
      route = "pytest_exception_continuation_region_unproven"
      or
      not selectedPytestRaisesObservation(productCall, _, _) and
      route = "normal_continuation_region_unproven"
    )
  )
}

predicate linkedProductObservation(Call productCall, Function function, Expr observation,
                                   int observationLine, string origin) {
  exists(Expr resultExpr |
    linkedProductResult(productCall, function, resultExpr, observation, observationLine, origin)
  )
  or
  exists(int visibleEnd, int resultMode, string runnerName |
    executedProductCall(productCall, function, visibleEnd, resultMode, runnerName, origin) and
    selectedPytestRaisesObservation(productCall, observation, observationLine)
  )
}

/** An endpoint contained by source that the Python extractor proves unreachable. */
predicate withinUnreachableStatement(AstNode endpoint) {
  exists(Stmt statement |
    (statement = endpoint or statement.contains(endpoint)) and statement.isUnreachable()
  )
}

/** Every inferred raised type has a concrete exceptional successor that handles it. */
predicate allKnownRaisedTypesHandled(RaisingNode raising) {
  exists(ClassValue raised | raised = raising.getARaisedType()) and
  not raising.raisesUnknownType() and
  forall(ClassValue raised | raised = raising.getARaisedType() |
    exists(ExceptFlowNodeWithPointsTo handler |
      handler = raising.getAnExceptionalSuccessor() and handler.handles(raised)
    )
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
      resolvedProductTarget(call, function) and
      not function.isAsync() and
      source = DataFlow::exprNode(call.getArg(index))
    )
  }

  predicate isSink(DataFlow::Node sink) {
    exists(Call call, Function function, int index |
      resolvedProductTarget(call, function) and
      not function.isAsync() and
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
    exists(Call call, Function function, int visibleEnd, int resultMode,
      string runnerName, string origin |
      authenticatedProductCall(call, function, visibleEnd, resultMode, runnerName, origin) and
      (origin = "native" and resultMode = 0 and relation = "resolved_test_product_call"
       or origin = "native" and resultMode in [1, 2] and relation = "resolved_async_test_product_call"
       or origin = "native" and resultMode = -1 and relation = "resolved_raw_async_test_product_call"
       or origin != "native" and resultMode = 0 and
          relation = "visible_" + origin + "_product_call"
       or origin != "native" and resultMode in [1, 2] and
          relation = "visible_async_" + origin + "_product_call"
       or origin != "native" and resultMode = -1 and
          relation = "visible_raw_async_" + origin + "_product_call") and
      sourcePath = call.getLocation().getFile().getRelativePath() and
      sourceLine = call.getLocation().getStartLine() and
      sourceEndLine = call.getLocation().getEndLine() and
      targetPath = function.getLocation().getFile().getRelativePath() and
      targetLine = function.getLocation().getStartLine() and targetEndLine = visibleEnd and
      observationLine = 0 and argumentIndex = -1 and detail = function.getName()
    )
    or
    exists(Call call, Function function, int index |
      resolvedProductTarget(call, function) and not function.isAsync() and
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
    exists(Call call, Function function, int visibleEnd, int resultMode,
      string runnerName, string origin, Expr resultExpr, Expr observation |
      linkedProductResult(call, function, resultExpr, observation, observationLine, origin) and
      executedProductCall(call, function, visibleEnd, resultMode, runnerName, origin) and
      (origin = "native" and resultMode = 0 and
       relation = "resolved_product_result_to_observation"
       or origin = "native" and resultMode != 0 and
          relation = "async_resolved_product_result_to_observation"
       or origin != "native" and resultMode = 0 and
          relation = origin + "_product_result_to_observation"
       or origin != "native" and resultMode != 0 and
          relation = "async_" + origin + "_product_result_to_observation") and
      sourcePath = function.getLocation().getFile().getRelativePath() and
      sourceLine = function.getLocation().getStartLine() and sourceEndLine = visibleEnd and
      targetPath = observation.getLocation().getFile().getRelativePath() and
      targetLine = observation.getLocation().getStartLine() and
      targetEndLine = observation.getLocation().getEndLine() and
      argumentIndex = -1 and detail = function.getName()
    )
    or
    exists(Call call, Function function, Expr resultExpr, Expr observation,
      Return returned, ControlFlowNode returnNode, string origin |
      linkedProductResult(call, function, resultExpr, observation, observationLine, origin) and
      returned.getScope() = function and exists(Expr value | value = returned.getValue()) and
      returnNode.getNode() = returned and function.getEntryNode().strictlyReaches(returnNode) and
      relation = "product_return_to_observation" and
      sourcePath = returned.getLocation().getFile().getRelativePath() and
      sourceLine = returned.getLocation().getStartLine() and
      sourceEndLine = returned.getLocation().getEndLine() and
      targetPath = observation.getLocation().getFile().getRelativePath() and
      targetLine = observation.getLocation().getStartLine() and
      targetEndLine = observation.getLocation().getEndLine() and
      argumentIndex = -1 and detail = origin + ":return_value"
    )
    or
    exists(Call call, Function function, Expr resultExpr, Expr observation,
      Return returned, If guard, ConditionBlock guardBlock, ControlFlowNode guardNode,
      ControlFlowNode returnNode, boolean truth, string origin |
      linkedProductResult(call, function, resultExpr, observation, observationLine, origin) and
      returned.getScope() = function and guard.getScope() = function and
      not guard.getTest() instanceof ImmutableLiteral and
      guardNode = guardBlock.getLastNode() and
      (guardNode.getNode() = guard.getTest() or guard.getTest().contains(guardNode.getNode())) and
      returnNode.getNode() = returned and
      function.getEntryNode().strictlyReaches(returnNode) and
      (guardBlock.controls(returnNode.getBasicBlock(), truth)
       or branchSelectsReturn(guardNode, returnNode, truth)) and
      relation = "product_guard_controls_return_to_observation" and
      sourcePath = guard.getLocation().getFile().getRelativePath() and
      sourceLine = guard.getLocation().getStartLine() and
      sourceEndLine = guard.getLocation().getEndLine() and
      targetPath = observation.getLocation().getFile().getRelativePath() and
      targetLine = observation.getLocation().getStartLine() and
      targetEndLine = observation.getLocation().getEndLine() and
      argumentIndex = -1 and
      detail = origin + ":return:" + returned.getLocation().getStartLine().toString() +
               ":branch:" + truth.toString()
    )
    or
    exists(Call call, Function function, Expr observation, string origin,
      int visibleEnd, int resultMode, string runnerName, RaisingNode exceptionSource |
      executedProductCall(call, function, visibleEnd, resultMode, runnerName, origin) and
      selectedPytestRaisesObservation(call, observation, observationLine) and
      exceptionSource.getNode().getScope() = function and
      function.getEntryNode().strictlyReaches(exceptionSource) and
      not withinUnreachableStatement(exceptionSource.getNode()) and
      ((exceptionSource.viableExceptionalExit(function, _)
        or exists(Raise raised |
          exceptionSource.getNode() = raised and exists(raised.getException()) and
          not allKnownRaisedTypesHandled(exceptionSource)
        )) and
       relation = "product_exception_exit_unknown"
       or
       exists(Raise rethrow, ExceptionHandler handler |
         exceptionSource.getNode() = rethrow and handler.contains(rethrow) and
         not exists(rethrow.getException())
       ) and relation = "product_rethrow_endpoint_unknown") and
      sourcePath = exceptionSource.getLocation().getFile().getRelativePath() and
      sourceLine = exceptionSource.getLocation().getStartLine() and
      sourceEndLine = exceptionSource.getLocation().getEndLine() and
      targetPath = observation.getLocation().getFile().getRelativePath() and
      targetLine = observation.getLocation().getStartLine() and
      targetEndLine = observation.getLocation().getEndLine() and
      argumentIndex = -1 and
      (relation = "product_exception_exit_unknown" and
       detail = origin + ":test_call:" + call.getLocation().getStartLine().toString() + ":" +
                call.getLocation().getEndLine().toString() + ":" +
                call.getLocation().getStartColumn().toString() + ":" +
                call.getLocation().getEndColumn().toString() +
                ":exceptional_exit_type_compatibility_unknown"
       or relation = "product_rethrow_endpoint_unknown" and
          detail = origin + ":test_call:" + call.getLocation().getStartLine().toString() + ":" +
                   call.getLocation().getEndLine().toString() + ":" +
                   call.getLocation().getStartColumn().toString() + ":" +
                   call.getLocation().getEndColumn().toString() +
                   ":bare_rethrow_outer_handler_and_type_compatibility_unknown")
    )
    or
    exists(Call call, Function function, Expr observation, Call effectCall,
      ControlFlowNode effectNode, string origin |
      linkedProductObservation(call, function, observation, observationLine, origin) and
      effectCall.getScope() = function and effectNode.getNode() = effectCall and
      function.getEntryNode().strictlyReaches(effectNode) and
      not withinUnreachableStatement(effectCall) and
      relation = "product_call_unknown_effect" and
      sourcePath = effectCall.getLocation().getFile().getRelativePath() and
      sourceLine = effectCall.getLocation().getStartLine() and
      sourceEndLine = effectCall.getLocation().getEndLine() and
      targetPath = observation.getLocation().getFile().getRelativePath() and
      targetLine = observation.getLocation().getStartLine() and
      targetEndLine = observation.getLocation().getEndLine() and
      argumentIndex = -1 and
      detail = origin + ":test_call:" + call.getLocation().getStartLine().toString() + ":" +
               call.getLocation().getEndLine().toString() + ":" +
               call.getLocation().getStartColumn().toString() + ":" +
               call.getLocation().getEndColumn().toString() +
               ":return_exception_state_unknown"
    )
    or
    exists(Call call, Function function, Expr observation,
      DataFlow::AttrWrite write, ControlFlowNode writeNode, string origin |
      linkedProductObservation(call, function, observation, observationLine, origin) and
      write.getScope() = function and writeNode = write.asCfgNode() and
      function.getEntryNode().strictlyReaches(writeNode) and
      not withinUnreachableStatement(writeNode.getNode()) and
      relation = "product_attribute_write_unknown_effect" and
      sourcePath = write.getLocation().getFile().getRelativePath() and
      sourceLine = write.getLocation().getStartLine() and
      sourceEndLine = write.getLocation().getEndLine() and
      targetPath = observation.getLocation().getFile().getRelativePath() and
      targetLine = observation.getLocation().getStartLine() and
      targetEndLine = observation.getLocation().getEndLine() and
      argumentIndex = -1 and
      detail = origin + ":test_call:" + call.getLocation().getStartLine().toString() + ":" +
               call.getLocation().getEndLine().toString() + ":" +
               call.getLocation().getStartColumn().toString() + ":" +
               call.getLocation().getEndColumn().toString() +
               ":alias_and_overwrite_not_proven"
    )
    or
    exists(Call call, Function function, Expr observation, Call effectCall,
      ControlFlowNode effectNode, string origin, string category, string route |
      productExecutionPrecedesPathObservation(
        call, function, observation, observationLine, origin, route
      ) and
      effectCall.getScope() = function and productExternalStateEndpoint(effectCall, category) and
      effectNode.getNode() = effectCall and function.getEntryNode().strictlyReaches(effectNode) and
      not withinUnreachableStatement(effectCall) and
      relation = "product_external_state_unanalysed" and
      sourcePath = effectCall.getLocation().getFile().getRelativePath() and
      sourceLine = effectCall.getLocation().getStartLine() and
      sourceEndLine = effectCall.getLocation().getEndLine() and
      targetPath = observation.getLocation().getFile().getRelativePath() and
      targetLine = observation.getLocation().getStartLine() and
      targetEndLine = observation.getLocation().getEndLine() and
      argumentIndex = -1 and
      detail = origin + ":test_call:" + call.getLocation().getStartLine().toString() + ":" +
               call.getLocation().getEndLine().toString() + ":" +
               call.getLocation().getStartColumn().toString() + ":" +
               call.getLocation().getEndColumn().toString() +
               ":" + route + ":" + category
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
