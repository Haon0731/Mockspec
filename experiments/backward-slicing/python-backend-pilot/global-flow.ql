import python
import semmle.python.dataflow.new.DataFlow

predicate namedCall(Call call, string name) {
  call.getFunc().(Name).getId() = name
}

private module ProbeConfig implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) {
    exists(Function f |
      f.getName() = "interprocedural" and
      source = DataFlow::parameterNode(f.getArg(0))
    )
  }

  predicate isSink(DataFlow::Node sink) {
    exists(Call call |
      namedCall(call, "observe") and
      call.getScope().(Function).getName() = "interprocedural" and
      sink = DataFlow::exprNode(call.getArg(0))
    )
  }
}

module ProbeFlow = DataFlow::Global<ProbeConfig>;

from DataFlow::Node source, DataFlow::Node sink, string check
where
  check = "actual_formal_return_observe" and
  ProbeFlow::flow(source, sink)
select check, source, sink
