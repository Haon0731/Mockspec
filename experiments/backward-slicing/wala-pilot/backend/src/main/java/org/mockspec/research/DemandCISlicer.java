package org.mockspec.research;

import com.ibm.wala.ipa.callgraph.CGNode;
import com.ibm.wala.ipa.callgraph.CallGraph;
import com.ibm.wala.ipa.callgraph.propagation.InstanceKey;
import com.ibm.wala.ipa.callgraph.propagation.PointerAnalysis;
import com.ibm.wala.ipa.modref.ModRef;
import com.ibm.wala.ipa.slicer.PDG;
import com.ibm.wala.ipa.slicer.SDG;
import com.ibm.wala.ipa.slicer.Slicer;
import com.ibm.wala.ipa.slicer.Statement;
import com.ibm.wala.ipa.slicer.thin.CISlicer;
import java.util.ArrayList;
import java.util.ArrayDeque;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import com.ibm.wala.util.graph.Graph;

/** Records the method PDGs that WALA constructs while answering demanded slices. */
final class DemandCISlicer {
  private final CountingSDG sdg;
  private final Graph<Statement> backwardGraph;
  private final int pdgsBeforeIndex;
  private final int pdgsAfterIndex;

  DemandCISlicer(
      CallGraph graph,
      PointerAnalysis<InstanceKey> pointers,
      ModRef<InstanceKey> modRef,
      Slicer.DataDependenceOptions data,
      Slicer.ControlDependenceOptions control,
      int maxPdgMethods) {
    sdg = new CountingSDG(graph, pointers, modRef, data, control, maxPdgMethods);
    pdgsBeforeIndex = sdg.loadedCount();
    CISlicer slicer = new CISlicer(sdg, pointers, modRef);
    backwardGraph = dependencyGraph(slicer);
    pdgsAfterIndex = sdg.loadedCount();
  }

  static final class MethodBudgetExceeded extends RuntimeException {
    final String frontier;
    final List<String> completedMethodContexts;

    MethodBudgetExceeded(String frontier, Set<String> completed) {
      super("method PDG budget exhausted before " + frontier);
      this.frontier = frontier;
      completedMethodContexts = new ArrayList<>(completed);
    }

    Map<String, Object> evidence() {
      Map<String, Object> value = new LinkedHashMap<>();
      value.put("reason", "method_pdg_budget");
      value.put("frontier_method_context", frontier);
      value.put("completed_method_contexts", completedMethodContexts);
      value.put("completed_method_count", completedMethodContexts.size());
      return value;
    }
  }

  SliceResult computeBackwardThinSlice(Collection<Statement> seeds) {
    Set<String> before = sdg.loadedMethods();
    Collection<Statement> statements = slowReachable(seeds);
    Set<String> after = sdg.loadedMethods();
    List<String> added = new ArrayList<>(after);
    added.removeAll(before);
    return new SliceResult(statements, before.size(), after.size(), added);
  }

  /**
   * WALA's public CISlicer traversal uses numbered DFS, whose getMaxNumber call eagerly creates
   * every PDG. The underlying mature CISDG remains lazy when traversed through plain Graph edges.
   */
  private Collection<Statement> slowReachable(Collection<Statement> seeds) {
    Set<Statement> reached = new LinkedHashSet<>();
    ArrayDeque<Statement> work = new ArrayDeque<>(seeds);
    while (!work.isEmpty()) {
      Statement current = work.removeFirst();
      if (!reached.add(current)) {
        continue;
      }
      backwardGraph.getSuccNodes(current).forEachRemaining(work::addLast);
    }
    return reached;
  }

  @SuppressWarnings("unchecked")
  private static Graph<Statement> dependencyGraph(CISlicer slicer) {
    try {
      var field = CISlicer.class.getDeclaredField("depGraph");
      field.setAccessible(true);
      return (Graph<Statement>) field.get(slicer);
    } catch (ReflectiveOperationException error) {
      throw new IllegalStateException("WALA 1.7.2 CISlicer dependency graph unavailable", error);
    }
  }

  Map<String, Object> telemetry() {
    Map<String, Object> value = new LinkedHashMap<>();
    value.put("pdg_methods_before_heap_index", pdgsBeforeIndex);
    value.put("pdg_methods_after_heap_index", pdgsAfterIndex);
    value.put("pdg_methods_after_all_demands", sdg.loadedCount());
    value.put("loaded_method_contexts", new ArrayList<>(sdg.loadedMethods()));
    value.put("heap_effect_index_scope", "all entry-reachable IR instructions");
    value.put(
        "heap_effect_index_limitation",
        "WALA CISlicer scans global Mod/Ref effects before demand; it does not construct method PDGs during that scan");
    value.put("method_dependency_graphs", "WALA SDG.getPDG lazy construction observed by this wrapper");
    return value;
  }

  static final class SliceResult {
    final Collection<Statement> statements;
    final int pdgMethodsBefore;
    final int pdgMethodsAfter;
    final List<String> newMethodContexts;

    SliceResult(Collection<Statement> statements, int before, int after, List<String> added) {
      this.statements = statements;
      pdgMethodsBefore = before;
      pdgMethodsAfter = after;
      newMethodContexts = added;
    }

    Map<String, Object> telemetry() {
      Map<String, Object> value = new LinkedHashMap<>();
      value.put("pdg_methods_before", pdgMethodsBefore);
      value.put("pdg_methods_after", pdgMethodsAfter);
      value.put("new_pdg_method_contexts", newMethodContexts);
      return value;
    }
  }

  private static final class CountingSDG extends SDG<InstanceKey> {
    private final Set<String> loaded = new LinkedHashSet<>();
    private final int maxPdgMethods;

    CountingSDG(
        CallGraph graph,
        PointerAnalysis<InstanceKey> pointers,
        ModRef<InstanceKey> modRef,
        Slicer.DataDependenceOptions data,
        Slicer.ControlDependenceOptions control,
        int maxPdgMethods) {
      super(graph, pointers, modRef, data, control, null);
      this.maxPdgMethods = maxPdgMethods;
    }

    @Override
    public PDG<InstanceKey> getPDG(CGNode node) {
      String identity = node.getMethod().getSignature() + " @ " + node.getContext();
      if (!loaded.contains(identity) && loaded.size() >= maxPdgMethods) {
        throw new MethodBudgetExceeded(identity, loaded);
      }
      loaded.add(identity);
      return super.getPDG(node);
    }

    int loadedCount() {
      return loaded.size();
    }

    Set<String> loadedMethods() {
      return new LinkedHashSet<>(loaded);
    }
  }
}
