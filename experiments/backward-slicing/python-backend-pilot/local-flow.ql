import python
import semmle.python.dataflow.new.DataFlow

predicate namedCall(Call call, string name) {
  call.getFunc().(Name).getId() = name
}

predicate inFunction(AstNode node, string name) {
  node.getScope().(Function).getName() = name
}

from Expr source, Call sink, string check
where
  check = "overwritten_unknown_return_reaches_observe" and
  namedCall(source.(Call), "unknown_value") and inFunction(source, "overwritten") and
  namedCall(sink, "observe") and inFunction(sink, "overwritten") and
  DataFlow::localFlow(DataFlow::exprNode(source), DataFlow::exprNode(sink.getArg(0)))
  or
  check = "constant_overwrite_reaches_observe" and
  source.(IntegerLiteral).getValue() = 0 and inFunction(source, "overwritten") and
  namedCall(sink, "observe") and inFunction(sink, "overwritten") and
  DataFlow::localFlow(DataFlow::exprNode(source), DataFlow::exprNode(sink.getArg(0)))
select check, source, sink
