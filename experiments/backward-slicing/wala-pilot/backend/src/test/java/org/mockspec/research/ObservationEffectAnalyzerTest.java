package org.mockspec.research;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.Set;
import org.junit.jupiter.api.Test;

final class ObservationEffectAnalyzerTest {
  @Test
  void sameBlockCallBeforeReadHasNormalContinuation() {
    assertTrue(ObservationEffectAnalyzer.sameBlockNormalOrder(4, 5));
    assertFalse(ObservationEffectAnalyzer.sameBlockNormalOrder(5, 4));
    assertFalse(ObservationEffectAnalyzer.sameBlockNormalOrder(5, 5));
  }

  @Test
  void sharedPointsToRegionIsPotentialNotDefiniteMutation() {
    assertEquals(
        "potential",
        ObservationEffectAnalyzer.classifyAlias(Set.of("alloc:box"), Set.of("alloc:box")));
  }

  @Test
  void freshObjectIsExcludedOnlyWhenBothPointsToSetsAreKnownAndDisjoint() {
    assertEquals(
        "excluded",
        ObservationEffectAnalyzer.classifyAlias(Set.of("alloc:fresh"), Set.of("alloc:box")));
    assertEquals(
        "unknown", ObservationEffectAnalyzer.classifyAlias(Set.of(), Set.of("alloc:box")));
    assertEquals("unknown", ObservationEffectAnalyzer.classifyAlias(Set.of("alloc:box"), Set.of()));
  }

  @Test
  void exceptionalMutationChannelRetainsPotentialUnknownAndDisjoint() {
    assertEquals(
        "potential",
        ObservationEffectAnalyzer.classifyExceptionalState(
            Set.of("alloc:box"), Set.of("alloc:box")));
    assertEquals(
        "unknown",
        ObservationEffectAnalyzer.classifyExceptionalState(Set.of(), Set.of("alloc:box")));
    assertEquals(
        "excluded",
        ObservationEffectAnalyzer.classifyExceptionalState(
            Set.of("alloc:fresh"), Set.of("alloc:box")));
  }

  @Test
  void traversalBudgetStopsDiscoveryAndMarksFrontier() {
    ObservationEffectAnalyzer.TraversalCost cost =
        new ObservationEffectAnalyzer.TraversalCost(2);
    assertTrue(cost.discover());
    assertTrue(cost.discover());
    assertFalse(cost.discover());
    assertTrue(cost.exhausted);
    assertEquals(3, cost.visited);
  }
}
