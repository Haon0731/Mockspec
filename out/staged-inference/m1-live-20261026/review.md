# M1 DeepSeek live check — 2026-10-26

Scope: one predeclared C-variant run on the existing reviewed Python `flask_308`
snapshot, through the unchanged staged research entry. This is a small live
observation for #11, not an M2/M3 experiment or a claim of inference quality.

## Configuration and execution

`preflight.json` records the input, model, official endpoint, prices and budget
before execution. The user selected `deepseek-flash`. Official DeepSeek documentation
confirms native Responses support: <https://api-docs.deepseek.com/guides/responses_api/>.
Peak cache-miss input ($0.30/M) and peak output ($1.20/M) rates were verified at
<https://api-docs.deepseek.com/quick_start/pricing/> on 2026-10-26. These conservative
rates are estimates rather than a billing statement.

Budget: 10 model requests, 6 query attempts, 8,192 output tokens per request,
60,000 total tokens, 300 seconds overall, 120 seconds per stage, $1 cost gate.
The output/stage limits are larger than the earlier proposed preflight to allow
the provider's default thinking mode. No technical retries or quality retries.

Result: `ERROR / model_error`, at `QUERY`. Two model requests started; one completed.
`EXTRACT` returned a structured result; the second request produced no captured
response or usage. The existing error handler records only `stage model call failed`;
stderr contains `Error getting response`, with no HTTP status or provider error code.
The evidence cannot distinguish a transport error from a provider/API rejection.
There was no restart, contract publication, source query, Mock generation or target
test execution. `run/` retains the input snapshot, record and manifest.

Known usage: 2,266 input + 5,734 output = 8,000 tokens, estimated $0.0075606.
The second request's usage is unknown. Total cost is unknown, not $0.0075606 and
not zero; the manifest correctly leaves total token/cost fields null while preserving
the known subtotal. The provider did not supply a resolved model version in the
captured response, so only the configured alias can be claimed.

## Readability and semantic review

Review uses the structured EXTRACT result and the retained visible task, not model
reasoning text or private evaluation material.

- Both candidate descriptions are readable: application-context delegation and
  Werkzeug fallback. Their source IDs, line ranges and quotations match the input.
- The visible test has five parameter rows spanning both context conditions, and
  conditional ordinary exception-message assertions. Those branches are known
  parts of the parameterized task, not established competing interpretations of
  the entire task. EXTRACT alone does not establish a valid later selection.
- Original-goal references include the parameterization header and exception
  invocation plus a MUT docstring. They do not by themselves capture the complete
  ordinary oracle. The candidate evidence does reference both message assertions.
- The run never reached CONSTRUCT, CONTRAST or DELIVER. Full constraint completeness,
  legal variations, degeneracy comparison and final-contract readability remain
  unobserved for this model. No valid final contract exists.
- Zero source queries means autonomous tool use was not observed. This run does not
  establish semantic correctness or generally effective anti-degeneracy behavior.

## Engineering verification

- `pytest.txt`: 401 passed, 13 real-data tests deselected, no skips.
- `ruff-scoped.txt`: prescribed source/test/script/staged-research scope passes.
- `mypy.txt`: 31 diagnostics in 9 production files, exactly matching the prior
  recorded baseline; nonzero exit is retained, not described as a typecheck pass.
- `verification.json`: hashes and identity fields of 15 final replay manifests and
  this live manifest verified; five frozen production/prompt/schema files unchanged.
- `ruff.txt`: an additional whole-tree scan reports 36 existing diagnostics in
  unrelated research code and generated candidates. They are outside this task's
  prescribed lint scope and remain unchanged; whole-tree lint is not claimed to pass.
- API key was checked absent from saved evidence. `.env.local` remains ignored and
  permission-restricted. The legacy Stub-Tester repository was read only.

The actual live check and failure/readability review are recorded. Successful
end-to-end live inference is not established. M2 generation integration and M3
ten-case/ninety-run experiments remain unimplemented.
