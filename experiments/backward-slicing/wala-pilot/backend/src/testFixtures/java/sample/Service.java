package sample;

public final class Service {
  private final Dependency dependency;

  public Service(Dependency dependency) {
    this.dependency = dependency;
  }

  public String render(String key) {
    String value = dependency.lookup(key);
    if (value == null) {
      throw new IllegalStateException(key);
    }
    return "result:" + value;
  }
}
