package org.mockspec.research;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.ibm.wala.classLoader.JarFileModule;
import com.ibm.wala.classLoader.Language;
import com.ibm.wala.core.util.config.AnalysisScopeReader;
import com.ibm.wala.ipa.callgraph.AnalysisCacheImpl;
import com.ibm.wala.ipa.callgraph.AnalysisOptions;
import com.ibm.wala.ipa.callgraph.CGNode;
import com.ibm.wala.ipa.callgraph.impl.DefaultEntrypoint;
import com.ibm.wala.ipa.callgraph.impl.Util;
import com.ibm.wala.ipa.callgraph.impl.PartialCallGraph;
import com.ibm.wala.ipa.modref.ModRef;
import com.ibm.wala.ipa.callgraph.propagation.InstanceKey;
import com.ibm.wala.ipa.callgraph.propagation.PointerKey;
import com.ibm.wala.ipa.cha.ClassHierarchyFactory;
import com.ibm.wala.ssa.SSAAbstractInvokeInstruction;
import com.ibm.wala.ssa.SSAReturnInstruction;
import com.ibm.wala.ipa.slicer.SDG;
import com.ibm.wala.ipa.slicer.Slicer;
import com.ibm.wala.ipa.slicer.NormalStatement;
import com.ibm.wala.ipa.slicer.Statement;
import com.ibm.wala.ipa.slicer.thin.CISlicer;
import com.ibm.wala.types.ClassLoaderReference;
import com.ibm.wala.types.MethodReference;
import com.ibm.wala.util.intset.OrdinalSet;
import java.io.File;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.jar.JarFile;

/** Diagnostic only: estimate heap-parameter pressure without materializing PDGs or running targets. */
public final class WalaScaleProbe {
  private static long millis(long start) {
    return (System.nanoTime() - start) / 1_000_000;
  }

  public static void main(String[] args) throws Exception {
    if (args.length != 4 && args.length != 5) {
      throw new IllegalArgumentException("application.jar library-classpath entry-class entry-selector [ENTRY_SCOPE|RUNTIME_REGION|ENTRY_SCOPE_RUNTIME_REGION|ENTRY_SCOPE_CI_HEAP|ENTRY_SCOPE_RUNTIME_REGION_CI_HEAP]");
    }
    boolean entryScope = args.length == 5 && (args[4].equals("ENTRY_SCOPE")
        || args[4].equals("ENTRY_SCOPE_RUNTIME_REGION")
        || args[4].equals("ENTRY_SCOPE_CI_HEAP")
        || args[4].equals("ENTRY_SCOPE_RUNTIME_REGION_CI_HEAP"));
    boolean runtimeRegion = args.length == 5 && (args[4].equals("RUNTIME_REGION")
        || args[4].equals("ENTRY_SCOPE_RUNTIME_REGION")
        || args[4].equals("ENTRY_SCOPE_RUNTIME_REGION_CI_HEAP"));
    boolean ciHeap = args.length == 5 && (args[4].equals("ENTRY_SCOPE_CI_HEAP")
        || args[4].equals("ENTRY_SCOPE_RUNTIME_REGION_CI_HEAP"));
    if (args.length == 5 && !entryScope && !runtimeRegion) {
      throw new IllegalArgumentException("unknown diagnostic mode: " + args[4]);
    }
    long total = System.nanoTime();
    Map<String, Long> timings = new LinkedHashMap<>();
    long start = System.nanoTime();
    var scope = AnalysisScopeReader.instance.makeJavaBinaryAnalysisScope(args[0], null);
    if (!args[1].equals("-")) {
      for (String jar : args[1].split(File.pathSeparator)) {
        scope.addToScope(ClassLoaderReference.Extension, new JarFileModule(new JarFile(jar, false)));
      }
    }
    var hierarchy = ClassHierarchyFactory.make(scope);
    var reference = MethodReference.findOrCreate(ClassLoaderReference.Application,
        "L" + args[2].replace('.', '/'), args[3].substring(0, args[3].indexOf('(')),
        args[3].substring(args[3].indexOf('(')));
    var method = hierarchy.resolveMethod(reference);
    if (method == null) throw new IllegalArgumentException("entry not resolved: " + reference);
    var options = new AnalysisOptions(scope, List.of(new DefaultEntrypoint(method, hierarchy)));
    options.setReflectionOptions(AnalysisOptions.ReflectionOptions.NONE);
    timings.put("scope_hierarchy_entry", millis(start));
    start = System.nanoTime();
    var builder = Util.makeZeroOneCFABuilder(Language.JAVA, options, new AnalysisCacheImpl(), hierarchy, scope);
    var fullGraph = builder.makeCallGraph(options, null);
    var pointers = builder.getPointerAnalysis();
    List<CGNode> entryCandidates = fullGraph.getEntrypointNodes().stream()
        .filter(node -> node.getMethod().getReference().equals(reference))
        .collect(java.util.stream.Collectors.toList());
    if (entryCandidates.size() != 1) {
      throw new IllegalStateException("expected exactly one default-entry context for " + reference
          + ", found " + entryCandidates.size());
    }
    CGNode entryNode = entryCandidates.get(0);
    List<String> entrySites = new ArrayList<>();
    fullGraph.getPossibleSites(fullGraph.getFakeRootNode(), entryNode)
        .forEachRemaining(site -> entrySites.add(site.toString()));
    if (entrySites.size() != 1) {
      throw new IllegalStateException("expected one fake-root callsite for selected entry context, found "
          + entrySites.size());
    }
    var graph = entryScope ? PartialCallGraph.make(fullGraph, List.of(entryNode)) : fullGraph;
    timings.put("call_graph_pointer", millis(start));
    System.err.println("SCALE_STAGE call_graph_complete full_nodes=" + fullGraph.getNumberOfNodes()
        + " scoped_nodes=" + graph.getNumberOfNodes()
        + " instances=" + pointers.getInstanceKeys().size());
    start = System.nanoTime();
    ModRef<InstanceKey> modRef = runtimeRegion ? new RuntimeRegionModRef() : ModRef.make();
    Map<CGNode, OrdinalSet<PointerKey>> mods = ciHeap
        ? new HashMap<>() : modRef.computeMod(graph, pointers);
    timings.put("transitive_mod", millis(start));
    System.err.println("SCALE_STAGE transitive_mod_complete");
    start = System.nanoTime();
    Map<CGNode, OrdinalSet<PointerKey>> refs = ciHeap
        ? new HashMap<>() : modRef.computeRef(graph, pointers);
    timings.put("transitive_ref", millis(start));
    System.err.println("SCALE_STAGE transitive_ref_complete");
    start = System.nanoTime();
    List<Map<String, Object>> rows = new ArrayList<>();
    List<Map<String, Object>> callRows = new ArrayList<>();
    Map<String, Integer> loaders = new LinkedHashMap<>();
    long totalPressure = 0;
    for (CGNode node : graph) {
      String loader = node.getMethod().getDeclaringClass().getClassLoader().getReference().toString();
      loaders.merge(loader, 1, Integer::sum);
      var ir = node.getIR();
      if (ir == null) continue;
      long pressure = 0;
      int calls = 0;
      int maxCallPressure = 0;
      String largestCall = "";
      for (var instruction : ir.getInstructions()) {
        if (!(instruction instanceof SSAAbstractInvokeInstruction)) continue;
        calls++;
        var invoke = (SSAAbstractInvokeInstruction) instruction;
        OrdinalSet<PointerKey> callMods = OrdinalSet.empty();
        OrdinalSet<PointerKey> callRefs = OrdinalSet.empty();
        var targets = graph.getPossibleTargets(node, invoke.getCallSite());
        List<Map<String, Object>> targetSizes = new ArrayList<>();
        for (CGNode target : targets) {
          if (mods.get(target) != null) callMods = OrdinalSet.unify(callMods, mods.get(target));
          if (refs.get(target) != null) callRefs = OrdinalSet.unify(callRefs, refs.get(target));
          targetSizes.add(Map.of("method", target.getMethod().getSignature(),
              "context", target.getContext().toString(),
              "mod_regions", mods.get(target) == null ? 0 : mods.get(target).size(),
              "ref_regions", refs.get(target) == null ? 0 : refs.get(target).size()));
        }
        int callPressure = callMods.size() + callRefs.size();
        pressure += callPressure;
        if (callPressure > maxCallPressure) {
          maxCallPressure = callPressure;
          largestCall = invoke.getDeclaredTarget().getSignature();
        }
        if (callPressure > 0) {
          Map<String, Object> callRow = new LinkedHashMap<>();
          callRow.put("caller", node.getMethod().getSignature());
          callRow.put("context", node.getContext().toString());
          callRow.put("loader", loader);
          callRow.put("call_site", invoke.getCallSite().toString());
          callRow.put("declared_target", invoke.getDeclaredTarget().getSignature());
          callRow.put("targets", targetSizes);
          callRow.put("target_count", targets.size());
          callRow.put("scalar_uses", invoke.getNumberOfUses());
          callRow.put("mod_union", callMods.size());
          callRow.put("ref_union", callRefs.size());
          callRow.put("heap_parameter_pressure", callPressure);
          callRows.add(callRow);
        }
      }
      totalPressure += pressure;
      Map<String, Object> row = new LinkedHashMap<>();
      row.put("method", node.getMethod().getSignature());
      row.put("context", node.getContext().toString());
      row.put("loader", loader);
      row.put("instruction_slots", ir.getInstructions().length);
      row.put("callsites", calls);
      row.put("transitive_mod_regions", mods.get(node) == null ? 0 : mods.get(node).size());
      row.put("transitive_ref_regions", refs.get(node) == null ? 0 : refs.get(node).size());
      row.put("heap_parameter_pressure", pressure);
      row.put("largest_call_pressure", maxCallPressure);
      row.put("largest_call", largestCall);
      rows.add(row);
    }
    rows.sort(Comparator.<Map<String, Object>>comparingLong(
        row -> ((Number) row.get("heap_parameter_pressure")).longValue()).reversed());
    callRows.sort(Comparator.<Map<String, Object>>comparingLong(
        row -> ((Number) row.get("mod_union")).longValue()).reversed());
    timings.put("pressure_mapping", millis(start));
    timings.put("total", millis(total));
    Map<String, Object> result = new LinkedHashMap<>();
    result.put("schema", "mockspec.wala-scale-diagnostic/v1");
    result.put("entry", method.getSignature());
    result.put("entry_method_context_candidates", entryCandidates.size());
    result.put("entry_context", entryNode.getContext().toString());
    result.put("entry_fake_root_callsites", entrySites);
    result.put("entry_origin", "diagnostic_override");
    result.put("target_executed", false);
    result.put("call_graph_nodes", graph.getNumberOfNodes());
    result.put("full_call_graph_nodes", fullGraph.getNumberOfNodes());
    result.put("scope", entryScope ? "entry_forward_reachable" : "full_call_graph");
    result.put("heap_precision", runtimeRegion ? "primordial_runtime_region_merged_weak_update" : "original_wala");
    result.put("slice_algorithm", ciHeap ? "WALA_CISlicer_NO_HEAP_SDG_plus_ModRef_may_deps"
        : "WALA_FULL_SDG_IFDS");
    result.put("pointer_instance_keys", pointers.getInstanceKeys().size());
    result.put("nodes_by_loader", loaders);
    result.put("total_heap_parameter_pressure", totalPressure);
    result.put("pressure_profile_status", ciHeap
        ? "skipped: CISlicer scans statement-level Mod/Ref itself; avoid duplicate transitive whole-graph computation"
        : "computed");
    result.put("pressure_definition", "whole-call-graph upper bound: sum of per-call transitive callee Mod and Ref union cardinalities; not actual slice materialization or PDG node count");
    result.put("top_methods", rows.subList(0, Math.min(100, rows.size())));
    result.put("application_methods", rows.stream()
        .filter(row -> row.get("loader").toString().contains("Application"))
        .collect(java.util.stream.Collectors.toList()));
    result.put("top_calls_by_mod_union", callRows.subList(0, Math.min(100, callRows.size())));
    result.put("timings_millis", timings);
    result.put("semantic_result", "not_evaluated; diagnostic pressure evidence only");
    String profileReceipt = System.getProperty("mockspec.profile.receipt");
    if (profileReceipt != null) {
      new ObjectMapper().writerWithDefaultPrettyPrinter().writeValue(new File(profileReceipt), result);
    }
    if (entryScope || runtimeRegion) {
      mods.clear();
      refs.clear();
      start = System.nanoTime();
      var instructions = entryNode.getIR().getInstructions();
      int seedIndex = -1;
      for (int i = 0; i < instructions.length; i++) {
        if (instructions[i] instanceof SSAReturnInstruction) seedIndex = i;
      }
      if (seedIndex < 0) throw new IllegalStateException("entry has no return criterion");
      System.err.println("SCALE_STAGE scoped_full_slice_started");
      Collection<Statement> slice;
      if (ciHeap) {
        var slicer = new CISlicer(graph, pointers, modRef, Slicer.DataDependenceOptions.NO_HEAP,
            Slicer.ControlDependenceOptions.FULL);
        slice = slicer.computeBackwardThinSlice(new NormalStatement(entryNode, seedIndex));
      } else {
        var sdg = new SDG<>(graph, pointers, modRef, Slicer.DataDependenceOptions.FULL,
            Slicer.ControlDependenceOptions.FULL);
        slice = Slicer.computeBackwardSlice(sdg, new NormalStatement(entryNode, seedIndex));
      }
      List<Map<String, Object>> members = new ArrayList<>();
      for (var statement : slice) {
        members.add(Map.of("method", statement.getNode().getMethod().getSignature(),
            "kind", statement.getKind().toString(), "statement", statement.toString()));
      }
      result.put("slice_statements", members);
      result.put("slice_statement_count", members.size());
      result.put("excluded_boundary", entryScope
          ? "pre-entry global/class initialization and callers are unknown, not absent effects; "
              + (runtimeRegion ? "runtime field distinctions merged as a weakly-updated unknown region"
                  : "original WALA heap locations retained")
          : "no CG nodes or heap effects excluded; runtime field distinctions merged as unknown");
      result.put("semantic_result", "partial diagnostic slice; boundary effects and original test observation mapping not yet validated; not formal Jib acceptance");
      if (ciHeap) {
        result.put("ci_heap_precision_boundary", "upstream experimental context-insensitive may-dependencies; overwritten writes and cross-call paths may be retained; native/no-IR effects remain explicit unknown boundaries");
      }
      timings.put("scoped_full_sdg_and_slice", millis(start));
      timings.put("total", millis(total));
      System.err.println("SCALE_STAGE scoped_full_slice_complete statements=" + members.size());
    }
    new ObjectMapper().writerWithDefaultPrettyPrinter().writeValue(System.out, result);
  }
}
