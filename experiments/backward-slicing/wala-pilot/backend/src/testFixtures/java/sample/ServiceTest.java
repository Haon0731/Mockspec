package sample;

public final class ServiceTest {
  private static void assertEquals(String expected, String actual) {
    if (!expected.equals(actual)) {
      throw new AssertionError(actual);
    }
  }

  public void target() {
    Dependency unknown = null;
    Service service = new Service(unknown);
    String actual = service.render("health");
    assertEquals("result:ok", actual);
  }
}
