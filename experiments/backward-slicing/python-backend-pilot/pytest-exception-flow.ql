import python
import LegacyPointsTo
import semmle.python.ApiGraphs
import semmle.python.types.FunctionObject
import semmle.python.types.Exceptions
import semmle.python.dataflow.new.DataFlow
import semmle.python.dataflow.new.internal.Attributes
import DemandRoots

/**
 * Bounded joint model: CodeQL establishes that the product call can exit the
 * selected test scope exceptionally.  The reviewed pytest.raises summary maps
 * a compatible exception to the call's first post-with normal continuation,
 * where CodeQL CFG reachability and local capture flow must reach the assert.
 * This is not a general CodeQL model of context-manager exception semantics.
 */

predicate resolvedCall(FunctionObject callee, Call call, ControlFlowNode callNode) {
  callee.getACall() = callNode and callNode.getNode() = call and
  DemandRoots::selectedTestCall(call) and
  DemandRoots::selectedProductFunction(callee.getFunction())
}

predicate pytestRaisesContext(
  With context, Call raisesCall, Expr capture, Expr expectedType, ClassValue expectedClass
) {
  context.getContextExpr() = raisesCall and
  DemandRoots::selectedObservation(context) and
  exists(DataFlow::CallCfgNode apiCall |
    apiCall = API::moduleImport("pytest").getMember("raises").getACall() and
    apiCall.asExpr() = raisesCall
  ) and
  expectedType = raisesCall.getArg(0) and
  raisesCall.getPositionalArgumentCount() = 1 and
  not exists(raisesCall.getANamedArg()) and
  not exists(raisesCall.getStarArg()) and not exists(raisesCall.getKwargs()) and
  capture = context.getOptionalVars() and
  exists(ControlFlowNodeWithPointsTo expectedNode |
    expectedNode.getNode() = expectedType and expectedNode.pointsTo(expectedClass)
  ) and
  count(ClassValue candidate |
    exists(ControlFlowNodeWithPointsTo expectedNode |
      expectedNode.getNode() = expectedType and expectedNode.pointsTo(candidate)
    )
  ) = 1 and
  expectedClass.getASuperType() = ClassValue::baseException()
}

predicate capturedValueMembershipObservation(
  With context, Expr capture, Assert observation, DataFlow::AttrRead valueRead
) {
  exists(Compare membership, In inOperator, Call strCall |
    observation.getScope() = context.getScope() and
    DemandRoots::selectedObservation(observation) and
    context.getLocation().getStartLine() < observation.getLocation().getStartLine() and
    not context.getBody().contains(observation) and
    observation.contains(membership) and membership.getAnOp() = inOperator and
    membership.contains(strCall) and
    exists(DataFlow::CallCfgNode builtinStrCall |
      builtinStrCall = API::builtin("str").getACall() and
      builtinStrCall.asExpr() = strCall
    ) and
    strCall.getArg(0) = valueRead.asCfgNode().getNode() and
    valueRead.getAttributeName() = "value" and
    DataFlow::localFlow(DataFlow::exprNode(capture), valueRead.getObject())
  )
}

predicate compatibleExceptionalExit(
  RaisingNode source, Scope scope, ClassValue expectedType, string compatibility
) {
  source.raisesUnknownType() and source.isExceptionalExit(scope) and
  not exists(ExceptFlowNodeWithPointsTo handler |
    handler = source.getAnExceptionalSuccessor() and handler.handles(expectedType)
  ) and
  compatibility = "unknown_may_match"
  or
  exists(ClassValue raised |
    source.viableExceptionalExit(scope, raised) and
    raised.getASuperType() = expectedType and compatibility = "known_subtype_match"
  )
}

from string relation, RaisingNode exceptionSource, FunctionObject callee, Call testCall,
  With context, Expr capture, Expr expectedType, Assert observation,
  DataFlow::AttrRead valueRead, string compatibility, ClassValue expectedClass,
  ControlFlowNode testCallNode, ControlFlowNode suppressedContinuation,
  ControlFlowNode observationNode
where
  pytestRaisesContext(context, _, capture, expectedType, expectedClass) and
  context.getBody().contains(testCall) and
  resolvedCall(callee, testCall, testCallNode) and
  exceptionSource.getNode().getScope() = callee.getFunction() and
  compatibleExceptionalExit(
    exceptionSource, callee.getFunction(), expectedClass, compatibility
  ) and
  capturedValueMembershipObservation(context, capture, observation, valueRead) and
  testCallNode instanceof RaisingNode and
  compatibleExceptionalExit(testCallNode, context.getScope(), expectedClass, _) and
  suppressedContinuation = testCallNode.getANormalSuccessor() and
  not context.getBody().contains(suppressedContinuation.getNode()) and
  observationNode.getNode() = observation and
  suppressedContinuation.strictlyReaches(observationNode) and
  relation = "captured_exception_may_reach_membership_observation"
select relation,
  exceptionSource.getLocation().getFile().getRelativePath() as sourcePath,
  exceptionSource.getLocation().getStartLine() as sourceLine,
  callee.getFunction().getLocation().getFile().getRelativePath() as calleePath,
  callee.getFunction().getLocation().getStartLine() as calleeLine,
  testCall.getLocation().getFile().getRelativePath() as testCallPath,
  testCall.getLocation().getStartLine() as testCallLine,
  context.getLocation().getStartLine() as withLine,
  observation.getLocation().getStartLine() as observationLine,
  capture.getLocation().getStartLine() as captureLine,
  compatibility as typeCompatibility,
  valueRead.getLocation().getStartLine() as valueReadLine,
  suppressedContinuation.getLocation().getStartLine() as suppressedContinuationLine
