package org.mockspec.research;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.ibm.wala.classLoader.Language;
import com.ibm.wala.core.util.config.AnalysisScopeReader;
import com.ibm.wala.ipa.callgraph.AnalysisCacheImpl;
import com.ibm.wala.ipa.callgraph.AnalysisOptions;
import com.ibm.wala.ipa.callgraph.CGNode;
import com.ibm.wala.ipa.callgraph.impl.DefaultEntrypoint;
import com.ibm.wala.ipa.callgraph.impl.Util;
import com.ibm.wala.ipa.callgraph.propagation.InstanceKey;
import com.ibm.wala.ipa.callgraph.propagation.StaticFieldKey;
import com.ibm.wala.ipa.cha.ClassHierarchyFactory;
import com.ibm.wala.ipa.slicer.Dependency;
import com.ibm.wala.ipa.slicer.HeapExclusions;
import com.ibm.wala.ipa.slicer.NormalStatement;
import com.ibm.wala.ipa.slicer.SDG;
import com.ibm.wala.ipa.slicer.Slicer;
import com.ibm.wala.ipa.slicer.StatementWithInstructionIndex;
import com.ibm.wala.ipa.slicer.thin.CISlicer;
import com.ibm.wala.ssa.SSAArrayLoadInstruction;
import com.ibm.wala.ssa.SSAArrayStoreInstruction;
import com.ibm.wala.ssa.SSAFieldAccessInstruction;
import com.ibm.wala.ssa.SSAGetInstruction;
import com.ibm.wala.ssa.SSAPutInstruction;
import com.ibm.wala.ssa.SSAReturnInstruction;
import com.ibm.wala.types.ClassLoaderReference;
import com.ibm.wala.types.MethodReference;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Executable regression gate for the runtime-region ModRef model. */
public final class RuntimeRegionProbe {
  public static void main(String[] args) throws Exception {
    if (args.length != 1) throw new IllegalArgumentException("fixture.jar");
    var scope = AnalysisScopeReader.instance.makeJavaBinaryAnalysisScope(args[0], null);
    var hierarchy = ClassHierarchyFactory.make(scope);
    var reference = MethodReference.findOrCreate(ClassLoaderReference.Application,
        "Lsample/RuntimeRegionFixture", "observe",
        "(Ljava/io/StreamTokenizer;Ljava/io/StreamTokenizer;[ILsample/RuntimeRegionFixture$Box;)D");
    var method = hierarchy.resolveMethod(reference);
    if (method == null) throw new IllegalStateException("fixture entry unresolved");
    var options = new AnalysisOptions(scope, List.of(new DefaultEntrypoint(method, hierarchy)));
    options.setReflectionOptions(AnalysisOptions.ReflectionOptions.NONE);
    // 1-CFA deliberately gives observe both its default-entry and ordinary-call contexts.
    var builder = Util.makeNCFABuilder(
        1, Language.JAVA, options, new AnalysisCacheImpl(), hierarchy);
    var graph = builder.makeCallGraph(options, null);
    var pointers = builder.getPointerAnalysis();
    List<CGNode> candidates = graph.getEntrypointNodes().stream()
        .filter(node -> node.getMethod().getReference().equals(reference))
        .collect(java.util.stream.Collectors.toList());
    if (candidates.size() != 1) throw new IllegalStateException("ambiguous fixture entry");
    CGNode node = candidates.get(0);
    var modRef = new RuntimeRegionModRef();
    var model = modRef.makeHeapModel(pointers);
    var sdg = new SDG<>(graph, pointers, modRef, Slicer.DataDependenceOptions.FULL,
        Slicer.ControlDependenceOptions.FULL);
    var pdg = sdg.getPDG(node);
    var instructions = node.getIR().getInstructions();
    List<Map<String, Object>> heapEdges = new ArrayList<>();
    for (int to = 0; to < instructions.length; to++) {
      if (!(instructions[to] instanceof SSAGetInstruction)
          && !(instructions[to] instanceof SSAArrayLoadInstruction)) continue;
      var target = new NormalStatement(node, to);
      // Heap dependencies are materialized lazily when predecessors are requested.
      pdg.getPredNodes(target).forEachRemaining(ignored -> { });
      for (int from = 0; from < instructions.length; from++) {
        if (!(instructions[from] instanceof SSAPutInstruction)
            && !(instructions[from] instanceof SSAArrayStoreInstruction)) continue;
        var source = new NormalStatement(node, from);
        if (pdg.getEdgeLabels(source, target).contains(Dependency.HEAP_DATA_DEP)) {
          heapEdges.add(Map.of("from", instructions[from].toString(),
              "to", instructions[to].toString()));
        }
      }
    }

    InstanceKey runtimeObject = pointers.getInstanceKeys().stream()
        .filter(key -> key.getConcreteType().getReference().getName().toString()
            .equals("Ljava/io/StreamTokenizer"))
        .findFirst().orElseThrow(() -> new IllegalStateException("no StreamTokenizer instance"));
    InstanceKey runtimeArray = pointers.getInstanceKeys().stream()
        .filter(key -> key.getConcreteType().isArrayClass())
        .findFirst().orElseThrow(() -> new IllegalStateException("no runtime array instance"));
    InstanceKey appBox = pointers.getInstanceKeys().stream()
        .filter(key -> key.getConcreteType().getReference().getName().toString()
            .equals("Lsample/RuntimeRegionFixture$Box"))
        .findFirst().orElseThrow(() -> new IllegalStateException("no Box instance"));
    SSAFieldAccessInstruction runtimeAccess = null;
    List<SSAFieldAccessInstruction> appAccesses = new ArrayList<>();
    for (var instruction : instructions) {
      if (!(instruction instanceof SSAFieldAccessInstruction)) continue;
      var access = (SSAFieldAccessInstruction) instruction;
      var resolvedField = hierarchy.resolveField(access.getDeclaredField());
      if (resolvedField == null) continue;
      if (resolvedField.getDeclaringClass().getClassLoader().getReference()
          .equals(ClassLoaderReference.Primordial)) runtimeAccess = access;
      if (resolvedField.getDeclaringClass().getClassLoader().getReference()
          .equals(ClassLoaderReference.Application) && access instanceof SSAPutInstruction) {
        appAccesses.add(access);
      }
    }
    if (runtimeAccess == null || appAccesses.size() != 2) {
      throw new IllegalStateException("fixture field accesses not recovered");
    }
    var runtimeField = hierarchy.resolveField(runtimeAccess.getDeclaredField());
    var systemClass = hierarchy.lookupClass(
        com.ibm.wala.types.TypeReference.JavaLangSystem);
    var systemOut = systemClass.getAllStaticFields().stream()
        .filter(field -> field.getName().toString().equals("out"))
        .findFirst().orElseThrow(() -> new IllegalStateException("System.out unresolved"));
    var appFieldX = hierarchy.resolveField(appAccesses.get(0).getDeclaredField());
    var appFieldY = hierarchy.resolveField(appAccesses.get(1).getDeclaredField());
    var runtimeInstanceKey = model.getPointerKeyForInstanceField(runtimeObject, runtimeField);
    var runtimeStaticKey = model.getPointerKeyForStaticField(systemOut);
    var runtimeContentsKey = model.getPointerKeyForArrayContents(runtimeArray);
    var runtimeLengthKey = model.getPointerKeyForArrayLength(runtimeArray);
    var appXKey = model.getPointerKeyForInstanceField(appBox, appFieldX);
    var appYKey = model.getPointerKeyForInstanceField(appBox, appFieldY);

    int returnIndex = -1;
    for (int i = 0; i < instructions.length; i++) {
      if (instructions[i] instanceof SSAReturnInstruction) returnIndex = i;
    }
    if (returnIndex < 0) throw new IllegalStateException("fixture return absent");
    var ciSlicer = new CISlicer(graph, pointers, modRef, Slicer.DataDependenceOptions.NO_HEAP,
        Slicer.ControlDependenceOptions.FULL);
    var ciSlice = ciSlicer.computeBackwardThinSlice(new NormalStatement(node, returnIndex));
    List<String> ciInstructions = new ArrayList<>();
    for (var statement : ciSlice) {
      if (statement.getNode().equals(node) && statement instanceof StatementWithInstructionIndex) {
        ciInstructions.add(((StatementWithInstructionIndex) statement).getInstruction().toString());
      }
    }
    var ciHeapOnlySlicer = new CISlicer(graph, pointers, modRef,
        Slicer.DataDependenceOptions.NO_HEAP, Slicer.ControlDependenceOptions.NONE);
    var ciHeapOnlySlice = ciHeapOnlySlicer.computeBackwardThinSlice(
        new NormalStatement(node, returnIndex));
    List<String> ciHeapOnlyInstructions = new ArrayList<>();
    for (var statement : ciHeapOnlySlice) {
      if (statement.getNode().equals(node) && statement instanceof StatementWithInstructionIndex) {
        ciHeapOnlyInstructions.add(
            ((StatementWithInstructionIndex) statement).getInstruction().toString());
      }
    }

    Map<String, Object> result = new LinkedHashMap<>();
    result.put("runtime_key_recognized", HeapExclusions.getType(runtimeInstanceKey) != null);
    result.put("runtime_key_uses_weak_update", !(runtimeInstanceKey instanceof StaticFieldKey));
    result.put("four_runtime_forms_merged", runtimeInstanceKey.equals(runtimeStaticKey)
        && runtimeInstanceKey.equals(runtimeContentsKey) && runtimeInstanceKey.equals(runtimeLengthKey));
    result.put("application_fields_distinct", !appXKey.equals(appYKey));
    result.put("heap_edges", heapEdges);
    result.put("entry_context_candidates", candidates.size());
    result.put("all_method_contexts", graph.getNodes(reference).size());
    result.put("ci_slice_instructions", ciInstructions);
    result.put("ci_heap_only_instructions", ciHeapOnlyInstructions);
    new ObjectMapper().writerWithDefaultPrettyPrinter().writeValue(System.out, result);
  }
}
