import io.shiftleft.semanticcpg.language.*
import io.joern.dataflowengineoss.language.*
import java.nio.file.{Files, Paths}

@main def exec(cpgFile: String) = {
  importCpg(cpgFile)
  run.ossdataflow
  def pos(n: io.shiftleft.codepropertygraph.generated.nodes.AstNode): ujson.Value = {
    ujson.Obj("id" -> n.id.toString, "label" -> n.label, "file" -> n.start.inAst.isMethod.headOption.map(_.filename).getOrElse("<unknown>"),
      "line" -> n.lineNumber.map(ujson.Num(_)).getOrElse(ujson.Null),
      "code" -> n.code.take(240))
  }
  val methods = cpg.method.filterNot(_.isExternal).map { m =>
    ujson.Obj("id" -> m.id.toString, "name" -> m.name, "fullName" -> m.fullName,
      "file" -> m.filename, "line" -> m.lineNumber.map(ujson.Num(_)).getOrElse(ujson.Null))
  }.l
  val calls = cpg.call.map { c =>
    ujson.Obj("id" -> c.id.toString, "name" -> c.name, "methodFullName" -> c.methodFullName,
      "dispatchType" -> c.dispatchType, "file" -> c.method.filename,
      "line" -> c.lineNumber.map(ujson.Num(_)).getOrElse(ujson.Null),
      "code" -> c.code.take(240),
      "controlledBy" -> ujson.Arr.from(c.start.controlledBy.map(pos).l))
  }.l
  val conditions = cpg.controlStructure.map { c =>
    ujson.Obj("id" -> c.id.toString, "type" -> c.controlStructureType,
      "file" -> c.method.filename, "line" -> c.lineNumber.map(ujson.Num(_)).getOrElse(ujson.Null),
      "conditions" -> ujson.Arr.from(c.start.condition.map(pos).l))
  }.l
  // Fixed sources/sinks. These are data-flow paths, never claimed as executable paths.
  def sources = cpg.call.name("getJavaVersion|getEntrypoint|getFrom|get|createLayers|computeEntrypoint|.*abort.*")
  def sinks = cpg.call.name("isEmpty|isPresent|setEntrypoint|setFileEntriesLayers|assert.*|<operator>.*")
  val flows = sinks.reachableByFlows(sources).take(101).map { flow =>
    ujson.Arr.from(flow.elements.map(pos))
  }.l
  val result = ujson.Obj("status" -> "queried", "methods" -> ujson.Arr.from(methods),
    "calls" -> ujson.Arr.from(calls), "controlStructures" -> ujson.Arr.from(conditions),
    "dataflow_paths" -> ujson.Arr.from(flows.take(100)), "paths_truncated" -> (flows.size > 100),
    "limitations" -> "Fixed source/sink candidate paths; no path feasibility or hidden implementation proof")
  Files.writeString(Paths.get("/work/facts.json"), ujson.write(result, indent = 2))
  save
}
