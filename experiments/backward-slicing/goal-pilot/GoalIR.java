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
import spoon.reflect.code.CtAssignment;
import spoon.reflect.code.CtBinaryOperator;
import spoon.reflect.code.CtBlock;
import spoon.reflect.code.CtConditional;
import spoon.reflect.code.CtConstructorCall;
import spoon.reflect.code.CtExpression;
import spoon.reflect.code.CtFieldAccess;
import spoon.reflect.code.CtIf;
import spoon.reflect.code.CtInvocation;
import spoon.reflect.code.CtLambda;
import spoon.reflect.code.CtLiteral;
import spoon.reflect.code.CtLocalVariable;
import spoon.reflect.code.CtReturn;
import spoon.reflect.code.CtStatement;
import spoon.reflect.code.CtSuperAccess;
import spoon.reflect.code.CtThisAccess;
import spoon.reflect.code.CtThrow;
import spoon.reflect.code.CtTypeAccess;
import spoon.reflect.code.CtUnaryOperator;
import spoon.reflect.code.CtVariableAccess;
import spoon.reflect.cu.SourcePosition;
import spoon.reflect.declaration.CtAnnotation;
import spoon.reflect.declaration.CtConstructor;
import spoon.reflect.declaration.CtElement;
import spoon.reflect.declaration.CtField;
import spoon.reflect.declaration.CtMethod;
import spoon.reflect.declaration.CtParameter;
import spoon.reflect.declaration.CtType;
import spoon.reflect.declaration.CtTypeMember;
import spoon.reflect.reference.CtTypeReference;
import spoon.support.compiler.VirtualFile;

/** Source-faithful syntax IR for bounded, host-side goal composition. */
public final class GoalIR {
    private static final ObjectMapper JSON = new ObjectMapper();

    private record SourceInfo(String sourceId, String logicalPath, int startLine) {}

    private final Map<String, SourceInfo> sources = new HashMap<>();
    private final List<Map<String, Object>> unsupported = new ArrayList<>();
    private final Set<String> unsupportedKeys = new HashSet<>();

    public static void main(String[] args) throws Exception {
        if (args.length != 0) throw new IllegalArgumentException("stdin only");
        Map<String, Object> request = JSON.readValue(System.in,
            new TypeReference<Map<String, Object>>() {});
        System.out.print(JSON.writeValueAsString(new GoalIR().run(request)));
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> run(Map<String, Object> request) {
        Object supplied = request.get("sources");
        if (!(supplied instanceof List<?> list) || list.isEmpty())
            throw new IllegalArgumentException("sources required");

        Launcher launcher = new Launcher();
        launcher.getEnvironment().setNoClasspath(true);
        launcher.getEnvironment().setComplianceLevel(17);
        launcher.getEnvironment().setShouldCompile(false);
        for (Object item : list) {
            if (!(item instanceof Map<?, ?> raw)) throw new IllegalArgumentException("invalid source");
            Map<String, Object> source = (Map<String, Object>) raw;
            String sourceId = requiredString(source, "source_id");
            String path = requiredString(source, "logical_path");
            String text = requiredString(source, "text");
            int startLine = source.get("start_line") instanceof Number number
                ? number.intValue() : 1;
            if (startLine < 1) throw new IllegalArgumentException("start_line must be positive");
            sources.put(path, new SourceInfo(sourceId, path, startLine));
            launcher.addInputResource(new VirtualFile(text, path));
        }
        launcher.buildModel();

        List<Map<String, Object>> types = new ArrayList<>();
        for (CtType<?> type : launcher.getModel().getAllTypes()) {
            if (!validPosition(type)) continue;
            types.add(type(type));
        }
        types.sort(Comparator.comparing(item -> String.valueOf(item.get("name"))));

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("backend", "spoon-source-goal-ir/v1");
        result.put("types", types);
        result.put("unsupported", unsupported);
        result.put("limitations", List.of(
            "The IR is syntax-level and uses Spoon no-classpath resolution.",
            "Unknown nodes retain source text and location in unsupported records.",
            "Type and declaring-type fields may be unknown and do not prove runtime dispatch.",
            "Expression evaluation order, heap aliases, exceptions, and library semantics are not inferred."));
        return result;
    }

    private Map<String, Object> type(CtType<?> type) {
        Map<String, Object> out = node("type", type);
        out.put("name", type.getQualifiedName());
        out.put("superclass", typeName(type.getSuperclass()));
        out.put("annotations", annotations(type.getAnnotations()));

        List<Map<String, Object>> fields = new ArrayList<>();
        for (CtField<?> field : type.getFields()) fields.add(field(field));
        fields.sort(Comparator.comparing(item -> String.valueOf(item.get("name"))));
        out.put("fields", fields);

        List<Map<String, Object>> constructors = new ArrayList<>();
        for (CtTypeMember member : type.getTypeMembers())
            if (member instanceof CtConstructor<?> constructor)
                constructors.add(constructor(constructor));
        constructors.sort(Comparator.comparing(item -> String.valueOf(item.get("signature"))));
        out.put("constructors", constructors);

        List<Map<String, Object>> methods = new ArrayList<>();
        for (CtMethod<?> method : type.getMethods()) methods.add(method(method));
        methods.sort(Comparator.comparing(item -> String.valueOf(item.get("signature"))));
        out.put("methods", methods);
        return out;
    }

    private Map<String, Object> field(CtField<?> field) {
        Map<String, Object> out = node("field_declaration", field);
        out.put("name", field.getSimpleName());
        out.put("type", typeName(field.getType()));
        out.put("has_initializer", field.getDefaultExpression() != null);
        out.put("initializer", field.getDefaultExpression() == null
            ? null : expression(field.getDefaultExpression()));
        out.put("annotations", annotations(field.getAnnotations()));
        return out;
    }

    private Map<String, Object> constructor(CtConstructor<?> constructor) {
        Map<String, Object> out = node("constructor", constructor);
        out.put("signature", constructor.getSignature());
        out.put("parameters", parameters(constructor.getParameters()));
        out.put("body", statement(constructor.getBody()));
        out.put("annotations", annotations(constructor.getAnnotations()));
        return out;
    }

    private Map<String, Object> method(CtMethod<?> method) {
        Map<String, Object> out = node("method", method);
        out.put("name", method.getSimpleName());
        out.put("signature", method.getSignature());
        out.put("return_type", typeName(method.getType()));
        out.put("static", method.isStatic());
        out.put("parameters", parameters(method.getParameters()));
        out.put("body", statement(method.getBody()));
        out.put("annotations", annotations(method.getAnnotations()));
        return out;
    }

    private List<Map<String, Object>> parameters(List<CtParameter<?>> values) {
        List<Map<String, Object>> result = new ArrayList<>();
        for (CtParameter<?> parameter : values) {
            Map<String, Object> item = node("parameter", parameter);
            item.put("name", parameter.getSimpleName());
            item.put("type", typeName(parameter.getType()));
            result.add(item);
        }
        return result;
    }

    private Map<String, Object> statement(CtStatement statement) {
        if (statement == null) return null;
        if (statement instanceof CtBlock<?> block) {
            Map<String, Object> out = node("block", block);
            out.put("statements", block.getStatements().stream().map(this::statement).toList());
            return out;
        }
        if (statement instanceof CtLocalVariable<?> local) {
            Map<String, Object> out = node("local", local);
            out.put("name", local.getSimpleName());
            out.put("type", typeName(local.getType()));
            out.put("value", local.getDefaultExpression() == null
                ? null : expression(local.getDefaultExpression()));
            return out;
        }
        if (statement instanceof CtAssignment<?, ?> assignment) {
            Map<String, Object> out = node("assign", assignment);
            out.put("target", expression(assignment.getAssigned()));
            out.put("value", expression(assignment.getAssignment()));
            return out;
        }
        if (statement instanceof CtIf condition) {
            Map<String, Object> out = node("if", condition);
            out.put("condition", expression(condition.getCondition()));
            out.put("then", statement(condition.getThenStatement()));
            out.put("else", statement(condition.getElseStatement()));
            return out;
        }
        if (statement instanceof CtReturn<?> returned) {
            Map<String, Object> out = node("return", returned);
            out.put("value", returned.getReturnedExpression() == null
                ? null : expression(returned.getReturnedExpression()));
            return out;
        }
        if (statement instanceof CtThrow thrown) {
            Map<String, Object> out = node("throw", thrown);
            out.put("value", expression(thrown.getThrownExpression()));
            return out;
        }
        if (statement instanceof CtExpression<?> value) {
            Map<String, Object> out = node("expr", statement);
            out.put("value", expression(value));
            return out;
        }
        return unsupportedStatement(statement);
    }

    private Map<String, Object> expression(CtExpression<?> expression) {
        if (expression == null) return null;
        if (expression instanceof CtLiteral<?> literal) {
            Map<String, Object> out = node("literal", literal);
            out.put("value", literal.getValue());
            return out;
        }
        if (expression instanceof CtThisAccess<?> access) {
            Map<String, Object> out = node("this", access);
            out.put("type", typeName(access.getType()));
            return out;
        }
        if (expression instanceof CtSuperAccess<?> access) {
            Map<String, Object> out = node("super", access);
            out.put("type", typeName(access.getType()));
            return out;
        }
        if (expression instanceof CtTypeAccess<?> access) {
            Map<String, Object> out = node("type", access);
            out.put("name", typeName(access.getAccessedType()));
            return out;
        }
        if (expression instanceof CtFieldAccess<?> access) {
            if ("class".equals(access.getVariable().getSimpleName())) {
                Map<String, Object> out = node("class_literal", access);
                out.put("type", access.getTarget() == null
                    ? "unknown" : typeName(access.getTarget().getType()));
                return out;
            }
            Map<String, Object> out = node("field", access);
            out.put("target", expression(access.getTarget()));
            out.put("name", access.getVariable().getSimpleName());
            return out;
        }
        if (expression instanceof CtVariableAccess<?> access) {
            Map<String, Object> out = node("var", access);
            out.put("name", access.getVariable().getSimpleName());
            return out;
        }
        if (expression instanceof CtInvocation<?> call) return call(call);
        if (expression instanceof CtConstructorCall<?> constructor) {
            Map<String, Object> out = node("new", constructor);
            out.put("type", typeName(constructor.getType()));
            out.put("arguments", constructor.getArguments().stream().map(this::expression).toList());
            return out;
        }
        if (expression instanceof CtBinaryOperator<?> binary) {
            Map<String, Object> out = node("binary", binary);
            out.put("operator", binary.getKind().name());
            out.put("left", expression(binary.getLeftHandOperand()));
            out.put("right", expression(binary.getRightHandOperand()));
            return out;
        }
        if (expression instanceof CtUnaryOperator<?> unary) {
            Map<String, Object> out = node("unary", unary);
            out.put("operator", unary.getKind().name());
            out.put("operand", expression(unary.getOperand()));
            return out;
        }
        if (expression instanceof CtConditional<?> conditional) {
            Map<String, Object> out = node("conditional", conditional);
            out.put("condition", expression(conditional.getCondition()));
            out.put("then", expression(conditional.getThenExpression()));
            out.put("else", expression(conditional.getElseExpression()));
            return out;
        }
        if (expression instanceof CtLambda<?> lambda) {
            Map<String, Object> out = node("lambda", lambda);
            out.put("opaque", true);
            out.put("parameters", parameters(lambda.getParameters()));
            recordUnsupported("opaque_expression", lambda);
            return out;
        }
        return unsupportedExpression(expression);
    }

    private Map<String, Object> call(CtInvocation<?> call) {
        Map<String, Object> out = node("call", call);
        String name = call.getExecutable() == null ? "unknown"
            : call.getExecutable().getSimpleName();
        out.put("name", name == null || name.isBlank() ? "unknown" : name);
        if ("<init>".equals(name) && call.getTarget() == null) {
            Map<String, Object> target = node("super", call);
            target.put("text", "super");
            target.put("type", call.getExecutable() == null
                ? "unknown" : typeName(call.getExecutable().getDeclaringType()));
            target.put("implicit", true);
            out.put("target", target);
        } else {
            out.put("target", expression(call.getTarget()));
        }
        out.put("declaring_type", call.getExecutable() == null
            ? "unknown" : typeName(call.getExecutable().getDeclaringType()));
        out.put("arguments", call.getArguments().stream().map(this::expression).toList());
        return out;
    }

    private Map<String, Object> unsupportedStatement(CtStatement value) {
        recordUnsupported("statement", value);
        return node("unsupported_statement", value);
    }

    private Map<String, Object> unsupportedExpression(CtExpression<?> value) {
        recordUnsupported("expression", value);
        return node("unsupported_expression", value);
    }

    private void recordUnsupported(String category, CtElement value) {
        String key = category + ":" + value.getClass().getName() + ":" + location(value);
        if (!unsupportedKeys.add(key)) return;
        Map<String, Object> item = node(category, value);
        item.put("node_type", value.getClass().getSimpleName());
        unsupported.add(item);
    }

    private Map<String, Object> node(String kind, CtElement element) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("kind", kind);
        result.put("text", element == null ? "" : element.toString());
        result.put("location", location(element));
        return result;
    }

    private Map<String, Object> location(CtElement element) {
        Map<String, Object> result = new LinkedHashMap<>();
        SourcePosition position = element == null ? null : element.getPosition();
        CtElement cursor = element;
        while ((position == null || !position.isValidPosition() || position.getFile() == null)
                && cursor != null && cursor.isParentInitialized()) {
            try {
                cursor = cursor.getParent();
                position = cursor == null ? null : cursor.getPosition();
            } catch (RuntimeException ignored) {
                cursor = null;
            }
        }
        if (position == null || !position.isValidPosition() || position.getFile() == null)
            return result;
        SourceInfo info = sourceInfo(position.getFile().getPath());
        int offset = info == null ? 0 : info.startLine() - 1;
        result.put("source_id", info == null ? "unknown" : info.sourceId());
        result.put("start_line", position.getLine() + offset);
        result.put("end_line", position.getEndLine() + offset);
        try {
            result.put("start_column", position.getColumn());
            result.put("end_column", position.getEndColumn());
        } catch (RuntimeException ignored) {
            result.put("start_column", -1);
            result.put("end_column", -1);
        }
        return result;
    }

    private SourceInfo sourceInfo(String actualPath) {
        return sources.entrySet().stream()
            .filter(entry -> actualPath.endsWith(entry.getKey()))
            .map(Map.Entry::getValue).findFirst().orElse(null);
    }

    private static boolean validPosition(CtElement element) {
        SourcePosition position = element == null ? null : element.getPosition();
        return position != null && position.isValidPosition() && position.getFile() != null;
    }

    private static List<String> annotations(List<CtAnnotation<?>> annotations) {
        return annotations.stream().map(annotation -> typeName(annotation.getAnnotationType())).toList();
    }

    private static String typeName(CtTypeReference<?> type) {
        if (type == null) return "unknown";
        try {
            String name = type.getQualifiedName();
            return name == null || name.isBlank() ? "unknown" : name;
        } catch (RuntimeException ignored) {
            return "unknown";
        }
    }

    private static String requiredString(Map<String, Object> object, String key) {
        Object value = object.get(key);
        if (!(value instanceof String text) || (text.isBlank() && !"text".equals(key)))
            throw new IllegalArgumentException(key);
        return text;
    }
}
