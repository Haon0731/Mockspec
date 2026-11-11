import python
import semmle.python.GuardedControlFlow
import semmle.python.dataflow.new.DataFlow

string testPath() { result = "tests/test_189is_encrypted.py" }
string productPath() { result = "src/src/backend/base/langflow/services/auth/mcp_encryption.py" }

predicate productFunction(Function function) {
  function.getName() = "is_encrypted" and
  function.getLocation().getFile().getRelativePath() = productPath() and
  function.getLocation().getStartLine() = 94
}

predicate selectedTestCall(Call call) {
  call.getFunc().(Name).getId() = "is_encrypted" and
  call.getLocation().getFile().getRelativePath() = testPath() and
  call.getLocation().getStartLine() = 104
}

predicate selectedObservation(Assert observation) {
  observation.getLocation().getFile().getRelativePath() = testPath() and
  observation.getLocation().getStartLine() in [106, 115]
}

from string relation, string sourcePath, int sourceLine, string targetPath, int targetLine,
  string detail, string modality
where
  (
    exists(Function function |
      productFunction(function) and relation = "product_function" and
      sourcePath = productPath() and sourceLine = function.getLocation().getStartLine() and
      targetPath = productPath() and targetLine = 118 and
      detail = function.getName() and modality = "certain"
    )
    or
    exists(Call call |
      selectedTestCall(call) and relation = "test_dynamic_call_syntax" and
      sourcePath = testPath() and sourceLine = call.getLocation().getStartLine() and
      targetPath = productPath() and targetLine = 94 and detail = "frontend_bridge_required" and
      modality = "authenticated_external_bridge"
    )
    or
    exists(Call call, Assert observation, Name resultUse |
      selectedTestCall(call) and selectedObservation(observation) and
      observation.getLocation().getStartLine() = 106 and
      resultUse.getId() = "result" and resultUse.getLocation().getStartLine() = 106 and
      resultUse.getScope() = observation.getScope() and observation.getTest().contains(resultUse) and
      DataFlow::localFlow(DataFlow::exprNode(call), DataFlow::exprNode(resultUse)) and
      relation = "test_call_result_to_observation" and sourcePath = testPath() and
      sourceLine = call.getLocation().getStartLine() and targetPath = testPath() and
      targetLine = observation.getLocation().getStartLine() and detail = "criterion:0" and
      modality = "analysis_may"
    )
    or
    exists(Function function, Return returned |
      productFunction(function) and returned.getScope() = function and
      relation = "product_return" and sourcePath = productPath() and
      sourceLine = returned.getLocation().getStartLine() and targetPath = productPath() and
      targetLine = 94 and detail = "is_encrypted" and modality = "analysis_may"
    )
    or
    exists(Function function, Call decryptCall |
      productFunction(function) and decryptCall.getScope() = function and
      decryptCall.getFunc().(Attribute).getName() = "decrypt_api_key" and
      DataFlow::localFlow(DataFlow::parameterNode(function.getArg(0)),
                          DataFlow::exprNode(decryptCall.getArg(0))) and
      relation = "parameter_to_decrypt_argument" and sourcePath = productPath() and
      sourceLine = function.getArg(0).getLocation().getStartLine() and targetPath = productPath() and
      targetLine = decryptCall.getLocation().getStartLine() and detail = "argument:0" and
      modality = "analysis_may"
    )
    or
    exists(Function function, Return returned, Name valueUse |
      productFunction(function) and returned.getScope() = function and
      returned.getLocation().getStartLine() = 115 and returned.contains(valueUse) and
      valueUse.getId() = "value" and
      DataFlow::localFlow(DataFlow::parameterNode(function.getArg(0)), DataFlow::exprNode(valueUse)) and
      relation = "parameter_to_return_value_use" and sourcePath = productPath() and
      sourceLine = function.getArg(0).getLocation().getStartLine() and targetPath = productPath() and
      targetLine = returned.getLocation().getStartLine() and detail = "return_comparison" and
      modality = "analysis_may"
    )
    or
    exists(Function function, Call dependency, string name |
      productFunction(function) and dependency.getScope() = function and
      (
        dependency.getFunc().(Name).getId() = "get_settings_service" and name = "get_settings_service"
        or
        dependency.getFunc().(Attribute).getName() = "decrypt_api_key" and name = "decrypt_api_key"
      ) and
      relation = "product_dependency_call" and sourcePath = productPath() and
      sourceLine = dependency.getLocation().getStartLine() and targetPath = productPath() and
      targetLine = 94 and detail = name and modality = "unknown_effect"
    )
    or
    exists(Function function, Call settingsCall, Call decryptCall |
      productFunction(function) and settingsCall.getScope() = function and decryptCall.getScope() = function and
      settingsCall.getFunc().(Name).getId() = "get_settings_service" and
      decryptCall.getFunc().(Attribute).getName() = "decrypt_api_key" and
      DataFlow::localFlow(DataFlow::exprNode(settingsCall), DataFlow::exprNode(decryptCall.getArg(1))) and
      relation = "settings_result_to_decrypt_argument" and sourcePath = productPath() and
      sourceLine = settingsCall.getLocation().getStartLine() and targetPath = productPath() and
      targetLine = decryptCall.getLocation().getStartLine() and detail = "argument:1" and
      modality = "analysis_may"
    )
    or
    exists(Function function, Call decryptCall, Name decryptedUse |
      productFunction(function) and decryptCall.getScope() = function and
      decryptCall.getFunc().(Attribute).getName() = "decrypt_api_key" and
      decryptedUse.getScope() = function and decryptedUse.getId() = "decrypted" and
      decryptedUse.getLocation().getStartLine() in [111, 115] and
      DataFlow::localFlow(DataFlow::exprNode(decryptCall), DataFlow::exprNode(decryptedUse)) and
      relation = "decrypt_result_to_downstream_use" and sourcePath = productPath() and
      sourceLine = decryptCall.getLocation().getStartLine() and targetPath = productPath() and
      targetLine = decryptedUse.getLocation().getStartLine() and
      (targetLine = 111 and detail = "guard" or targetLine = 115 and detail = "return_comparison") and
      modality = "analysis_may"
    )
    or
    exists(Function function, Call decryptCall, Return handledReturn, ControlFlowNode callNode,
      ControlFlowNode exceptionalSuccessor, ControlFlowNode returnNode |
      productFunction(function) and decryptCall.getScope() = function and
      decryptCall.getFunc().(Attribute).getName() = "decrypt_api_key" and
      handledReturn.getScope() = function and handledReturn.getLocation().getStartLine() = 118 and
      callNode.getNode() = decryptCall and exceptionalSuccessor = callNode.getAnExceptionalSuccessor() and
      returnNode.getNode() = handledReturn and exceptionalSuccessor.strictlyReaches(returnNode) and
      relation = "decrypt_exception_to_handler_return" and sourcePath = productPath() and
      sourceLine = decryptCall.getLocation().getStartLine() and targetPath = productPath() and
      targetLine = handledReturn.getLocation().getStartLine() and detail = "caught_path_candidate" and
      modality = "analysis_may"
    )
    or
    exists(Function function, If guard, Return returned, ControlFlowNode guardNode,
      ControlFlowNode returnNode, boolean truth |
      productFunction(function) and guard.getScope() = function and returned.getScope() = function and
      guardNode.getNode() = guard.getTest() and returnNode.getNode() = returned and
      guardNode.getBasicBlock().(ConditionBlock).controls(returnNode.getBasicBlock(), truth) and
      relation = "guard_controls_product_return" and sourcePath = productPath() and
      sourceLine = guard.getLocation().getStartLine() and targetPath = productPath() and
      targetLine = returned.getLocation().getStartLine() and detail = truth.toString() and
      modality = "analysis_may"
    )
    or
    exists(Function function, Name invalidToken |
      productFunction(function) and invalidToken.getScope() = function and invalidToken.getId() = "InvalidToken" and
      relation = "product_global_reference" and sourcePath = productPath() and
      sourceLine = invalidToken.getLocation().getStartLine() and targetPath = productPath() and
      targetLine = 94 and detail = "InvalidToken" and modality = "analysis_may"
    )
    or
    exists(Assert observation, Compare identity, Name localClass, Subscript globalLookup,
      Attribute globals, Name functionObject, StringLiteral key |
      selectedObservation(observation) and observation.getLocation().getStartLine() = 115 and
      observation.getTest() = identity and identity.getOp(0) instanceof Is and
      identity.getLeft() = localClass and localClass.getId() = "invalid_token_cls" and
      identity.getComparator(0) = globalLookup and globalLookup.getValue() = globals and
      globals.getAttr() = "__globals__" and globals.getValue() = functionObject and
      functionObject.getId() = "is_encrypted" and globalLookup.getIndex() = key and
      key.getText() = "InvalidToken" and
      relation = "dynamic_global_identity_observation" and sourcePath = testPath() and
      sourceLine = observation.getLocation().getStartLine() and targetPath = productPath() and
      targetLine = 94 and detail = "frontend_namespace_bridge_required" and
      modality = "authenticated_external_bridge"
    )
  )
select relation, sourcePath, sourceLine, targetPath, targetLine, detail, modality
