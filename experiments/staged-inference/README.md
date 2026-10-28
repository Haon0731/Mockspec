# Staged inference research entry

This research path implements the auditable five-stage Scenario Agent for explicitly reviewed
live inputs and explicit synthetic replay with a fixed SDK model. Run `run.py --mode synthetic_replay --variant B
--replay-case selected --output-dir /tmp/mockspec-selected` with `PYTHONPATH=src`
and the project virtualenv. Select `ambiguous`, `inconclusive`, or `error` for
the other complete-entry replays; use a fresh output directory each time.
Commands, artifact fields, exit codes, and downstream meanings are maintained
in [the usage guide](../../docs/usage.md#分阶段研究回放m1-1--m1-4).

Issues #7, #8, and #9 established the original success path, four result states, source rejection,
failure evidence, bounded queries, and shared run budgets. The tests exercise the SDK controller and artifact boundary
without paid models, external datasets, or target test execution. Input rejection
is synthetic schema validation, not proof of reviewed Adapter admission.

Use `--query-count 6` to replay all three read-only tools twice, or
`--query-count 7` to preserve the seventh rejection. Registration precedes argument
validation; every failure consumes the same six-attempt allowance. Model calls,
token accounting, query counts, and the deadline persist across stages. Receipts
and unfinished assessments remain in the record. No technical retries occur.

The current controller keeps the five responsibilities while allowing CONSTRUCT,
CONTRAST, or REVISE to reopen QUERY for a material evidence gap. Original goals,
question and candidate history, observations, and all budgets carry across the loop.
Semantic continuation is distinct from the one separately counted format repair.
B disables contrast; it does not change evidence or output requirements.

The implementation separates stable responsibilities: `stage_protocol.py` owns the strict
stage DTOs, source-locator materialization, and constructed/contrast result validation;
`investigation.py` owns the original-goal question ledger and stop consistency.
`query.py`, `specification.py`, and `provider_capture.py` respectively own query execution,
the consumer specification, and provider-response evidence. `controller.py` composes those
modules while retaining stage order, shared resources, repair, partial checkpoints, and
manifest-last publication. This is still a research entry and is not wired into the production CLI.

Successful runs can publish `dependency_spec.json` (`mock-spec.dependency/v0.1`),
which embeds the reviewed task, formal v0.1 contract, scope, premises, branch decisions,
goal coverage, allowed variations, unknowns, conflicts, and stop summary. `ALTERNATIVES`
means multiple branches are positively supported and selectable; `AMBIGUOUS` retains a
material legality or selection uncertainty. The validators check references and state
consistency, not semantic entailment or real-model reasoning quality.
`ALTERNATIVES` adds the fifth current result state; historical four-state reports retain their original scope.

Live runs capture non-streaming provider responses before SDK terminal-status parsing.
Incomplete/failed response status, usage, and bounded redacted body remain diagnostic;
missing usage stays unknown and prevents another model call. Capturing a partial response
does not turn it into a contract or trigger continuation-style output repair.
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
