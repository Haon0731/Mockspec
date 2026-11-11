# Issue 28 Java method-context demand acceptance

Status: accepted with an explicit negative scale finding. The implementation constructs WALA method-context PDGs on demand without changing the accepted r6 value, control, state, exception, normal-return, or unknown-boundary evidence. It does not make the real Jib case faster.

## Evidence

- The isolated mechanism probe passed all 10 checks. It began with zero PDGs, retained exact eager/lazy slice equality, loaded 4 of 5 reachable method contexts, excluded the reachable unrelated method, terminated a real mutual-recursion cycle, and preserved completed evidence plus an explicit frontier at a method budget of 2.
- The final real-Jib cold run executed all 19 observation units. All 19 raw canonical witnesses equal r6, and the strict full semantic summary equals r6: 173 relation objects, 126 control relations, 3 optional relations, 14 exact observations, and 5 opaque-may observations.
- PDG telemetry was 0 before construction, 0 after the eager global Mod/Ref index, and 11,184 after all demands, against 11,256 entry-reachable method contexts. This is genuine demand construction but only a 72-context reduction on this conservative heap graph.
- The identity-bound warm run produced 19 cache hits, executed no Java analysis, and retained full semantic equivalence.
- With a two-observation plan and a 25-PDG limit, criterion 0 remained complete and equivalent; criterion 1 stopped at `ContainerBuildPlan.getPlatforms()@Everywhere`; the output retained one method-budget frontier and 17 unit-budget frontiers.

## Cost

The real-Jib cold backend run took 48.618 seconds total (39.653 Java, 1.161 compilation, 7.804 mapping) with Java peak RSS 1,620,631,552 bytes. The accepted r6 baseline reported 38.187 seconds and 1,508,073,472 bytes. The warm materialization took 9.734 seconds with no Java run. These are descriptive costs with non-isolated OS/Maven caches; no speedup is claimed. The prepared application JAR was reused, so its prior adapter/compilation cost is excluded.

## Semantic comparison contract

The comparator includes all observation identity/status/missing fields, witnesses, relation objects and modalities, relation coverage, entry binding, outcome, optional evidence, and unknown/unanalysed boundaries. It excludes only run/cost telemetry, ordering, absolute javap paths while retaining content hashes, and truncated presentation samples while retaining counts. Tests accept metadata/order-only changes and reject both relation removal and exact-to-opaque changes.

## Scope boundary

Call graph construction, pointer analysis, and global Mod/Ref scanning remain eager. Only method-context PDGs are demand constructed, under the existing NONE-control/NO_HEAP-base configuration with the existing CI Mod/Ref may-dependencies. No target test, dataset mutation, or full-165 experiment was performed.

Machine-readable hashes and measurements are in `acceptance.json`. The rejected prefix/one-hop pruning experiments remain historical diagnostics and are not part of the accepted implementation because they lost relations or changed exact evidence to opaque-may evidence.
