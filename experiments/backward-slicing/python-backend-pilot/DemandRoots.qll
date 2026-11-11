import python

/** Placeholder roots. The demand runner replaces this module in its temporary query pack. */
module DemandRoots {
  predicate selectedTestPath(string path) { none() }
  predicate selectedObservation(AstNode node) { none() }
  predicate selectedGuard(If guard) { none() }
  predicate selectedTestCall(Call call) { none() }
  predicate selectedProductFunction(Function function) { none() }
  predicate selectedStateObservation(Function function, AstNode node) { none() }
  predicate selectedRecursiveFunction(Function function) { none() }
  predicate selectedRecursiveObservation(Function function, int line) { none() }
}
