package org.mockspec.research;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.ibm.wala.classLoader.IMethod;
import com.ibm.wala.classLoader.IClass;
import com.ibm.wala.classLoader.IField;
import com.ibm.wala.classLoader.IBytecodeMethod;
import com.ibm.wala.classLoader.CallSiteReference;
import com.ibm.wala.classLoader.JarFileModule;
import com.ibm.wala.classLoader.Language;
import com.ibm.wala.ipa.callgraph.AnalysisCacheImpl;
import com.ibm.wala.ipa.callgraph.AnalysisOptions;
import com.ibm.wala.ipa.callgraph.AnalysisScope;
import com.ibm.wala.ipa.callgraph.CGNode;
import com.ibm.wala.ipa.callgraph.CallGraph;
import com.ibm.wala.ipa.callgraph.CallGraphBuilder;
import com.ibm.wala.ipa.callgraph.Entrypoint;
import com.ibm.wala.ipa.callgraph.MethodTargetSelector;
import com.ibm.wala.ipa.callgraph.impl.DefaultEntrypoint;
import com.ibm.wala.ipa.callgraph.impl.Util;
import com.ibm.wala.ipa.callgraph.propagation.InstanceKey;
import com.ibm.wala.ipa.callgraph.propagation.PointerAnalysis;
import com.ibm.wala.ipa.callgraph.propagation.PointerKey;
import com.ibm.wala.ipa.modref.ExtendedHeapModel;
import com.ibm.wala.ipa.modref.ModRef;
import com.ibm.wala.ipa.cha.ClassHierarchyFactory;
import com.ibm.wala.ipa.cha.IClassHierarchy;
import com.ibm.wala.ipa.slicer.Dependency;
import com.ibm.wala.ipa.slicer.NormalStatement;
import com.ibm.wala.ipa.slicer.SDG;
import com.ibm.wala.ipa.slicer.Slicer;
import com.ibm.wala.ipa.slicer.Statement;
import com.ibm.wala.ipa.slicer.StatementWithInstructionIndex;
import com.ibm.wala.ipa.slicer.HeapStatement;
import com.ibm.wala.ipa.slicer.GetCaughtExceptionStatement;
import com.ibm.wala.ipa.summaries.MethodSummary;
import com.ibm.wala.ipa.summaries.SummarizedMethod;
import com.ibm.wala.ssa.IR;
import com.ibm.wala.ssa.ISSABasicBlock;
import com.ibm.wala.ssa.SSAAbstractInvokeInstruction;
import com.ibm.wala.ssa.SSAInstruction;
import com.ibm.wala.ssa.SSAReturnInstruction;
import com.ibm.wala.ssa.SSAThrowInstruction;
import com.ibm.wala.ssa.ConstantValue;
import com.ibm.wala.ssa.SSAInstructionFactory;
import com.ibm.wala.classLoader.NewSiteReference;
import com.ibm.wala.shrike.shrikeBT.IConditionalBranchInstruction;
import com.ibm.wala.shrike.shrikeBT.IInvokeInstruction;
import com.ibm.wala.types.TypeReference;
import com.ibm.wala.types.ClassLoaderReference;
import com.ibm.wala.types.MethodReference;
import com.ibm.wala.util.CancelException;
import com.ibm.wala.util.graph.dominators.Dominators;
import com.ibm.wala.core.util.config.AnalysisScopeReader;
import java.io.File;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.jar.JarFile;

/** Bounded WALA 1.7.2 probe. It analyses bytecode and never loads application classes. */
public final class WalaSliceProbe {
  private static final ObjectMapper JSON = new ObjectMapper();

  private WalaSliceProbe() {}

  public static void main(String[] args) throws Exception {
    if (args.length != 5 && args.length != 6) {
      throw new IllegalArgumentException(
          "usage: <application-jar> <library-classpath-or-dash> <entry-class> <entry-selector> <criterion> [OPAQUE_SUMMARIES]");
    }
    Path application = Path.of(args[0]).toAbsolutePath().normalize();
    String libraryClasspath = args[1];
    String entryClass = args[2];
    String entrySelector = args[3];
    String criterion = args[4];
    boolean opaqueSummaries = args.length == 6 && "OPAQUE_SUMMARIES".equals(args[5]);
    Map<String, Long> timings = new LinkedHashMap<>();
    long totalStart = System.nanoTime();

    long started = System.nanoTime();
    AnalysisScope scope =
        AnalysisScopeReader.instance.makeJavaBinaryAnalysisScope(application.toString(), null);
    int libraryCount = 0;
    if (!"-".equals(libraryClasspath)) {
      for (String element : libraryClasspath.split(File.pathSeparator)) {
        if (element.isBlank()) continue;
        scope.addToScope(
            ClassLoaderReference.Extension, new JarFileModule(new JarFile(element, false)));
        libraryCount++;
      }
    }
    timings.put("scope", elapsed(started));

    started = System.nanoTime();
    IClassHierarchy hierarchy = ClassHierarchyFactory.make(scope);
    MethodReference entryReference =
        MethodReference.findOrCreate(
            ClassLoaderReference.Application,
            "L" + entryClass.replace('.', '/'),
            entrySelector.substring(0, entrySelector.indexOf('(')),
            entrySelector.substring(entrySelector.indexOf('(')));
    IMethod entryMethod = hierarchy.resolveMethod(entryReference);
    if (entryMethod == null) {
      throw new IllegalArgumentException("entry method not resolved: " + entryReference);
    }
    timings.put("class_hierarchy_and_entry", elapsed(started));

    started = System.nanoTime();
    DefaultEntrypoint entrypoint = new DefaultEntrypoint(entryMethod, hierarchy);
    if (opaqueSummaries) configureOpaqueCarriers(entrypoint, entryMethod, hierarchy);
    Iterable<Entrypoint> entries = List.of(entrypoint);
    AnalysisOptions options = new AnalysisOptions(scope, entries);
    options.setReflectionOptions(AnalysisOptions.ReflectionOptions.NONE);
    Map<MethodReference, MethodSummary> summaries = new LinkedHashMap<>();
    if (opaqueSummaries) {
      summaries.putAll(buildOpaqueSummaries(hierarchy));
      int summaryStatements = 0;
      for (MethodSummary summary : summaries.values()) {
        summaryStatements += summary.getNumberOfStatements();
      }
      System.err.printf(
          "WALA_STAGE opaque_summaries_configured methods=%d statements=%d%n",
          summaries.size(), summaryStatements);
    }
    CallGraphBuilder<InstanceKey> builder =
        Util.makeZeroOneCFABuilder(
            Language.JAVA, options, new AnalysisCacheImpl(), hierarchy, scope);
    if (opaqueSummaries) {
      options.setSelector(
          new ExactSummaryTargetSelector(options.getMethodTargetSelector(), summaries, hierarchy));
    }
    CallGraph callGraph = builder.makeCallGraph(options, null);
    PointerAnalysis<InstanceKey> pointerAnalysis = builder.getPointerAnalysis();
    timings.put("call_graph_and_pointer", elapsed(started));
    Set<String> callGraphContexts = new LinkedHashSet<>();
    int applicationCallGraphNodes = 0;
    for (CGNode node : callGraph) {
      callGraphContexts.add(node.getContext().toString());
      if (node.getMethod().getDeclaringClass().getClassLoader().getReference()
          .equals(ClassLoaderReference.Application)) applicationCallGraphNodes++;
    }
    System.err.printf(
        "WALA_STAGE call_graph_complete nodes=%d application_nodes=%d contexts=%d pointer_instance_keys=%d elapsed_ms=%d%n",
        callGraph.getNumberOfNodes(), applicationCallGraphNodes, callGraphContexts.size(),
        pointerAnalysis.getInstanceKeys().size(), timings.get("call_graph_and_pointer"));

    CGNode entryNode = findNode(callGraph, entryMethod.getReference());
    List<Map<String, Object>> localPointsTo = describeLocalPointsTo(entryNode, pointerAnalysis);
    List<Map<String, Object>> instructionEffects =
        describeInstructionEffects(entryNode, pointerAnalysis);
    List<Map<String, Object>> entryControlFlow = describeEntryControlFlow(entryNode);
    List<Map<String, Object>> summaryTargets =
        describeSummaryTargets(callGraph, summaries, pointerAnalysis);
    NormalStatement seed = findSeed(entryNode, criterion);
    started = System.nanoTime();
    SDG<InstanceKey> sdg =
        new SDG<>(
            callGraph,
            pointerAnalysis,
            Slicer.DataDependenceOptions.FULL,
            Slicer.ControlDependenceOptions.FULL);
    timings.put("sdg_initialization", elapsed(started));
    System.err.printf(
        "WALA_STAGE sdg_initialized lazy_pdg=true elapsed_ms=%d%n",
        timings.get("sdg_initialization"));

    started = System.nanoTime();
    System.err.println("WALA_STAGE backward_slice_started pdg_population=on_demand");
    Collection<Statement> slice = Slicer.computeBackwardSlice(sdg, seed);
    timings.put("backward_slice", elapsed(started));
    int lazySdgNodes = 0;
    var lazyNodes = sdg.iterateLazyNodes();
    while (lazyNodes.hasNext()) {
      lazyNodes.next();
      lazySdgNodes++;
    }
    System.err.printf(
        "WALA_STAGE backward_slice_complete slice_statements=%d lazy_sdg_nodes=%d elapsed_ms=%d%n",
        slice.size(), lazySdgNodes, timings.get("backward_slice"));
    List<Map<String, Object>> effectForwardSlices =
        describeEffectForwardSlices(callGraph, summaries, sdg, entryMethod.getSignature());

    started = System.nanoTime();
    List<Map<String, Object>> statements = new ArrayList<>();
    List<Statement> ordered = new ArrayList<>(slice);
    ordered.sort(
        Comparator.comparing((Statement s) -> s.getNode().getMethod().getSignature())
            .thenComparing(Statement::getKind)
            .thenComparing(Object::toString));
    for (Statement statement : ordered) statements.add(describe(statement));
    Set<Statement> sliceSet = new LinkedHashSet<>(slice);
    Map<String, Integer> edgeKinds = new LinkedHashMap<>();
    List<Map<String, Object>> edges = new ArrayList<>();
    List<Map<String, Object>> callBindings = new ArrayList<>();
    int edgeCount = 0;
    for (Statement source : ordered) {
      if (source instanceof StatementWithInstructionIndex) {
        SSAInstruction instruction = ((StatementWithInstructionIndex) source).getInstruction();
        if (instruction instanceof SSAAbstractInvokeInstruction
            && source.getKind() == Statement.Kind.NORMAL) {
          SSAAbstractInvokeInstruction invocation = (SSAAbstractInvokeInstruction) instruction;
          Set<CGNode> targets = callGraph.getPossibleTargets(source.getNode(), invocation.getCallSite());
          List<String> targetMethods = new ArrayList<>();
          for (CGNode target : targets) targetMethods.add(target.getMethod().getSignature());
          targetMethods.sort(String::compareTo);
          Map<String, Object> binding = new LinkedHashMap<>();
          binding.put("caller", source.getNode().getMethod().getSignature());
          binding.put("caller_context", source.getNode().getContext().toString());
          binding.put(
              "instruction_index",
              ((StatementWithInstructionIndex) source).getInstructionIndex());
          binding.put("call_site", invocation.getCallSite().toString());
          binding.put("declared_target", invocation.getDeclaredTarget().getSignature());
          binding.put("possible_targets", targetMethods);
          binding.put("status", targetMethods.isEmpty() ? "unresolved" : "resolved_candidates");
          callBindings.add(binding);
        }
      }
      var successors = sdg.getSuccNodes(source);
      while (successors.hasNext()) {
        Statement target = successors.next();
        if (!sliceSet.contains(target)) continue;
        edgeCount++;
        List<String> labels = new ArrayList<>();
        for (Dependency label : sdg.getEdgeLabels(source, target)) {
          edgeKinds.merge(label.name(), 1, Integer::sum);
          labels.add(label.name());
        }
        labels.sort(String::compareTo);
        edges.add(Map.of(
            "from", ordered.indexOf(source), "to", ordered.indexOf(target), "labels", labels));
      }
    }
    timings.put("source_mapping", elapsed(started));
    timings.put("total", elapsed(totalStart));

    Map<String, Object> result = new LinkedHashMap<>();
    result.put("schema_version", "mockspec.wala-capability/v2");
    result.put("wala_version", "1.7.2");
    result.put("configuration", Map.of(
        "call_graph", "0-1-CFA",
        "data_dependence", "FULL",
        "control_dependence", "FULL",
        "reflection", "NONE",
        "opaque_effect_model", "bounded-effect-token-v1",
        "entry_origin", "explicit_diagnostic",
        "opaque_summary_mode", opaqueSummaries));
    List<Map<String, Object>> opaqueEffects = new ArrayList<>();
    if (opaqueSummaries) {
      String effectKind = effectKind(entrySelector);
      opaqueEffects.add(Map.of(
          "kind", effectKind,
          "target", effectKind.equals("alias") ? "UnknownDependency.alias" : "UnknownDependency.mutate",
          "provenance", "wala_method_summary",
          "summary_count", summaries.size(),
          "model_status", "bounded_mechanism_only",
          "semantics", "unknown_scalar_writes_to_visible_receiver_argument_one_hop_and_application_static_regions_with_argument_or_fresh_return_and_normal_or_throw_not_mock_behavior"));
    }
    result.put("opaque_effects", opaqueEffects);
    result.put("opaque_effect_model_scope", opaqueSummaries
        ? Map.of(
            "modeled", List.of(
                "receiver direct int fields",
                "direct reference-argument int fields",
                "one reference-field hop to int fields",
                "Application static int fields",
                "Application static reference fields one hop to int fields",
                "compatible argument-or-fresh reference return",
                "normal completion or RuntimeException after modeled writes"),
            "unknown", List.of(
                "reference-field replacement",
                "heap paths deeper than one reference field",
                "arrays and collection contents",
                "library and runtime globals",
                "escaped objects not reachable through modeled parameters or Application statics",
                "null and arbitrary exception types"))
        : Map.of("modeled", List.of(), "unknown", List.of("opaque calls are not summarized")));
    result.put("application", application.toString());
    result.put("library_count", libraryCount);
    result.put("entry", entryMethod.getSignature());
    result.put("criterion", Map.of("query", criterion, "seed", describe(seed)));
    result.put("class_hierarchy_classes", hierarchy.getNumberOfClasses());
    List<String> unresolvedClasses = new ArrayList<>();
    for (Object unresolved : hierarchy.getUnresolvedClasses()) {
      unresolvedClasses.add(unresolved.toString());
    }
    unresolvedClasses.sort(String::compareTo);
    result.put("unresolved_classes", unresolvedClasses);
    result.put("call_graph_nodes", callGraph.getNumberOfNodes());
    result.put("application_call_graph_nodes", applicationCallGraphNodes);
    result.put("call_graph_contexts", callGraphContexts.size());
    result.put("pointer_instance_keys", pointerAnalysis.getInstanceKeys().size());
    result.put("entry_local_points_to", localPointsTo);
    result.put("entry_instruction_effects", instructionEffects);
    result.put("entry_control_flow", entryControlFlow);
    result.put("summary_targets", summaryTargets);
    result.put("configured_summaries", describeConfiguredSummaries(summaries));
    result.put("effect_forward_slices", effectForwardSlices);
    result.put("lazy_sdg_nodes_after_slice", lazySdgNodes);
    result.put("slice_statements", statements);
    result.put("slice_statement_count", statements.size());
    result.put("slice_edge_count", edgeCount);
    result.put("slice_edge_labels", edgeKinds);
    result.put("slice_edges", edges);
    result.put("call_bindings", callBindings);
    result.put("timings_millis", timings);
    result.put("limitations", List.of(
        "A slice member is a possible dependence under this call graph, not proof of path feasibility.",
        "Fresh and compatible-argument return alternatives exist in IR, but type-based PA may merge their regions; null and other return sources remain outside the model.",
        "The bounded probe covers visible direct int fields on concrete receivers and reference arguments, one reference-field hop to int fields, and Application static/static-reachable int regions.",
        "Reference-field replacement, deeper heap paths, arrays/collections, library/runtime globals, unbounded escaped regions, null, and arbitrary exception types remain unknown.",
        "The RuntimeException branch is a fixture mechanism probe, not a complete nondeterministic exception model.",
        "Bytecode line tables may not map synthetic or library statements to reviewed source."));
    JSON.writerWithDefaultPrettyPrinter().writeValue(System.out, result);
  }

  private static String effectKind(String entrySelector) {
    if (entrySelector.startsWith("unknownAliasObservation")) return "alias";
    if (entrySelector.startsWith("unknownCompletionObservation")) return "normal_completion";
    if (entrySelector.startsWith("unknownExceptionObservation")) return "write_before_throw";
    if (entrySelector.startsWith("unknownFreshObservation")) return "fresh_negative";
    return "state";
  }

  /** Ensures exact native carrier targets use the supplied SSA summary instead of empty native IR. */
  private static final class ExactSummaryTargetSelector implements MethodTargetSelector {
    private final MethodTargetSelector parent;
    private final Map<MethodReference, MethodSummary> summaries;
    private final IClassHierarchy hierarchy;
    private final Map<MethodReference, IMethod> methods = new LinkedHashMap<>();

    private ExactSummaryTargetSelector(
        MethodTargetSelector parent,
        Map<MethodReference, MethodSummary> summaries,
        IClassHierarchy hierarchy) {
      this.parent = parent;
      this.summaries = summaries;
      this.hierarchy = hierarchy;
    }

    @Override
    public IMethod getCalleeTarget(
        CGNode caller, com.ibm.wala.classLoader.CallSiteReference site, IClass receiver) {
      IMethod selected = parent.getCalleeTarget(caller, site, receiver);
      if (selected == null) return null;
      MethodReference reference = selected.getReference();
      MethodSummary summary = summaries.get(reference);
      if (summary == null) return selected;
      return methods.computeIfAbsent(
          reference,
          ignored ->
              new SummarizedMethod(
                  reference, summary, hierarchy.lookupClass(reference.getDeclaringClass())));
    }
  }

  private static List<Map<String, Object>> describeLocalPointsTo(
      CGNode node, PointerAnalysis<InstanceKey> pointerAnalysis) {
    List<Map<String, Object>> result = new ArrayList<>();
    IR ir = node.getIR();
    if (ir == null) return result;
    int maxValue = ir.getSymbolTable().getMaxValueNumber();
    for (int value = 1; value <= maxValue; value++) {
      PointerKey key = pointerAnalysis.getHeapModel().getPointerKeyForLocal(node, value);
      List<String> regions = new ArrayList<>();
      for (InstanceKey instance : pointerAnalysis.getPointsToSet(key)) {
        regions.add(instance.toString());
      }
      if (!regions.isEmpty()) {
        regions.sort(String::compareTo);
        result.add(Map.of("value_number", value, "pointer_key", key.toString(), "regions", regions));
      }
    }
    return result;
  }

  private static List<Map<String, Object>> describeInstructionEffects(
      CGNode node, PointerAnalysis<InstanceKey> pointerAnalysis) {
    List<Map<String, Object>> result = new ArrayList<>();
    IR ir = node.getIR();
    if (ir == null) return result;
    ModRef<InstanceKey> modRef = ModRef.make();
    ExtendedHeapModel heapModel = modRef.makeHeapModel(pointerAnalysis);
    SSAInstruction[] instructions = ir.getInstructions();
    for (int index = 0; index < instructions.length; index++) {
      SSAInstruction instruction = instructions[index];
      if (instruction == null) continue;
      List<String> modRegions = pointerKeys(
          modRef.getMod(node, heapModel, pointerAnalysis, instruction, null));
      List<String> refRegions = pointerKeys(
          modRef.getRef(node, heapModel, pointerAnalysis, instruction, null));
      if (modRegions.isEmpty() && refRegions.isEmpty()
          && !(instruction instanceof SSAAbstractInvokeInstruction)) continue;
      List<Integer> uses = new ArrayList<>();
      for (int use = 0; use < instruction.getNumberOfUses(); use++) {
        uses.add(instruction.getUse(use));
      }
      List<Integer> definitions = new ArrayList<>();
      for (int definition = 0; definition < instruction.getNumberOfDefs(); definition++) {
        definitions.add(instruction.getDef(definition));
      }
      Map<String, Object> row = new LinkedHashMap<>();
      row.put("instruction_index", index);
      row.put("instruction", instruction.toString());
      row.put("uses", uses);
      row.put("definitions", definitions);
      row.put("mod_regions", modRegions);
      row.put("ref_regions", refRegions);
      result.add(row);
    }
    return result;
  }

  private static List<Map<String, Object>> describeSummaryTargets(
      CallGraph callGraph,
      Map<MethodReference, MethodSummary> summaries,
      PointerAnalysis<InstanceKey> pointerAnalysis) {
    List<Map<String, Object>> result = new ArrayList<>();
    List<CGNode> summaryNodes = new ArrayList<>();
    for (CGNode node : callGraph) {
      if (summaries.containsKey(node.getMethod().getReference())) summaryNodes.add(node);
    }
    if (summaryNodes.isEmpty()) return result;
    ModRef<InstanceKey> modRef = ModRef.make();
    Map<CGNode, com.ibm.wala.util.intset.OrdinalSet<PointerKey>> mods =
        modRef.computeMod(callGraph, pointerAnalysis);
    for (CGNode node : summaryNodes) {
      IR ir = node.getIR();
      List<String> instructions = new ArrayList<>();
      List<Integer> putIndexes = new ArrayList<>();
      List<Integer> throwIndexes = new ArrayList<>();
      List<Integer> returnIndexes = new ArrayList<>();
      List<Map<String, Object>> putThrowDominancePairs = new ArrayList<>();
      boolean putDominatesThrow = false;
      if (ir != null) {
        SSAInstruction[] irInstructions = ir.getInstructions();
        for (int index = 0; index < irInstructions.length; index++) {
          SSAInstruction instruction = irInstructions[index];
          if (instruction == null) continue;
          instructions.add(instruction.toString());
          if (instruction.toString().startsWith("putfield")) putIndexes.add(index);
          if (instruction instanceof SSAThrowInstruction) throwIndexes.add(index);
          if (instruction instanceof SSAReturnInstruction) returnIndexes.add(index);
        }
        if (!putIndexes.isEmpty() && !throwIndexes.isEmpty()) {
          Dominators<ISSABasicBlock> dominators =
              Dominators.make(ir.getControlFlowGraph(), ir.getControlFlowGraph().entry());
          putDominatesThrow = true;
          for (int throwIndex : throwIndexes) {
            ISSABasicBlock throwBlock = ir.getControlFlowGraph().getBlockForInstruction(throwIndex);
            boolean dominated = false;
            for (int putIndex : putIndexes) {
              ISSABasicBlock putBlock = ir.getControlFlowGraph().getBlockForInstruction(putIndex);
              boolean orderedDominance =
                  putBlock.equals(throwBlock)
                      ? putIndex < throwIndex
                      : dominators.isDominatedBy(throwBlock, putBlock);
              if (orderedDominance) {
                putThrowDominancePairs.add(Map.of(
                    "put_instruction_index", putIndex,
                    "throw_instruction_index", throwIndex,
                    "put_basic_block", putBlock.getNumber(),
                    "throw_basic_block", throwBlock.getNumber()));
              }
              dominated |= orderedDominance;
            }
            putDominatesThrow &= dominated;
          }
        }
      }
      Map<String, Object> row = new LinkedHashMap<>();
      row.put("method", node.getMethod().getSignature());
      row.put("implementation_class", node.getMethod().getClass().getName());
      row.put("is_native", node.getMethod().isNative());
      row.put("is_abstract", node.getMethod().isAbstract());
      row.put("context", node.getContext().toString());
      row.put("ir_instructions", instructions);
      row.put("put_instruction_indexes", putIndexes);
      row.put("throw_instruction_indexes", throwIndexes);
      row.put("return_instruction_indexes", returnIndexes);
      row.put("put_dominates_throw", putDominatesThrow);
      row.put("put_throw_dominance_pairs", putThrowDominancePairs);
      row.put("instruction_effects", describeInstructionEffects(node, pointerAnalysis));
      row.put("mod_regions", pointerKeys(mods.get(node)));
      result.add(row);
    }
    return result;
  }

  /**
   * Export the exact first CFG edge after each entry-method invoke.  WALA's SDG does not expose a
   * normal-return statement for void calls, so normal completion is evidenced by a summary normal
   * exit plus this call-site-specific normal continuation.  Exceptional and normal first edges are
   * kept separate even when later finally blocks merge them.
   */
  private static List<Map<String, Object>> describeEntryControlFlow(CGNode entryNode) {
    List<Map<String, Object>> result = new ArrayList<>();
    IR ir = entryNode.getIR();
    if (ir == null) return result;
    var cfg = ir.getControlFlowGraph();
    SSAInstruction[] instructions = ir.getInstructions();
    for (int index = 0; index < instructions.length; index++) {
      SSAInstruction instruction = instructions[index];
      if (!(instruction instanceof SSAAbstractInvokeInstruction)) continue;
      SSAAbstractInvokeInstruction invoke = (SSAAbstractInvokeInstruction) instruction;
      ISSABasicBlock callBlock = cfg.getBlockForInstruction(index);
      Collection<ISSABasicBlock> normal = cfg.getNormalSuccessors(callBlock);
      Collection<ISSABasicBlock> exceptional = cfg.getExceptionalSuccessors(callBlock);
      Map<String, Object> row = new LinkedHashMap<>();
      row.put("caller", entryNode.getMethod().getSignature());
      row.put("caller_context", entryNode.getContext().toString());
      row.put("instruction_index", index);
      row.put("call_site", invoke.getCallSite().toString());
      row.put("declared_target", invoke.getDeclaredTarget().getSignature());
      row.put("call_block", callBlock.getNumber());
      row.put("normal_successor_blocks", blockNumbers(normal));
      row.put("exceptional_successor_blocks", blockNumbers(exceptional));
      row.put("normal_reachable", reachableInstructions(cfg, normal));
      row.put("exceptional_reachable", reachableInstructions(cfg, exceptional));
      result.add(row);
    }
    return result;
  }

  private static List<Integer> blockNumbers(Collection<ISSABasicBlock> blocks) {
    List<Integer> result = new ArrayList<>();
    for (ISSABasicBlock block : blocks) result.add(block.getNumber());
    result.sort(Integer::compareTo);
    return result;
  }

  private static List<Map<String, Object>> reachableInstructions(
      com.ibm.wala.ssa.SSACFG cfg, Collection<ISSABasicBlock> starts) {
    Set<ISSABasicBlock> visited = new LinkedHashSet<>();
    List<ISSABasicBlock> pending = new ArrayList<>(starts);
    while (!pending.isEmpty()) {
      ISSABasicBlock block = pending.remove(0);
      if (!visited.add(block)) continue;
      var successors = cfg.getSuccNodes(block);
      while (successors.hasNext()) pending.add(successors.next());
    }
    List<Map<String, Object>> result = new ArrayList<>();
    for (ISSABasicBlock block : visited) {
      var blockInstructions = block.iterator();
      while (blockInstructions.hasNext()) {
        SSAInstruction instruction = blockInstructions.next();
        result.add(Map.of(
            "basic_block", block.getNumber(),
            "instruction_index", instruction.iIndex(),
            "instruction", instruction.toString()));
      }
    }
    result.sort(
        Comparator.comparingInt(row -> (Integer) row.get("instruction_index")));
    return result;
  }

  private static List<Map<String, Object>> describeConfiguredSummaries(
      Map<MethodReference, MethodSummary> summaries) {
    List<Map<String, Object>> result = new ArrayList<>();
    for (Map.Entry<MethodReference, MethodSummary> entry : summaries.entrySet()) {
      List<String> instructions = new ArrayList<>();
      for (SSAInstruction instruction : entry.getValue().getStatements()) {
        if (instruction != null) instructions.add(instruction.toString());
      }
      result.add(Map.of("method", entry.getKey().getSignature(), "instructions", instructions));
    }
    return result;
  }

  private static List<Map<String, Object>> describeEffectForwardSlices(
      CallGraph callGraph,
      Map<MethodReference, MethodSummary> summaries,
      SDG<InstanceKey> sdg,
      String entrySignature) throws CancelException {
    List<Map<String, Object>> result = new ArrayList<>();
    for (CGNode node : callGraph) {
      if (!summaries.containsKey(node.getMethod().getReference())) continue;
      IR ir = node.getIR();
      if (ir == null) continue;
      SSAInstruction[] instructions = ir.getInstructions();
      for (int index = 0; index < instructions.length; index++) {
        SSAInstruction instruction = instructions[index];
        String exitKind;
        if (instruction instanceof SSAThrowInstruction) {
          exitKind = "exception";
        } else if (instruction instanceof SSAReturnInstruction) {
          exitKind = "normal";
        } else {
          continue;
        }
        Collection<Statement> forward =
            Slicer.computeForwardSlice(sdg, new NormalStatement(node, index));
        List<Map<String, Object>> entryStatements = new ArrayList<>();
        for (Statement reached : forward) {
          if (reached.getNode().getMethod().getSignature().equals(entrySignature)) {
            entryStatements.add(describe(reached));
          }
        }
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("summary_method", node.getMethod().getSignature());
        row.put("summary_context", node.getContext().toString());
        row.put("exit_kind", exitKind);
        row.put("source_instruction_index", index);
        row.put("source_instruction", instruction.toString());
        row.put("entry_reachable_statements", entryStatements);
        result.add(row);
      }
    }
    return result;
  }

  private static List<String> pointerKeys(Iterable<PointerKey> keys) {
    List<String> result = new ArrayList<>();
    if (keys == null) return result;
    for (PointerKey key : keys) result.add(key.toString());
    result.sort(String::compareTo);
    return result;
  }

  private static void configureOpaqueCarriers(
      DefaultEntrypoint entrypoint, IMethod entryMethod, IClassHierarchy hierarchy) {
    for (int parameter = 0; parameter < entryMethod.getNumberOfParameters(); parameter++) {
      IClass declared = hierarchy.lookupClass(entryMethod.getParameterType(parameter));
      if (declared == null || (!declared.isInterface() && !declared.isAbstract())) continue;
      List<TypeReference> carriers = new ArrayList<>();
      for (IClass candidate : hierarchy) {
        if (!candidate.getClassLoader().getReference().equals(ClassLoaderReference.Application)) continue;
        if (candidate.isInterface() || candidate.isAbstract()) continue;
        if (hierarchy.isAssignableFrom(declared, candidate)) carriers.add(candidate.getReference());
      }
      if (!carriers.isEmpty()) {
        entrypoint.setParameterTypes(parameter, carriers.toArray(TypeReference[]::new));
      }
    }
  }

  /**
   * Model a bounded opaque effect. Primitive field values and branch choices come from a native
   * EffectSource token with no implementation or fixed value. Reference returns retain both a
   * compatible argument alias and a fresh alternative. Unsupported regions remain unmodelled and
   * must be reported unknown; this is not a general havoc model or a Mock response.
   */
  private static final class SummaryCursor {
    int pc;
    int nextValue;
    int branchValue = -1;

    SummaryCursor(int nextValue) {
      this.nextValue = nextValue;
    }
  }

  private static void addUnknownIntWrite(
      MethodSummary summary,
      SSAInstructionFactory instructions,
      MethodReference unknownInt,
      SummaryCursor cursor,
      int baseValue,
      IField field) {
    int unknownValue = cursor.nextValue++;
    int sourceException = cursor.nextValue++;
    summary.addStatement(
        instructions.InvokeInstruction(
            cursor.pc,
            unknownValue,
            new int[0],
            sourceException,
            CallSiteReference.make(cursor.pc, unknownInt, IInvokeInstruction.Dispatch.STATIC),
            null));
    cursor.pc++;
    summary.addStatement(
        instructions.PutInstruction(
            cursor.pc++, baseValue, unknownValue, field.getReference()));
    if (cursor.branchValue < 0) cursor.branchValue = unknownValue;
  }

  private static void addUnknownStaticIntWrite(
      MethodSummary summary,
      SSAInstructionFactory instructions,
      MethodReference unknownInt,
      SummaryCursor cursor,
      IField field) {
    int unknownValue = cursor.nextValue++;
    int sourceException = cursor.nextValue++;
    summary.addStatement(
        instructions.InvokeInstruction(
            cursor.pc,
            unknownValue,
            new int[0],
            sourceException,
            CallSiteReference.make(cursor.pc, unknownInt, IInvokeInstruction.Dispatch.STATIC),
            null));
    cursor.pc++;
    summary.addStatement(
        instructions.PutInstruction(cursor.pc++, unknownValue, field.getReference()));
    if (cursor.branchValue < 0) cursor.branchValue = unknownValue;
  }

  private static Map<MethodReference, MethodSummary> buildOpaqueSummaries(IClassHierarchy hierarchy) {
    Map<MethodReference, MethodSummary> summaries = new LinkedHashMap<>();
    SSAInstructionFactory instructions = Language.JAVA.instructionFactory();
    MethodReference unknownInt =
        MethodReference.findOrCreate(
            ClassLoaderReference.Application,
            "Lsample/MechanismProbe$EffectSource",
            "unknownInt",
            "()I");
    for (IClass klass : hierarchy) {
      if (!klass.getClassLoader().getReference().equals(ClassLoaderReference.Application)) continue;
      for (IMethod method : klass.getDeclaredMethods()) {
        if (!method.isAbstract() && !method.isNative()) continue;
        MethodSummary summary = new MethodSummary(method.getReference());
        summary.setStatic(method.isStatic());
        SummaryCursor cursor = new SummaryCursor(method.getNumberOfParameters() + 1);
        TypeReference branchType = TypeReference.Int;
        for (int parameter = 0;
            parameter < method.getNumberOfParameters(); parameter++) {
          TypeReference type = method.getParameterType(parameter);
          if (!type.isReferenceType()) continue;
          IClass argumentClass = hierarchy.lookupClass(type);
          if (argumentClass == null) continue;
          int argumentValue = parameter + 1;
          for (IField field : argumentClass.getAllInstanceFields()) {
            if (field.getFieldTypeReference().equals(TypeReference.Int)) {
              addUnknownIntWrite(
                  summary, instructions, unknownInt, cursor, argumentValue, field);
            } else if (field.getFieldTypeReference().isReferenceType()) {
              IClass reachableClass = hierarchy.lookupClass(field.getFieldTypeReference());
              if (reachableClass == null || reachableClass.isArrayClass()) continue;
              int reachableValue = cursor.nextValue++;
              summary.addStatement(
                  instructions.GetInstruction(
                      cursor.pc++, reachableValue, argumentValue, field.getReference()));
              for (IField reachableField : reachableClass.getAllInstanceFields()) {
                if (reachableField.getFieldTypeReference().equals(TypeReference.Int)) {
                  addUnknownIntWrite(
                      summary,
                      instructions,
                      unknownInt,
                      cursor,
                      reachableValue,
                      reachableField);
                }
              }
            }
          }
        }
        for (IClass globalOwner : hierarchy) {
          if (!globalOwner.getClassLoader().getReference()
              .equals(ClassLoaderReference.Application)) continue;
          for (IField field : globalOwner.getDeclaredStaticFields()) {
            if (field.getFieldTypeReference().equals(TypeReference.Int)) {
              addUnknownStaticIntWrite(
                  summary, instructions, unknownInt, cursor, field);
            } else if (field.getFieldTypeReference().isReferenceType()) {
              IClass reachableClass = hierarchy.lookupClass(field.getFieldTypeReference());
              if (reachableClass == null || reachableClass.isArrayClass()) continue;
              int reachableValue = cursor.nextValue++;
              summary.addStatement(
                  instructions.GetInstruction(
                      cursor.pc++, reachableValue, field.getReference()));
              for (IField reachableField : reachableClass.getAllInstanceFields()) {
                if (reachableField.getFieldTypeReference().equals(TypeReference.Int)) {
                  addUnknownIntWrite(
                      summary,
                      instructions,
                      unknownInt,
                      cursor,
                      reachableValue,
                      reachableField);
                }
              }
            }
          }
        }
        if (cursor.branchValue < 0) {
          cursor.branchValue = cursor.nextValue++;
          int sourceException = cursor.nextValue++;
          summary.addStatement(
              instructions.InvokeInstruction(
                  cursor.pc,
                  cursor.branchValue,
                  new int[0],
                  sourceException,
                  CallSiteReference.make(
                      cursor.pc, unknownInt, IInvokeInstruction.Dispatch.STATIC),
                  null));
          cursor.pc++;
        }
        TypeReference returnType = method.getReturnType();
        if (returnType.isReferenceType()) {
          int returned = -1;
          for (int parameter = method.isStatic() ? 0 : 1;
              parameter < method.getNumberOfParameters(); parameter++) {
            if (method.getParameterType(parameter).equals(returnType)) {
              returned = parameter + 1;
              break;
            }
          }
          if (returned < 0) continue;
          int comparisonValue = cursor.nextValue++;
          summary.addConstant(comparisonValue, new ConstantValue(0));
          int branchPc = cursor.pc++;
          int aliasReturnPc = cursor.pc++;
          int freshPc = cursor.pc++;
          int freshReturnPc = cursor.pc++;
          summary.addStatement(
              instructions.ConditionalBranchInstruction(
                  branchPc,
                  IConditionalBranchInstruction.Operator.EQ,
                  branchType,
                  cursor.branchValue,
                  comparisonValue,
                  freshPc));
          summary.addStatement(instructions.ReturnInstruction(aliasReturnPc, returned, false));
          int freshValue = cursor.nextValue++;
          summary.addStatement(
              instructions.NewInstruction(
                  freshPc, freshValue, NewSiteReference.make(freshPc, returnType)));
          summary.addStatement(
              instructions.ReturnInstruction(freshReturnPc, freshValue, false));
        } else if (returnType.equals(TypeReference.Void)) {
          int comparisonValue = cursor.nextValue++;
          summary.addConstant(comparisonValue, new ConstantValue(0));
          int branchPc = cursor.pc++;
          int normalPc = cursor.pc++;
          int exceptionalPc = cursor.pc++;
          int throwPc = cursor.pc++;
          summary.addStatement(
              instructions.ConditionalBranchInstruction(
                  branchPc,
                  IConditionalBranchInstruction.Operator.EQ,
                  branchType,
                  cursor.branchValue,
                  comparisonValue,
                  exceptionalPc));
          summary.addStatement(instructions.ReturnInstruction(normalPc));
          int exceptionValue = cursor.nextValue++;
          summary.addStatement(
              instructions.NewInstruction(
                  exceptionalPc,
                  exceptionValue,
                  NewSiteReference.make(exceptionalPc, TypeReference.JavaLangRuntimeException)));
          summary.addStatement(instructions.ThrowInstruction(throwPc, exceptionValue));
        } else {
          continue;
        }
        summaries.put(method.getReference(), summary);
      }
    }
    return summaries;
  }

  private static long elapsed(long started) {
    return Math.round((System.nanoTime() - started) / 1_000_000.0);
  }

  private static CGNode findNode(CallGraph graph, MethodReference reference) {
    for (CGNode node : graph) {
      if (node.getMethod().getReference().equals(reference)) return node;
    }
    throw new IllegalArgumentException("entry node absent from call graph: " + reference);
  }

  private static NormalStatement findSeed(CGNode node, String criterion) {
    IR ir = node.getIR();
    if (ir == null) throw new IllegalArgumentException("entry has no IR");
    NormalStatement returnSeed = null;
    SSAInstruction[] instructions = ir.getInstructions();
    for (int index = 0; index < instructions.length; index++) {
      SSAInstruction instruction = instructions[index];
      if (instruction == null) continue;
      if (instruction instanceof SSAReturnInstruction) returnSeed = new NormalStatement(node, index);
      if (instruction instanceof SSAAbstractInvokeInstruction) {
        SSAAbstractInvokeInstruction invoke = (SSAAbstractInvokeInstruction) instruction;
        if (invoke.getDeclaredTarget().getSignature().contains(criterion)) {
          return new NormalStatement(node, index);
        }
      }
    }
    if ("RETURN".equals(criterion) && returnSeed != null) return returnSeed;
    throw new IllegalArgumentException("criterion absent from entry IR: " + criterion + "\n" + ir);
  }

  private static Map<String, Object> describe(Statement statement) {
    Map<String, Object> row = new LinkedHashMap<>();
    CGNode node = statement.getNode();
    row.put("kind", statement.getKind().name());
    row.put("method", node.getMethod().getSignature());
    row.put("context", node.getContext().toString());
    row.put("instruction", null);
    row.put("instruction_index", null);
    row.put("line", null);
    row.put("basic_block", null);
    row.put("statement_text", statement.toString());
    row.put("heap_location",
        statement instanceof HeapStatement
            ? ((HeapStatement) statement).getLocation().toString()
            : null);
    row.put("call_instruction_index", null);
    row.put("call_site", null);
    row.put("declared_target", null);
    if (statement instanceof HeapStatement.HeapReturnCaller) {
      HeapStatement.HeapReturnCaller caller = (HeapStatement.HeapReturnCaller) statement;
      row.put("call_instruction_index", caller.getCallIndex());
      row.put("call_site", caller.getCall().getCallSite().toString());
      row.put("declared_target", caller.getCall().getDeclaredTarget().getSignature());
    } else if (statement instanceof HeapStatement.HeapParamCaller) {
      HeapStatement.HeapParamCaller caller = (HeapStatement.HeapParamCaller) statement;
      row.put("call_instruction_index", caller.getCallIndex());
      row.put("call_site", caller.getCall().getCallSite().toString());
      row.put("declared_target", caller.getCall().getDeclaredTarget().getSignature());
    }
    if (statement instanceof StatementWithInstructionIndex) {
      StatementWithInstructionIndex indexed = (StatementWithInstructionIndex) statement;
      int instructionIndex = indexed.getInstructionIndex();
      row.put("instruction_index", instructionIndex);
      SSAInstruction instruction = indexed.getInstruction();
      row.put("instruction", instruction == null ? null : instruction.toString());
      if (instruction instanceof SSAAbstractInvokeInstruction) {
        SSAAbstractInvokeInstruction invoke = (SSAAbstractInvokeInstruction) instruction;
        row.put("call_instruction_index", instructionIndex);
        row.put("call_site", invoke.getCallSite().toString());
        row.put("declared_target", invoke.getDeclaredTarget().getSignature());
      }
      if (node.getIR() != null) {
        row.put(
            "basic_block",
            node.getIR().getControlFlowGraph().getBlockForInstruction(instructionIndex).getNumber());
      }
      try {
        IMethod method = node.getMethod();
        if (method instanceof IBytecodeMethod<?>) {
          int bytecodeIndex = ((IBytecodeMethod<?>) method).getBytecodeIndex(instructionIndex);
          row.put("line", method.getLineNumber(bytecodeIndex));
        }
      } catch (Exception ignored) {
        row.put("line", null);
      }
    } else if (statement instanceof GetCaughtExceptionStatement) {
      SSAInstruction caught = ((GetCaughtExceptionStatement) statement).getInstruction();
      row.put("instruction", caught.toString());
      row.put(
          "basic_block",
          ((com.ibm.wala.ssa.SSAGetCaughtExceptionInstruction) caught).getBasicBlockNumber());
    }
    return row;
  }
}
