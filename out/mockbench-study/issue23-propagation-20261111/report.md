# Issue 23 WALA summary propagation follow-up

Status: **blocked**. Two bounded propagation mechanisms now have strict backend witnesses, but this does not complete the unknown-effect or real-Jib gates.

## Correction to the previous interpretation

The r5 call target was reachable, but it was a native `ShrikeCTMethod` with empty IR and empty ModRef. Therefore the earlier statement that `MethodSummary` had already entered CG/PA/SDG was too strong. Builder creation replaced the selector installed before it, so WALA continued to analyze the native carrier.

The correction installs the exact summary selector after the builder establishes its default selector. The canonical regression then reports `SummarizedMethod`, non-empty summary IR, field ModRef, heap-return statements, and pointer regions.

## Verified improvements

- State-effect plumbing: summary `putfield` → `HEAP_RET_CALLEE` → the same call site's `HEAP_RET_CALLER` → observed `getfield`, all for the same heap location.
- Return alias: the summary returns its explicit argument; pointer analysis gives the argument and caller return value the same region; the caller's write through the returned value has a `HEAP_DATA_DEP` to reading the original argument.

These are genuine backend relations, not node-presence checks. The validator rejects native empty IR, wrong heap locations, disjoint return regions, missing heap edges, and duplicate matching call sites.

## Still blocked

- The bounded state summary writes a field value back to itself. This proves WALA effect propagation, not arbitrary-value havoc or a complete may-write model.
- Normal completion and write-before-throw were not evaluated by the final two-probe regression.
- Fresh-object exclusion remains blocked: the current points-to abstraction represents the entry `Box` and fresh `Box` with the same type-based region, so disjointness is not proved.
- Receiver, global, escaped, null, fresh-return, and alternative-argument effects remain outside the bounded summary.
- Real Jib was not rerun because no PDG-scale improvement was introduced; the known FULL/FULL 2 GiB failure remains at PDG population/parameter passing.

## Configuration and attempt policy

Two configuration families were used across ten recorded attempts. Family A retained the original analysis and added evidence export. Family B repaired selector timing and exact summary selection; its diagnostic revisions changed the analysis path and are all retained in the machine-readable ledger. The final result is not selected from only successful reruns. Reported probe seconds exclude compilation, packaging, and input adaptation.

Canonical current-code evidence is in `../issue23-propagation-20261111-final-regression-r3/`. Full witnesses, hashes, costs, failed attempts, and configuration purposes are in `report.json`.

## Reproduction

```bash
mvn -q -f experiments/backward-slicing/wala-pilot/backend/pom.xml \
  dependency:build-classpath -Dmdep.outputFile=/tmp/wala-cp.txt
python3 experiments/backward-slicing/wala-pilot/run_capability.py \
  --wala-classpath-file /tmp/wala-cp.txt \
  --output /tmp/issue23-state-alias \
  --probe unknown_write --probe unknown_alias
python3 experiments/backward-slicing/wala-pilot/summarize_repair.py \
  --propagation-study-root out/mockbench-study \
  --output /tmp/issue23-propagation-report.json
pytest -q experiments/backward-slicing/wala-pilot/test_capability.py
```
