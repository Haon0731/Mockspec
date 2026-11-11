import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import spoon.Launcher;
import spoon.reflect.CtModel;
import spoon.reflect.code.CtAssignment;
import spoon.reflect.code.CtExpression;
import spoon.reflect.code.CtIf;
import spoon.reflect.code.CtInvocation;
import spoon.reflect.code.CtLocalVariable;
import spoon.reflect.code.CtReturn;
import spoon.reflect.code.CtStatement;
import spoon.reflect.code.CtTypeAccess;
import spoon.reflect.code.CtVariableAccess;
import spoon.reflect.cu.SourcePosition;
import spoon.reflect.declaration.CtElement;
import spoon.reflect.declaration.CtMethod;
import spoon.reflect.declaration.ModifierKind;
import spoon.reflect.declaration.CtParameter;
import spoon.reflect.declaration.CtType;
import spoon.reflect.reference.CtExecutableReference;
import spoon.reflect.visitor.filter.TypeFilter;
import spoon.support.compiler.VirtualFile;

/** Bounded research probe for source-level backward evidence slices. */
public final class SliceProbe {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final int MAX_NODES = 2000;
    private static final int MAX_EDGES = 10000;

    private final Map<String, String> sourceIds = new HashMap<>();
    private final Map<String, Map<String, Object>> nodes = new LinkedHashMap<>();
    private final List<Map<String, Object>> edges = new ArrayList<>();
    private final Set<String> edgeKeys = new HashSet<>();
    private final Set<String> boundaryTypes = new LinkedHashSet<>();
    private CtModel model;
    private List<Map<String, Object>> opaqueMethods = List.of();

    private enum Compatibility { MATCH, UNKNOWN, MISMATCH }

    public static void main(String[] args) throws Exception {
        if (args.length != 0) throw new IllegalArgumentException("SliceProbe accepts stdin only");
        Map<String, Object> input = JSON.readValue(System.in,
            new TypeReference<Map<String, Object>>() {});
        System.out.print(JSON.writeValueAsString(new SliceProbe().run(input)));
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> run(Map<String, Object> input) {
        List<Map<String, Object>> sources = (List<Map<String, Object>>) input.get("sources");
        List<Map<String, Object>> seeds = (List<Map<String, Object>>) input.get("seeds");
        List<String> boundaries = (List<String>) input.getOrDefault("boundary_types", List.of());
        if (sources == null || sources.isEmpty() || seeds == null || seeds.isEmpty()) {
            throw new IllegalArgumentException("sources and seeds must be non-empty arrays");
        }
        boundaryTypes.addAll(boundaries);
        opaqueMethods = (List<Map<String, Object>>) input.getOrDefault("opaque_methods", List.of());
        Launcher launcher = new Launcher();
        launcher.getEnvironment().setNoClasspath(true);
        launcher.getEnvironment().setComplianceLevel(17);
        launcher.getEnvironment().setShouldCompile(false);
        for (Map<String, Object> source : sources) {
            String id = requiredString(source, "source_id");
            String path = requiredString(source, "logical_path");
            String text = requiredString(source, "text");
            if (sourceIds.put(path, id) != null) throw new IllegalArgumentException("duplicate path");
            launcher.addInputResource(new VirtualFile(text, path));
        }
        launcher.buildModel();
        model = launcher.getModel();

        // Materialize statement nodes first, then evidence edges. Edges always point
        // from a possible prerequisite to the statement affected by it.
        for (CtStatement statement : model.getElements(new TypeFilter<>(CtStatement.class))) {
            node(statement, statement.getClass().getSimpleName());
        }
        addLexicalControlEdges();
        addExpressionContainmentEdges();
        addDataCandidateEdges();
        addCallEdges();

        List<String> seedIds = new ArrayList<>();
        List<Map<String, Object>> seedBindings = new ArrayList<>();
        for (Map<String, Object> seed : seeds) {
            String id = locateSeed(seed);
            if (id == null) throw new IllegalArgumentException("no statement contains seed line");
            if (!seedIds.contains(id)) seedIds.add(id);
            Map<String, Object> binding = new LinkedHashMap<>();
            binding.put("criterion_id", seed.get("criterion_id"));
            binding.put("seed", id);
            seedBindings.add(binding);
        }
        Set<String> closure = backwardClosure(seedIds);
        retainCallBindingEvidence(closure);
        List<Map<String, Object>> selectedNodes = nodes.entrySet().stream()
            .filter(entry -> closure.contains(entry.getKey())).map(Map.Entry::getValue)
            .sorted(Comparator.comparing(n -> String.valueOf(n.get("id")))).toList();
        List<Map<String, Object>> selectedEdges = edges.stream()
            .filter(edge -> closure.contains(edge.get("from")) && closure.contains(edge.get("to")))
            .toList();
        return Map.of(
            "status", "ok",
            "backend", "spoon-source-evidence-prototype",
            "seeds", seedIds,
            "seed_bindings", seedBindings,
            "nodes", selectedNodes,
            "edges", selectedEdges,
            "limitations", List.of(
                "data_candidate edges are conservative name/declaration candidates, not a sound reaching-def analysis",
                "lexical_control edges describe AST ancestry, not post-dominator control dependence",
                "call_return edges use no-classpath resolution plus name/arity virtual candidates and do not prove dispatch",
                "expression_value_candidate edges preserve syntax containment, not runtime evaluation or value equality",
                "heap aliasing, reflection, exceptions, concurrency, and path feasibility are not modeled",
                "boundary calls are retained as nodes and are not traversed into dependency implementations"));
    }

    private void addExpressionContainmentEdges() {
        for (CtExpression<?> expression : model.getElements(new TypeFilter<>(CtExpression.class))) {
            CtElement parent = safeParent(expression);
            while (parent != null && !(parent instanceof CtExpression<?>)
                    && !(parent instanceof CtStatement)) parent = safeParent(parent);
            if (parent != null) addEdge(node(expression, "expression"), node(parent, null),
                "expression_value_candidate",
                Map.of("precision", "direct_ast_value_containment"));
        }
    }

    private void addLexicalControlEdges() {
        for (CtStatement statement : model.getElements(new TypeFilter<>(CtStatement.class))) {
            CtElement child = statement;
            for (CtElement parent = safeParent(child); parent != null; parent = safeParent(parent)) {
                if (parent instanceof CtIf branch && child != branch.getCondition()) {
                    String condition = node(branch.getCondition(), "if_condition");
                    addEdge(condition, node(statement, null), "lexical_control",
                        Map.of("branch", branchOf(branch, child), "precision", "ast_ancestry"));
                }
                child = parent;
            }
        }
    }

    private void addDataCandidateEdges() {
        List<CtStatement> statements = model.getElements(new TypeFilter<>(CtStatement.class));
        for (CtStatement use : statements) {
            CtMethod<?> method = use.getParent(CtMethod.class);
            if (method == null) continue;
            for (CtVariableAccess<?> access : use.getElements(new TypeFilter<>(CtVariableAccess.class))) {
                String name = access.getVariable() == null ? "" : access.getVariable().getSimpleName();
                if (name.isBlank()) continue;
                CtElement declaration = safeDeclaration(access);
                String declarationId = declaration == null ? null : declarationNode(declaration);
                if (declarationId != null) {
                    addEdge(declarationId, node(use, null),
                        "data_candidate", Map.of("variable", name, "precision", "resolved_declaration"));
                }
                for (CtStatement candidate : statements) {
                    if (candidate == use || candidate.getParent(CtMethod.class) != method) continue;
                    if (line(candidate) >= line(use) || !writes(candidate, name)) continue;
                    addEdge(node(candidate, null), node(use, null), "data_candidate",
                        Map.of("variable", name, "precision", "prior_write_same_method"));
                }
            }
        }
    }

    private String declarationNode(CtElement declaration) {
        if (declaration instanceof CtParameter<?> parameter) {
            CtMethod<?> method = parameter.getParent(CtMethod.class);
            if (method != null) {
                int index = method.getParameters().indexOf(parameter);
                if (index >= 0) return parameterNode(method, parameter, index);
            }
        }
        return node(declaration, "variable_declaration");
    }

    private void addCallEdges() {
        List<CtMethod<?>> methods = model.getElements(new TypeFilter<>(CtMethod.class));
        for (CtInvocation<?> call : model.getElements(new TypeFilter<>(CtInvocation.class))) {
            CtStatement caller = call.getParent(CtStatement.class);
            if (caller == null) continue;
            String callerId = node(caller, null);
            if (callerId == null) continue;
            String callSite = node(call, "call_invocation");
            if (callSite == null) callSite = callerId;
            if (isBoundary(call)) {
                Map<String, Object> marker = nodes.get(callerId);
                marker.put("contains_boundary_call", true);
                appendString(marker, "boundary_calls", compact(call.toString()));
                continue;
            }
            LinkedHashSet<CtMethod<?>> targets = new LinkedHashSet<>();
            LinkedHashSet<CtMethod<?>> directTargets = new LinkedHashSet<>();
            Map<CtMethod<?>, Compatibility> candidateCompatibility = new HashMap<>();
            CtExecutableReference<?> executable = call.getExecutable();
            try {
                if (executable != null && executable.getDeclaration() instanceof CtMethod<?> method
                        && inInput(method)) {
                    targets.add(method);
                    directTargets.add(method);
                }
            } catch (RuntimeException ignored) {}
            String name = executable == null ? "" : executable.getSimpleName();
            int arity = call.getArguments().size();
            String receiver = receiverType(call);
            boolean resolvedStatic = !directTargets.isEmpty()
                && directTargets.stream().allMatch(method -> method.hasModifier(ModifierKind.STATIC));
            if (!resolvedStatic) {
                for (CtMethod<?> method : methods) {
                    if (!method.getSimpleName().equals(name)
                            || method.getParameters().size() != arity
                            || (!receiver.isBlank() && !relatedType(receiver, method.getDeclaringType()))) {
                        continue;
                    }
                    Compatibility compatibility = signatureCompatibility(call, method);
                    if (compatibility != Compatibility.MISMATCH) {
                        targets.add(method);
                        candidateCompatibility.put(method, compatibility);
                    }
                }
            }
            if (targets.isEmpty()) {
                Map<String, Object> marker = nodes.get(callerId);
                marker.put("contains_unknown_call", true);
                @SuppressWarnings("unchecked")
                List<String> unknown = (List<String>) marker.computeIfAbsent(
                    "unknown_calls", ignored -> new ArrayList<String>());
                unknown.add(compact(call.toString()));
                @SuppressWarnings("unchecked")
                List<Map<String, Object>> details = (List<Map<String, Object>>) marker.computeIfAbsent(
                    "unknown_call_details", ignored -> new ArrayList<Map<String, Object>>());
                details.add(Map.of("call", compact(call.toString()), "name", name, "arity", arity,
                    "receiver", receiver, "source_name_arity_candidates", methods.stream()
                        .filter(method -> method.getSimpleName().equals(name)
                            && method.getParameters().size() == arity)
                        .map(method -> Map.of("callee", qualified(method), "receiver_related",
                            receiver.isBlank() || relatedType(receiver, method.getDeclaringType()),
                            "signature_compatibility", signatureCompatibility(call, method)
                                .name().toLowerCase(), "arguments", argumentCompatibility(call, method)))
                        .toList()));
            }
            for (CtMethod<?> target : targets) {
                String precision = bindingPrecision(target, directTargets, candidateCompatibility,
                    receiver, targets.size());
                if (isOpaque(target)) {
                    nodes.get(callerId).put("contains_boundary_call", true);
                    appendString(nodes.get(callerId), "boundary_calls", compact(call.toString()));
                    continue;
                }
                boolean supportedUniqueBinding = targets.size() == 1
                    && (directTargets.contains(target) || !receiver.isBlank());
                addEdge(node(target, "method_declaration"), callSite,
                    supportedUniqueBinding ? "call_binding" : "call_binding_candidate",
                    Map.of("callee", qualified(target), "call_site", callSite, "receiver", receiver,
                        "precision", precision));
                for (CtReturn<?> returned : target.getElements(new TypeFilter<>(CtReturn.class))) {
                    addEdge(node(returned, "return"), callerId, "call_return",
                        Map.of("callee", qualified(target), "call_site", callSite,
                            "precision", precision));
                }
                for (int i = 0; i < Math.min(arity, target.getParameters().size()); i++) {
                    CtParameter<?> parameter = target.getParameters().get(i);
                    addEdge(node(call.getArguments().get(i), "call_argument"),
                        parameterNode(target, parameter, i), "argument_binding_candidate",
                        Map.of("callee", qualified(target), "call_site", callSite,
                            "parameter", parameter.getSimpleName(), "parameter_index", i,
                            "precision", precision));
                }
            }
        }
    }

    private String parameterNode(CtMethod<?> method, CtParameter<?> parameter, int index) {
        SourcePosition methodPosition = method.getPosition();
        if (methodPosition == null || !methodPosition.isValidPosition()
                || methodPosition.getFile() == null) return null;
        String sourceId = sourceId(methodPosition.getFile().getPath());
        if (sourceId == null) return null;
        SourcePosition parameterPosition = parameter.getPosition();
        boolean exactPosition = parameterPosition != null && parameterPosition.isValidPosition()
            && parameterPosition.getFile() != null
            && sourceId.equals(sourceId(parameterPosition.getFile().getPath()));
        SourcePosition position = exactPosition ? parameterPosition : methodPosition;
        String id = sourceId + ":" + position.getLine() + ":" + position.getColumn()
            + ":CtParameterImpl:" + index;
        nodes.computeIfAbsent(id, ignored -> {
            if (nodes.size() >= MAX_NODES) throw new IllegalStateException("node limit exceeded");
            Map<String, Object> value = new LinkedHashMap<>();
            value.put("id", id);
            value.put("source_id", sourceId);
            value.put("start_line", position.getLine());
            value.put("end_line", position.getEndLine());
            value.put("kind", "parameter");
            value.put("code", compact(parameter.toString()));
            value.put("parameter_index", index);
            value.put("location_precision", exactPosition ? "parameter_position"
                : "declaring_method_signature");
            return value;
        });
        return id;
    }

    private String receiverType(CtInvocation<?> call) {
        try {
            if (call.getTarget() instanceof CtTypeAccess<?> access
                    && access.getAccessedType() != null) {
                return access.getAccessedType().getQualifiedName();
            }
            if (call.getTarget() != null && call.getTarget().getType() != null)
                return call.getTarget().getType().getQualifiedName();
            if (call.getExecutable() != null && call.getExecutable().getDeclaringType() != null)
                return call.getExecutable().getDeclaringType().getQualifiedName();
        } catch (RuntimeException ignored) {}
        return "";
    }

    private String bindingPrecision(CtMethod<?> target, Set<CtMethod<?>> directTargets,
            Map<CtMethod<?>, Compatibility> candidateCompatibility, String receiver,
            int targetCount) {
        if (directTargets.contains(target)) {
            return targetCount == 1 ? "resolved_declaration" : "resolved_virtual_declaration";
        }
        Compatibility compatibility = candidateCompatibility.getOrDefault(
            target, Compatibility.UNKNOWN);
        String basis;
        if (receiver.isBlank()) basis = "name_arity";
        else if (compatibility == Compatibility.MATCH) basis = "receiver_and_signature";
        else basis = "receiver_and_arity_signature_unknown";
        return targetCount == 1 ? basis : basis + "_candidate";
    }

    private Compatibility signatureCompatibility(CtInvocation<?> call, CtMethod<?> method) {
        Compatibility result = Compatibility.MATCH;
        for (int i = 0; i < call.getArguments().size(); i++) {
            Compatibility item = argumentCompatibility(call.getArguments().get(i),
                method.getParameters().get(i));
            if (item == Compatibility.MISMATCH) return item;
            if (item == Compatibility.UNKNOWN) result = item;
        }
        return result;
    }

    private List<Map<String, Object>> argumentCompatibility(CtInvocation<?> call, CtMethod<?> method) {
        List<Map<String, Object>> result = new ArrayList<>();
        for (int i = 0; i < call.getArguments().size(); i++) {
            String actual = qualifiedType(call.getArguments().get(i).getType());
            String formal = qualifiedType(method.getParameters().get(i).getType());
            result.add(Map.of("index", i, "actual", actual, "formal", formal, "compatibility",
                argumentCompatibility(call.getArguments().get(i), method.getParameters().get(i))
                    .name().toLowerCase()));
        }
        return result;
    }

    private Compatibility argumentCompatibility(CtExpression<?> argument, CtParameter<?> parameter) {
        if (argument instanceof CtVariableAccess<?> access && safeDeclaration(access) == null) {
            return Compatibility.UNKNOWN;
        }
        return typeCompatibility(argument.getType(), parameter.getType());
    }

    private Compatibility typeCompatibility(spoon.reflect.reference.CtTypeReference<?> actualReference,
            spoon.reflect.reference.CtTypeReference<?> formalReference) {
        String actual = qualifiedType(actualReference);
        String formal = qualifiedType(formalReference);
        if (actual.isBlank() || formal.isBlank()) return Compatibility.UNKNOWN;
        if (sameType(actual, formal)) return Compatibility.MATCH;
        Compatibility primitive = primitiveCompatibility(actual, formal);
        if (primitive != Compatibility.UNKNOWN) return primitive;
        try {
            if (actualReference.isSubtypeOf(formalReference)) return Compatibility.MATCH;
        } catch (RuntimeException ignored) {}
        if (hierarchyIncludes(actual, formal)) return Compatibility.MATCH;
        CtType<?> actualType = model.getAllTypes().stream()
            .filter(type -> sameType(actual, type.getQualifiedName())).findFirst().orElse(null);
        CtType<?> formalType = model.getAllTypes().stream()
            .filter(type -> sameType(formal, type.getQualifiedName())).findFirst().orElse(null);
        if (actualType != null && formalType != null
                && !hierarchyIncludes(actual, formal) && !hierarchyIncludes(formal, actual)) {
            return Compatibility.MISMATCH;
        }
        return Compatibility.UNKNOWN;
    }

    private static Compatibility primitiveCompatibility(String actual, String formal) {
        Map<String, Integer> numeric = Map.of("byte", 1, "short", 2, "char", 2, "int", 3,
            "long", 4, "float", 5, "double", 6);
        String unboxedActual = unboxed(actual);
        String unboxedFormal = unboxed(formal);
        if (unboxedActual.equals(unboxedFormal)) return Compatibility.MATCH;
        boolean actualPrimitive = isPrimitive(actual);
        boolean actualWrapper = !actual.equals(unboxedActual);
        boolean formalWrapper = !formal.equals(unboxedFormal);
        if ((actualWrapper && formalWrapper) || (actualPrimitive && formalWrapper)) {
            return Compatibility.MISMATCH;
        }
        if (unboxedActual.equals("boolean") || unboxedFormal.equals("boolean")) {
            return isPrimitive(actual) || isPrimitive(formal) || actualWrapper || formalWrapper
                ? Compatibility.MISMATCH : Compatibility.UNKNOWN;
        }
        if (numeric.containsKey(unboxedActual) && numeric.containsKey(unboxedFormal)) {
            if (unboxedActual.equals("char") && Set.of("byte", "short").contains(unboxedFormal))
                return Compatibility.MISMATCH;
            if (Set.of("byte", "short").contains(unboxedActual) && unboxedFormal.equals("char"))
                return Compatibility.MISMATCH;
            return numeric.get(unboxedActual) <= numeric.get(unboxedFormal)
                ? Compatibility.MATCH : Compatibility.MISMATCH;
        }
        return Compatibility.UNKNOWN;
    }

    private static boolean isPrimitive(String type) {
        return Set.of("boolean", "byte", "short", "int", "long", "char", "float", "double")
            .contains(type);
    }

    private static String unboxed(String type) {
        return switch (type) {
            case "java.lang.Boolean" -> "boolean";
            case "java.lang.Byte" -> "byte";
            case "java.lang.Short" -> "short";
            case "java.lang.Character" -> "char";
            case "java.lang.Integer" -> "int";
            case "java.lang.Long" -> "long";
            case "java.lang.Float" -> "float";
            case "java.lang.Double" -> "double";
            default -> type;
        };
    }

    private boolean hierarchyIncludes(String child, String ancestor) {
        CtType<?> childType = model.getAllTypes().stream()
            .filter(type -> sameType(child, type.getQualifiedName())).findFirst().orElse(null);
        return childType != null && hierarchyIncludes(childType, ancestor, new HashSet<>());
    }

    private boolean hierarchyIncludes(CtType<?> child, String ancestor, Set<String> seen) {
        if (child == null || !seen.add(child.getQualifiedName())) return false;
        if (sameType(child.getQualifiedName(), ancestor)) return true;
        List<spoon.reflect.reference.CtTypeReference<?>> parents = new ArrayList<>();
        try {
            if (child.getSuperclass() != null) parents.add(child.getSuperclass());
            parents.addAll(child.getSuperInterfaces());
        } catch (RuntimeException ignored) {}
        for (spoon.reflect.reference.CtTypeReference<?> parent : parents) {
            if (sameType(qualifiedType(parent), ancestor)) return true;
            try {
                if (parent.getTypeDeclaration() instanceof CtType<?> declaration
                        && hierarchyIncludes(declaration, ancestor, seen)) return true;
            } catch (RuntimeException ignored) {}
        }
        return false;
    }

    private static String qualifiedType(spoon.reflect.reference.CtTypeReference<?> type) {
        if (type == null) return "";
        try { return type.getQualifiedName(); }
        catch (RuntimeException exception) { return ""; }
    }

    private boolean relatedType(String receiver, CtType<?> declaring) {
        if (declaring == null) return false;
        String declared = declaring.getQualifiedName();
        if (sameType(receiver, declared)) return true;
        for (CtType<?> type : model.getAllTypes()) {
            CtType<?> cursor = type;
            boolean receiverChain = sameType(receiver, type.getQualifiedName());
            boolean declaredChain = sameType(declared, type.getQualifiedName());
            if (!receiverChain && !declaredChain) continue;
            Set<String> seen = new HashSet<>();
            while (cursor != null && seen.add(cursor.getQualifiedName())) {
                if ((receiverChain && sameType(declared, cursor.getQualifiedName()))
                        || (declaredChain && sameType(receiver, cursor.getQualifiedName()))) return true;
                try {
                    if (cursor.getSuperclass() == null) break;
                    CtElement parent = cursor.getSuperclass().getTypeDeclaration();
                    cursor = parent instanceof CtType<?> next ? next : null;
                } catch (RuntimeException exception) { break; }
            }
        }
        return false;
    }

    private static boolean sameType(String left, String right) {
        if (left == null || right == null) return false;
        return left.equals(right) || left.endsWith("." + right) || right.endsWith("." + left);
    }

    private Set<String> backwardClosure(List<String> seeds) {
        Map<String, List<String>> incoming = new HashMap<>();
        for (Map<String, Object> edge : edges) {
            incoming.computeIfAbsent((String) edge.get("to"), ignored -> new ArrayList<>())
                .add((String) edge.get("from"));
        }
        Set<String> found = new LinkedHashSet<>(seeds);
        ArrayDeque<String> queue = new ArrayDeque<>(seeds);
        while (!queue.isEmpty()) {
            for (String prior : incoming.getOrDefault(queue.removeFirst(), List.of())) {
                if (found.add(prior)) queue.addLast(prior);
            }
        }
        return found;
    }

    private void retainCallBindingEvidence(Set<String> closure) {
        Set<String> callSites = new LinkedHashSet<>();
        for (Map<String, Object> edge : edges) {
            String kind = String.valueOf(edge.get("kind"));
            if ((kind.equals("call_binding") || kind.equals("call_binding_candidate"))
                    && closure.contains(String.valueOf(edge.get("to")))) {
                callSites.add(String.valueOf(edge.get("call_site")));
            }
        }
        for (Map<String, Object> edge : edges) {
            if (callSites.contains(String.valueOf(edge.get("call_site")))) {
                closure.add(String.valueOf(edge.get("from")));
                closure.add(String.valueOf(edge.get("to")));
            }
        }
    }

    private String locateSeed(Map<String, Object> seed) {
        String sourceId = requiredString(seed, "source_id");
        int line = requiredInt(seed, "line");
        Object kindValue = seed.get("kind");
        String kind = kindValue instanceof String text ? text : "";
        Object callValue = seed.get("call");
        String expectedCall = callValue instanceof String text ? text : "";
        if (!kind.isBlank()) {
            List<CtInvocation<?>> matching = new ArrayList<>();
            for (CtInvocation<?> call : model.getElements(new TypeFilter<>(CtInvocation.class))) {
                SourcePosition position = call.getPosition();
                if (position == null || !position.isValidPosition() || position.getFile() == null) continue;
                if (!sourceId.equals(sourceId(position.getFile().getPath()))) continue;
                if (position.getLine() > line || line > position.getEndLine()) continue;
                if (!criterionCall(kind, call)) continue;
                if (!expectedCall.isBlank() && !expectedCall.equals(callName(call))) continue;
                matching.add(call);
            }
            if (matching.size() > 1) {
                throw new IllegalArgumentException("criterion seed is ambiguous on line " + line
                    + ": " + matching.size() + " matching calls");
            }
            if (matching.size() == 1) return node(matching.get(0), "criterion_call");
        }
        return nodes.values().stream()
            .filter(n -> sourceId.equals(n.get("source_id")))
            .filter(n -> (Integer) n.get("start_line") <= line && line <= (Integer) n.get("end_line"))
            .filter(n -> String.valueOf(n.get("kind")).startsWith("Ct")
                && (String.valueOf(n.get("kind")).contains("Statement")
                    || String.valueOf(n.get("kind")).contains("Invocation")
                    || String.valueOf(n.get("kind")).contains("Assert")
                    || String.valueOf(n.get("kind")).contains("If")
                    || String.valueOf(n.get("kind")).contains("Return")
                    || String.valueOf(n.get("kind")).contains("LocalVariable")
                    || String.valueOf(n.get("kind")).contains("Assignment")))
            .min(Comparator.comparingInt(n -> (Integer) n.get("end_line") - (Integer) n.get("start_line")))
            .map(n -> (String) n.get("id")).orElse(null);
    }

    private static boolean criterionCall(String kind, CtInvocation<?> call) {
        String name = callName(call);
        return switch (kind) {
            case "value_assertion" -> Set.of("assertEquals", "assertNotEquals", "assertThat",
                "assertTrue", "assertFalse", "assertNull", "assertNotNull", "assertSame",
                "assertNotSame", "assertArrayEquals").contains(name);
            case "exception_observation" -> Set.of("assertThrows", "assertDoesNotThrow",
                "assertThatThrownBy", "assertThatExceptionOfType", "assertThatCode",
                "expectThrows").contains(name);
            case "interaction_observation" -> Set.of("verify", "verifyZeroInteractions",
                "verifyNoInteractions", "verifyNoMoreInteractions", "inOrder").contains(name);
            case "candidate_event" -> true;
            default -> false;
        };
    }

    private static String callName(CtInvocation<?> call) {
        CtExecutableReference<?> executable = call.getExecutable();
        return executable == null ? "" : executable.getSimpleName();
    }

    @SuppressWarnings("unchecked")
    private static void appendString(Map<String, Object> marker, String key, String value) {
        List<String> values = (List<String>) marker.computeIfAbsent(
            key, ignored -> new ArrayList<String>());
        if (!values.contains(value)) values.add(value);
    }

    private String node(CtElement element, String kind) {
        SourcePosition position = element == null ? null : element.getPosition();
        if (position == null || !position.isValidPosition() || position.getFile() == null) return null;
        String sourceId = sourceId(position.getFile().getPath());
        if (sourceId == null) return null;
        if (isOpaque(element)) return null;
        String id = sourceId + ":" + position.getLine() + ":" + position.getColumn()
            + ":" + element.getClass().getSimpleName();
        nodes.computeIfAbsent(id, ignored -> {
            if (nodes.size() >= MAX_NODES) throw new IllegalStateException("node limit exceeded");
            Map<String, Object> value = new LinkedHashMap<>();
            value.put("id", id);
            value.put("source_id", sourceId);
            value.put("start_line", position.getLine());
            value.put("end_line", position.getEndLine());
            value.put("kind", kind == null ? element.getClass().getSimpleName() : kind);
            value.put("code", compact(element.toString()));
            return value;
        });
        return id;
    }

    private void addEdge(String from, String to, String kind, Map<String, Object> details) {
        if (from == null || to == null || from.equals(to)) return;
        String key = from + "\0" + to + "\0" + kind + "\0" + details;
        if (!edgeKeys.add(key)) return;
        if (edges.size() >= MAX_EDGES) throw new IllegalStateException("edge limit exceeded");
        Map<String, Object> edge = new LinkedHashMap<>();
        edge.put("from", from); edge.put("to", to); edge.put("kind", kind);
        edge.putAll(details); edges.add(edge);
    }

    private boolean isBoundary(CtInvocation<?> call) {
        String target = String.valueOf(call.getTarget());
        String declaring = "";
        try {
            if (call.getExecutable() != null && call.getExecutable().getDeclaringType() != null)
                declaring = call.getExecutable().getDeclaringType().getQualifiedName();
        } catch (RuntimeException ignored) {}
        for (String boundary : boundaryTypes) {
            if (declaring.equals(boundary) || declaring.endsWith("." + boundary)
                    || target.equals(boundary) || target.startsWith(boundary + ".")) return true;
        }
        return false;
    }

    private boolean isOpaque(CtElement element) {
        SourcePosition position = element == null ? null : element.getPosition();
        if (position == null || !position.isValidPosition() || position.getFile() == null) return false;
        String id = sourceId(position.getFile().getPath());
        for (Map<String, Object> boundary : opaqueMethods) {
            if (String.valueOf(boundary.get("source_id")).equals(id)
                && ((Number) boundary.get("start_line")).intValue() <= position.getLine()
                && position.getLine() <= ((Number) boundary.get("end_line")).intValue()) return true;
        }
        return false;
    }

    private static CtElement safeParent(CtElement element) {
        try { return element != null && element.isParentInitialized() ? element.getParent() : null; }
        catch (RuntimeException exception) { return null; }
    }

    private static CtElement safeDeclaration(CtVariableAccess<?> access) {
        try { return access.getVariable() == null ? null : access.getVariable().getDeclaration(); }
        catch (RuntimeException exception) { return null; }
    }

    private boolean inInput(CtElement element) { return node(element, null) != null; }
    private static int line(CtElement element) {
        return element.getPosition() != null && element.getPosition().isValidPosition()
            ? element.getPosition().getLine() : Integer.MAX_VALUE;
    }

    private static boolean writes(CtStatement statement, String name) {
        if (statement instanceof CtLocalVariable<?> local) return local.getSimpleName().equals(name);
        if (statement instanceof CtAssignment<?, ?> assignment)
            return assignment.getAssigned().toString().equals(name)
                || assignment.getAssigned().toString().endsWith("." + name);
        return false;
    }

    private String sourceId(String path) {
        for (Map.Entry<String, String> entry : sourceIds.entrySet())
            if (path.equals(entry.getKey()) || path.endsWith(entry.getKey())) return entry.getValue();
        return null;
    }

    private static String branchOf(CtIf branch, CtElement child) {
        CtElement cursor = child;
        while (cursor != null && cursor.getParent() != branch) cursor = safeParent(cursor);
        if (cursor == branch.getThenStatement()) return "true";
        if (cursor == branch.getElseStatement()) return "false";
        return "unknown";
    }

    private static String qualified(CtMethod<?> method) {
        CtType<?> type = method.getDeclaringType();
        return (type == null ? "" : type.getQualifiedName() + ".") + method.getSignature();
    }

    private static String compact(String value) {
        value = value.replaceAll("\\s+", " ").trim();
        return value.length() <= 300 ? value : value.substring(0, 297) + "...";
    }

    private static String requiredString(Map<String, Object> object, String key) {
        Object value = object.get(key);
        if (!(value instanceof String text) || text.isBlank())
            throw new IllegalArgumentException(key + " must be a non-empty string");
        return text;
    }

    private static int requiredInt(Map<String, Object> object, String key) {
        Object value = object.get(key);
        if (!(value instanceof Integer number) || number < 1)
            throw new IllegalArgumentException(key + " must be a positive integer");
        return number;
    }
}
