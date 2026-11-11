package sample;

import java.io.StreamTokenizer;

public final class RuntimeRegionFixture {
  public static final class Box {
    public int x;
    public int y;
  }

  public double observe(
      StreamTokenizer first, StreamTokenizer second, int[] array, Box box) {
    first.nval = 1.0;
    second.ttype = 2;
    array[0] = 3;
    box.x = 4;
    box.y = 5;
    if (array.length < 0) {
      return new RuntimeRegionFixture().observe(first, second, array, box);
    }
    return first.nval + array[0] + box.x;
  }
}
