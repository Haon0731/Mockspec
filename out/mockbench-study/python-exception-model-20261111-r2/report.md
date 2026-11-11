# Python exception-observation model verification

## Result

The bounded joint model recovered the real `flask_308` exception influence from
the selected product function to all three original observations. The result is
`partial` / `verified_incomplete`: influence is supported, while exact exception
type, text, and `code`-to-message construction remain unknown.

The six accepted paths are product source lines 282, 283, and 285 to test
observations 120 and 126. Each path is also associated with the
`pytest.raises` observation at line 102. The post-`with` CFG continuation is
line 107.

## Model boundary

This is an explicit bounded joint model, not CodeQL's native general-purpose
context-manager exception analysis:

1. CodeQL proves a type-compatible exceptional exit from the product source and
   the test call, rejecting exceptions handled inside the callee or `with` body.
2. A reviewed `pytest.raises` summary maps a matching exception to the first
   post-`with` normal continuation.
3. CodeQL CFG reachability and local flow connect that continuation and the same
   capture's `.value` through builtin `str` to the membership assertion.

The host pytest 9.1.1 source version and two source digests were verified before
the run. They are a `host_verified_reference`; the frozen case's pytest version
is unknown and is not claimed to match the host.

Unsupported forms do not emit capture relations: tuple or dynamic expected
types, `match` and `check`, non-pytest or rebound `raises`, a shadowed `str`,
capture overwrite, cross-capture mixing, callee/internal catches, normal-only
calls, observations inside the `with`, and unreachable observations.

## Acceptance evidence

- `verdict.json`: issue-24 bounded effect gate passed.
- `probe-pytest-exception-flow.csv`: exactly three registered positive paths;
  all registered negative forms are absent.
- `flask-pytest-exception-flow.csv`: six real combined paths.
- `observation-study.json`: all three original criteria have backend-linked
  exception influence; `unknown_observation_mappings` is empty; exact values
  remain explicitly unknown.
- Flask timing coverage: complete, 56.91 seconds summed per-process stages.
- Mechanism timing coverage: complete, 81.8 seconds summed per-process stages.

## Reproduction

```bash
experiments/backward-slicing/python-backend-pilot/run_pilot.sh \
  /tmp/mockspec-issue24-tools/codeql-v2.27.4/codeql/codeql \
  /home/haon/projects/Stub-Tester/.omx/developer-realistic-registry-max \
  out/mockbench-study/python-exception-model-20261111-r2
```

Each database creation and query is limited to 180 seconds and 4 GiB RAM by
the runner. No target test, complete 165-case batch, hidden answer, or paid model
was executed.
