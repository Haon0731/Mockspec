package sample;

public final class Cut {
  public static int first(int value) {
    return Relay.relay(value);
  }

  public static int second(int value) {
    int ignored = Relay.relay(value + 100);
    int result = Relay.relay(value);
    if (result < 10) {
      return result + 1;
    }
    return result - 1;
  }
}
