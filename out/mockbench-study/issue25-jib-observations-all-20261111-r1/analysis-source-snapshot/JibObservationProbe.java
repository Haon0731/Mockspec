package org.mockspec.research;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.ibm.wala.classLoader.JarFileModule;
import com.ibm.wala.classLoader.Language;
import com.ibm.wala.core.util.config.AnalysisScopeReader;
import com.ibm.wala.ipa.callgraph.AnalysisCacheImpl;
import com.ibm.wala.ipa.callgraph.AnalysisOptions;
import com.ibm.wala.ipa.callgraph.CGNode;
import com.ibm.wala.ipa.callgraph.CallGraph;
import com.ibm.wala.ipa.callgraph.impl.DefaultEntrypoint;
import com.ibm.wala.ipa.callgraph.impl.PartialCallGraph;
import com.ibm.wala.ipa.callgraph.impl.Util;
import com.ibm.wala.ipa.cha.ClassHierarchyFactory;
import com.ibm.wala.ipa.callgraph.propagation.InstanceKey;
import com.ibm.wala.ipa.callgraph.propagation.PointerAnalysis;
import com.ibm.wala.ipa.callgraph.propagation.PointerKey;
import com.ibm.wala.ipa.modref.ModRef;
import com.ibm.wala.ipa.modref.ExtendedHeapModel;
import com.ibm.wala.ipa.slicer.NormalStatement;
import com.ibm.wala.ipa.slicer.Slicer;
import com.ibm.wala.ipa.slicer.Statement;
import com.ibm.wala.ipa.slicer.StatementWithInstructionIndex;
import com.ibm.wala.ipa.slicer.thin.CISlicer;
import com.ibm.wala.ssa.SSAAbstractInvokeInstruction;
import com.ibm.wala.ssa.SSAInstruction;
import com.ibm.wala.types.ClassLoaderReference;
import com.ibm.wala.types.MethodReference;
import java.io.File;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.jar.JarFile;

/**
 * Analysis-only adapter that seeds WALA's context-insensitive slicer at each mapped observation sink.
 * It never executes the target and does not infer concrete Mock values.
 */
public final class JibObservationProbe {
  private static long millis(long start) {
    return (System.nanoTime() - start) / 1_000_000;
  }

  private static List<String> pointerKeys(Iterable<PointerKey> keys) {
    List<String> result = new ArrayList<>();
    if (keys != null) for (PointerKey key : keys) result.add(key.toString());
    result.sort(String::compareTo);
    return result;
  }

  private static Map<String, Object> localPointsTo(
      CGNode node, int value, PointerAnalysis<InstanceKey> pointers) {
    if (value <= 0) {
      return Map.of(
          "value_number", value,
          "pointer_key", "not-a-local-constant-or-exception",
          "points_to_count", 0,
          "instance_keys", List.of());
    }
    PointerKey key = pointers.getHeapModel().getPointerKeyForLocal(node, value);
    List<String> instances = new ArrayList<>();
    for (InstanceKey instance : pointers.getPointsToSet(key)) instances.add(instance.toString());
    instances.sort(String::compareTo);
    Map<String, Object> result = new LinkedHashMap<>();
    result.put("value_number", value);
    result.put("pointer_key", key.toString());
    result.put("points_to_count", instances.size());
    result.put("instance_keys", instances);
    return result;
  }

  private static Map<String, Object> statement(
      Statement item, ModRef<InstanceKey> modRef, PointerAnalysis<InstanceKey> pointers,
      ExtendedHeapModel heapModel, CallGraph graph) {
    Map<String, Object> row = new LinkedHashMap<>();
    row.put("kind", item.getKind().toString());
    row.put("method", item.getNode().getMethod().getSignature());
    row.put("context", item.getNode().getContext().toString());
    row.put("loader", item.getNode().getMethod().getDeclaringClass()
        .getClassLoader().getReference().toString());
    row.put("statement", item.toString());
    if (item instanceof StatementWithInstructionIndex) {
      int index = ((StatementWithInstructionIndex) item).getInstructionIndex();
      row.put("instruction_index", index);
      SSAInstruction instruction = item.getNode().getIR().getInstructions()[index];
      row.put("instruction", instruction == null ? null : instruction.toString());
      if (instruction != null) {
        List<Integer> uses = new ArrayList<>();
        for (int use = 0; use < instruction.getNumberOfUses(); use++) uses.add(instruction.getUse(use));
        List<Integer> definitions = new ArrayList<>();
        for (int definition = 0; definition < instruction.getNumberOfDefs(); definition++) {
          definitions.add(instruction.getDef(definition));
        }
        row.put("uses", uses);
        row.put("definitions", definitions);
        row.put("mod_regions", pointerKeys(modRef.getMod(
            item.getNode(), heapModel, pointers, instruction, null)));
        row.put("ref_regions", pointerKeys(modRef.getRef(
            item.getNode(), heapModel, pointers, instruction, null)));
      }
      if (instruction instanceof SSAAbstractInvokeInstruction) {
        SSAAbstractInvokeInstruction invoke = (SSAAbstractInvokeInstruction) instruction;
        row.put("call_site", invoke.getCallSite().toString());
        row.put("declared_target", invoke.getDeclaredTarget().getSignature());
        List<String> targets = new ArrayList<>();
        List<Map<String, Object>> targetDetails = new ArrayList<>();
        for (CGNode target : graph.getPossibleTargets(item.getNode(), invoke.getCallSite())) {
          targets.add(target.getMethod().getSignature() + " @ " + target.getContext());
          List<Integer> parameters = new ArrayList<>();
          if (target.getIR() != null) {
            for (int parameter = 0; parameter < target.getMethod().getNumberOfParameters(); parameter++) {
              parameters.add(target.getIR().getParameter(parameter));
            }
          }
          Map<String, Object> detail = new LinkedHashMap<>();
          detail.put("method", target.getMethod().getSignature());
          detail.put("context", target.getContext().toString());
          detail.put("native", target.getMethod().isNative());
          detail.put("has_ir", target.getIR() != null);
          detail.put("parameter_value_numbers", parameters);
          targetDetails.add(detail);
        }
        targets.sort(String::compareTo);
        row.put("possible_targets", targets);
        row.put("possible_target_details", targetDetails);
        List<Map<String, Object>> arguments = new ArrayList<>();
        for (int use = 0; use < invoke.getNumberOfUses(); use++) {
          Map<String, Object> argument = new LinkedHashMap<>(
              localPointsTo(item.getNode(), invoke.getUse(use), pointers));
          argument.put("argument_index", use);
          arguments.add(argument);
        }
        row.put("argument_points_to", arguments);
        if (invoke.getNumberOfDefs() > 0) {
          row.put("result_points_to", localPointsTo(
              item.getNode(), invoke.getDef(), pointers));
        }
      }
    }
    return row;
  }

  private static boolean retain(Statement item) {
    String loader = item.getNode().getMethod().getDeclaringClass()
        .getClassLoader().getReference().toString();
    String method = item.getNode().getMethod().getSignature();
    return loader.contains("Application") || method.startsWith("com.google.cloud.tools.jib.");
  }

  public static void main(String[] args) throws Exception {
    if (args.length != 5) {
      throw new IllegalArgumentException(
          "application.jar library-classpath entry-class entry-selector criterion1,criterion2,...");
    }
    long total = System.nanoTime();
    long started = System.nanoTime();
    var scope = AnalysisScopeReader.instance.makeJavaBinaryAnalysisScope(args[0], null);
    for (String jar : args[1].split(File.pathSeparator)) {
      scope.addToScope(ClassLoaderReference.Extension, new JarFileModule(new JarFile(jar, false)));
    }
    var hierarchy = ClassHierarchyFactory.make(scope);
    int selectorStart = args[3].indexOf('(');
    var reference = MethodReference.findOrCreate(ClassLoaderReference.Application,
        "L" + args[2].replace('.', '/'), args[3].substring(0, selectorStart),
        args[3].substring(selectorStart));
    var method = hierarchy.resolveMethod(reference);
    if (method == null) throw new IllegalArgumentException("entry not resolved: " + reference);
    var options = new AnalysisOptions(scope, List.of(new DefaultEntrypoint(method, hierarchy)));
    options.setReflectionOptions(AnalysisOptions.ReflectionOptions.NONE);
    Map<String, Long> timings = new LinkedHashMap<>();
    timings.put("scope_hierarchy_entry", millis(started));

    started = System.nanoTime();
    var builder = Util.makeZeroOneCFABuilder(
        Language.JAVA, options, new AnalysisCacheImpl(), hierarchy, scope);
    var fullGraph = builder.makeCallGraph(options, null);
    var pointers = builder.getPointerAnalysis();
    List<CGNode> candidates = fullGraph.getEntrypointNodes().stream()
        .filter(node -> node.getMethod().getReference().equals(reference))
        .collect(java.util.stream.Collectors.toList());
    if (candidates.size() != 1) {
      throw new IllegalStateException("expected one default entry context, found " + candidates.size());
    }
    CGNode entry = candidates.get(0);
    List<String> entrySites = new ArrayList<>();
    fullGraph.getPossibleSites(fullGraph.getFakeRootNode(), entry)
        .forEachRemaining(site -> entrySites.add(site.toString()));
    if (entrySites.size() != 1) {
      throw new IllegalStateException("expected one fake-root entry callsite, found " + entrySites.size());
    }
    var graph = PartialCallGraph.make(fullGraph, List.of(entry));
    timings.put("call_graph_pointer", millis(started));

    started = System.nanoTime();
    var modRef = ModRef.make();
    ExtendedHeapModel heapModel = modRef.makeHeapModel(pointers);
    var slicer = new CISlicer(graph, pointers, modRef, Slicer.DataDependenceOptions.NO_HEAP,
        Slicer.ControlDependenceOptions.FULL);
    timings.put("ci_graph", millis(started));

    Map<String, NormalStatement> seeds = new LinkedHashMap<>();
    Set<String> queries = new LinkedHashSet<>(List.of(args[4].split(",")));
    SSAInstruction[] instructions = entry.getIR().getInstructions();
    for (int index = 0; index < instructions.length; index++) {
      if (!(instructions[index] instanceof SSAAbstractInvokeInstruction)) continue;
      SSAAbstractInvokeInstruction invoke = (SSAAbstractInvokeInstruction) instructions[index];
      String signature = invoke.getDeclaredTarget().getSignature();
      for (String query : queries) {
        if (!signature.equals(query)) continue;
        if (seeds.put(query, new NormalStatement(entry, index)) != null) {
          throw new IllegalStateException("duplicate criterion seed: " + query);
        }
      }
    }
    if (!seeds.keySet().equals(queries)) {
      Set<String> missing = new LinkedHashSet<>(queries);
      missing.removeAll(seeds.keySet());
      throw new IllegalStateException("criterion seeds absent: " + missing + "\n" + entry.getIR());
    }

    List<Map<String, Object>> observations = new ArrayList<>();
    for (String query : queries) {
      started = System.nanoTime();
      NormalStatement seed = seeds.get(query);
      Collection<Statement> slice = slicer.computeBackwardThinSlice(seed);
      List<Map<String, Object>> retained = new ArrayList<>();
      for (Statement item : slice) {
        if (retain(item)) retained.add(statement(item, modRef, pointers, heapModel, graph));
      }
      Map<String, Object> observation = new LinkedHashMap<>();
      observation.put("criterion", query);
      observation.put("seed", statement(seed, modRef, pointers, heapModel, graph));
      observation.put("slice_statement_count", slice.size());
      observation.put("retained_statement_count", retained.size());
      observation.put("statements", retained);
      observation.put("slice_millis", millis(started));
      observations.add(observation);
    }
    timings.put("total", millis(total));

    Map<String, Object> result = new LinkedHashMap<>();
    result.put("schema", "mockspec.jib-original-observation-probe/v1");
    result.put("wala_version", "1.7.2");
    result.put("target_executed", false);
    result.put("entry_origin", "diagnostic_override_mapped_from_original_observations");
    result.put("entry", method.getSignature());
    result.put("entry_context", entry.getContext().toString());
    List<Map<String, Object>> entryParameters = new ArrayList<>();
    for (int parameter = 0; parameter < method.getNumberOfParameters(); parameter++) {
      Map<String, Object> facts = new LinkedHashMap<>(localPointsTo(entry, parameter + 1, pointers));
      facts.put("parameter_index", parameter);
      entryParameters.add(facts);
    }
    result.put("entry_parameter_points_to", entryParameters);
    result.put("entry_fake_root_callsites", entrySites);
    result.put("full_call_graph_nodes", fullGraph.getNumberOfNodes());
    result.put("entry_reachable_call_graph_nodes", graph.getNumberOfNodes());
    result.put("pointer_instance_keys", pointers.getInstanceKeys().size());
    result.put("configuration", Map.of(
        "call_graph", "0-1-CFA",
        "reflection", "NONE",
        "heap_locations", "original_wala",
        "slice", "CISlicer_NO_HEAP_base_SDG_plus_statement_ModRef_may_dependencies",
        "control_dependence", "FULL"));
    result.put("observations", observations);
    result.put("timings_millis", timings);
    result.put("limitations", List.of(
        "The structural entry is diagnostic; automatic original test-to-MUT binding is separate evidence.",
        "Context-insensitive heap may-dependencies can retain overwritten or unrelated writes.",
        "Native/no-IR effects and pre-entry initialization remain explicit unknown boundaries.",
        "Slice membership is may-influence evidence, not path feasibility or a concrete Mock value."));
    new ObjectMapper().writerWithDefaultPrettyPrinter().writeValue(System.out, result);
  }
}
