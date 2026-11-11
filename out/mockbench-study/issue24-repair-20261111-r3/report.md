# Issue #24 repair evidence (2026-11-11, r3)

Verdict: `passed` for the bounded pilot gate (`mock-spec.issue24-verdict/v2`). This does not mean
that the general Python heap problem or all 165 cases pass. It permits only the supported-domain
follow-up in Issues #26/#27.

## What is now verified

- The gate consumes backend-produced path rows, not candidate labels. Each accepted row binds the
  function, call line, region, field, intermediate node, observation line, and exit kind.
- An opaque call's direct argument region may affect a later field read.
- A call return may alias its argument; a same-field write through that return reaches the original
  argument's later read. A different-field write does not establish that alias-write relation.
- A same-field overwrite kills the earlier unknown state only in the supported unique-local-source
  region when the overwrite dominates the read. A conditional overwrite alone does not kill it;
  a later unconditional dominating overwrite does.
- Normal-completion and exception influence remain recorded after a state kill.
- A single combined relation connects the unknown write effect through the exceptional CFG to both
  the handler and finally reads. This is not assembled from separate may-write and may-raise labels.
- The frozen Flask case still resolves the masked test call to `helpers.py:265`, retains the normal
  fallback path, and records the unknown exceptional exit.

## Deliberate limits

This is an explicit bounded joint model over CodeQL CFG and value-flow, not CodeQL's general heap
analysis. It covers direct positional arguments, one returned alias, direct known fields, supported
must-kill, and same-try exception paths. The trusted fresh probe only proves that the selected direct
argument/returned-alias channel from pass-only `Box()` is disjoint. Receiver, global, escaped,
transitive heap, dynamic field, property, `__setattr__`, container, factory-return, and multi-source
effects remain unknown.

`probe-effects.csv` remains candidate evidence. Only `probe-unknown-effect-flow.csv` and
`probe-unknown-exception-state.csv` can satisfy the two repaired propagation checks.

## Reproduction and history

Command:

```text
experiments/backward-slicing/python-backend-pilot/run_pilot.sh \
  /tmp/mockspec-issue24-tools/codeql-v2.27.4/codeql/codeql \
  /home/haon/projects/Stub-Tester/.omx/developer-realistic-registry-max \
  out/mockbench-study/issue24-repair-20261111-r3
```

Every database creation and query retained the 180-second and 4-GiB limits. The longest new query
was 31.16 seconds with 1,449,936 KiB maximum RSS. Databases, BQRS, and materialized source stayed in
`/tmp`; this directory contains identities, hashes, CSV, logs, timings, and verdict only. No target
test, model call, or 165-case run occurred.

The original 2026-11-10 evidence is unchanged. The first repair run is intentionally preserved as
`issue24-repair-20261111` with `blocked` status because its exception CSV schema did not match the
verifier. `r2` records the schema repair. `r3` is the final narrowed, adversarially checked result.

Key hashes:

- `verdict.json`: `6cdee76d0742e398c82f9d4de673d91ee685e01b375e7a772b5b174a4a98d61a`
- `probe-unknown-effect-flow.csv`: `9ac42f243c1346ff4ec53911803997ff115fbe7ca97270645942f30ce24d6cb9`
- `probe-unknown-exception-state.csv`: `e0c040060c86f4664d1962033eea7527c6bcb51d71073144feacf9ea518c02b4`
