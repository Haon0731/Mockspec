import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.boot.actuate.endpoint.EndpointId;
import org.springframework.util.StringUtils;
import org.springframework.web.util.UriComponentsBuilder;

/** Strict evaluator for a small, explicit subset of the frozen Spring runtime. */
public final class LibraryOracle {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final String STRING_UTILS = "org.springframework.util.StringUtils";
    private static final String URI_BUILDER = "org.springframework.web.util.UriComponentsBuilder";
    private static final String ENDPOINT_ID = "org.springframework.boot.actuate.endpoint.EndpointId";

    private static final class Unsupported extends Exception {
        Unsupported(String message) { super(message); }
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 0) throw new IllegalArgumentException("stdin only");
        Map<String, Object> request = JSON.readValue(
            System.in, new TypeReference<Map<String, Object>>() {});
        Object rawQueries = request.get("queries");
        if (!(rawQueries instanceof List<?>))
            throw new IllegalArgumentException("queries must be an array");
        List<?> queries = (List<?>) rawQueries;
        List<Map<String, Object>> results = new ArrayList<>();
        for (Object rawQuery : queries) results.add(evaluateQuery(rawQuery));
        System.out.print(JSON.writeValueAsString(results));
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> evaluateQuery(Object rawQuery) {
        Map<String, Object> result = new LinkedHashMap<>();
        if (!(rawQuery instanceof Map<?, ?>)) {
            result.put("id", null);
            result.put("status", "unsupported");
            result.put("exception", "query must be an object");
            return result;
        }
        Map<?, ?> rawMap = (Map<?, ?>) rawQuery;
        Map<String, Object> query = (Map<String, Object>) rawMap;
        Object id = query.get("id");
        result.put("id", id);
        try {
            if (!(id instanceof String) || ((String) id).isEmpty())
                throw new Unsupported("id must be a non-empty string");
            Object value = evaluate(query.get("expr"));
            result.put("status", "value");
            result.put("value", externalValue(value));
        } catch (Unsupported error) {
            result.put("status", "unsupported");
            result.put("exception", error.getMessage());
        } catch (Throwable error) {
            result.put("status", "raise");
            result.put("exception", error.getClass().getName() + ": " + String.valueOf(error.getMessage()));
        }
        return result;
    }

    @SuppressWarnings("unchecked")
    private static Object evaluate(Object rawExpression) throws Exception {
        if (!(rawExpression instanceof Map<?, ?>))
            throw new Unsupported("expression must be an object");
        Map<?, ?> rawMap = (Map<?, ?>) rawExpression;
        Map<String, Object> expression = (Map<String, Object>) rawMap;
        String kind = requireString(expression, "kind");
        if ("literal".equals(kind)) {
            Object value = expression.get("value");
            if (value == null || value instanceof String || value instanceof Boolean
                    || value instanceof Number) return value;
            throw new Unsupported("literal value type is not supported");
        }
        if (!"call".equals(kind)) throw new Unsupported("unsupported expression kind: " + kind);
        String owner = requireString(expression, "owner");
        String name = requireString(expression, "name");
        Object rawArguments = expression.get("arguments");
        if (!(rawArguments instanceof List<?>))
            throw new Unsupported("call arguments must be an array");
        List<?> argumentExpressions = (List<?>) rawArguments;
        Object target = expression.get("target") == null ? null : evaluate(expression.get("target"));
        List<Object> arguments = new ArrayList<>();
        for (Object argument : argumentExpressions) arguments.add(evaluate(argument));
        return invoke(owner, name, target, arguments);
    }

    private static Object invoke(String owner, String name, Object target, List<Object> arguments)
            throws Exception {
        if (STRING_UTILS.equals(owner)) {
            requireNullTarget(target, owner, name);
            requireArity(arguments, 1, owner, name);
            if ("hasText".equals(name)) return StringUtils.hasText(asString(arguments.get(0), true));
            if ("isEmpty".equals(name)) return StringUtils.isEmpty(arguments.get(0));
            throw unsupportedCall(owner, name);
        }
        if (URI_BUILDER.equals(owner)) {
            if ("fromHttpUrl".equals(name) || "fromUriString".equals(name)) {
                requireNullTarget(target, owner, name);
                requireArity(arguments, 1, owner, name);
                String input = asString(arguments.get(0), false);
                return "fromHttpUrl".equals(name)
                    ? UriComponentsBuilder.fromHttpUrl(input)
                    : UriComponentsBuilder.fromUriString(input);
            }
            if ("path".equals(name)) {
                UriComponentsBuilder builder = asBuilder(target);
                requireArity(arguments, 1, owner, name);
                return builder.path(asString(arguments.get(0), false));
            }
            if ("toUriString".equals(name)) {
                UriComponentsBuilder builder = asBuilder(target);
                requireArity(arguments, 0, owner, name);
                return builder.toUriString();
            }
            throw unsupportedCall(owner, name);
        }
        if (ENDPOINT_ID.equals(owner)) {
            if (!"of".equals(name)) throw unsupportedCall(owner, name);
            requireNullTarget(target, owner, name);
            requireArity(arguments, 1, owner, name);
            return EndpointId.of(asString(arguments.get(0), false));
        }
        throw new Unsupported("owner is not allowed: " + owner);
    }

    private static Object externalValue(Object value) throws Unsupported {
        if (value == null || value instanceof String || value instanceof Boolean || value instanceof Number)
            return value;
        if (value instanceof EndpointId) return value.toString();
        throw new Unsupported("top-level result type is not serializable");
    }

    private static String requireString(Map<String, Object> value, String key) throws Unsupported {
        Object item = value.get(key);
        if (!(item instanceof String) || ((String) item).isEmpty())
            throw new Unsupported(key + " must be a non-empty string");
        return (String) item;
    }

    private static String asString(Object value, boolean nullable) throws Unsupported {
        if (value == null && nullable) return null;
        if (!(value instanceof String)) throw new Unsupported("expected string argument");
        return (String) value;
    }

    private static UriComponentsBuilder asBuilder(Object value) throws Unsupported {
        if (!(value instanceof UriComponentsBuilder))
            throw new Unsupported("expected UriComponentsBuilder target");
        return (UriComponentsBuilder) value;
    }

    private static void requireNullTarget(Object target, String owner, String name) throws Unsupported {
        if (target != null) throw new Unsupported(owner + "." + name + " must be a static call");
    }

    private static void requireArity(List<Object> values, int arity, String owner, String name)
            throws Unsupported {
        if (values.size() != arity)
            throw new Unsupported(owner + "." + name + " expects " + arity + " arguments");
    }

    private static Unsupported unsupportedCall(String owner, String name) {
        return new Unsupported("method is not allowed: " + owner + "." + name);
    }
}
