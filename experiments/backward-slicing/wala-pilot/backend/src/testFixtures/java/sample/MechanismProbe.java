package sample;

public final class MechanismProbe {
  public static final class Box {
    public int value;
    public int ready;
    public Box child;
  }

  public static Box escaped;
  public static int globalReady;

  /** Native analysis input: no concrete Mock response or runtime implementation is supplied. */
  public static final class EffectSource {
    public static native int unknownInt();
  }

  public interface UnknownDependency {
    Box alias(Box box);
    void mutate(Box box);
  }

  /** Allocation carrier only; WALA replaces these native declarations with conservative summaries. */
  public static final class OpaqueCarrier implements UnknownDependency {
    public int receiverState;
    public native Box alias(Box box);
    public native void mutate(Box box);
  }

  private static int visibleTransform(int input) {
    int overwritten = input + 100;
    overwritten = input * 2;
    return overwritten;
  }

  private static void visibleWrite(Box alias, int value) {
    alias.value = value;
  }

  public static int visibleObservation(int input, Box box) {
    int transformed = visibleTransform(input);
    visibleWrite(box, transformed);
    try {
      if (input < 0) throw new IllegalArgumentException("negative");
    } catch (IllegalArgumentException expected) {
      box.value = -1;
    } finally {
      transformed = box.value;
    }
    return transformed;
  }

  public static int unknownObservation(UnknownDependency dependency, Box box) {
    dependency.mutate(box);
    return box.value;
  }

  public static int unknownReadyObservation(UnknownDependency dependency, Box box) {
    dependency.mutate(box);
    return box.ready;
  }

  public static int unknownAliasObservation(UnknownDependency dependency, Box box) {
    Box returned = dependency.alias(box);
    returned.value = 7;
    return box.value;
  }

  public static int unknownCompletionObservation(UnknownDependency dependency, Box box) {
    dependency.mutate(box);
    return 1;
  }

  public static int unknownExceptionObservation(UnknownDependency dependency, Box box) {
    try {
      dependency.mutate(box);
      return 0;
    } catch (RuntimeException expected) {
      return box.value;
    } finally {
      box.value = box.value;
    }
  }

  public static int unknownFreshObservation(UnknownDependency dependency, Box observed) {
    Box fresh = new Box();
    dependency.mutate(fresh);
    return observed.value;
  }

  public static int unknownReceiverObservation(OpaqueCarrier dependency, Box box) {
    dependency.mutate(box);
    return dependency.receiverState;
  }

  public static int unknownReachableObservation(UnknownDependency dependency, Box box) {
    Box child = new Box();
    box.child = child;
    dependency.mutate(box);
    return child.ready;
  }

  public static int unknownEscapedObservation(
      UnknownDependency dependency, Box direct, Box global) {
    escaped = global;
    dependency.mutate(direct);
    return global.ready;
  }

  public static int unknownStaticObservation(UnknownDependency dependency, Box box) {
    dependency.mutate(box);
    return globalReady;
  }
}
