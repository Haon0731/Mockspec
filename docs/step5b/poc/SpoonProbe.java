import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import java.io.IOException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import spoon.Launcher;
import spoon.reflect.CtModel;
import spoon.reflect.code.CtConditional;
import spoon.reflect.code.CtIf;
import spoon.reflect.code.CtInvocation;
import spoon.reflect.code.CtVariableRead;
import spoon.reflect.cu.SourcePosition;
import spoon.reflect.declaration.CtElement;
import spoon.reflect.declaration.CtMethod;
import spoon.reflect.declaration.CtType;
import spoon.reflect.declaration.CtVariable;
import spoon.reflect.reference.CtExecutableReference;
import spoon.reflect.reference.CtVariableReference;
import spoon.reflect.visitor.filter.TypeFilter;

/**
 * Fixed, read-only Spoon probe for the Step 5B isolated /input tree.
 *
 * <p>The probe deliberately accepts no query or source-path arguments. It parses only /input,
 * uses noClasspath mode, never compiles the target, and emits bounded machine-readable facts.
 */
public final class SpoonProbe {
    private static final Path INPUT_ROOT = Path.of("/input").toAbsolutePath().normalize();
    private static final int MAX_TEXT_LENGTH = 240;
    private static final ObjectMapper JSON = new ObjectMapper()
        .enable(SerializationFeature.INDENT_OUTPUT);

    private SpoonProbe() {
    }

    public static void main(String[] args) {
        Map<String, Object> output = baseOutput();
        int exitCode = 0;
        try {
            if (args.length != 0) {
                throw new IllegalArgumentException("SpoonProbe accepts no arguments; input is fixed at /input");
            }
            CtModel model = buildModel();
            extractFacts(model, output);
            @SuppressWarnings("unchecked")
            Map<String, Object> counts = (Map<String, Object>) output.get("counts");
            int factCount = counts.values().stream().mapToInt(value -> (Integer) value).sum();
            if (factCount == 0) {
                addDiagnostic(output, "error", "zero_facts", null,
                    "Spoon built a model but produced no requested facts");
                output.put("status", "failure");
                exitCode = 3;
            } else {
                output.put("status", "success");
            }
        } catch (Exception exception) {
            output.put("status", "failure");
            addDiagnostic(output, "error", "model_build_failed", null, safeMessage(exception));
            exitCode = 3;
        }

        try {
            System.out.println(JSON.writeValueAsString(output));
        } catch (IOException exception) {
            System.err.println("Unable to serialize Spoon probe result: " + safeMessage(exception));
            System.exit(4);
        }
        System.exit(exitCode);
    }

    private static CtModel buildModel() {
        Launcher launcher = new Launcher();
        launcher.getEnvironment().setNoClasspath(true);
        launcher.getEnvironment().setComplianceLevel(11);
        launcher.getEnvironment().setShouldCompile(false);
        launcher.getEnvironment().setCommentEnabled(false);
        launcher.addInputResource(INPUT_ROOT.toString());
        launcher.buildModel();
        return launcher.getModel();
    }

    private static Map<String, Object> baseOutput() {
        Map<String, Object> output = new LinkedHashMap<>();
        output.put("schema_version", "step5b-spoon-probe-v1");
        output.put("backend", "spoon");
        output.put("spoon_version", "11.2.1");
        output.put("input_root", "/input");
        output.put("mode", mapOf(
            "no_classpath", true,
            "compliance_level", 11,
            "should_compile", false
        ));
        output.put("status", "running");
        output.put("facts", new LinkedHashMap<String, Object>());
        output.put("counts", new LinkedHashMap<String, Object>());
        output.put("diagnostics", new ArrayList<Map<String, Object>>());
        return output;
    }

    private static void extractFacts(CtModel model, Map<String, Object> output) {
        Map<String, Object> facts = facts(output);
        addFactGroup(facts, output, "methods", methodFacts(model));
        addFactGroup(facts, output, "calls", callFacts(model, output));
        addFactGroup(facts, output, "ifs", ifFacts(model));
        addFactGroup(facts, output, "conditionals", conditionalFacts(model));
        addFactGroup(facts, output, "variable_reads", variableReadFacts(model, output));
    }

    private static List<Map<String, Object>> methodFacts(CtModel model) {
        List<Map<String, Object>> facts = new ArrayList<>();
        for (CtMethod<?> method : model.getElements(new TypeFilter<>(CtMethod.class))) {
            CtType<?> declaringType = method.getDeclaringType();
            Map<String, Object> fact = locatedFact("method", method);
            fact.put("name", method.getSimpleName());
            fact.put("signature", bounded(method.getSignature()));
            fact.put("declaring_type", declaringType == null ? null : declaringType.getQualifiedName());
            facts.add(fact);
        }
        sortFacts(facts);
        return facts;
    }

    private static List<Map<String, Object>> callFacts(CtModel model, Map<String, Object> output) {
        List<Map<String, Object>> facts = new ArrayList<>();
        for (CtInvocation<?> invocation : model.getElements(new TypeFilter<>(CtInvocation.class))) {
            CtExecutableReference<?> executable = invocation.getExecutable();
            CtMethod<?> enclosingMethod = safeParent(invocation, CtMethod.class);
            Map<String, Object> fact = locatedFact("call", invocation);
            fact.put("text", bounded(invocation));
            fact.put("callee", safeExecutableSignature(executable));
            fact.put("callee_name", safeExecutableName(executable));
            fact.put("declaring_type", safeDeclaringType(executable));
            fact.put("caller_method", enclosingMethod == null ? null : enclosingMethod.getSimpleName());
            fact.put("control_ancestors", controlAncestors(invocation));

            CtElement declaration = safeExecutableDeclaration(executable);
            Map<String, Object> declarationSource = sourceSpan(declaration);
            boolean resolvedInInput = isInInput(declarationSource);
            fact.put("declaration", declarationSource);
            fact.put("declaration_status", resolvedInInput ? "resolved_in_input" : "unresolved");
            if (!resolvedInInput) {
                addDiagnostic(output, "info", "unresolved_call_declaration", sourceSpan(invocation),
                    "No declaration in the isolated Spoon model for call "
                        + (executable == null ? "<unknown>" : safeExecutableSignature(executable)));
            }
            facts.add(fact);
        }
        sortFacts(facts);
        return facts;
    }

    private static List<Map<String, Object>> ifFacts(CtModel model) {
        List<Map<String, Object>> facts = new ArrayList<>();
        for (CtIf ifElement : model.getElements(new TypeFilter<>(CtIf.class))) {
            Map<String, Object> fact = locatedFact("if", ifElement);
            fact.put("condition", bounded(ifElement.getCondition()));
            fact.put("then_branch", sourceSpan(ifElement.getThenStatement()));
            fact.put("else_branch", sourceSpan(ifElement.getElseStatement()));
            fact.put("control_ancestors", controlAncestors(ifElement));
            facts.add(fact);
        }
        sortFacts(facts);
        return facts;
    }

    private static List<Map<String, Object>> conditionalFacts(CtModel model) {
        List<Map<String, Object>> facts = new ArrayList<>();
        for (CtConditional<?> conditional : model.getElements(new TypeFilter<>(CtConditional.class))) {
            Map<String, Object> fact = locatedFact("conditional", conditional);
            fact.put("condition", bounded(conditional.getCondition()));
            fact.put("then_expression", bounded(conditional.getThenExpression()));
            fact.put("else_expression", bounded(conditional.getElseExpression()));
            fact.put("control_ancestors", controlAncestors(conditional));
            facts.add(fact);
        }
        sortFacts(facts);
        return facts;
    }

    private static List<Map<String, Object>> variableReadFacts(CtModel model, Map<String, Object> output) {
        List<Map<String, Object>> facts = new ArrayList<>();
        for (CtVariableRead<?> read : model.getElements(new TypeFilter<>(CtVariableRead.class))) {
            CtVariableReference<?> reference = read.getVariable();
            CtVariable<?> declaration = safeVariableDeclaration(reference);
            Map<String, Object> declarationSource = sourceSpan(declaration);
            boolean resolvedInInput = isInInput(declarationSource);
            Map<String, Object> fact = locatedFact("variable_read", read);
            fact.put("name", reference == null ? null : reference.getSimpleName());
            fact.put("text", bounded(read));
            fact.put("declaration", declarationSource);
            fact.put("declaration_kind", declaration == null ? null
                : declaration.getClass().getSimpleName());
            fact.put("declaration_status", resolvedInInput ? "resolved_in_input" : "unresolved");
            fact.put("control_ancestors", controlAncestors(read));
            if (!resolvedInInput) {
                addDiagnostic(output, "info", "unresolved_variable_declaration", sourceSpan(read),
                    "No declaration in the isolated Spoon model for variable "
                        + (reference == null ? "<unknown>" : reference.getSimpleName()));
            }
            facts.add(fact);
        }
        sortFacts(facts);
        return facts;
    }

    private static List<Map<String, Object>> controlAncestors(CtElement element) {
        List<Map<String, Object>> ancestors = new ArrayList<>();
        CtElement child = element;
        CtElement parent = safeDirectParent(child);
        while (parent != null) {
            if (parent instanceof CtIf) {
                CtIf ifElement = (CtIf) parent;
                ancestors.add(controlAncestor("if", ifElement, branchWithin(ifElement, child)));
            } else if (parent instanceof CtConditional<?>) {
                CtConditional<?> conditional = (CtConditional<?>) parent;
                ancestors.add(controlAncestor(
                    "conditional", conditional, branchWithin(conditional, child)));
            }
            child = parent;
            parent = safeDirectParent(parent);
        }
        Collections.reverse(ancestors);
        return ancestors;
    }

    private static Map<String, Object> controlAncestor(
        String kind, CtElement control, String branch
    ) {
        Map<String, Object> ancestor = new LinkedHashMap<>();
        ancestor.put("kind", kind);
        ancestor.put("source", sourceSpan(control));
        ancestor.put("branch", branch);
        if (control instanceof CtIf) {
            CtIf ifElement = (CtIf) control;
            ancestor.put("condition", bounded(ifElement.getCondition()));
        } else if (control instanceof CtConditional<?>) {
            CtConditional<?> conditional = (CtConditional<?>) control;
            ancestor.put("condition", bounded(conditional.getCondition()));
        }
        return ancestor;
    }

    private static String branchWithin(CtIf control, CtElement directChild) {
        if (directChild == control.getCondition()) {
            return "condition";
        }
        if (directChild == control.getThenStatement()) {
            return "true";
        }
        if (directChild == control.getElseStatement()) {
            return "false";
        }
        return "unknown";
    }

    private static String branchWithin(CtConditional<?> control, CtElement directChild) {
        if (directChild == control.getCondition()) {
            return "condition";
        }
        if (directChild == control.getThenExpression()) {
            return "true";
        }
        if (directChild == control.getElseExpression()) {
            return "false";
        }
        return "unknown";
    }

    private static CtElement safeExecutableDeclaration(CtExecutableReference<?> reference) {
        if (reference == null) {
            return null;
        }
        try {
            return reference.getDeclaration();
        } catch (RuntimeException ignored) {
            return null;
        }
    }

    private static String safeExecutableSignature(CtExecutableReference<?> reference) {
        if (reference == null) {
            return null;
        }
        try {
            return bounded(reference.getSignature());
        } catch (RuntimeException ignored) {
            return null;
        }
    }

    private static String safeExecutableName(CtExecutableReference<?> reference) {
        if (reference == null) {
            return null;
        }
        try {
            return reference.getSimpleName();
        } catch (RuntimeException ignored) {
            return null;
        }
    }

    private static String safeDeclaringType(CtExecutableReference<?> reference) {
        if (reference == null || reference.getDeclaringType() == null) {
            return null;
        }
        try {
            return reference.getDeclaringType().getQualifiedName();
        } catch (RuntimeException ignored) {
            return null;
        }
    }

    private static CtVariable<?> safeVariableDeclaration(CtVariableReference<?> reference) {
        if (reference == null) {
            return null;
        }
        try {
            return reference.getDeclaration();
        } catch (RuntimeException ignored) {
            return null;
        }
    }

    private static CtElement safeDirectParent(CtElement element) {
        if (element == null || !element.isParentInitialized()) {
            return null;
        }
        try {
            return element.getParent();
        } catch (RuntimeException ignored) {
            return null;
        }
    }

    private static <T extends CtElement> T safeParent(CtElement element, Class<T> type) {
        try {
            return element.getParent(type);
        } catch (RuntimeException ignored) {
            return null;
        }
    }

    private static Map<String, Object> locatedFact(String kind, CtElement element) {
        Map<String, Object> fact = new LinkedHashMap<>();
        fact.put("kind", kind);
        fact.put("source", sourceSpan(element));
        return fact;
    }

    private static Map<String, Object> sourceSpan(CtElement element) {
        if (element == null) {
            return null;
        }
        SourcePosition position = element.getPosition();
        if (position == null || !position.isValidPosition() || position.getFile() == null) {
            return null;
        }
        Path source = position.getFile().toPath().toAbsolutePath().normalize();
        String relativeFile = source.startsWith(INPUT_ROOT)
            ? INPUT_ROOT.relativize(source).toString().replace('\\', '/')
            : "<outside-input>";
        return mapOf(
            "file", relativeFile,
            "line", position.getLine(),
            "end_line", position.getEndLine()
        );
    }

    private static boolean isInInput(Map<String, Object> source) {
        return source != null && !"<outside-input>".equals(source.get("file"));
    }

    private static String bounded(Object value) {
        if (value == null) {
            return null;
        }
        String text;
        try {
            text = value.toString();
        } catch (RuntimeException exception) {
            return "<unrenderable>";
        }
        text = text.replaceAll("\\s+", " ").strip();
        if (text.length() <= MAX_TEXT_LENGTH) {
            return text;
        }
        return text.substring(0, MAX_TEXT_LENGTH - 1) + "…";
    }

    private static String safeMessage(Throwable throwable) {
        String message = throwable.getMessage();
        if (message == null || message.isBlank()) {
            message = throwable.getClass().getSimpleName();
        }
        message = message.replace(INPUT_ROOT.toString(), "/input");
        return bounded(message);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> facts(Map<String, Object> output) {
        return (Map<String, Object>) output.get("facts");
    }

    @SuppressWarnings("unchecked")
    private static void addDiagnostic(
        Map<String, Object> output,
        String severity,
        String code,
        Map<String, Object> source,
        String message
    ) {
        List<Map<String, Object>> diagnostics =
            (List<Map<String, Object>>) output.get("diagnostics");
        diagnostics.add(mapOf(
            "severity", severity,
            "code", code,
            "source", source,
            "message", bounded(message)
        ));
    }

    @SuppressWarnings("unchecked")
    private static void addFactGroup(
        Map<String, Object> facts,
        Map<String, Object> output,
        String name,
        List<Map<String, Object>> group
    ) {
        facts.put(name, group);
        Map<String, Object> counts = (Map<String, Object>) output.get("counts");
        counts.put(name, group.size());
    }

    private static void sortFacts(List<Map<String, Object>> facts) {
        facts.sort(Comparator
            .comparing((Map<String, Object> fact) -> sourceFile(fact))
            .thenComparingInt(SpoonProbe::sourceLine)
            .thenComparing(fact -> String.valueOf(fact.get("kind")))
            .thenComparing(fact -> String.valueOf(fact)));
    }

    @SuppressWarnings("unchecked")
    private static String sourceFile(Map<String, Object> fact) {
        Map<String, Object> source = (Map<String, Object>) fact.get("source");
        return source == null ? "" : String.valueOf(source.get("file"));
    }

    @SuppressWarnings("unchecked")
    private static int sourceLine(Map<String, Object> fact) {
        Map<String, Object> source = (Map<String, Object>) fact.get("source");
        return source == null ? Integer.MAX_VALUE : (Integer) source.get("line");
    }

    private static Map<String, Object> mapOf(Object... entries) {
        Map<String, Object> result = new LinkedHashMap<>();
        for (int index = 0; index < entries.length; index += 2) {
            result.put((String) entries[index], entries[index + 1]);
        }
        return result;
    }
}
