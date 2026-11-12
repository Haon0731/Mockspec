package org.mockspec.research;

import com.ibm.wala.ipa.callgraph.CGNode;
import com.ibm.wala.classLoader.IBytecodeMethod;
import com.ibm.wala.classLoader.IMethod;
import com.ibm.wala.ipa.callgraph.propagation.InstanceKey;
import com.ibm.wala.ipa.callgraph.propagation.PointerAnalysis;
import com.ibm.wala.ipa.callgraph.propagation.PointerKey;
import com.ibm.wala.ipa.modref.ExtendedHeapModel;
import com.ibm.wala.ipa.modref.ModRef;
import com.ibm.wala.ipa.slicer.Dependency;
import com.ibm.wala.ipa.slicer.SDG;
import com.ibm.wala.ipa.slicer.Statement;
import com.ibm.wala.ipa.slicer.StatementWithInstructionIndex;
import com.ibm.wala.ssa.IR;
import com.ibm.wala.ssa.ISSABasicBlock;
import com.ibm.wala.ssa.SSAAbstractInvokeInstruction;
import com.ibm.wala.ssa.SSAGetInstruction;
import com.ibm.wala.ssa.SSAInstruction;
import com.ibm.wala.ssa.SSANewInstruction;
import com.ibm.wala.ssa.SSAPutInstruction;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Observation-directed heap/effect evidence over WALA's actual SDG, Mod/Ref and points-to APIs.
 *
 * <p>The result is static may-depend evidence. It never claims that a write occurs at runtime or
 * invents a field value for an opaque dependency. Traversal is restricted to an already retained
 * observation slice and an explicit node budget.
 */
public final class ObservationEffectAnalyzer {
  private ObservationEffectAnalyzer() {}

  public static Map<String, Object> analyze(
      SDG<InstanceKey> sdg,
      PointerAnalysis<InstanceKey> pointers,
      ModRef<InstanceKey> modRef,
      Collection<Statement> retainedObservationSlice,
      Statement observationRoot,
      Statement fieldRead,
      Collection<Statement> candidateWrites,
      Collection<Statement> authenticatedOpaqueCalls,
      boolean heapDependenceEnabled,
      int maxVisitedStatements) {
    if (maxVisitedStatements <= 0) throw new IllegalArgumentException("positive budget required");
    Set<Statement> retained = identitySet(retainedObservationSlice);
    if (!retained.contains(observationRoot) || !retained.contains(fieldRead)) {
      throw new IllegalArgumentException("observation root and read must be retained");
    }
    InstructionSite read = instructionSite(fieldRead, SSAGetInstruction.class);
    TraversalCost cost = new TraversalCost(maxVisitedStatements);
    Path readToRoot = dependencyPath(sdg, fieldRead, observationRoot, retained, null, cost);
    if (readToRoot == null) {
      return result("unlinked_read", cost, List.of(), List.of(), List.of());
    }

    ExtendedHeapModel heap = modRef.makeHeapModel(pointers);
    Set<PointerKey> readRefs = pointerKeySet(
        modRef.getRef(read.node, heap, pointers, read.instruction, null));
    List<Map<String, Object>> visible = new ArrayList<>();
    for (Statement candidate : candidateWrites) {
      if (!heapDependenceEnabled) break;
      if (!retained.contains(candidate)) continue;
      InstructionSite write;
      try {
        write = instructionSite(candidate, SSAPutInstruction.class);
      } catch (IllegalArgumentException ignored) {
        continue;
      }
      Path heapPath = dependencyPath(
          sdg, candidate, fieldRead, retained, Dependency.HEAP_DATA_DEP, cost);
      Set<PointerKey> mods = pointerKeySet(
          modRef.getMod(write.node, heap, pointers, write.instruction, null));
      Set<PointerKey> shared = intersection(mods, readRefs);
      if (heapPath != null && !shared.isEmpty()) {
        visible.add(row(
            "relation", "visible_state_may_depend",
            "certainty", "analysis_may",
            "write", describe(candidate),
            "read", describe(fieldRead),
            "heap_regions", sortedDescriptions(shared),
            "heap_path", heapPath.descriptions,
            "read_to_observation", readToRoot.descriptions));
      }
    }

    List<Map<String, Object>> opaque = new ArrayList<>();
    List<Map<String, Object>> exceptions = new ArrayList<>();
    for (Statement callStatement : authenticatedOpaqueCalls) {
      InstructionSite call;
      try {
        call = instructionSite(callStatement, SSAAbstractInvokeInstruction.class);
      } catch (IllegalArgumentException ignored) {
        continue;
      }
      if (!call.node.equals(read.node)) {
        opaque.add(row(
            "relation", "opaque_state_effect",
            "certainty", "unknown",
            "reason", "cross_method_cfg_route_not_expanded",
            "call", describe(callStatement),
            "read", describe(fieldRead)));
        continue;
      }
      Path normal = cfgPath(call.node, call.index, read.index, false, null, cost);
      Path exceptional = cfgPath(call.node, call.index, read.index, true, null, cost);
      Set<InstanceKey> callRegions = invokeReferenceRegions(call, pointers);
      Set<InstanceKey> readRegions = receiverRegions(read, pointers);
      Set<InstanceKey> shared = intersection(callRegions, readRegions);
      Map<String, Object> distinctAllocationProof = distinctAllocationSiteProof(call, read, pointers);
      Statement definiteKill = findDefiniteKill(
          call, read, candidateWrites, cost);
      if (cost.exhausted) {
        opaque.add(row(
            "relation", "opaque_argument_rooted_state_effect",
            "route", "normal",
            "certainty", "unknown",
            "reason", "analysis_budget_frontier",
            "call", describe(callStatement),
            "read", describe(fieldRead)));
      } else if (normal == null) {
        opaque.add(row(
            "relation", "opaque_argument_rooted_state_effect",
            "certainty", "excluded",
            "reason", "no_exact_normal_cfg_route",
            "call", describe(callStatement),
            "read", describe(fieldRead)));
      } else if (definiteKill != null) {
        opaque.add(row(
            "relation", "opaque_argument_rooted_state_effect",
            "certainty", "excluded",
            "reason", "exact_receiver_field_constant_overwrite_on_all_normal_routes",
            "call", describe(callStatement),
            "read", describe(fieldRead),
            "kill", describe(definiteKill)));
      } else if (distinctAllocationProof != null) {
        opaque.add(row(
            "relation", "opaque_argument_rooted_state_effect",
            "certainty", "excluded",
            "reason", "distinct_ssa_allocation_sites_despite_merged_points_to_region",
            "call_regions", sortedDescriptions(callRegions),
            "read_regions", sortedDescriptions(readRegions),
            "direct_allocation_proof", distinctAllocationProof,
            "call", describe(callStatement),
            "read", describe(fieldRead)));
      } else if (classifyAlias(callRegions, readRegions).equals("potential")) {
        opaque.add(row(
            "relation", "opaque_argument_rooted_state_effect",
            "certainty", "potential",
            "call", describe(callStatement),
            "read", describe(fieldRead),
            "aliased_regions", sortedDescriptions(shared),
            "normal_cfg_path", normal.descriptions,
            "unknown", "opaque body may mutate this region; concrete value is not inferred"));
      } else if (classifyAlias(callRegions, readRegions).equals("excluded")) {
        opaque.add(row(
            "relation", "opaque_argument_rooted_state_effect",
            "certainty", "excluded",
            "reason", "points_to_regions_disjoint",
            "call_regions", sortedDescriptions(callRegions),
            "read_regions", sortedDescriptions(readRegions),
            "call", describe(callStatement),
            "read", describe(fieldRead)));
      } else {
        opaque.add(row(
            "relation", "opaque_argument_rooted_state_effect",
            "certainty", "unknown",
            "reason", "points_to_evidence_incomplete",
            "call", describe(callStatement),
            "read", describe(fieldRead)));
      }
      if (exceptional != null) {
        String exceptionalState = classifyExceptionalState(callRegions, readRegions);
        Map<String, Object> exceptionalRow = row(
            "relation", "opaque_argument_rooted_state_effect",
            "route", "exceptional",
            "certainty", exceptionalState,
            "call", describe(callStatement),
            "read", describe(fieldRead),
            "exceptional_cfg_path", exceptional.descriptions);
        if (exceptionalState.equals("potential")) {
          exceptionalRow.put("aliased_regions", sortedDescriptions(shared));
          exceptionalRow.put(
              "unknown", "opaque call may mutate the aliased region before throwing");
        } else if (exceptionalState.equals("unknown")) {
          exceptionalRow.put("reason", "points_to_evidence_incomplete");
        } else {
          exceptionalRow.put("reason", "points_to_regions_disjoint");
        }
        opaque.add(exceptionalRow);
      }
      if (exceptional != null) {
        exceptions.add(row(
            "relation", "opaque_exception_to_observation",
            "certainty", "potential",
            "call", describe(callStatement),
            "read", describe(fieldRead),
            "exceptional_cfg_path", exceptional.descriptions,
            "read_to_observation", readToRoot.descriptions,
            "observation_root", describe(observationRoot),
            "unknown", "opaque throw type and runtime feasibility are not proved"));
      }
    }
    Map<String, Object> answer = result("linked", cost, visible, opaque, exceptions);
    answer.put("heap_dependence_mode", heapDependenceEnabled ? "FULL" : "NO_HEAP");
    answer.put(
        "heap_claim_scope",
        heapDependenceEnabled
            ? "WALA HEAP_DATA_DEP plus same Mod/Ref pointer key"
            : "visible heap-flow claims disabled; only points-to-bounded opaque may-effects retained");
    return answer;
  }

  private static Statement findDefiniteKill(
      InstructionSite call,
      InstructionSite read,
      Collection<Statement> candidates,
      TraversalCost cost) {
    IR ir = call.node.getIR();
    if (ir == null) return null;
    ISSABasicBlock callBlock = ir.getControlFlowGraph().getBlockForInstruction(call.index);
    ISSABasicBlock readBlock = ir.getControlFlowGraph().getBlockForInstruction(read.index);
    for (Statement statement : candidates) {
      InstructionSite write;
      try {
        write = instructionSite(statement, SSAPutInstruction.class);
      } catch (IllegalArgumentException ignored) {
        continue;
      }
      if (!write.node.equals(call.node)) continue;
      if (!strongSameLocation(
          (SSAPutInstruction) write.instruction, (SSAGetInstruction) read.instruction)) continue;
      if (!ir.getSymbolTable().isConstant(((SSAPutInstruction) write.instruction).getVal())) continue;
      ISSABasicBlock writeBlock = ir.getControlFlowGraph().getBlockForInstruction(write.index);
      if (call.index < write.index
          && write.index < read.index
          && !hasInvokeBetween(ir, call.index, read.index, cost)
          && !hasLaterPutToDeclaredField(
              ir, (SSAPutInstruction) write.instruction, write.index, read.index, cost)
          && !hasNormalCycleOnRoute(ir.getControlFlowGraph(), callBlock, readBlock, cost)
          && cfgPath(call.node, call.index, read.index, false, writeBlock, cost) == null
          && !cost.exhausted) {
        return statement;
      }
    }
    return null;
  }

  private static boolean hasInvokeBetween(
      IR ir, int lowerExclusive, int upperExclusive, TraversalCost cost) {
    SSAInstruction[] instructions = ir.getInstructions();
    for (int index = lowerExclusive + 1; index < upperExclusive; index++) {
      if (!cost.discover()) return true;
      if (instructions[index] instanceof SSAAbstractInvokeInstruction) return true;
    }
    return false;
  }

  private static boolean hasLaterPutToDeclaredField(
      IR ir, SSAPutInstruction selected, int lowerExclusive, int upperExclusive,
      TraversalCost cost) {
    SSAInstruction[] instructions = ir.getInstructions();
    for (int index = lowerExclusive + 1; index < upperExclusive; index++) {
      if (!cost.discover()) return true;
      if (instructions[index] instanceof SSAPutInstruction
          && ((SSAPutInstruction) instructions[index]).getDeclaredField()
              .equals(selected.getDeclaredField())) return true;
    }
    return false;
  }

  private static boolean hasNormalCycleOnRoute(
      com.ibm.wala.ssa.SSACFG cfg,
      ISSABasicBlock source,
      ISSABasicBlock target,
      TraversalCost cost) {
    Set<ISSABasicBlock> region = Collections.newSetFromMap(new IdentityHashMap<>());
    ArrayDeque<ISSABasicBlock> pending = new ArrayDeque<>();
    region.add(source);
    pending.add(source);
    while (!pending.isEmpty()) {
      ISSABasicBlock current = pending.removeFirst();
      if (!cost.discover()) return true;
      if (current.equals(target)) continue;
      for (ISSABasicBlock successor : cfg.getNormalSuccessors(current)) {
        if (region.add(successor)) pending.addLast(successor);
      }
    }
    Map<ISSABasicBlock, Integer> indegree = new IdentityHashMap<>();
    for (ISSABasicBlock block : region) indegree.put(block, 0);
    for (ISSABasicBlock block : region) {
      if (block.equals(target)) continue;
      for (ISSABasicBlock successor : cfg.getNormalSuccessors(block)) {
        if (region.contains(successor)) indegree.put(successor, indegree.get(successor) + 1);
      }
    }
    ArrayDeque<ISSABasicBlock> ready = new ArrayDeque<>();
    for (Map.Entry<ISSABasicBlock, Integer> entry : indegree.entrySet()) {
      if (entry.getValue() == 0) ready.add(entry.getKey());
    }
    int removed = 0;
    while (!ready.isEmpty()) {
      ISSABasicBlock block = ready.removeFirst();
      removed++;
      if (block.equals(target)) continue;
      for (ISSABasicBlock successor : cfg.getNormalSuccessors(block)) {
        if (!indegree.containsKey(successor)) continue;
        int remaining = indegree.get(successor) - 1;
        indegree.put(successor, remaining);
        if (remaining == 0) ready.addLast(successor);
      }
    }
    return removed != region.size();
  }

  private static Path dependencyPath(
      SDG<InstanceKey> sdg,
      Statement source,
      Statement target,
      Set<Statement> retained,
      Dependency required,
      TraversalCost cost) {
    DependencyStep first = new DependencyStep(source, required == null);
    ArrayDeque<DependencyStep> work = new ArrayDeque<>();
    Map<DependencyStep, DependencyStep> parent = new LinkedHashMap<>();
    Set<DependencyStep> discovered = new LinkedHashSet<>();
    discovered.add(first);
    if (!cost.discover()) return null;
    work.add(first);
    while (!work.isEmpty()) {
      DependencyStep current = work.removeFirst();
      if (current.statement.equals(target) && current.requiredSeen) {
        List<DependencyStep> steps = reconstruct(parent, first, current);
        List<Statement> statements = new ArrayList<>();
        for (DependencyStep step : steps) statements.add(step.statement);
        return Path.ofStatements(statements, sdg);
      }
      var successors = sdg.getSuccNodes(current.statement);
      while (successors.hasNext()) {
        Statement successor = successors.next();
        if (!retained.contains(successor)) continue;
        boolean requiredSeen = current.requiredSeen
            || required == null || hasLabel(sdg, current.statement, successor, required);
        DependencyStep next = new DependencyStep(successor, requiredSeen);
        if (!discovered.add(next)) continue;
        if (!cost.discover()) return null;
        parent.put(next, current);
        work.addLast(next);
      }
    }
    return null;
  }

  private static boolean hasLabel(
      SDG<InstanceKey> sdg, Statement left, Statement right, Dependency required) {
    for (Dependency label : sdg.getEdgeLabels(left, right)) {
      if (label == required) return true;
    }
    return false;
  }

  private static Path cfgPath(
      CGNode node,
      int sourceIndex,
      int targetIndex,
      boolean exceptionalFirst,
      ISSABasicBlock omitted,
      TraversalCost cost) {
    IR ir = node.getIR();
    if (ir == null) return null;
    var cfg = ir.getControlFlowGraph();
    ISSABasicBlock source = cfg.getBlockForInstruction(sourceIndex);
    ISSABasicBlock target = cfg.getBlockForInstruction(targetIndex);
    if (source.equals(omitted) || target.equals(omitted)) return null;
    if (!exceptionalFirst && source.equals(target) && sameBlockNormalOrder(sourceIndex, targetIndex)) {
      return Path.ofBlocks(List.of(source), cfg);
    }
    Collection<ISSABasicBlock> starts = exceptionalFirst
        ? cfg.getExceptionalSuccessors(source)
        : cfg.getNormalSuccessors(source);
    ArrayDeque<ISSABasicBlock> work = new ArrayDeque<>();
    Map<ISSABasicBlock, ISSABasicBlock> parent = new IdentityHashMap<>();
    Set<ISSABasicBlock> discovered = Collections.newSetFromMap(new IdentityHashMap<>());
    discovered.add(source);
    if (!cost.discover()) return null;
    for (ISSABasicBlock start : starts) {
      if (!discovered.add(start)) continue;
      if (!cost.discover()) return null;
      parent.put(start, source);
      work.add(start);
    }
    while (!work.isEmpty()) {
      ISSABasicBlock current = work.removeFirst();
      if (current.equals(omitted)) continue;
      if (current.equals(target)) {
        return Path.ofBlocks(reconstruct(parent, source, target), cfg);
      }
      Collection<ISSABasicBlock> successors = exceptionalFirst
          ? allSuccessors(cfg, current)
          : cfg.getNormalSuccessors(current);
      for (ISSABasicBlock successor : successors) {
        if (!discovered.add(successor)) continue;
        if (!cost.discover()) return null;
        parent.put(successor, current);
        work.addLast(successor);
      }
    }
    return null;
  }

  private static Collection<ISSABasicBlock> allSuccessors(
      com.ibm.wala.ssa.SSACFG cfg, ISSABasicBlock block) {
    List<ISSABasicBlock> result = new ArrayList<>();
    var successors = cfg.getSuccNodes(block);
    while (successors.hasNext()) result.add(successors.next());
    return result;
  }

  private static Set<InstanceKey> invokeReferenceRegions(
      InstructionSite site, PointerAnalysis<InstanceKey> pointers) {
    Set<InstanceKey> result = new LinkedHashSet<>();
    for (int use = 0; use < site.instruction.getNumberOfUses(); use++) {
      result.addAll(localRegions(site.node, site.instruction.getUse(use), pointers));
    }
    return result;
  }

  private static Set<InstanceKey> receiverRegions(
      InstructionSite site, PointerAnalysis<InstanceKey> pointers) {
    if (site.instruction.getNumberOfUses() == 0) return Set.of();
    return localRegions(site.node, site.instruction.getUse(0), pointers);
  }

  private static Map<String, Object> distinctAllocationSiteProof(
      InstructionSite call, InstructionSite read, PointerAnalysis<InstanceKey> pointers) {
    if (!call.node.equals(read.node) || read.instruction.getNumberOfUses() == 0) return null;
    IR ir = call.node.getIR();
    if (ir == null) return null;
    com.ibm.wala.ssa.DefUse defUse = new com.ibm.wala.ssa.DefUse(ir);
    int readValue = read.instruction.getUse(0);
    SSAInstruction readDefinition = defUse.getDef(readValue);
    if (!(readDefinition instanceof SSANewInstruction)) return null;
    Set<InstanceKey> readRegions = receiverRegions(read, pointers);
    List<Map<String, Object>> callArguments = new ArrayList<>();
    for (int use = 0; use < call.instruction.getNumberOfUses(); use++) {
      int value = call.instruction.getUse(use);
      Set<InstanceKey> regions = localRegions(call.node, value, pointers);
      if (intersection(regions, readRegions).isEmpty()) continue;
      SSAInstruction definition = defUse.getDef(value);
      if (!(definition instanceof SSANewInstruction)
          || ((SSANewInstruction) definition).getNewSite()
              .equals(((SSANewInstruction) readDefinition).getNewSite())) return null;
      callArguments.add(row(
          "value_number", value,
          "allocation_site", ((SSANewInstruction) definition).getNewSite().toString(),
          "allocation_instruction", definition.toString()));
    }
    if (callArguments.isEmpty()) return null;
    return row(
        "caller", call.node.getMethod().getSignature(),
        "caller_context", call.node.getContext().toString(),
        "read_value_number", readValue,
        "read_allocation_site", ((SSANewInstruction) readDefinition).getNewSite().toString(),
        "read_allocation_instruction", readDefinition.toString(),
        "intersecting_call_arguments", callArguments,
        "scope", "direct SSA receiver/argument allocations only; deep, global and static regions unmodeled");
  }

  private static Set<InstanceKey> localRegions(
      CGNode node, int value, PointerAnalysis<InstanceKey> pointers) {
    PointerKey key = pointers.getHeapModel().getPointerKeyForLocal(node, value);
    Set<InstanceKey> result = new LinkedHashSet<>();
    for (InstanceKey instance : pointers.getPointsToSet(key)) result.add(instance);
    return result;
  }

  private static Set<PointerKey> pointerKeySet(Iterable<PointerKey> keys) {
    Set<PointerKey> result = new LinkedHashSet<>();
    if (keys != null) for (PointerKey key : keys) result.add(key);
    return result;
  }

  private static <T> Set<T> intersection(Set<T> left, Set<T> right) {
    Set<T> result = new LinkedHashSet<>(left);
    result.retainAll(right);
    return result;
  }

  private static <T> List<T> reconstruct(Map<T, T> parent, T source, T target) {
    List<T> reversed = new ArrayList<>();
    T cursor = target;
    while (cursor != null) {
      reversed.add(cursor);
      if (cursor.equals(source)) break;
      cursor = parent.get(cursor);
    }
    Collections.reverse(reversed);
    return reversed;
  }

  static String classifyAlias(Set<?> callRegions, Set<?> readRegions) {
    if (callRegions.isEmpty() || readRegions.isEmpty()) return "unknown";
    return Collections.disjoint(callRegions, readRegions) ? "excluded" : "potential";
  }

  static boolean strongSameLocation(SSAPutInstruction write, SSAGetInstruction read) {
    if (write.isStatic() != read.isStatic()) return false;
    if (!write.getDeclaredField().equals(read.getDeclaredField())) return false;
    return write.isStatic() || write.getRef() == read.getRef();
  }

  static boolean sameBlockNormalOrder(int callInstructionIndex, int readInstructionIndex) {
    return callInstructionIndex < readInstructionIndex;
  }

  static String classifyExceptionalState(Set<?> callRegions, Set<?> readRegions) {
    return classifyAlias(callRegions, readRegions);
  }

  private static List<String> sortedDescriptions(Set<?> values) {
    List<String> result = new ArrayList<>();
    for (Object value : values) result.add(value.toString());
    Collections.sort(result);
    return result;
  }

  private static InstructionSite instructionSite(
      Statement statement, Class<? extends SSAInstruction> expected) {
    if (!(statement instanceof StatementWithInstructionIndex)) {
      throw new IllegalArgumentException("statement has no SSA instruction index");
    }
    int index = ((StatementWithInstructionIndex) statement).getInstructionIndex();
    CGNode node = statement.getNode();
    IR ir = node.getIR();
    if (ir == null || index < 0 || index >= ir.getInstructions().length) {
      throw new IllegalArgumentException("statement instruction is unavailable");
    }
    SSAInstruction instruction = ir.getInstructions()[index];
    if (!expected.isInstance(instruction)) {
      throw new IllegalArgumentException("unexpected instruction: " + instruction);
    }
    return new InstructionSite(node, index, instruction);
  }

  private static Set<Statement> identitySet(Collection<Statement> values) {
    return new LinkedHashSet<>(values);
  }

  private static Map<String, Object> describe(Statement statement) {
    Map<String, Object> result = new LinkedHashMap<>();
    result.put("kind", statement.getKind().name());
    result.put("method", statement.getNode().getMethod().getSignature());
    result.put("context", statement.getNode().getContext().toString());
    if (statement instanceof StatementWithInstructionIndex) {
      int instructionIndex = ((StatementWithInstructionIndex) statement).getInstructionIndex();
      result.put("instruction_index", instructionIndex);
      SSAInstruction instruction = ((StatementWithInstructionIndex) statement).getInstruction();
      if (instruction instanceof SSAGetInstruction) {
        result.put("declared_field", ((SSAGetInstruction) instruction).getDeclaredField().getSignature());
      } else if (instruction instanceof SSAPutInstruction) {
        result.put("declared_field", ((SSAPutInstruction) instruction).getDeclaredField().getSignature());
      }
      try {
        IMethod method = statement.getNode().getMethod();
        if (method instanceof IBytecodeMethod<?>) {
          int bytecodeIndex = ((IBytecodeMethod<?>) method).getBytecodeIndex(instructionIndex);
          result.put("line", method.getLineNumber(bytecodeIndex));
        }
      } catch (Exception ignored) {
        result.put("line", null);
      }
    }
    result.put("statement", statement.toString());
    return result;
  }

  private static Map<String, Object> result(
      String status,
      TraversalCost cost,
      List<Map<String, Object>> visible,
      List<Map<String, Object>> opaque,
      List<Map<String, Object>> exceptions) {
    return row(
        "schema_version", "mockspec.wala-observation-effects/v1",
        "status", cost.exhausted ? "partial" : status,
        "analysis_semantics", "context-sensitive static may-depend evidence",
        "visible_state", visible,
        "opaque_state", opaque,
        "exceptions", exceptions,
        "unmodeled_effect_channels", List.of(
            "static_or_global_state",
            "previously_escaped_objects",
            "arrays_collections_and_deep_heap",
            "native_or_reflective_effects"),
        "cost", row(
            "visited_statements_and_blocks", cost.visited,
            "budget", cost.limit,
            "frontier", cost.exhausted ? "statement_or_cfg_budget" : null));
  }

  private static Map<String, Object> row(Object... entries) {
    Map<String, Object> result = new LinkedHashMap<>();
    for (int index = 0; index < entries.length; index += 2) {
      result.put((String) entries[index], entries[index + 1]);
    }
    return result;
  }

  private static final class InstructionSite {
    final CGNode node;
    final int index;
    final SSAInstruction instruction;

    InstructionSite(CGNode node, int index, SSAInstruction instruction) {
      this.node = node;
      this.index = index;
      this.instruction = instruction;
    }
  }

  private static final class DependencyStep {
    final Statement statement;
    final boolean requiredSeen;

    DependencyStep(Statement statement, boolean requiredSeen) {
      this.statement = statement;
      this.requiredSeen = requiredSeen;
    }

    @Override
    public boolean equals(Object other) {
      if (!(other instanceof DependencyStep)) return false;
      DependencyStep step = (DependencyStep) other;
      return statement.equals(step.statement) && requiredSeen == step.requiredSeen;
    }

    @Override
    public int hashCode() {
      return 31 * statement.hashCode() + Boolean.hashCode(requiredSeen);
    }
  }

  private static final class Path {
    final List<Map<String, Object>> descriptions;

    Path(List<Map<String, Object>> descriptions) {
      this.descriptions = descriptions;
    }

    static Path ofStatements(List<Statement> values, SDG<InstanceKey> sdg) {
      List<Map<String, Object>> rows = new ArrayList<>();
      for (int index = 0; index < values.size(); index++) {
        Map<String, Object> description = describe(values.get(index));
        List<String> labels = new ArrayList<>();
        if (index > 0) {
          for (Dependency label : sdg.getEdgeLabels(values.get(index - 1), values.get(index))) {
            labels.add(label.name());
          }
          labels.sort(String::compareTo);
        }
        description.put("incoming_dependency_labels", labels);
        rows.add(description);
      }
      return new Path(rows);
    }

    static Path ofBlocks(List<ISSABasicBlock> values) {
      List<Map<String, Object>> rows = new ArrayList<>();
      for (ISSABasicBlock value : values) {
        rows.add(row("basic_block", value.getNumber()));
      }
      return new Path(rows);
    }

    static Path ofBlocks(List<ISSABasicBlock> values, com.ibm.wala.ssa.SSACFG cfg) {
      List<Map<String, Object>> rows = new ArrayList<>();
      for (int index = 0; index < values.size(); index++) {
        ISSABasicBlock value = values.get(index);
        String incoming = "source";
        if (index > 0) {
          ISSABasicBlock previous = values.get(index - 1);
          incoming = cfg.getNormalSuccessors(previous).contains(value) ? "normal" : "exceptional";
        }
        rows.add(row("basic_block", value.getNumber(), "incoming_edge_kind", incoming));
      }
      return new Path(rows);
    }
  }

  static final class TraversalCost {
    final int limit;
    int visited;
    boolean exhausted;

    TraversalCost(int limit) {
      this.limit = limit;
    }

    boolean discover() {
      visited++;
      if (visited > limit) {
        exhausted = true;
        return false;
      }
      return true;
    }
  }
}
