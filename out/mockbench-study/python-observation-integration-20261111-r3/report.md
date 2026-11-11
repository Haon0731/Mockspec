# Python observation integration — Flask single case

This run maps fixed CodeQL 2.27.4 evidence to the three explicit observations extracted from the
frozen `flask_308` masked test. The external case result is intentionally `partial` with semantic
status `verified_incomplete`; it does not claim that Issues #26 or #27 are fully complete.

## Per-observation result

- `criterion:0`, line 102: the `pytest.raises` observation is retained. Product exception to pytest
  capture-object semantics is not expanded, so the value mapping is `unknown`.
- `criterion:1`, line 120: CodeQL proves the line-111 true control region. The product exception to
  `exc_info.value` string flow remains `unknown`.
- `criterion:2`, line 126: CodeQL proves the line-111 false control region. The product exception to
  `exc_info.value` string flow remains `unknown`.

The mapper automatically binds the test call at line 103 to `helpers.py:265` and recovers positional
argument 0 to formal parameter 0. `*args` and `**kwargs` are not recovered and value-flow coverage is
therefore `verified_incomplete`. It does not equate test `has_app_context` with product `ctx`.

## Product-internal evidence

Within the bound `abort` function (lines 265–285), the normal completion of the call at line 283
reaches the fallback at line 285. Both callsites have an exceptional exit. These relations have empty
`observation_ids`: no unsupported pytest capture link is invented.

The state mechanism probe retains positive may-write/return-alias evidence, negative trusted-fresh and
other-field evidence, dominance kills, conditional non-kills, and combined exceptional handler/finally
paths. It is reported separately and is not promoted to a real Flask state observation. Flask has no
visible field observation in this case, so `state_flow` is `not_evaluated`, not “no effect.”

Unknown regions explicitly include receiver, direct arguments, return alias, global/escaped state,
transitive heap, dynamic fields, pytest capture semantics, hidden Mock behavior, and path feasibility.

## Reproduction and cost

```text
experiments/backward-slicing/python-backend-pilot/run_pilot.sh \
  /tmp/mockspec-issue24-tools/codeql-v2.27.4/codeql/codeql \
  /home/haon/projects/Stub-Tester/.omx/developer-realistic-registry-max \
  out/mockbench-study/python-observation-integration-20261111-r3
```

Every database and query retained the 180-second, 4-GiB, two-thread budget. Flask case cost and
mechanism cost are separate in `observation-study.json`; both expected timing sets are complete.
Peak memory is the maximum per-process max RSS, not a simultaneous runner peak. Adapter admission,
criterion extraction, mapping, serialization, and path feasibility are explicitly unavailable phases.

The failed initial and r2 directories are preserved. No target test, hidden answer, full-165 run, paid
model, or dataset mutation occurred.
