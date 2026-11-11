import python
import semmle.python.dataflow.new.DataFlow
import semmle.python.dataflow.new.internal.Attributes

predicate inFunction(AstNode node, string name) {
  node.getScope().(Function).getName() = name
}

from string relation, string functionName, AstNode evidence, string detail
where
  exists(DataFlow::AttrWrite write |
    write.getAttributeName() = "ready" and
    inFunction(write.asCfgNode().getNode(), functionName) and
    functionName in ["visible_write", "unrelated_write", "proven_fresh_write"] and
    evidence = write.asCfgNode().getNode() and detail = write.getObject().toString() and
    relation = "visible_attr_write"
  )
  or
  exists(DataFlow::AttrRead read |
    read.getAttributeName() = "ready" and
    inFunction(read.asCfgNode().getNode(), functionName) and
    functionName in ["visible_write", "unrelated_write", "proven_fresh_write", "unknown_write", "caught_and_finally"] and
    evidence = read.asCfgNode().getNode() and detail = read.getObject().toString() and
    relation = "attr_read"
  )
  or
  exists(Call call |
    call.getFunc().(Attribute).getName() = "fill" and
    inFunction(call, functionName) and
    evidence = call and detail = "call_only_no_syntactic_attr_write" and
    relation = "unknown_fill_call"
  )
select relation, functionName, evidence, detail, evidence.getLocation().getStartLine()
