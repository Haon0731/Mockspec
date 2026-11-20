package sample;

public final class Entry {
  public static void observe(int firstInput, int secondInput) {
    int ignored = Cut.first(firstInput);
    int actual = Cut.second(secondInput);
    observeValue(actual);
  }

  public static void observeValue(int value) {}
}
