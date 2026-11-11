package org.mockspec.research;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.ibm.wala.classLoader.Language;
import com.ibm.wala.core.util.config.AnalysisScopeReader;
import com.ibm.wala.ipa.callgraph.AnalysisCacheImpl;
import com.ibm.wala.ipa.callgraph.AnalysisOptions;
import com.ibm.wala.ipa.callgraph.CGNode;
import com.ibm.wala.ipa.callgraph.CallGraph;
import com.ibm.wala.ipa.callgraph.impl.DefaultEntrypoint;
import com.ibm.wala.ipa.callgraph.impl.PartialCallGraph;
import com.ibm.wala.ipa.callgraph.impl.Util;
import com.ibm.wala.ipa.callgraph.propagation.InstanceKey;
import com.ibm.wala.ipa.callgraph.propagation.PointerAnalysis;
import com.ibm.wala.ipa.cha.ClassHierarchyFactory;
import com.ibm.wala.ipa.modref.ModRef;
import com.ibm.wala.ipa.slicer.NormalReturnCaller;
import com.ibm.wala.ipa.slicer.NormalStatement;
import com.ibm.wala.ipa.slicer.PDG;
import com.ibm.wala.ipa.slicer.SDG;
import com.ibm.wala.ipa.slicer.Slicer;
import com.ibm.wala.ipa.slicer.Statement;
import com.ibm.wala.ipa.slicer.thin.CISlicer;
import com.ibm.wala.ipa.slicer.thin.CISDG;
import com.ibm.wala.ssa.SSAAbstractInvokeInstruction;
import com.ibm.wala.ssa.SSAInstruction;
import com.ibm.wala.ssa.SSAReturnInstruction;
import com.ibm.wala.types.ClassLoaderReference;
import com.ibm.wala.types.MethodReference;
import com.ibm.wala.util.graph.impl.GraphInverter;
import com.ibm.wala.util.graph.traverse.SlowDFSFinishTimeIterator;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/** Measures real WALA lazy PDGs, including a recursive method boundary. */
public final class DemandMechanismProbe {
  private static String identity(CGNode node) {
    return node.getMethod().getSignature() + "@" + node.getContext();
  }

  private static final class MethodBudget extends RuntimeException {
    MethodBudget(String method) { super(method); }
  }

  private static final class MeasuredSDG extends SDG<InstanceKey> {
    final Set<String> loaded = new LinkedHashSet<>();
    final Set<String> frontier = new LinkedHashSet<>();
    final int budget;

    MeasuredSDG(CallGraph graph, PointerAnalysis<InstanceKey> pointers,
        ModRef<InstanceKey> modRef, int budget) {
      super(graph, pointers, modRef, Slicer.DataDependenceOptions.NO_HEAP,
          Slicer.ControlDependenceOptions.NONE);
      this.budget = budget;
    }

    @Override public PDG<InstanceKey> getPDG(CGNode node) {
      String method = identity(node);
      if (!loaded.contains(method) && loaded.size() >= budget) {
        frontier.add(method);
        throw new MethodBudget(method);
      }
      PDG<InstanceKey> result = super.getPDG(node);
      loaded.add(method);
      return result;
    }
  }

  private static Set<String> statements(Collection<Statement> slice) {
    Set<String> result = new TreeSet<>();
    for (Statement statement : slice) result.add(statement.toString());
    return result;
  }

  private static final class DemandGraph extends CISDG {
    DemandGraph(SDG<InstanceKey> sdg, PointerAnalysis<InstanceKey> pointers, ModRef<InstanceKey> modRef) {
      super(sdg, CISlicer.scanForMod(sdg, pointers, modRef), CISlicer.scanForRef(sdg, pointers, modRef));
    }

    Set<Statement> backward(Statement seed) {
      Set<Statement> reached = new LinkedHashSet<>();
      // WALA's numbered DFS requests getMaxNumber(), which eagerly builds every PDG.
      // The hash-backed WALA iterator traverses the same predecessor relation lazily.
      var iterator = new SlowDFSFinishTimeIterator<Statement>(GraphInverter.invert(this), seed);
      iterator.forEachRemaining(reached::add);
      return reached;
    }
  }

  public static void main(String[] args) throws Exception {
    if (args.length != 1) throw new IllegalArgumentException("fixture.jar");
    long started = System.nanoTime();
    var scope = AnalysisScopeReader.instance.makeJavaBinaryAnalysisScope(args[0], null);
    var hierarchy = ClassHierarchyFactory.make(scope);
    var reference = MethodReference.findOrCreate(ClassLoaderReference.Application,
        "Lsample/DemandScopeFixture", "observe", "(II)I");
    var method = hierarchy.resolveMethod(reference);
    if (method == null) throw new IllegalStateException("fixture entry not resolved");
    var options = new AnalysisOptions(scope, List.of(new DefaultEntrypoint(method, hierarchy)));
    options.setReflectionOptions(AnalysisOptions.ReflectionOptions.NONE);
    var builder = Util.makeZeroOneCFABuilder(Language.JAVA, options, new AnalysisCacheImpl(), hierarchy, scope);
    var fullGraph = builder.makeCallGraph(options, null);
    var entries = new ArrayList<CGNode>();
    for (CGNode node : fullGraph.getEntrypointNodes()) {
      if (node.getMethod().getReference().equals(reference)) entries.add(node);
    }
    if (entries.size() != 1) throw new IllegalStateException("fixture entry context not unique");
    CGNode entry = entries.get(0);
    var graph = PartialCallGraph.make(fullGraph, List.of(entry));
    var pointers = builder.getPointerAnalysis();
    var modRef = ModRef.make();
    long cgDone = System.nanoTime();
    Statement seed = null;
    Statement directSeed = null;
    SSAInstruction[] instructions = entry.getIR().getInstructions();
    for (int index = 0; index < instructions.length; index++) {
      if (instructions[index] instanceof SSAReturnInstruction) seed = new NormalStatement(entry, index);
      if (instructions[index] instanceof SSAAbstractInvokeInstruction
          && ((SSAAbstractInvokeInstruction) instructions[index]).getDeclaredTarget().getName().toString().equals("direct")) {
        directSeed = new NormalReturnCaller(entry, index);
      }
    }
    if (seed == null || directSeed == null) throw new IllegalStateException("fixture observations missing");
    var lazy = new MeasuredSDG(graph, pointers, modRef, Integer.MAX_VALUE);
    int beforeConstructor = lazy.loaded.size();
    var lazySlicer = new DemandGraph(lazy, pointers, modRef);
    int beforeSlice = lazy.loaded.size();
    Set<String> lazySlice = statements(lazySlicer.backward(seed));
    long lazyDone = System.nanoTime();

    var eager = new MeasuredSDG(graph, pointers, modRef, Integer.MAX_VALUE);
    // Iteration requests WALA's full construction; getPDG counts stay observable.
    eager.iterator().forEachRemaining(statement -> { });
    Set<String> eagerSlice = statements(new CISlicer(eager, pointers, modRef).computeBackwardThinSlice(seed));
    long eagerDone = System.nanoTime();

    var limited = new MeasuredSDG(graph, pointers, modRef, 2);
    var limitedSlicer = new DemandGraph(limited, pointers, modRef);
    Set<String> completedEvidence = statements(limitedSlicer.backward(directSeed));
    boolean exhausted = false;
    try { limitedSlicer.backward(seed); }
    catch (MethodBudget expected) { exhausted = true; }
    List<Map<String, String>> recursion = new ArrayList<>();
    boolean unrelatedReachable = false;
    for (CGNode from : graph) {
      String fromName = from.getMethod().getName().toString();
      if (fromName.equals("unrelated")) unrelatedReachable = true;
      var successors = graph.getSuccNodes(from);
      while (successors.hasNext()) {
        CGNode to = successors.next();
        String toName = to.getMethod().getName().toString();
        if ((fromName.equals("left") && toName.equals("right"))
            || (fromName.equals("right") && toName.equals("left"))) {
          recursion.add(Map.of("from", identity(from), "to", identity(to)));
        }
      }
    }
    Map<String, Boolean> checks = new LinkedHashMap<>();
    checks.put("no_pdg_before_slicer", beforeConstructor == 0);
    checks.put("no_pdg_after_slicer_constructor", beforeSlice == 0);
    checks.put("exact_slice_equivalence", lazySlice.equals(eagerSlice));
    checks.put("unrelated_is_callgraph_reachable", unrelatedReachable);
    checks.put("unrelated_pdg_not_loaded_lazily", lazy.loaded.stream().noneMatch(name -> name.contains(".unrelated(")));
    checks.put("eager_loads_unrelated_pdg", eager.loaded.stream().anyMatch(name -> name.contains(".unrelated(")));
    checks.put("mutual_recursion_in_callgraph", recursion.size() == 2);
    checks.put("both_recursive_pdgs_loaded", lazy.loaded.stream().anyMatch(name -> name.contains(".left("))
        && lazy.loaded.stream().anyMatch(name -> name.contains(".right(")));
    checks.put("budget_exhaustion_is_explicit", exhausted && !limited.frontier.isEmpty());
    checks.put("budget_keeps_prior_completed_evidence", !completedEvidence.isEmpty()
        && completedEvidence.stream().anyMatch(statement -> statement.contains(".direct(")));
    Map<String, Object> result = new LinkedHashMap<>();
    result.put("schema", "mockspec.java-demand-method-mechanism/v1");
    result.put("target_executed", false);
    result.put("checks", checks);
    result.put("passed", checks.values().stream().allMatch(Boolean::booleanValue));
    result.put("call_graph_nodes", graph.getNumberOfNodes());
    result.put("pdgs_before_slicer", beforeConstructor);
    result.put("pdgs_after_slicer_constructor", beforeSlice);
    result.put("lazy_loaded_methods", lazy.loaded);
    result.put("eager_loaded_methods", eager.loaded);
    result.put("lazy_slice", lazySlice);
    result.put("eager_slice", eagerSlice);
    result.put("recursive_call_edges", recursion);
    result.put("budget", Map.of("max_methods", 2, "status", exhausted ? "partial" : "complete",
        "loaded_methods", limited.loaded, "unexpanded_frontier", limited.frontier,
        "completed_direct_slice", completedEvidence));
    result.put("timings_seconds", Map.of("scope_callgraph_pointer", (cgDone - started) / 1e9,
        "lazy_dependency_slice", (lazyDone - cgDone) / 1e9,
        "eager_dependency_slice", (eagerDone - lazyDone) / 1e9,
        "total", (System.nanoTime() - started) / 1e9));
    result.put("boundaries", List.of("Global Mod/Ref scanning and inverse indexes remain eager and are included in dependency costs.",
        "Mechanism uses NO_HEAP base SDG, CI Mod/Ref heap may dependencies and NONE control; real-case control is verified separately.",
        "Warm JVM and shared CG/PA mean lazy/eager timing differences are descriptive, not an isolated benchmark."));
    System.out.println(new ObjectMapper().writerWithDefaultPrettyPrinter().writeValueAsString(result));
    if (!checks.values().stream().allMatch(Boolean::booleanValue)) System.exit(2);
  }
}
