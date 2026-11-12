package org.mockspec.research;

import com.sun.source.tree.CompilationUnitTree;
import com.sun.source.tree.ExpressionTree;
import com.sun.source.tree.IdentifierTree;
import com.sun.source.tree.ImportTree;
import com.sun.source.tree.MemberSelectTree;
import com.sun.source.tree.MethodInvocationTree;
import com.sun.source.tree.MethodTree;
import com.sun.source.tree.NewClassTree;
import com.sun.source.tree.Tree;
import com.sun.source.util.JavacTask;
import com.sun.source.util.SourcePositions;
import com.sun.source.util.TreePath;
import com.sun.source.util.TreePathScanner;
import com.sun.source.util.Trees;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;
import javax.lang.model.element.Element;
import javax.lang.model.element.ElementKind;
import javax.lang.model.element.ExecutableElement;
import javax.lang.model.element.Modifier;
import javax.lang.model.element.TypeElement;
import javax.lang.model.type.ArrayType;
import javax.lang.model.type.DeclaredType;
import javax.lang.model.type.ExecutableType;
import javax.lang.model.type.TypeKind;
import javax.lang.model.type.TypeMirror;
import javax.lang.model.util.Elements;
import javax.lang.model.util.Types;
import javax.tools.JavaCompiler;
import javax.tools.JavaFileObject;
import javax.tools.StandardJavaFileManager;
import javax.tools.ToolProvider;

/** Extracts evidence-backed structural parameter types from admitted Java sources. */
public final class JavaTypeConstraints {
  private JavaTypeConstraints() {}

  public static void main(String[] args) throws Exception {
    if (args.length < 6) {
      throw new IllegalArgumentException(
          "usage: <source-root> <classpath-or-dash> <test-path> <method> <start-line> <end-line> [missing ...]");
    }
    Path root = Path.of(args[0]).toAbsolutePath().normalize();
    String classpath = args[1].equals("-") ? "" : args[1];
    Path test = root.resolve(args[2]).normalize();
    if (!test.startsWith(root)) throw new IllegalArgumentException("test path escapes source root");
    String method = args[3];
    int startLine = Integer.parseInt(args[4]);
    int endLine = Integer.parseInt(args[5]);
    Set<String> missing = new LinkedHashSet<>();
    for (int index = 6; index < args.length; index++) missing.add(args[index]);
    System.out.println(analyse(root, classpath, test, method, startLine, endLine, missing));
  }

  private static String analyse(
      Path root, String classpath, Path test, String method, int startLine, int endLine,
      Set<String> missing) throws IOException {
    JavaCompiler compiler = ToolProvider.getSystemJavaCompiler();
    if (compiler == null) throw new IOException("system Java compiler unavailable");
    List<Path> sources;
    try (var stream = Files.walk(root)) {
      sources = stream.filter(path -> Files.isRegularFile(path) && path.toString().endsWith(".java"))
          .sorted().collect(Collectors.toList());
    }
    try (StandardJavaFileManager files = compiler.getStandardFileManager(null, null, null)) {
      Iterable<? extends JavaFileObject> units = files.getJavaFileObjectsFromPaths(sources);
      List<String> options = new ArrayList<>(List.of("-proc:none", "-parameters", "-sourcepath", root.toString()));
      if (!classpath.isEmpty()) options.addAll(List.of("-classpath", classpath));
      JavacTask task = (JavacTask) compiler.getTask(null, files, null, options, null, units);
      List<CompilationUnitTree> parsed = new ArrayList<>();
      task.parse().forEach(parsed::add);
      try {
        task.analyze();
      } catch (RuntimeException ignored) {
        // Missing masked declarations intentionally leave attribution errors. Resolved receivers,
        // argument literals and admitted source/dependency signatures remain queryable.
      }
      Trees trees = Trees.instance(task);
      ConstraintScanner scanner = new ConstraintScanner(
          trees, task.getElements(), task.getTypes(), root, test, method, startLine, endLine, missing);
      for (CompilationUnitTree unit : parsed) scanner.scan(unit, null);
      return scanner.resultJson();
    }
  }

  private static final class ConstraintScanner extends TreePathScanner<Void, Void> {
    private final Trees trees;
    private final Elements elements;
    private final Types types;
    private final SourcePositions positions;
    private final Path root;
    private final Path test;
    private final String method;
    private final int startLine;
    private final int endLine;
    private final Set<String> missing;
    private final Map<String, List<UseEvidence>> evidence = new LinkedHashMap<>();
    private CompilationUnitTree currentUnit;
    private boolean selectedMethod;
    private Set<TypeElement> importedTypes = Set.of();

    ConstraintScanner(
        Trees trees, Elements elements, Types types, Path root, Path test, String method,
        int startLine, int endLine, Set<String> missing) {
      this.trees = trees;
      this.elements = elements;
      this.types = types;
      this.positions = trees.getSourcePositions();
      this.root = root;
      this.test = test;
      this.method = method;
      this.startLine = startLine;
      this.endLine = endLine;
      this.missing = missing;
      for (String name : missing) evidence.put(name, new ArrayList<>());
    }

    @Override public Void visitCompilationUnit(CompilationUnitTree node, Void unused) {
      CompilationUnitTree previous = currentUnit;
      Set<TypeElement> previousImports = importedTypes;
      currentUnit = node;
      importedTypes = explicitImports(node);
      super.visitCompilationUnit(node, unused);
      currentUnit = previous;
      importedTypes = previousImports;
      return null;
    }

    @Override public Void visitMethod(MethodTree node, Void unused) {
      boolean previous = selectedMethod;
      long begin = positions.getStartPosition(currentUnit, node);
      long finish = positions.getEndPosition(currentUnit, node);
      long beginLine = begin < 0 ? -1 : currentUnit.getLineMap().getLineNumber(begin);
      long finishLine = finish < 0 ? -1 : currentUnit.getLineMap().getLineNumber(finish);
      selectedMethod = path().equals(test) && node.getName().contentEquals(method)
          && beginLine <= endLine && finishLine >= startLine;
      super.visitMethod(node, unused);
      selectedMethod = previous;
      return null;
    }

    @Override public Void visitIdentifier(IdentifierTree node, Void unused) {
      if (selectedMethod && missing.contains(node.getName().toString())) constrainIdentifier(node);
      return super.visitIdentifier(node, unused);
    }

    private void constrainIdentifier(IdentifierTree identifier) {
      TreePath identifierPath = getCurrentPath();
      TreePath parent = identifierPath.getParentPath();
      if (parent == null) return;
      if (parent.getLeaf() instanceof MethodInvocationTree) {
        MethodInvocationTree invocation = (MethodInvocationTree) parent.getLeaf();
        int argumentIndex = invocation.getArguments().indexOf(identifier);
        if (argumentIndex >= 0) constrainInvocation(identifier, invocation, parent, argumentIndex);
      } else if (parent.getLeaf() instanceof NewClassTree) {
        NewClassTree constructor = (NewClassTree) parent.getLeaf();
        int argumentIndex = constructor.getArguments().indexOf(identifier);
        if (argumentIndex >= 0) constrainConstructor(identifier, constructor, parent, argumentIndex);
      } else if (parent.getLeaf() instanceof MemberSelectTree
          && ((MemberSelectTree) parent.getLeaf()).getExpression() == identifier) {
        constrainMissingReceiver(identifier, (MemberSelectTree) parent.getLeaf(), parent);
      }
    }

    private void constrainInvocation(
        IdentifierTree identifier, MethodInvocationTree invocation, TreePath invocationPath,
        int argumentIndex) {
      String name;
      ExpressionTree receiver = null;
      if (invocation.getMethodSelect() instanceof MemberSelectTree) {
        MemberSelectTree select = (MemberSelectTree) invocation.getMethodSelect();
        name = select.getIdentifier().toString();
        receiver = select.getExpression();
      } else if (invocation.getMethodSelect() instanceof IdentifierTree) {
        IdentifierTree select = (IdentifierTree) invocation.getMethodSelect();
        name = select.getName().toString();
      } else return;
      List<MethodCandidate> candidates = methodCandidates(invocationPath, receiver, name);
      addInvocationEvidence(identifier, invocation, invocationPath, argumentIndex, candidates, "call_argument");
    }

    private void constrainConstructor(
        IdentifierTree identifier, NewClassTree constructor, TreePath constructorPath,
        int argumentIndex) {
      TypeMirror constructed = trees.getTypeMirror(new TreePath(constructorPath, constructor.getIdentifier()));
      TypeElement owner = asTypeElement(constructed);
      if (owner == null) return;
      List<MethodCandidate> candidates = new ArrayList<>();
      for (Element element : owner.getEnclosedElements()) {
        if (element.getKind() != ElementKind.CONSTRUCTOR) continue;
        ExecutableElement executable = (ExecutableElement) element;
        candidates.add(new MethodCandidate(owner, executable, (ExecutableType) executable.asType()));
      }
      addInvocationEvidence(identifier, constructor, constructorPath, argumentIndex, candidates, "constructor_argument");
    }

    private void constrainMissingReceiver(
        IdentifierTree identifier, MemberSelectTree select, TreePath selectPath) {
      TreePath parent = selectPath.getParentPath();
      List<? extends ExpressionTree> arguments = List.of();
      String kind = "field_receiver";
      if (parent != null && parent.getLeaf() instanceof MethodInvocationTree
          && ((MethodInvocationTree) parent.getLeaf()).getMethodSelect() == select) {
        MethodInvocationTree invocation = (MethodInvocationTree) parent.getLeaf();
        arguments = invocation.getArguments();
        kind = "method_receiver";
      }
      final List<? extends ExpressionTree> callArguments = arguments;
      List<TypeCandidate> candidates = new ArrayList<>();
      for (TypeElement imported : importedTypes) {
        List<MethodCandidate> members = methods(imported, select.getIdentifier().toString());
        List<? extends Element> fields = elements.getAllMembers(imported).stream()
            .filter(member -> member.getKind().isField()
                && member.getSimpleName().contentEquals(select.getIdentifier()))
            .collect(Collectors.toList());
        boolean matches = kind.equals("field_receiver")
            ? !fields.isEmpty()
            : members.stream().anyMatch(candidate -> compatible(callArguments, selectPath, candidate.type));
        if (matches) {
          candidates.add(new TypeCandidate(
              imported.getQualifiedName().toString(), imported.asType(), imported,
              kind.equals("field_receiver") ? fields.stream()
                  .map(field -> field.getEnclosingElement() + "." + field.getSimpleName() + ":" + field.asType())
                  .sorted().collect(Collectors.joining(" | "))
                  : members.stream().filter(candidate -> compatible(callArguments, selectPath, candidate.type))
                      .map(MethodCandidate::signature).sorted().collect(Collectors.joining(" | ")),
              kind.equals("field_receiver") ? fields.stream()
                  .map(field -> field.getEnclosingElement().toString()).distinct().sorted().collect(Collectors.toList())
                  : members.stream().filter(candidate -> compatible(callArguments, selectPath, candidate.type))
                      .map(candidate -> candidate.element.getEnclosingElement().toString())
                      .distinct().sorted().collect(Collectors.toList())));
        }
      }
      addUse(identifier, kind, -1, candidates);
    }

    private void addInvocationEvidence(
        IdentifierTree identifier, Tree invocation, TreePath invocationPath, int argumentIndex,
        List<MethodCandidate> candidates, String kind) {
      List<? extends ExpressionTree> arguments = invocation instanceof MethodInvocationTree
          ? ((MethodInvocationTree) invocation).getArguments() : ((NewClassTree) invocation).getArguments();
      List<TypeCandidate> compatible = candidates.stream()
          .filter(candidate -> compatible(arguments, invocationPath, candidate.type))
          .filter(candidate -> argumentIndex < candidate.type.getParameterTypes().size())
          .map(candidate -> new TypeCandidate(
              render(candidate.type.getParameterTypes().get(argumentIndex)),
              candidate.type.getParameterTypes().get(argumentIndex), candidate.owner,
              candidate.signature(), List.of(candidate.element.getEnclosingElement().toString())))
          .filter(candidate -> candidate.type != null)
          .collect(Collectors.toList());
      addUse(identifier, kind, argumentIndex, compatible);
    }

    private List<MethodCandidate> methodCandidates(
        TreePath invocationPath, ExpressionTree receiver, String name) {
      if (receiver == null) {
        Element enclosing = trees.getElement(invocationPath);
        TypeElement owner = enclosingType(enclosing);
        return owner == null ? List.of() : methods(owner, name);
      }
      TreePath receiverPath = new TreePath(invocationPath, receiver);
      TypeElement owner = asTypeElement(trees.getTypeMirror(receiverPath));
      if (owner == null) owner = asTypeElement(trees.getElement(receiverPath));
      return owner == null ? List.of() : methods(owner, name);
    }

    private List<MethodCandidate> methods(TypeElement owner, String name) {
      List<MethodCandidate> result = new ArrayList<>();
      DeclaredType ownerType = (DeclaredType) owner.asType();
      for (Element member : elements.getAllMembers(owner)) {
        if (member.getKind() != ElementKind.METHOD || !member.getSimpleName().contentEquals(name)) continue;
        ExecutableElement method = (ExecutableElement) member;
        ExecutableType type;
        try {
          type = (ExecutableType) types.asMemberOf(ownerType, method);
        } catch (IllegalArgumentException ignored) {
          type = (ExecutableType) method.asType();
        }
        result.add(new MethodCandidate(owner, method, type));
      }
      return result;
    }

    private boolean compatible(
        List<? extends ExpressionTree> arguments, TreePath parentPath, ExecutableType executable) {
      if (arguments.size() != executable.getParameterTypes().size()) return false;
      for (int index = 0; index < arguments.size(); index++) {
        ExpressionTree argument = arguments.get(index);
        if (argument instanceof IdentifierTree
            && missing.contains(((IdentifierTree) argument).getName().toString())) {
          continue;
        }
        TypeMirror actual = trees.getTypeMirror(new TreePath(parentPath, argument));
        TypeMirror formal = executable.getParameterTypes().get(index);
        if (!isUsable(actual) || !isUsable(formal)) continue;
        if (!assignable(actual, formal)) return false;
      }
      return true;
    }

    private boolean assignable(TypeMirror actual, TypeMirror formal) {
      if (types.isAssignable(actual, formal)) return true;
      try {
        if (actual.getKind().isPrimitive()) return types.isAssignable(types.boxedClass((javax.lang.model.type.PrimitiveType) actual).asType(), formal);
        if (formal.getKind().isPrimitive()) return types.isAssignable(actual, types.boxedClass((javax.lang.model.type.PrimitiveType) formal).asType());
      } catch (IllegalArgumentException ignored) {
        return false;
      }
      return false;
    }

    private void addUse(
        IdentifierTree identifier, String kind, int argumentIndex, List<TypeCandidate> candidates) {
      Map<String, TypeCandidate> byType = new LinkedHashMap<>();
      for (TypeCandidate candidate : candidates) {
        if (candidate.type != null) byType.putIfAbsent(candidate.type + "\u0000" + candidate.signature, candidate);
      }
      long position = positions.getStartPosition(currentUnit, identifier);
      int line = position < 0 ? -1 : (int) currentUnit.getLineMap().getLineNumber(position);
      String logicalPath = path().startsWith(root) ? root.relativize(path()).toString().replace('\\', '/') : path().toString();
      evidence.get(identifier.getName().toString()).add(new UseEvidence(
          logicalPath, line, kind, argumentIndex, new ArrayList<>(byType.values())));
    }

    private Set<TypeElement> explicitImports(CompilationUnitTree unit) {
      Set<TypeElement> result = new LinkedHashSet<>();
      for (ImportTree imported : unit.getImports()) {
        if (imported.isStatic() || imported.getQualifiedIdentifier().toString().endsWith(".*")) continue;
        TypeElement type = elements.getTypeElement(imported.getQualifiedIdentifier().toString());
        if (type != null) result.add(type);
      }
      return result;
    }

    private TypeElement asTypeElement(TypeMirror mirror) {
      return mirror != null && mirror.getKind() == TypeKind.DECLARED
          ? (TypeElement) ((DeclaredType) mirror).asElement() : null;
    }

    private TypeElement asTypeElement(Element element) {
      if (element instanceof TypeElement) return (TypeElement) element;
      return element == null ? null : enclosingType(element);
    }

    private TypeElement enclosingType(Element element) {
      while (element != null && !(element instanceof TypeElement)) element = element.getEnclosingElement();
      return (TypeElement) element;
    }

    private boolean isUsable(TypeMirror type) {
      return type != null && type.getKind() != TypeKind.ERROR && type.getKind() != TypeKind.NONE
          && type.getKind() != TypeKind.OTHER;
    }

    private String render(TypeMirror type) {
      if (!isUsable(type) || containsTypeVariable(type)) return null;
      return type.toString();
    }

    private boolean containsTypeVariable(TypeMirror type) {
      if (type.getKind() == TypeKind.TYPEVAR || type.getKind() == TypeKind.WILDCARD) return true;
      if (type.getKind() == TypeKind.ARRAY) return containsTypeVariable(((ArrayType) type).getComponentType());
      if (type.getKind() == TypeKind.DECLARED) {
        return ((DeclaredType) type).getTypeArguments().stream().anyMatch(this::containsTypeVariable);
      }
      return false;
    }

    private Path path() {
      return Path.of(currentUnit.getSourceFile().toUri()).toAbsolutePath().normalize();
    }

    String resultJson() {
      List<String> parameters = new ArrayList<>();
      List<String> unresolved = new ArrayList<>();
      for (String name : missing) {
        List<UseEvidence> uses = evidence.get(name);
        Map<String, TypeMirror> candidateTypes = new LinkedHashMap<>();
        for (UseEvidence use : uses) {
          for (TypeCandidate candidate : use.candidates) {
            candidateTypes.putIfAbsent(candidate.type, candidate.mirror);
          }
        }
        Set<String> viable = candidateTypes.entrySet().stream()
            .filter(candidate -> uses.stream()
                .filter(use -> !use.candidates.isEmpty())
                .allMatch(use -> use.candidates.stream()
                    .anyMatch(formal -> assignable(candidate.getValue(), formal.mirror))))
            .map(Map.Entry::getKey)
            .collect(Collectors.toCollection(LinkedHashSet::new));
        if (candidateTypes.isEmpty() || viable.size() != 1) {
          unresolved.add(object(Map.of("name", name, "reason",
              candidateTypes.isEmpty() ? "no_structural_type_evidence" : "ambiguous_or_conflicting_type_domain",
              "candidate_types", viable,
              "evidence", uses)));
          continue;
        }
        String type = viable.iterator().next();
        TypeMirror selected = candidateTypes.get(type);
        List<UseEvidence> supporting = uses.stream()
            .filter(use -> use.candidates.stream()
                .anyMatch(formal -> assignable(selected, formal.mirror)))
            .collect(Collectors.toList());
        parameters.add(object(Map.of("name", name, "type", type, "evidence", supporting)));
      }
      return "{\"schema\":\"mock-spec.java-type-constraints/v1\",\"parameters\":["
          + String.join(",", parameters) + "],\"unresolved\":[" + String.join(",", unresolved) + "]}";
    }
  }

  private static final class MethodCandidate {
    final TypeElement owner;
    final ExecutableElement element;
    final ExecutableType type;

    MethodCandidate(TypeElement owner, ExecutableElement element, ExecutableType type) {
      this.owner = owner;
      this.element = element;
      this.type = type;
    }

    String signature() {
      return element.getEnclosingElement() + "." + element.getSimpleName() + type;
    }
  }

  private static final class TypeCandidate {
    final String type;
    final TypeMirror mirror;
    final TypeElement owner;
    final String signature;
    final List<String> declaringOwners;

    TypeCandidate(
        String type, TypeMirror mirror, TypeElement owner, String signature,
        List<String> declaringOwners) {
      this.type = type;
      this.mirror = mirror;
      this.owner = owner;
      this.signature = signature;
      this.declaringOwners = declaringOwners;
    }
  }

  private static final class UseEvidence {
    final String sourcePath;
    final int line;
    final String kind;
    final int argumentIndex;
    final List<TypeCandidate> candidates;

    UseEvidence(
        String sourcePath, int line, String kind, int argumentIndex,
        List<TypeCandidate> candidates) {
      this.sourcePath = sourcePath;
      this.line = line;
      this.kind = kind;
      this.argumentIndex = argumentIndex;
      this.candidates = candidates;
    }
  }

  private static String object(Map<String, ?> values) {
    return values.entrySet().stream().sorted(Map.Entry.comparingByKey())
        .map(entry -> quote(entry.getKey()) + ":" + json(entry.getValue()))
        .collect(Collectors.joining(",", "{", "}"));
  }

  private static String json(Object value) {
    if (value == null) return "null";
    if (value instanceof String) return quote((String) value);
    if (value instanceof Number || value instanceof Boolean) return value.toString();
    if (value instanceof TypeCandidate) {
      TypeCandidate candidate = (TypeCandidate) value;
      return object(Map.of("type", candidate.type, "owner", candidate.owner.getQualifiedName().toString(),
          "receiver_owner", candidate.owner.getQualifiedName().toString(),
          "declaring_owners", candidate.declaringOwners, "signature", candidate.signature));
    }
    if (value instanceof UseEvidence) {
      UseEvidence use = (UseEvidence) value;
      return object(Map.of("source_path", use.sourcePath, "line", use.line, "kind", use.kind,
          "argument_index", use.argumentIndex, "candidates", use.candidates));
    }
    if (value instanceof Iterable<?>) {
      Iterable<?> iterable = (Iterable<?>) value;
      List<String> items = new ArrayList<>();
      iterable.forEach(item -> items.add(json(item)));
      return "[" + String.join(",", items) + "]";
    }
    throw new IllegalArgumentException("unsupported JSON value: " + value.getClass());
  }

  private static String quote(String value) {
    StringBuilder result = new StringBuilder("\"");
    for (char character : value.toCharArray()) {
      switch (character) {
        case '\\': result.append("\\\\"); break;
        case '"': result.append("\\\""); break;
        case '\n': result.append("\\n"); break;
        case '\r': result.append("\\r"); break;
        case '\t': result.append("\\t"); break;
        default: result.append(character < 0x20 ? String.format("\\u%04x", (int) character) : character);
      }
    }
    return result.append('"').toString();
  }
}
