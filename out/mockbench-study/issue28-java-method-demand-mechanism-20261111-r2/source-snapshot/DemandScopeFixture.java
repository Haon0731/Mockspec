package sample;

/** Static-analysis input only: the research runner never executes these methods. */
public final class DemandScopeFixture {
  public static int observe(int value, int depth) {
    int ignored = unrelated(value);
    int first = direct(value);
    return left(first, depth);
  }

  static int direct(int value) { return value + 7; }
  static int unrelated(int value) { return value * 101; }
  static int left(int value, int depth) {
    return depth <= 0 ? value : right(value + 1, depth - 1);
  }
  static int right(int value, int depth) {
    return depth <= 0 ? value : left(value + 2, depth - 1);
  }
}
