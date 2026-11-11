package sample;

public final class GenericProductEvidenceFixture {
  public static int product(int input) {
    int value;
    if (input < 0) {
      value = -input;
    } else {
      value = input + 1;
    }
    if (input == 7) {
      int ignored = input;
    }
    return value;
  }

  private static void assertEquals(int expected, int actual) {
    if (expected != actual) throw new AssertionError(actual);
  }

  public static void observe(int input) {
    assertEquals(2, product(input));
  }
}
