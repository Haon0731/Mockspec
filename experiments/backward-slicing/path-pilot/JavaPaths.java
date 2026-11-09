import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import spoon.Launcher;
import spoon.reflect.CtModel;
import spoon.reflect.code.CtAssignment;
import spoon.reflect.code.CtBlock;
import spoon.reflect.code.CtConditional;
import spoon.reflect.code.CtExpression;
import spoon.reflect.code.CtIf;
import spoon.reflect.code.CtInvocation;
import spoon.reflect.code.CtLocalVariable;
import spoon.reflect.code.CtLoop;
import spoon.reflect.code.CtReturn;
import spoon.reflect.code.CtStatement;
import spoon.reflect.code.CtThrow;
import spoon.reflect.code.CtTry;
import spoon.reflect.cu.SourcePosition;
import spoon.reflect.declaration.CtElement;
import spoon.reflect.declaration.CtMethod;
import spoon.reflect.declaration.CtParameter;
import spoon.reflect.declaration.CtType;
import spoon.reflect.reference.CtExecutableReference;
import spoon.reflect.visitor.filter.TypeFilter;
import spoon.support.compiler.VirtualFile;

/** Bounded guarded value/event summaries over host-supplied Java source strings. */
public final class JavaPaths {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final int MAX_DEPTH = 5;
    private static final int MAX_PATHS = 32;
    private final Map<String, String> sourceIds = new HashMap<>();
    private final Set<String> boundaries = new HashSet<>();
    private CtModel model;
    private int expansions;
    private int maxExpanded;
    private boolean truncated;
    private String mode = "full";
    private int maxDepth = MAX_DEPTH;

    public static void main(String[] args) throws Exception {
        if (args.length != 0) throw new IllegalArgumentException("stdin only");
        Map<String, Object> request = JSON.readValue(System.in,
            new TypeReference<Map<String, Object>>() {});
        System.out.print(JSON.writeValueAsString(new JavaPaths().run(request)));
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> run(Map<String, Object> request) {
        List<Map<String, Object>> sources = (List<Map<String, Object>>) request.get("sources");
        if (sources == null || sources.isEmpty()) throw new IllegalArgumentException("sources required");
        boundaries.addAll((List<String>) request.getOrDefault("boundary_types", List.of()));
        mode = String.valueOf(request.getOrDefault("mode", "full"));
        if (!Set.of("guard_only", "value_effect", "full").contains(mode))
            throw new IllegalArgumentException("mode must be guard_only, value_effect, or full");
        Object requestedDepth = request.get("max_depth");
        if (requestedDepth != null) {
            if (!(requestedDepth instanceof Integer depth) || depth < 1 || depth > MAX_DEPTH)
                throw new IllegalArgumentException("max_depth must be 1..5");
            maxDepth = depth;
        }
        Launcher launcher = new Launcher();
        launcher.getEnvironment().setNoClasspath(true);
        launcher.getEnvironment().setComplianceLevel(17);
        launcher.getEnvironment().setShouldCompile(false);
        for (Map<String, Object> source : sources) {
            String path = string(source, "logical_path");
            sourceIds.put(path, string(source, "source_id"));
            launcher.addInputResource(new VirtualFile(string(source, "text"), path));
        }
        launcher.buildModel();
        model = launcher.getModel();
        List<String> entries = (List<String>) request.getOrDefault("entry_methods", List.of());
        Set<CtMethod<?>> selected = selectMethods(entries);
        List<Map<String, Object>> summaries = new ArrayList<>();
        for (CtMethod<?> method : model.getElements(new TypeFilter<>(CtMethod.class))) {
            if (span(method) != null && selected.contains(method)) summaries.add(summarize(method));
        }
        summaries.sort(Comparator.comparing(m -> String.valueOf(m.get("qualified_signature"))));
        return Map.of(
            "status", truncated ? "incomplete" : "ok",
            "backend", "spoon-guarded-method-summary-prototype",
            "mode", mode,
            "bounds", Map.of("max_expansion_depth", maxDepth, "max_expanded_paths", MAX_PATHS,
                "max_observed_expanded_paths_per_method", maxExpanded),
            "methods", summaries,
            "limitations", List.of(
                "Summaries preserve source expressions and guards; they do not interpret URI, string, collection, or framework semantics.",
                "Visible call targets are conservative type/name/arity candidates and do not prove runtime dispatch.",
                "Opaque calls may return normally or throw; their effects and values are unknown.",
                "In value_effect mode call records inherit their enclosing method location to keep bounded packets compact.",
                "Loops and try/catch are reported unsupported and are not path-expanded.",
                "Assignments are lexical substitutions without heap alias or reaching-definition proofs.",
                "Expansion is bounded and an incomplete result must not be treated as absence of behavior."));
    }

    private Set<CtMethod<?>> selectMethods(List<String> entries) {
        List<CtMethod<?>> all = model.getElements(new TypeFilter<>(CtMethod.class));
        if (entries.isEmpty()) return new HashSet<>(all);
        Set<CtMethod<?>> selected = new HashSet<>();
        List<CtMethod<?>> frontier = new ArrayList<>();
        for (CtMethod<?> method : all) {
            if (entries.stream().anyMatch(entry -> qualified(method).equals(entry)
                    || qualified(method).endsWith(entry))) {
                selected.add(method); frontier.add(method);
            }
        }
        for (int depth = 0; depth < maxDepth && !frontier.isEmpty(); depth++) {
            List<CtMethod<?>> next = new ArrayList<>();
            for (CtMethod<?> owner : frontier) {
                for (CtInvocation<?> call : owner.getElements(new TypeFilter<>(CtInvocation.class))) {
                    for (CtMethod<?> target : candidates(call, owner))
                        if (selected.add(target)) next.add(target);
                }
            }
            frontier = next;
        }
        return selected;
    }

    private Map<String, Object> summarize(CtMethod<?> method) {
        expansions = 0;
        Map<String, CtExpression<?>> locals = new LinkedHashMap<>();
        List<Map<String, Object>> assignments = new ArrayList<>();
        for (CtLocalVariable<?> local : method.getElements(new TypeFilter<>(CtLocalVariable.class))) {
            if (local.getDefaultExpression() != null) {
                locals.put(local.getSimpleName(), local.getDefaultExpression());
                assignments.add(Map.of("name", local.getSimpleName(),
                    "value", local.getDefaultExpression().toString(), "location", span(local)));
            }
        }
        for (CtAssignment<?, ?> assignment : method.getElements(new TypeFilter<>(CtAssignment.class))) {
            assignments.add(Map.of("name", assignment.getAssigned().toString(),
                "value", assignment.getAssignment().toString(), "location", span(assignment)));
        }
        List<Map<String, Object>> returns = new ArrayList<>();
        for (CtReturn<?> returned : method.getElements(new TypeFilter<>(CtReturn.class))) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("location", span(returned));
            if (!mode.equals("guard_only"))
                item.put("expression", String.valueOf(returned.getReturnedExpression()));
            item.put("guards", guards(returned));
            if (mode.equals("full"))
                item.put("value_summary", value(returned.getReturnedExpression(), method, locals, 0, new HashSet<>()));
            returns.add(item);
        }
        List<Map<String, Object>> calls = new ArrayList<>();
        for (CtInvocation<?> call : method.getElements(new TypeFilter<>(CtInvocation.class))) {
            if (!mode.equals("value_effect") || informativeCall(call, method))
                calls.add(callSummary(call, method, locals, 0, new HashSet<>(), false));
        }
        List<Map<String, Object>> events = new ArrayList<>();
        for (CtThrow thrown : method.getElements(new TypeFilter<>(CtThrow.class))) {
            events.add(Map.of("kind", "throw", "expression", thrown.getThrownExpression().toString(),
                "guards", guards(thrown), "location", span(thrown)));
        }
        List<String> unsupported = new ArrayList<>();
        if (!method.getElements(new TypeFilter<>(CtLoop.class)).isEmpty()) unsupported.add("loop");
        if (!method.getElements(new TypeFilter<>(CtTry.class)).isEmpty()) unsupported.add("try_catch");
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("qualified_signature", qualified(method));
        out.put("visibility", method.getVisibility() == null ? "package" : method.getVisibility().toString());
        out.put("location", span(method));
        out.put("parameters", method.getParameters().stream().map(this::parameter).toList());
        out.put("assignments", mode.equals("guard_only") ? List.of() : assignments);
        out.put("returns", returns);
        out.put("calls", mode.equals("guard_only") ? List.of() : calls);
        out.put("events", events);
        out.put("unsupported", unsupported);
        maxExpanded = Math.max(maxExpanded, expansions);
        return out;
    }

    private boolean informativeCall(CtInvocation<?> call, CtMethod<?> owner) {
        if (!candidates(call, owner).isEmpty()) return true;
        String receiver = call.getTarget() == null ? "this" : call.getTarget().toString();
        return receiver.matches("[A-Za-z_$][A-Za-z0-9_$.]*")
            && !receiver.substring(receiver.lastIndexOf('.') + 1).matches("[A-Z][A-Za-z0-9_$]*");
    }

    private Object value(CtExpression<?> expression, CtMethod<?> owner,
                         Map<String, CtExpression<?>> locals, int depth, Set<String> stack) {
        if (expression == null) return null;
        if (depth >= maxDepth || expansions >= MAX_PATHS) {
            truncated = true;
            return Map.of("kind", "bounded", "expression", expression.toString());
        }
        String text = expression.toString();
        if (locals.containsKey(text)) {
            return Map.of("kind", "local_substitution", "name", text,
                "value", value(locals.get(text), owner, locals, depth + 1, stack));
        }
        if (expression instanceof CtInvocation<?> call)
            return callSummary(call, owner, locals, depth, stack, true);
        if (expression instanceof CtConditional<?> conditional) {
            return Map.of("kind", "conditional", "condition", conditional.getCondition().toString(),
                "true_value", value(conditional.getThenExpression(), owner, locals, depth + 1, stack),
                "false_value", value(conditional.getElseExpression(), owner, locals, depth + 1, stack));
        }
        List<CtInvocation<?>> nested = expression.getElements(new TypeFilter<>(CtInvocation.class));
        return Map.of("kind", "expression", "expression", text,
            "nested_calls", nested.stream().map(call -> callSummary(
                call, owner, locals, depth + 1, stack, false)).toList());
    }

    private Map<String, Object> callSummary(CtInvocation<?> call, CtMethod<?> owner,
            Map<String, CtExpression<?>> locals, int depth, Set<String> stack, boolean expand) {
        CtExecutableReference<?> executable = call.getExecutable();
        String name = executable == null ? "<unknown>" : executable.getSimpleName();
        String receiver = call.getTarget() == null ? "this" : call.getTarget().toString();
        List<CtMethod<?>> candidates = candidates(call, owner);
        boolean boundary = candidates.isEmpty() || boundaries.stream().anyMatch(
            type -> receiver.equals(type) || receiver.startsWith(type + "."));
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("expression", call.toString());
        out.put("receiver", receiver);
        out.put("name", name);
        out.put("arguments", call.getArguments().stream().map(Object::toString).toList());
        out.put("guards", guards(call));
        if (!mode.equals("value_effect")) out.put("location", span(call));
        out.put("normal_outcome", boundary ? "opaque_value_if_returns_normally" : "visible_candidate_value");
        out.put("exception_outcome", "possible_unknown_exception");
        out.put("target_candidates", candidates.stream().map(JavaPaths::qualified).toList());
        out.put("resolution", boundary ? "opaque_or_unresolved" :
            (candidates.size() == 1 ? "single_visible_candidate" : "dynamic_dispatch_candidates"));
        if (mode.equals("full") && depth < maxDepth && expansions < MAX_PATHS) {
            List<Map<String, Object>> nestedVisible = new ArrayList<>();
            for (CtInvocation<?> nested : call.getElements(new TypeFilter<>(CtInvocation.class))) {
                if (nested == call) continue;
                if (!candidates(nested, owner).isEmpty())
                    nestedVisible.add(callSummary(nested, owner, locals, depth + 1, stack, true));
            }
            if (!nestedVisible.isEmpty()) out.put("nested_visible_calls", nestedVisible);
        }
        if (mode.equals("full") && expand && !boundary && depth < maxDepth && expansions < MAX_PATHS) {
            List<Map<String, Object>> expanded = new ArrayList<>();
            for (CtMethod<?> candidate : candidates) {
                String key = qualified(candidate);
                if (!stack.add(key)) continue;
                for (CtReturn<?> returned : candidate.getElements(new TypeFilter<>(CtReturn.class))) {
                    if (expansions >= MAX_PATHS) { truncated = true; break; }
                    expansions++;
                    Map<String, Object> expansion = new LinkedHashMap<>();
                    expansion.put("callee", key);
                    expansion.put("guards", guards(returned));
                    expansion.put("value", value(returned.getReturnedExpression(), candidate,
                        localMap(candidate), depth + 1, stack));
                    expanded.add(expansion);
                }
                stack.remove(key);
            }
            out.put("expanded_returns", expanded);
        }
        return out;
    }

    private List<CtMethod<?>> candidates(CtInvocation<?> call, CtMethod<?> owner) {
        String name = call.getExecutable() == null ? "" : call.getExecutable().getSimpleName();
        int arity = call.getArguments().size();
        String receiverType = "";
        try {
            if (call.getTarget() != null && call.getTarget().getType() != null)
                receiverType = call.getTarget().getType().getQualifiedName();
            else if (call.getExecutable() != null && call.getExecutable().getDeclaringType() != null)
                receiverType = call.getExecutable().getDeclaringType().getQualifiedName();
        } catch (RuntimeException ignored) {}
        if (receiverType.isBlank() && owner.getDeclaringType() != null)
            receiverType = owner.getDeclaringType().getQualifiedName();
        List<CtMethod<?>> result = new ArrayList<>();
        for (CtMethod<?> method : model.getElements(new TypeFilter<>(CtMethod.class))) {
            if (!method.getSimpleName().equals(name) || method.getParameters().size() != arity) continue;
            if (receiverType.isBlank() || related(receiverType, method.getDeclaringType())) result.add(method);
        }
        return result;
    }

    private boolean related(String receiver, CtType<?> declaring) {
        if (declaring == null) return false;
        if (same(receiver, declaring.getQualifiedName())) return true;
        for (CtType<?> type : model.getAllTypes()) {
            CtType<?> cursor = type;
            if (!same(receiver, type.getQualifiedName()) && !same(declaring.getQualifiedName(), type.getQualifiedName())) continue;
            for (int i = 0; cursor != null && i < MAX_DEPTH; i++) {
                if (same(receiver, cursor.getQualifiedName()) || same(declaring.getQualifiedName(), cursor.getQualifiedName())) {
                    if (i > 0) return true;
                }
                try {
                    cursor = cursor.getSuperclass() == null ? null : cursor.getSuperclass().getTypeDeclaration();
                } catch (RuntimeException exception) { cursor = null; }
            }
        }
        return false;
    }

    private List<Map<String, Object>> guards(CtElement element) {
        List<Map<String, Object>> result = new ArrayList<>();
        CtElement child = element;
        for (CtElement parent = safeParent(child); parent != null; parent = safeParent(parent)) {
            if (parent instanceof CtIf condition) result.add(0, Map.of(
                "condition", condition.getCondition().toString(), "polarity", branch(condition, child),
                "kind", "lexical_if_guard"));
            else if (parent instanceof CtConditional<?> condition) result.add(0, Map.of(
                "condition", condition.getCondition().toString(), "polarity", branch(condition, child),
                "kind", "lexical_conditional_guard"));
            child = parent;
        }
        addSequentialExitGuards(element, result);
        return result;
    }

    private void addSequentialExitGuards(CtElement element, List<Map<String, Object>> result) {
        CtElement cursor = element;
        for (CtElement parent = safeParent(cursor); parent != null; parent = safeParent(parent)) {
            if (parent instanceof CtBlock<?> block) {
                CtElement direct = cursor;
                while (direct != null && safeParent(direct) != block) direct = safeParent(direct);
                int index = direct instanceof CtStatement statement
                    ? block.getStatements().indexOf(statement) : -1;
                for (int i = 0; i < index; i++) {
                    if (!(block.getStatement(i) instanceof CtIf prior)) continue;
                    boolean trueExits = definitelyExits(prior.getThenStatement());
                    boolean falseExits = definitelyExits(prior.getElseStatement());
                    if (trueExits && !falseExits) result.add(Map.of(
                        "condition", prior.getCondition().toString(), "polarity", "false",
                        "kind", "sequential_early_exit_guard",
                        "precision", "same_block_true_branch_definitely_exits"));
                    else if (!trueExits && falseExits) result.add(Map.of(
                        "condition", prior.getCondition().toString(), "polarity", "true",
                        "kind", "sequential_early_exit_guard",
                        "precision", "same_block_false_branch_definitely_exits"));
                    else if (trueExits && falseExits) result.add(Map.of(
                        "condition", prior.getCondition().toString(),
                        "polarity", "unreachable_after_both_branches_exit",
                        "kind", "sequential_early_exit_guard",
                        "precision", "same_block_both_branches_definitely_exit"));
                }
            }
            cursor = parent;
        }
    }

    private static boolean definitelyExits(CtStatement statement) {
        if (statement == null) return false;
        if (statement instanceof CtReturn<?> || statement instanceof CtThrow) return true;
        if (statement instanceof CtBlock<?> block) {
            List<CtStatement> statements = block.getStatements();
            return !statements.isEmpty() && definitelyExits(statements.get(statements.size() - 1));
        }
        if (statement instanceof CtIf condition)
            return definitelyExits(condition.getThenStatement())
                && definitelyExits(condition.getElseStatement());
        return false;
    }

    private static String branch(CtIf parent, CtElement child) {
        CtElement cursor = child;
        while (cursor != null && safeParent(cursor) != parent) cursor = safeParent(cursor);
        return cursor == parent.getThenStatement() ? "true" :
            (cursor == parent.getElseStatement() ? "false" : "unknown");
    }

    private static String branch(CtConditional<?> parent, CtElement child) {
        CtElement cursor = child;
        while (cursor != null && safeParent(cursor) != parent) cursor = safeParent(cursor);
        return cursor == parent.getThenExpression() ? "true" :
            (cursor == parent.getElseExpression() ? "false" : "unknown");
    }

    private Map<String, CtExpression<?>> localMap(CtMethod<?> method) {
        Map<String, CtExpression<?>> result = new LinkedHashMap<>();
        for (CtLocalVariable<?> local : method.getElements(new TypeFilter<>(CtLocalVariable.class)))
            if (local.getDefaultExpression() != null) result.put(local.getSimpleName(), local.getDefaultExpression());
        return result;
    }

    private Map<String, Object> parameter(CtParameter<?> parameter) {
        return Map.of("name", parameter.getSimpleName(), "type", parameter.getType().getQualifiedName());
    }

    private Map<String, Object> span(CtElement element) {
        SourcePosition position = element == null ? null : element.getPosition();
        if (position == null || !position.isValidPosition() || position.getFile() == null) return Map.of();
        String path = position.getFile().getPath();
        String id = sourceIds.entrySet().stream().filter(e -> path.endsWith(e.getKey()))
            .map(Map.Entry::getValue).findFirst().orElse("unknown");
        return Map.of("source_id", id, "start_line", position.getLine(), "end_line", position.getEndLine());
    }

    private static CtElement safeParent(CtElement element) {
        try { return element != null && element.isParentInitialized() ? element.getParent() : null; }
        catch (RuntimeException exception) { return null; }
    }

    private static boolean same(String left, String right) {
        return left.equals(right) || left.endsWith("." + right) || right.endsWith("." + left);
    }

    private static String qualified(CtMethod<?> method) {
        return method.getDeclaringType().getQualifiedName() + "." + method.getSignature();
    }

    private static String string(Map<String, Object> object, String key) {
        Object value = object.get(key);
        if (!(value instanceof String text) || text.isBlank()) throw new IllegalArgumentException(key);
        return text;
    }
}
