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
import spoon.reflect.code.CtVariableAccess;
import spoon.reflect.cu.SourcePosition;
import spoon.reflect.declaration.CtElement;
import spoon.reflect.declaration.CtMethod;
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
        for (Map<String, Object> seed : seeds) {
            String id = locateSeed(requiredString(seed, "source_id"), requiredInt(seed, "line"));
            if (id == null) throw new IllegalArgumentException("no statement contains seed line");
            seedIds.add(id);
        }
        Set<String> closure = backwardClosure(seedIds);
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
                if (declaration != null && inInput(declaration)) {
                    addEdge(node(declaration, "variable_declaration"), node(use, null),
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

    private void addCallEdges() {
        List<CtMethod<?>> methods = model.getElements(new TypeFilter<>(CtMethod.class));
        for (CtInvocation<?> call : model.getElements(new TypeFilter<>(CtInvocation.class))) {
            CtStatement caller = call.getParent(CtStatement.class);
            if (caller == null) continue;
            String callerId = node(caller, null);
            if (callerId == null) continue;
            if (isBoundary(call)) {
                Map<String, Object> marker = nodes.get(callerId);
                marker.put("contains_boundary_call", true);
                marker.put("boundary_call", compact(call.toString()));
                continue;
            }
            LinkedHashSet<CtMethod<?>> targets = new LinkedHashSet<>();
            CtExecutableReference<?> executable = call.getExecutable();
            try {
                if (executable != null && executable.getDeclaration() instanceof CtMethod<?> method
                        && inInput(method)) targets.add(method);
            } catch (RuntimeException ignored) {}
            String name = executable == null ? "" : executable.getSimpleName();
            int arity = call.getArguments().size();
            String receiver = receiverType(call);
            for (CtMethod<?> method : methods) {
                if (method.getSimpleName().equals(name) && method.getParameters().size() == arity
                        && (receiver.isBlank() || relatedType(receiver, method.getDeclaringType()))) {
                    targets.add(method);
                }
            }
            if (targets.isEmpty()) {
                Map<String, Object> marker = nodes.get(callerId);
                marker.put("contains_unknown_call", true);
                @SuppressWarnings("unchecked")
                List<String> unknown = (List<String>) marker.computeIfAbsent(
                    "unknown_calls", ignored -> new ArrayList<String>());
                unknown.add(compact(call.toString()));
            }
            String precision = targets.size() == 1 ? "single_source_candidate" : "name_arity_candidates";
            for (CtMethod<?> target : targets) {
                for (CtReturn<?> returned : target.getElements(new TypeFilter<>(CtReturn.class))) {
                    addEdge(node(returned, "return"), callerId, "call_return",
                        Map.of("callee", qualified(target), "precision", precision));
                }
                for (int i = 0; i < Math.min(arity, target.getParameters().size()); i++) {
                    CtParameter<?> parameter = target.getParameters().get(i);
                    addEdge(node(call.getArguments().get(i), "call_argument"),
                        node(parameter, "parameter"), "argument_binding_candidate",
                        Map.of("callee", qualified(target), "parameter", parameter.getSimpleName(),
                            "precision", precision));
                }
            }
        }
    }

    private String receiverType(CtInvocation<?> call) {
        try {
            if (call.getTarget() != null && call.getTarget().getType() != null)
                return call.getTarget().getType().getQualifiedName();
            if (call.getExecutable() != null && call.getExecutable().getDeclaringType() != null)
                return call.getExecutable().getDeclaringType().getQualifiedName();
        } catch (RuntimeException ignored) {}
        return "";
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

    private String locateSeed(String sourceId, int line) {
        return nodes.values().stream()
            .filter(n -> sourceId.equals(n.get("source_id")))
            .filter(n -> (Integer) n.get("start_line") <= line && line <= (Integer) n.get("end_line"))
            .min(Comparator.comparingInt(n -> (Integer) n.get("end_line") - (Integer) n.get("start_line")))
            .map(n -> (String) n.get("id")).orElse(null);
    }

    private String node(CtElement element, String kind) {
        SourcePosition position = element == null ? null : element.getPosition();
        if (position == null || !position.isValidPosition() || position.getFile() == null) return null;
        String sourceId = sourceId(position.getFile().getPath());
        if (sourceId == null) return null;
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
