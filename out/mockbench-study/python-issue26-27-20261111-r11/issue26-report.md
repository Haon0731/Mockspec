# Issue #26 closure report — Flask unified observation result

## Result

`issue26-verdict.json` is `passed`; all 12 pre-registered checks are true. The real frozen
`flask_308` case is mapped to one `mock-spec.backward-analysis-case/v1` result with three
observations and 13 relations. This is a bounded may-influence result, not an exact Mock-value
inference result.

## Accepted evidence

- Test call `tests/test_308abort.py:103` is bound to `src/src/flask/helpers.py:265`, including
  actual argument 0 to formal argument 0.
- The branch at test line 111 controls the assertions at lines 120 and 126 on their respective
  branches.
- Product line 283 has a normal-continuation relation to line 285.
- Product lines 282, 283, and 285 each have exception-dependence paths to both value assertions;
  those paths share the registered exception observation at test line 102.
- No registered observation is unmapped. Exact exception type, text, and code-to-message
  construction remain explicitly unknown.
- Overwrite, actual/formal, early return, catch/finally, visible-pure and cost gates also passed.

## Identity and reproducibility

- Final directory: `out/mockbench-study/python-issue26-27-20261111-r11`
- Run command:
  `experiments/backward-slicing/python-backend-pilot/run_pilot.sh /tmp/mockspec-issue24-tools/codeql-v2.27.4/codeql/codeql /home/haon/projects/Stub-Tester/.omx/developer-realistic-registry-max out/mockbench-study/python-issue26-27-20261111-r11`
- `issue26-pre-registration.json` SHA-256:
  `2abbbc5ad56241aa39c0f0f708ceecc43c0eaece4f93bec66b45a6044d39d724`
- `observation-study.json` SHA-256:
  `386f489632e1b982f62898018fa23e6a09a82f8a95dd1b9d9099a61266e5777f`
- `issue26-verdict.json` SHA-256:
  `09b66e984db1291c5b9e58f2a96fc13aa0c37a2481d3256cdbb11a273ddc6f74`
- The verdict binds the case result hash above and backend verdict SHA-256
  `818563c8f7955a722c342062ab1a9c94ebaebc82776204e3d30c86b6cc9e13b2`.

## Cost and boundary

The Flask database plus Flask-prefixed queries took 42.13 seconds. The maximum recorded
per-process max RSS was 675,471,360 bytes; it is not a simultaneous whole-run peak. Completion
remains `partial` / `verified_incomplete` because exact values, hidden Mock behavior, path
satisfiability, receiver/global/escaped transitive effects, and some pytest-version compatibility
remain unknown. No target test or 165-case batch was run.

