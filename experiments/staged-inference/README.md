# Staged inference M1 replay

This research path checks auditable variant-B/C outcomes with a Python synthetic
task and a fixed SDK model. Run `run.py --mode synthetic_replay --variant B
--replay-case selected --output-dir /tmp/mockspec-selected` with `PYTHONPATH=src`
and the project virtualenv. Select `ambiguous`, `inconclusive`, or `error` for
the other complete-entry replays; use a fresh output directory each time.
Commands, artifact fields, exit codes, and downstream meanings are maintained
in [the usage guide](../../docs/usage.md#分阶段研究回放m1-1--m1-4).

Issues #7, #8, and #9 cover the success path, four result states, source rejection,
failure evidence, bounded queries, and shared run budgets. The tests exercise the SDK controller and artifact boundary
without paid models, external datasets, or target test execution. Input rejection
is synthetic schema validation, not proof of reviewed Adapter admission.

Use `--query-count 6` to replay all three read-only tools twice, or
`--query-count 7` to preserve the seventh rejection. Registration precedes argument
validation; every failure consumes the same six-attempt allowance. Model calls,
token accounting, query counts, and the deadline persist across stages. Receipts
and unfinished assessments remain in the record. No technical retries occur.

Issue #10 adds one static CONTRAST for C, at most one semantic REVISE, and one
separately counted format repair under the same budget. B disables only contrast.
The cache-miss, unspecified-intent, legal-variation, and second-revision replays
exercise AC-05/06/07/08 without executing target tests. See the usage guide for
commands and repair limits. Real-model inference quality and the full M1 research
program remain unverified.

Validation for #9 (2026-10-23): the non-`real_data` regression passed with
365 tests and 13 deselected; Ruff passed for `src`, `tests`, `scripts`, and this
research directory. Production mypy retains its prior 31 diagnostics in nine
files; checking the four research modules reports only 16 inherited diagnostics
in four production modules, with no research-module diagnostics. Timeout and
cancellation entry tests use harmless real sleeping subprocesses and verify
that they are terminated and reaped. No paid model or external dataset run was
performed; these results establish engineering behavior, not inference quality.

Validation for #10 (2026-10-23): the non-`real_data` regression passed with
392 tests and 13 deselected. After the final actual-token-overrun delivery fix,
the contrast/repair suites passed 29 tests, including valid and malformed
responses that exhaust the shared budget on receipt. Ruff and `git diff --check`
passed. Production mypy remains at 31 existing diagnostics in nine files;
research-module checking remains at 16 inherited diagnostics in four production
files, identical to the pre-change baseline and with no research diagnostics.
The variant-C cache-miss CLI replay returned SELECTED. These are static and
scripted engineering checks, not target-test execution or quality evidence.

M1-5 (#11) adds a reproducible acceptance inventory and bounded live-entry
preflight. The requirement matrix, validation evidence and incomplete real-model
check are tracked in [the acceptance record](acceptance-report.md). A passing
fixed replay is engineering evidence only; M2 generation and M3 experiments are
not implemented by this acceptance work.
