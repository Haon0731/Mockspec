package sample;

public final class ObservationEffectFixture {
  private static int globalReady;

  public static final class Box {
    public int ready;
    public int other;
  }

  public interface Dependency {
    void fill(Box box);
  }

  public static final class OpaqueCarrier implements Dependency {
    public native void fill(Box box);
  }

  private static void assertEquals(int expected, int actual) {
    if (expected != actual) throw new AssertionError(actual);
  }

  private static void visibleFill(Box box) {
    box.ready = 1;
  }

  public static void visible(Box box) {
    visibleFill(box);
    assertEquals(1, box.ready);
  }

  public static void opaque(Dependency dependency, Box box) {
    dependency.fill(box);
    assertEquals(1, box.ready);
  }

  public static void fresh(Dependency dependency, Box observed) {
    Box fresh = new Box();
    dependency.fill(fresh);
    assertEquals(1, observed.ready);
  }

  public static void overwritten(Dependency dependency, Box box) {
    dependency.fill(box);
    box.ready = 0;
    assertEquals(1, box.ready);
  }

  public static void selfCopy(Dependency dependency, Box box) {
    dependency.fill(box);
    box.ready = box.ready;
    assertEquals(1, box.ready);
  }

  public static void reintroduced(Dependency dependency, Box box) {
    dependency.fill(box);
    int prior = box.ready;
    box.ready = 0;
    box.ready = prior;
    assertEquals(1, box.ready);
  }

  public static void conditional(Dependency dependency, Box box, boolean replace) {
    dependency.fill(box);
    if (replace) box.ready = 0;
    assertEquals(1, box.ready);
  }

  public static void exceptional(Dependency dependency, Box box) {
    try {
      dependency.fill(box);
    } catch (RuntimeException expected) {
      assertEquals(1, box.ready);
    }
  }

  public static void observeAll(Dependency dependency, boolean replace, Box parameterBox) {
    Box visibleBox = new Box();
    visibleFill(visibleBox);
    assertEquals(1, visibleBox.ready);

    Box opaqueBox = new Box();
    dependency.fill(opaqueBox);
    assertEquals(1, opaqueBox.ready);

    Box observedFresh = new Box();
    Box unrelatedFresh = new Box();
    dependency.fill(unrelatedFresh);
    assertEquals(1, observedFresh.ready);

    Box killedBox = new Box();
    dependency.fill(killedBox);
    killedBox.ready = 0;
    assertEquals(1, killedBox.ready);

    Box copiedBox = new Box();
    dependency.fill(copiedBox);
    copiedBox.ready = copiedBox.ready;
    assertEquals(1, copiedBox.ready);

    Box reintroducedBox = new Box();
    dependency.fill(reintroducedBox);
    int prior = reintroducedBox.ready;
    reintroducedBox.ready = 0;
    reintroducedBox.ready = prior;
    assertEquals(1, reintroducedBox.ready);

    Box conditionalBox = new Box();
    dependency.fill(conditionalBox);
    if (replace) conditionalBox.ready = 0;
    assertEquals(1, conditionalBox.ready);

    Box exceptionalBox = new Box();
    try {
      dependency.fill(exceptionalBox);
    } catch (RuntimeException expected) {
      assertEquals(1, exceptionalBox.ready);
    }

    dependency.fill(parameterBox);
    assertEquals(1, parameterBox.ready);

    Box loopBox = null;
    for (int index = 0; index < 2; index++) loopBox = new Box();
    dependency.fill(loopBox);
    assertEquals(1, loopBox.ready);

    Box cyclicBox = new Box();
    dependency.fill(cyclicBox);
    int attempts = 0;
    do {
      cyclicBox.ready = 0;
      attempts++;
    } while (replace && attempts < 2);
    assertEquals(1, cyclicBox.ready);

    assertEquals(1, globalReady);
  }
}
