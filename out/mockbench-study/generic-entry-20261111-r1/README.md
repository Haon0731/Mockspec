# Issue #32: generic intake targeted gate

Status: **passed_bounded_entry**. This directory records the development attempts,
negative results, final five-case gate and independent reviews before the separate
full165 run. It is not a full-cohort or semantic-correctness result.

Implementation: commit `0e689e2` (runtime file digests are authoritative in the
individual runs and `targeted-acceptance.json`). No target or loader was executed,
no Mock was generated, no dependencies were rehashed, and frozen inputs were not
edited. Temporary source/database material is not included.

The final Python run is `python-final-r4`: Click direct import, Stable Diffusion
WebUI visible loader/multiline keyword call, and Langflow sliced-source loader with
visible helper-global injection. All three preserve their original assertion and
have one composed function-binding + CodeQL test-local result-flow relation.
The product function span is provenance scope, not an internal return-site edge.

Final Java runs are `java-zipkin-direct-r6` and `r7`: two distinct frozen non-Jib
Zipkin cases/snapshots, both using the same method shape. Each maps both original
observations and retains one four-edge DATA_DEP path from `Schema.java:103` to the
original `assertSame` at test line38 or39. The second observation concerns an
unknown entry parameter and has no fabricated product influence. Generic WALA
uses NO_HEAP and no control dependence, context-insensitive may traversal, and an
explicit 50,000-statement frontier. Legacy FULL/matched-call behavior remains.
Paths crossing frozen opaque placeholders are excluded and remain unknown.

Independent reviews: [Python](python-independent-review.md),
[Java](java-independent-review.md). Machine-readable hashes and checks are in
[targeted-acceptance.json](targeted-acceptance.json). Every new relation and every
coverage category remains `not_evaluated`.

Earlier directories are retained development evidence: initial Python dynamic
binding failures, the fixed loader path, Java compilation failures, FULL heap OOM,
and timeouts caused by an unused legacy forward-slice diagnostic. They are not
additional acceptance cases and must not be mixed with final-hash results.
`prior-smokes/preservation.json` records the exact copied logs/results/CSV; no
source tree or database was copied.

Verification is in `verification/`: 159 Python/integration checks passed;
109 Java checks passed and one historical-source-pin assertion initially failed.
That test now verifies that the old accepted receipt stays immutable while the
historical evaluator rejects the changed current probe; the focused regression
passed. No old receipt or evaluator was altered. Initial Python collection errors
were invocation-path errors; the corrected command and successful output are
retained separately. Ruff and scoped mypy passed. The legacy WALA synthetic
state/alias checks were included in the Java run.

The broader run is separately registered at
`../backward-slice-generic165-20261111-r1/`; it uses original Java59/Python106,
`--backend-mode generic --workers 3 --semantic-seconds 1200`, with at most two
mature backends. Backend completion, original-root mapping, call binding and
observation-linked cross-source influence remain separate from semantic success.
