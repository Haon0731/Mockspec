import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import spoon.Launcher;
import spoon.reflect.CtModel;
import spoon.reflect.code.CtCatch;
import spoon.reflect.code.CtConditional;
import spoon.reflect.code.CtIf;
import spoon.reflect.code.CtInvocation;
import spoon.reflect.code.CtLambda;
import spoon.reflect.code.CtLoop;
import spoon.reflect.code.CtReturn;
import spoon.reflect.code.CtSwitch;
import spoon.reflect.code.CtThrow;
import spoon.reflect.code.CtTry;
import spoon.reflect.code.CtTryWithResource;
import spoon.reflect.code.CtVariableRead;
import spoon.reflect.cu.SourcePosition;
import spoon.reflect.declaration.CtElement;
import spoon.reflect.declaration.CtMethod;
import spoon.reflect.declaration.CtVariable;
import spoon.reflect.reference.CtExecutableReference;
import spoon.reflect.reference.CtVariableReference;
import spoon.reflect.visitor.filter.TypeFilter;
import spoon.support.compiler.VirtualFile;

/** Fixed stdin/stdout Spoon adapter for one host-approved source string. */
public final class ScenarioSpoon {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final int MAX_FACTS = 500;
    private static final int MAX_TEXT = 400;

    private String sourceId;
    private String sourceText;
    private String[] sourceLines;
    private int sourceStartLine;
    private int sourceLineCount;
    private boolean truncated;
    private boolean unsupportedEncountered;
    private final List<Map<String, Object>> facts = new ArrayList<>();
    private final List<Map<String, Object>> diagnostics = new ArrayList<>();

    private ScenarioSpoon() {}

    public static void main(String[] args) throws IOException {
        ScenarioSpoon tool = new ScenarioSpoon();
        Map<String, Object> result;
        if (args.length != 0) {
            result = tool.error("analysis_error", "ScenarioSpoon accepts no arguments");
        } else {
            try {
                result = tool.analyze(JSON.readValue(
                    System.in, new TypeReference<Map<String, Object>>() {}));
            } catch (Exception exception) {
                result = tool.error("analysis_error", "Invalid stdin request.");
            }
        }
        System.out.print(JSON.writeValueAsString(result));
    }

    private Map<String, Object> analyze(Map<String, Object> input) {
        if (!input.keySet().equals(java.util.Set.of("source_id", "text", "start_line"))) {
            return error("analysis_error", "input must contain only source_id, text, start_line");
        }
        Object id = input.get("source_id");
        Object text = input.get("text");
        Object start = input.get("start_line");
        if (!(id instanceof String) || ((String) id).isBlank()
                || !(text instanceof String) || ((String) text).isBlank()
                || !(start instanceof Integer) || ((Integer) start) < 1) {
            return error("analysis_error", "invalid source input");
        }
        sourceId = (String) id;
        sourceText = (String) text;
        sourceLines = sourceText.split("\\R", -1);
        sourceStartLine = (Integer) start;
        sourceLineCount = sourceLines.length;
        if (sourceText.endsWith("\n") || sourceText.endsWith("\r")) {
            sourceLineCount -= 1;
        }

        try {
            Launcher launcher = new Launcher();
            launcher.getEnvironment().setNoClasspath(true);
            launcher.getEnvironment().setComplianceLevel(17);
            launcher.getEnvironment().setShouldCompile(false);
            launcher.getEnvironment().setCommentEnabled(false);
            launcher.addInputResource(new VirtualFile(sourceText, "ReviewedSource.java"));
            launcher.buildModel();
            extract(launcher.getModel());
        } catch (Exception exception) {
            return error(
                "analysis_error", "Spoon could not build a model for the supplied source.");
        }

        facts.sort(Comparator
            .comparingInt((Map<String, Object> fact) -> (Integer) fact.get("start_line"))
            .thenComparing(fact -> String.valueOf(fact.get("kind")))
            .thenComparing(fact -> String.valueOf(fact.getOrDefault("name", ""))));
        return result(truncated || unsupportedEncountered ? "incomplete" : "ok");
    }

    private void extract(CtModel model) {
        noteUnsupported(model, CtLoop.class, "loop");
        noteUnsupported(model, CtSwitch.class, "switch");
        noteUnsupported(model, CtLambda.class, "lambda");
        for (CtMethod<?> element : model.getElements(new TypeFilter<>(CtMethod.class))) {
            Map<String, Object> fact = located("method", element);
            if (fact == null) continue;
            fact.put("name", element.getSimpleName());
            fact.put("signature", bounded(element.getSignature()));
            add(fact);
        }
        for (CtInvocation<?> element : model.getElements(new TypeFilter<>(CtInvocation.class))) {
            CtExecutableReference<?> reference = element.getExecutable();
            Map<String, Object> fact = located("call", element);
            if (fact == null) continue;
            fact.put("name", reference == null ? null : reference.getSimpleName());
            fact.put("signature", safeExecutableSignature(reference));
            fact.put("target", bounded(element.getTarget()));
            fact.put("lexical_guards", lexicalGuards(element));
            CtElement declaration = safeExecutableDeclaration(reference);
            putReference(fact, declaration);
            add(fact);
        }
        for (CtVariableRead<?> element : model.getElements(new TypeFilter<>(CtVariableRead.class))) {
            CtVariableReference<?> reference = element.getVariable();
            Map<String, Object> fact = located("variable_read", element);
            if (fact == null) continue;
            fact.put("name", reference == null ? null : reference.getSimpleName());
            fact.put("lexical_guards", lexicalGuards(element));
            CtVariable<?> declaration = safeVariableDeclaration(reference);
            putReference(fact, declaration);
            add(fact);
        }
        for (CtIf element : model.getElements(new TypeFilter<>(CtIf.class))) {
            Map<String, Object> fact = located("if", element);
            if (fact == null) continue;
            fact.put("condition", bounded(element.getCondition()));
            fact.put("then_branch", span(element.getThenStatement()));
            fact.put("else_branch", span(element.getElseStatement()));
            fact.put("lexical_guards", lexicalGuards(element));
            add(fact);
        }
        for (CtConditional<?> element : model.getElements(new TypeFilter<>(CtConditional.class))) {
            Map<String, Object> fact = located("conditional", element);
            if (fact == null) continue;
            fact.put("condition", bounded(element.getCondition()));
            fact.put("then_expression", bounded(element.getThenExpression()));
            fact.put("else_expression", bounded(element.getElseExpression()));
            fact.put("lexical_guards", lexicalGuards(element));
            add(fact);
        }
        for (CtReturn<?> element : model.getElements(new TypeFilter<>(CtReturn.class))) {
            Map<String, Object> fact = located("return", element);
            if (fact == null) continue;
            fact.put("expression", bounded(element.getReturnedExpression()));
            fact.put("lexical_guards", lexicalGuards(element));
            add(fact);
        }
        for (CtThrow element : model.getElements(new TypeFilter<>(CtThrow.class))) {
            Map<String, Object> fact = located("throw", element);
            if (fact == null) continue;
            fact.put("expression", bounded(element.getThrownExpression()));
            fact.put("lexical_guards", lexicalGuards(element));
            add(fact);
        }
        for (CtTry element : model.getElements(new TypeFilter<>(CtTry.class))) {
            Map<String, Object> fact = located("try", element);
            if (fact == null) continue;
            fact.put("body", span(element.getBody()));
            fact.put("finally_block", span(element.getFinalizer()));
            fact.put("resource_count", element instanceof CtTryWithResource
                ? ((CtTryWithResource) element).getResources().size() : 0);
            fact.put("lexical_guards", lexicalGuards(element));
            add(fact);
        }
        for (CtCatch element : model.getElements(new TypeFilter<>(CtCatch.class))) {
            Map<String, Object> fact = located("catch", element);
            if (fact == null) continue;
            fact.put("parameter", bounded(element.getParameter()));
            fact.put("body", span(element.getBody()));
            fact.put("lexical_guards", lexicalGuards(element));
            add(fact);
        }
    }

    private <T extends CtElement> void noteUnsupported(
        CtModel model, Class<T> type, String name
    ) {
        int count = model.getElements(new TypeFilter<T>(type)).size();
        if (count == 0) return;
        unsupportedEncountered = true;
        diagnostics.add(diagnostic(
            "unsupported",
            "unsupported_" + name,
            "Encountered " + count + " " + name
                + " syntax node(s); lexical semantics for this construct were not modeled."));
    }

    private void add(Map<String, Object> fact) {
        if (facts.size() < MAX_FACTS) {
            facts.add(fact);
        } else if (!truncated) {
            truncated = true;
            diagnostics.add(diagnostic(
                "unsupported", "fact_limit", "Facts were truncated at " + MAX_FACTS));
        }
    }

    private Map<String, Object> located(String kind, CtElement element) {
        Map<String, Object> fact = span(element);
        if (fact == null) return null;
        fact.put("kind", kind);
        String quote = quote(element);
        if (quote != null) {
            fact.put("quote", quote);
        }
        return fact;
    }

    private void putReference(Map<String, Object> fact, CtElement declaration) {
        Map<String, Object> declarationSpan = span(declaration);
        if (declarationSpan == null) {
            fact.put("declaration_status", "unresolved");
            fact.put("declared_at", null);
        } else {
            fact.put("declaration_status", "resolved_local");
            fact.put("declared_at", declarationSpan);
        }
    }

    private List<Map<String, Object>> lexicalGuards(CtElement element) {
        List<Map<String, Object>> guards = new ArrayList<>();
        CtElement child = element;
        CtElement parent = safeParent(child);
        while (parent != null) {
            if (parent instanceof CtIf) {
                CtIf condition = (CtIf) parent;
                guards.add(0, guard("if", condition, branch(condition, child)));
            } else if (parent instanceof CtConditional<?>) {
                CtConditional<?> condition = (CtConditional<?>) parent;
                guards.add(0, guard("conditional", condition, branch(condition, child)));
            } else if (parent instanceof CtCatch) {
                guards.add(0, guard("catch", parent, "body"));
            } else if (parent instanceof CtTry) {
                guards.add(0, guard("try", parent, branch((CtTry) parent, child)));
            }
            child = parent;
            parent = safeParent(parent);
        }
        return guards;
    }

    private Map<String, Object> guard(String kind, CtElement element, String branch) {
        Map<String, Object> guard = span(element);
        if (guard == null) {
            guard = new LinkedHashMap<>();
            guard.put("source_id", sourceId);
        }
        guard.put("kind", kind);
        guard.put("branch", branch);
        if (element instanceof CtIf) {
            guard.put("condition", bounded(((CtIf) element).getCondition()));
        } else if (element instanceof CtConditional<?>) {
            guard.put("condition", bounded(((CtConditional<?>) element).getCondition()));
        }
        return guard;
    }

    private String branch(CtIf parent, CtElement child) {
        if (child == parent.getCondition()) return "condition";
        if (child == parent.getThenStatement()) return "true";
        if (child == parent.getElseStatement()) return "false";
        return "unknown";
    }

    private String branch(CtConditional<?> parent, CtElement child) {
        if (child == parent.getCondition()) return "condition";
        if (child == parent.getThenExpression()) return "true";
        if (child == parent.getElseExpression()) return "false";
        return "unknown";
    }

    private String branch(CtTry parent, CtElement child) {
        if (child == parent.getBody()) return "body";
        if (child == parent.getFinalizer()) return "finally";
        if (parent.getCatchers().contains(child)) return "catch";
        return "resource_or_unknown";
    }

    private Map<String, Object> span(CtElement element) {
        if (element == null) return null;
        SourcePosition position = element.getPosition();
        if (position == null || !position.isValidPosition() || position.getFile() == null
                || !"ReviewedSource.java".equals(position.getFile().getName())) return null;
        int localStart = position.getLine();
        int localEnd = position.getEndLine();
        if (localStart < 1 || localEnd < localStart || localEnd > sourceLineCount) return null;
        Map<String, Object> span = new LinkedHashMap<>();
        span.put("source_id", sourceId);
        span.put("start_line", sourceStartLine + localStart - 1);
        span.put("end_line", sourceStartLine + localEnd - 1);
        return span;
    }

    private String quote(CtElement element) {
        SourcePosition position = element.getPosition();
        if (position == null || !position.isValidPosition() || position.getFile() == null
                || !"ReviewedSource.java".equals(position.getFile().getName())) return null;
        int start = position.getLine();
        int end = position.getEndLine();
        if (start < 1 || end < start || end > sourceLineCount) return null;
        String value = String.join(
            "\n", java.util.Arrays.copyOfRange(sourceLines, start - 1, end));
        return value.length() <= MAX_TEXT ? value : null;
    }

    private Map<String, Object> result(String status) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("status", status);
        result.put("backend", "spoon");
        result.put("source_id", sourceId);
        result.put("facts", facts);
        result.put("limitations", List.of(
            "Facts are syntax-tree relationships, not proof of data flow or path feasibility.",
            "No-classpath resolution may leave external declarations unresolved.",
            "Exception syntax does not prove propagation or runtime behavior.",
            "Lexical guards cover if, conditional, try, and catch ancestry; loop, switch, and lambda semantics are unsupported."));
        result.put("diagnostics", diagnostics);
        return result;
    }

    private Map<String, Object> error(String code, String message) {
        Map<String, Object> result = result("error");
        result.put("error_code", code);
        diagnostics.add(diagnostic("analysis_error", code, message));
        result.put("diagnostics", diagnostics);
        return result;
    }

    private static Map<String, Object> diagnostic(String category, String code, String message) {
        Map<String, Object> diagnostic = new LinkedHashMap<>();
        diagnostic.put("category", category);
        diagnostic.put("code", code);
        diagnostic.put("message", bounded(message));
        return diagnostic;
    }

    private static CtElement safeExecutableDeclaration(CtExecutableReference<?> reference) {
        if (reference == null) return null;
        try { return reference.getDeclaration(); }
        catch (RuntimeException ignored) { return null; }
    }

    private static String safeExecutableSignature(CtExecutableReference<?> reference) {
        if (reference == null) return null;
        try { return bounded(reference.getSignature()); }
        catch (RuntimeException ignored) { return null; }
    }

    private static CtVariable<?> safeVariableDeclaration(CtVariableReference<?> reference) {
        if (reference == null) return null;
        try { return reference.getDeclaration(); }
        catch (RuntimeException ignored) { return null; }
    }

    private static CtElement safeParent(CtElement element) {
        if (element == null || !element.isParentInitialized()) return null;
        try { return element.getParent(); }
        catch (RuntimeException ignored) { return null; }
    }

    private static String bounded(Object value) {
        if (value == null) return null;
        String text;
        try { text = value.toString(); }
        catch (RuntimeException exception) { return "<unrenderable>"; }
        text = text.replaceAll("\\s+", " ").strip();
        return text.length() <= MAX_TEXT ? text : text.substring(0, MAX_TEXT - 1) + "…";
    }

}
