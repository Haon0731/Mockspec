import python
import semmle.python.dataflow.new.DataFlow
import semmle.python.dataflow.new.internal.Attributes
import DemandRoots

predicate uniqueSameRegion(DataFlow::Node left, DataFlow::Node right) {
  left.getALocalSource() = right.getALocalSource() and
  count(DataFlow::LocalSourceNode source | source = left.getALocalSource()) = 1 and
  count(DataFlow::LocalSourceNode source | source = right.getALocalSource()) = 1
}

predicate selectedRead(Function function, DataFlow::AttrRead read) {
  read.getScope() = function and
  DemandRoots::selectedStateObservation(function, read.asCfgNode().getNode()) and
  exists(Return statement |
    statement.getScope() = function and
    statement.getValue() = read.asCfgNode().getNode()
  )
}

predicate noLaterFieldKill(DataFlow::AttrWrite write, DataFlow::AttrRead observed) {
  not exists(DataFlow::AttrWrite later |
    uniqueSameRegion(write.getObject(), later.getObject()) and
    later.getAttributeName() = observed.getAttributeName() and
    write.asCfgNode().strictlyReaches(later.asCfgNode()) and
    not later.asCfgNode().strictlyReaches(write.asCfgNode()) and
    later.asCfgNode().dominates(observed.asCfgNode())
  )
}

from Function function, DataFlow::AttrWrite write, DataFlow::AttrRead observed
where
  write.getScope() = function and selectedRead(function, observed) and
  uniqueSameRegion(write.getObject(), observed.getObject()) and
  write.getAttributeName() = observed.getAttributeName() and
  write.asCfgNode().strictlyReaches(observed.asCfgNode()) and
  noLaterFieldKill(write, observed)
select "visible_write_to_read" as relation, function.getName() as functionName,
  observed.getAttributeName() as fieldName,
  write.getObject().getALocalSource().toString() as regionKey,
  write, observed,
  write.getLocation().getStartLine() as writeLine,
  observed.getLocation().getStartLine() as observedLine
