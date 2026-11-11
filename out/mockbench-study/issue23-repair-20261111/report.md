# Issue 23 WALA gate repair (v2)

Status: **blocked**. The repaired gate does not upgrade call-target reachability into relation success.

The overall gate is the conjunction of three independent checks: mechanism relations, positive real-input analysis completion, and acceptance of the unknown-effect model. In this run all three are false. Even a future all-green mechanism probe remains blocked while Jib is not run or the model remains bounded.

## What changed

- WALA 1.7.2 `MethodSummary` objects are installed with `BypassMethodTargetSelector` before call-graph, pointer, and SDG construction.
- The mechanism input supplies a behavior-free native allocation carrier so WALA can create an instance key for an otherwise implementation-free interface parameter.
- Opaque mechanism calls changed from zero possible targets to one bounded summary target.
- The validator now checks state, return alias, normal completion, write-before-throw, and fresh/no-alias separately. It requires typed, endpoint-consistent paths and explicit disjoint-region evidence for a negative conclusion.
- The model is explicitly a bounded mechanism probe, not a complete conservative unknown-call model and not Mock behavior.

## Result

All five strict relations remain blocked:

| Relation | Summary target | Strict witness |
| --- | ---: | --- |
| argument state may-write | 1 | absent |
| return alias | 1 | absent |
| normal completion | 1 | absent |
| write-before-throw to handler observation | 1 | absent |
| fresh/no-alias negative | 1 | absent (no explicit disjoint-region proof) |

The alias probe is especially informative: WALA reports a summarized callee return, but writing the returned object does not produce a heap-dependence witness to reading the original argument. The fresh probe also cannot be declared unrelated; heap-parameter propagation remains conservative and no region-disjointness receipt exists.

## Cost and scale

Opaque probes each used 9 call-graph nodes, 4 application nodes, 1 context, and 5 pointer instance keys. Their end-to-end times were 3.5–5.0 seconds. The visible mechanism retained 12,058 call-graph nodes, 55 contexts, 4,532 pointer instance keys, and 100,261 lazily materialized SDG nodes.

Real Jib was not rerun. The previous bounded run already failed at `PDG.populate` / `PDG.addParamPassingStatements` under WALA 1.7.2, 0-1-CFA, FULL/FULL, 2 GiB, 180 seconds. This repair did not add a scale mechanism that could change that outcome. New stage markers record the last completed phase without forcing every lazy PDG merely to count it. A run counts only with positive completion evidence; absence of OOM is not success.

## Reproduction

```bash
mvn -q -f experiments/backward-slicing/wala-pilot/backend/pom.xml \
  dependency:build-classpath -Dmdep.outputFile=/tmp/wala-cp.txt
python3 experiments/backward-slicing/wala-pilot/run_capability.py \
  --wala-classpath-file /tmp/wala-cp.txt \
  --output /tmp/issue23-mechanisms
python3 experiments/backward-slicing/wala-pilot/summarize_repair.py \
  --mechanisms /tmp/issue23-mechanisms \
  --historical out/mockbench-study/issue23-wala-evidence-20261110 \
  --output /tmp/issue23-report.json
pytest -q experiments/backward-slicing/wala-pilot/test_capability.py
```

Machine-readable details are in `report.json`; raw final mechanism logs are in `../issue23-repair-20261111-mechanisms-r5/`. Earlier negative evidence remains unchanged.
