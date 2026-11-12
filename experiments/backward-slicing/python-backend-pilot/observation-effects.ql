/** Observation-linked state and exceptional-flow candidates.
 *
 * This query deliberately separates visible writes from opaque call effects.  A
 * call that receives the observed region is evidence of a possible mutation,
 * never evidence of a particular value.  A dominating same-field assignment
 * between the call and observation is recorded as an exclusion.
 */
import python
import semmle.python.ApiGraphs
import semmle.python.dataflow.new.DataFlow
import semmle.python.dataflow.new.internal.Attributes
import semmle.python.types.FunctionObject

external string selectedTestPathFact();
external string selectedTestFunctionFact();
external int selectedTestStartLineFact();
external int selectedObservationLineFact();
external predicate selectedSupportPathFact(string path);
external predicate selectedObservationExprFact(
  int originalLine, int expressionLine, int column, string kind
);
external predicate selectedOpaqueCallFact(int line, int column, string origin);
external predicate visibleLoaderBindingFact(int callLine, string name, string productPath,
                                            int functionLine, string functionName, int functionEnd,
                                            int resultMode, string runnerName);
external predicate visibleImportBindingFact(int callLine, string name, string productPath,
                                            int functionLine, string functionName, int functionEnd,
                                            int resultMode, string runnerName);

predicate inSelectedTest(AstNode node) {
  node.getLocation().getFile().getRelativePath() = selectedTestPathFact() and
  node.getScope().(Function).getName() = selectedTestFunctionFact() and
  node.getScope().(Function).getLocation().getStartLine() = selectedTestStartLineFact()
}

predicate selectedObservation(Expr observation, int originalLine) {
  originalLine = selectedObservationLineFact() and inSelectedTest(observation) and
  exists(string kind |
    selectedObservationExprFact(
      originalLine, observation.getLocation().getStartLine(),
      observation.getLocation().getStartColumn(), kind
    ) and
    (kind = "assert" and exists(Assert assertion | assertion.getTest() = observation)
     or kind = "call" and observation instanceof Call)
  )
}

predicate selectedAttributeRead(
  Expr observation, int originalLine, DataFlow::AttrRead read
) {
  selectedObservation(observation, originalLine) and
  (observation = read.asCfgNode().getNode() or
   observation.contains(read.asCfgNode().getNode()))
}

predicate opaqueCallDependency(DataFlow::CallCfgNode call, DataFlow::Node dependency) {
  exists(DataFlow::MethodCallNode method |
    method = call and dependency = method.getObject()
  )
  or
  exists(Call expression |
    expression = call.asExpr() and expression.getFunc() instanceof Name and
    dependency = DataFlow::exprNode(expression.getFunc())
  )
}

predicate flowsFromSelectedParameter(DataFlow::CallCfgNode call) {
  exists(DataFlow::Node dependency, Function function, int index |
    function = call.asExpr().getScope() and
    function.getName() = selectedTestFunctionFact() and
    function.getLocation().getStartLine() = selectedTestStartLineFact() and
    opaqueCallDependency(call, dependency) and
    DataFlow::localFlow(DataFlow::parameterNode(function.getArg(index)), dependency)
  )
}

predicate authenticatedOpaqueCall(DataFlow::CallCfgNode call, string origin) {
  inSelectedTest(call.asExpr()) and
  selectedOpaqueCallFact(
    call.getLocation().getStartLine(), call.getLocation().getStartColumn(), origin
  ) and
  origin in ["unresolved_parameter_boundary", "unresolved_product_boundary"] and
  flowsFromSelectedParameter(call)
}

predicate sameUniqueRegion(DataFlow::Node left, DataFlow::Node right) {
  left.getALocalSource() = right.getALocalSource() and
  count(DataFlow::LocalSourceNode source | source = left.getALocalSource()) = 1 and
  count(DataFlow::LocalSourceNode source | source = right.getALocalSource()) = 1
}

predicate sameVisibleVariableRegion(DataFlow::Node left, DataFlow::Node right) {
  exists(NameNode leftName, NameNode rightName, LocalVariable variable |
    left.asCfgNode() = leftName and right.asCfgNode() = rightName and
    variable.getScope() = leftName.getScope() and leftName.getScope() = rightName.getScope() and
    leftName.uses(variable) and rightName.uses(variable) and
    not exists(NameNode rebind |
      rebind.defines(variable) and left.asCfgNode().strictlyReaches(rebind) and
      rebind.strictlyReaches(right.asCfgNode())
    )
  )
}

predicate sameObservedRegion(DataFlow::Node left, DataFlow::Node right) {
  sameUniqueRegion(left, right) or sameVisibleVariableRegion(left, right)
}

predicate possibleAliasRegion(DataFlow::Node left, DataFlow::Node right) {
  not sameObservedRegion(left, right) and
  (DataFlow::localFlow(left, right) or DataFlow::localFlow(right, left))
}

predicate callRegion(
  DataFlow::CallCfgNode call, DataFlow::Node region, int argumentIndex
) {
  exists(DataFlow::MethodCallNode method |
    method = call and argumentIndex = -1 and region = method.getObject()
  )
  or
  argumentIndex >= 0 and region = call.getArg(argumentIndex)
}

predicate betweenCallAndRead(
  DataFlow::CallCfgNode call, DataFlow::Node middle, DataFlow::AttrRead read
) {
  normalStrictlyReaches(call.asCfgNode(), middle.asCfgNode()) and
  normalStrictlyReaches(middle.asCfgNode(), read.asCfgNode())
}

predicate normalStrictlyReaches(ControlFlowNode source, ControlFlowNode target) {
  target = source.getANormalSuccessor()
  or
  exists(ControlFlowNode middle |
    middle = source.getANormalSuccessor() and normalStrictlyReaches(middle, target)
  )
}

predicate normalReaches(ControlFlowNode source, ControlFlowNode target) {
  source = target or normalStrictlyReaches(source, target)
}

predicate independentLiteralWrite(DataFlow::AttrWrite write) {
  write.getValue().asCfgNode().getNode() instanceof ImmutableLiteral and
  not withinUnreachableStatement(write.asCfgNode().getNode())
}

/** Holds when `node` is inside a statement proven unreachable by the Python library. */
predicate withinUnreachableStatement(AstNode node) {
  exists(Stmt statement |
    statement.isUnreachable() and
    (node = statement or statement.contains(node))
  )
}

predicate lastIndependentLiteralWrite(
  DataFlow::Node region, DataFlow::AttrRead read, DataFlow::AttrWrite overwrite
) {
  sameObservedRegion(region, overwrite.getObject()) and
  independentLiteralWrite(overwrite) and
  overwrite.getAttributeName() = read.getAttributeName() and
  normalStrictlyReaches(overwrite.asCfgNode(), read.asCfgNode()) and
  overwrite.asCfgNode().dominates(read.asCfgNode()) and
  not exists(DataFlow::AttrWrite later |
    later != overwrite and
    not withinUnreachableStatement(later.asCfgNode().getNode()) and
    (sameObservedRegion(region, later.getObject()) or
     possibleAliasRegion(region, later.getObject())) and
    later.getAttributeName() = read.getAttributeName() and
    normalStrictlyReaches(overwrite.asCfgNode(), later.asCfgNode()) and
    normalStrictlyReaches(later.asCfgNode(), read.asCfgNode())
  )
}

predicate definiteSameFieldOverwrite(
  DataFlow::CallCfgNode call, DataFlow::Node region, DataFlow::AttrRead read,
  DataFlow::AttrWrite overwrite
) {
  sameObservedRegion(region, read.getObject()) and
  lastIndependentLiteralWrite(region, read, overwrite) and
  betweenCallAndRead(call, overwrite, read) and
  not normalStrictlyReaches(overwrite.asCfgNode(), call.asCfgNode())
}

predicate normalPathToRead(DataFlow::CallCfgNode call, DataFlow::AttrRead read) {
  normalStrictlyReaches(call.asCfgNode(), read.asCfgNode())
}

predicate exceptionalPathToRead(DataFlow::CallCfgNode call, DataFlow::AttrRead read) {
  exists(ControlFlowNode successor |
    successor = call.asCfgNode().getAnExceptionalSuccessor() and
    normalReaches(successor, read.asCfgNode())
  )
}

predicate resolvedCallTarget(DataFlow::CallCfgNode call, Function target) {
  exists(FunctionObject callee, ControlFlowNode callNode |
    callee.getACall() = callNode and callNode.getNode() = call.asExpr() and
    target = callee.getFunction()
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

predicate executedResolvedCall(DataFlow::CallCfgNode call, Function target) {
  not target.isAsync()
  or
  target.isAsync() and exists(Await await | await.getValue() = call.asExpr())
  or
  target.isAsync() and exists(Call runner |
    runner.getArg(0) = call.asExpr() and asyncioRun(runner)
  )
}

predicate executedVisibleFrontendCall(DataFlow::CallCfgNode call, Function target) {
  exists(Call expression, int functionEnd, int resultMode, string runnerName |
    expression = call.asExpr() and inSelectedTest(expression) and
    expression.getFunc() instanceof Name and
    (
      visibleLoaderBindingFact(
        expression.getLocation().getStartLine(), expression.getFunc().(Name).getId(),
        target.getLocation().getFile().getRelativePath(),
        target.getLocation().getStartLine(), target.getName(), functionEnd,
        resultMode, runnerName
      )
      or
      visibleImportBindingFact(
        expression.getLocation().getStartLine(), expression.getFunc().(Name).getId(),
        target.getLocation().getFile().getRelativePath(),
        target.getLocation().getStartLine(), target.getName(), functionEnd,
        resultMode, runnerName
      )
    ) and
    resultMode in [0, 1, 2] and
    target.getLocation().getFile().getRelativePath() != selectedTestPathFact() and
    not selectedSupportPathFact(target.getLocation().getFile().getRelativePath())
  )
}

predicate executedNativeProductCall(DataFlow::CallCfgNode call, Function target) {
  resolvedCallTarget(call, target) and executedResolvedCall(call, target) and
  target.getLocation().getFile().getRelativePath() != selectedTestPathFact() and
  not selectedSupportPathFact(target.getLocation().getFile().getRelativePath())
}

predicate actualMapsToFormal(
  DataFlow::CallCfgNode call, Function target, int index, DataFlow::ParameterNode formal
) {
  executedVisibleFrontendCall(call, target) and
  formal = DataFlow::parameterNode(target.getArg(index))
  or
  executedNativeProductCall(call, target) and
  exists(FunctionObject callee, CallNode callNode, int formalIndex |
    callee.getFunction() = target and callNode.getNode() = call.asExpr() and
    callee.getArgumentForCall(callNode, formalIndex) = call.getArg(index).asCfgNode() and
    formal = DataFlow::parameterNode(target.getArg(formalIndex))
  )
}

predicate executedProductCall(DataFlow::CallCfgNode call, Function target) {
  executedNativeProductCall(call, target)
  or
  executedVisibleFrontendCall(call, target)
}

predicate pytestRaisesContains(Call call, Expr observation) {
  exists(With context, Call raisesCall, DataFlow::CallCfgNode apiCall |
    observation = raisesCall and context.getContextExpr() = raisesCall and
    context.getBody().contains(call) and
    apiCall = API::moduleImport("pytest").getMember("raises").getACall() and
    apiCall.asExpr() = raisesCall and raisesCall.getPositionalArgumentCount() = 1 and
    not exists(raisesCall.getANamedArg()) and not exists(raisesCall.getStarArg()) and
    not exists(raisesCall.getKwargs())
  )
}

predicate pytestRaisesBody(Call call, With context) {
  exists(Call raisesCall, DataFlow::CallCfgNode apiCall |
    context.getContextExpr() = raisesCall and context.getBody().contains(call) and
    apiCall = API::moduleImport("pytest").getMember("raises").getACall() and
    apiCall.asExpr() = raisesCall and raisesCall.getPositionalArgumentCount() = 1 and
    not exists(raisesCall.getANamedArg()) and not exists(raisesCall.getStarArg()) and
    not exists(raisesCall.getKwargs())
  )
}

predicate normalContinuationToRead(DataFlow::CallCfgNode call, DataFlow::AttrRead read) {
  normalPathToRead(call, read) and
  not exists(With context |
    pytestRaisesBody(call.asExpr(), context) and
    not context.getBody().contains(read.asCfgNode().getNode())
  )
}

predicate noCallEvidence(
  string callPath, int callLine, int callEndLine, int callColumn, int callEndColumn,
  string productPath, int productLine, int productEndLine, string productName
) {
  callPath = "" and callLine = 0 and callEndLine = 0 and callColumn = 0 and
  callEndColumn = 0 and productPath = "" and productLine = 0 and productEndLine = 0 and
  productName = ""
}

predicate callEvidence(
  DataFlow::CallCfgNode call,
  string callPath, int callLine, int callEndLine, int callColumn, int callEndColumn,
  string productPath, int productLine, int productEndLine, string productName
) {
  callPath = call.getLocation().getFile().getRelativePath() and
  callLine = call.getLocation().getStartLine() and callEndLine = call.getLocation().getEndLine() and
  callColumn = call.getLocation().getStartColumn() and
  callEndColumn = call.getLocation().getEndColumn() and
  productPath = "" and productLine = 0 and productEndLine = 0 and productName = ""
}

predicate productCallEvidence(
  DataFlow::CallCfgNode call, Function target,
  string callPath, int callLine, int callEndLine, int callColumn, int callEndColumn,
  string productPath, int productLine, int productEndLine, string productName
) {
  callPath = call.getLocation().getFile().getRelativePath() and
  callLine = call.getLocation().getStartLine() and callEndLine = call.getLocation().getEndLine() and
  callColumn = call.getLocation().getStartColumn() and
  callEndColumn = call.getLocation().getEndColumn() and
  productPath = target.getLocation().getFile().getRelativePath() and
  productLine = target.getLocation().getStartLine() and
  productEndLine = target.getLocation().getEndLine() and productName = target.getName()
}

from string relation, string certainty, string route, Expr observation,
  int observationLine, DataFlow::Node observedNode, DataFlow::Node effect,
  DataFlow::Node region, int argumentIndex, string fieldName, string detail,
  string callPath, int callLine, int callEndLine, int callColumn, int callEndColumn,
  string productPath, int productLine, int productEndLine, string productName
where
  selectedObservation(observation, observationLine) and
  (
    exists(DataFlow::AttrRead read, DataFlow::AttrWrite write |
      selectedAttributeRead(observation, observationLine, read) and observedNode = read and
      fieldName = read.getAttributeName() and
      write.getScope() = observation.getScope() and
      not withinUnreachableStatement(write.asCfgNode().getNode()) and
      sameObservedRegion(write.getObject(), read.getObject()) and
      write.getAttributeName() = read.getAttributeName() and
      normalStrictlyReaches(write.asCfgNode(), read.asCfgNode()) and
      not exists(DataFlow::AttrWrite later |
        normalStrictlyReaches(write.asCfgNode(), later.asCfgNode()) and
        lastIndependentLiteralWrite(write.getObject(), read, later)
      ) and
      effect = write and region = write.getObject() and argumentIndex = -1 and
      relation = "visible_write_to_observation" and certainty = "candidate" and
      route = "normal" and detail = "same_unique_region_and_field" and
      noCallEvidence(callPath, callLine, callEndLine, callColumn, callEndColumn,
                     productPath, productLine, productEndLine, productName)
    )
    or
    exists(DataFlow::AttrRead read, DataFlow::CallCfgNode call |
      selectedAttributeRead(observation, observationLine, read) and observedNode = read and
      fieldName = read.getAttributeName() and
      call.getScope() = observation.getScope() and authenticatedOpaqueCall(call, _) and
      callRegion(call, region, argumentIndex) and
      sameObservedRegion(region, read.getObject()) and
      normalStrictlyReaches(call.asCfgNode(), read.asCfgNode()) and
      normalContinuationToRead(call, read) and
      not exists(DataFlow::AttrWrite overwrite |
        definiteSameFieldOverwrite(call, region, read, overwrite)
      ) and
      effect = call and relation = "unknown_region_effect_to_observation" and
      certainty = "unknown_effect" and route = "normal" and
      detail = "call_receives_observed_region_no_definite_same_field_overwrite" and
      callEvidence(call, callPath, callLine, callEndLine, callColumn, callEndColumn,
                   productPath, productLine, productEndLine, productName)
    )
    or
    exists(DataFlow::AttrRead read, DataFlow::CallCfgNode call |
      selectedAttributeRead(observation, observationLine, read) and observedNode = read and
      fieldName = read.getAttributeName() and
      call.getScope() = observation.getScope() and authenticatedOpaqueCall(call, _) and
      callRegion(call, region, argumentIndex) and
      possibleAliasRegion(region, read.getObject()) and
      normalStrictlyReaches(call.asCfgNode(), read.asCfgNode()) and
      normalContinuationToRead(call, read) and
      effect = call and relation = "possible_alias_effect_to_observation" and
      certainty = "unknown_effect" and route = "normal" and
      detail = "local_flow_alias_evidence_without_unique_region_proof" and
      callEvidence(call, callPath, callLine, callEndLine, callColumn, callEndColumn,
                   productPath, productLine, productEndLine, productName)
    )
    or
    exists(DataFlow::AttrRead read, DataFlow::CallCfgNode call, DataFlow::AttrWrite overwrite |
      selectedAttributeRead(observation, observationLine, read) and observedNode = read and
      fieldName = read.getAttributeName() and
      call.getScope() = observation.getScope() and authenticatedOpaqueCall(call, _) and
      callRegion(call, region, argumentIndex) and
      definiteSameFieldOverwrite(call, region, read, overwrite) and
      effect = call and relation = "region_effect_excluded_by_overwrite" and
      certainty = "excluded" and route = "normal" and
      detail = "dominating_same_region_same_field_overwrite:" +
        overwrite.getLocation().getStartLine().toString() and
      normalContinuationToRead(call, read) and
      callEvidence(call, callPath, callLine, callEndLine, callColumn, callEndColumn,
                   productPath, productLine, productEndLine, productName)
    )
    or
    exists(DataFlow::AttrRead read, DataFlow::CallCfgNode call |
      selectedAttributeRead(observation, observationLine, read) and observedNode = read and
      fieldName = read.getAttributeName() and
      call.getScope() = observation.getScope() and authenticatedOpaqueCall(call, _) and
      callRegion(call, region, argumentIndex) and
      sameObservedRegion(region, read.getObject()) and
      exceptionalPathToRead(call, read) and
      effect = call and relation = "unknown_exception_path_to_observation" and
      certainty = "unknown_effect" and route = "exception" and
      detail = "exceptional_successor_reaches_observed_region_read_type_unknown" and
      callEvidence(call, callPath, callLine, callEndLine, callColumn, callEndColumn,
                   productPath, productLine, productEndLine, productName)
    )
    or
    exists(DataFlow::CallCfgNode call |
      call.getScope() = observation.getScope() and authenticatedOpaqueCall(call, _) and
      pytestRaisesContains(call.asExpr(), observation) and
      observedNode = DataFlow::exprNode(observation) and effect = call and
      callRegion(call, region, argumentIndex) and fieldName = "" and
      relation = "unknown_exception_to_pytest_observation" and
      certainty = "unknown_effect" and route = "pytest_raises" and
      detail = "pytest_raises_shape_authenticated_exception_type_compatibility_unknown" and
      callEvidence(call, callPath, callLine, callEndLine, callColumn, callEndColumn,
                   productPath, productLine, productEndLine, productName)
    )
    or
    exists(DataFlow::AttrRead read, DataFlow::CallCfgNode call, Function target,
      int index, DataFlow::AttrWrite write |
      selectedAttributeRead(observation, observationLine, read) and observedNode = read and
      fieldName = read.getAttributeName() and call.getScope() = observation.getScope() and
      executedProductCall(call, target) and
      region = call.getArg(index) and
      sameUniqueRegion(region, read.getObject()) and
      write.getScope() = target and
      not withinUnreachableStatement(write.asCfgNode().getNode()) and
      actualMapsToFormal(call, target, index, write.getObject().getALocalSource()) and
      write.getAttributeName() = read.getAttributeName() and
      normalStrictlyReaches(target.getEntryNode(), write.asCfgNode()) and
      normalStrictlyReaches(write.asCfgNode(), target.getANormalExit()) and
      normalContinuationToRead(call, read) and
      not exists(DataFlow::AttrWrite overwrite |
        definiteSameFieldOverwrite(call, region, read, overwrite)
      ) and
      effect = write and argumentIndex = index and
      relation = "visible_product_write_to_observation" and certainty = "candidate" and
      route = "normal" and detail = "resolved_actual_formal_visible_field_write" and
      productCallEvidence(call, target, callPath, callLine, callEndLine, callColumn,
                          callEndColumn, productPath, productLine, productEndLine, productName)
    )
  )
select relation, certainty, route,
  effect.getLocation().getFile().getRelativePath() as sourcePath,
  effect.getLocation().getStartLine() as sourceLine,
  effect.getLocation().getEndLine() as sourceEndLine,
  effect.getLocation().getStartColumn() as sourceColumn,
  effect.getLocation().getEndColumn() as sourceEndColumn,
  observation.getLocation().getFile().getRelativePath() as targetPath,
  observation.getLocation().getStartLine() as targetLine,
  observation.getLocation().getEndLine() as targetEndLine,
  observation.getLocation().getStartColumn() as targetColumn,
  observation.getLocation().getEndColumn() as targetEndColumn,
  observationLine, fieldName,
  region.getALocalSource().toString() as regionKey, argumentIndex, detail,
  callPath, callLine, callEndLine, callColumn, callEndColumn,
  productPath, productLine, productEndLine, productName
