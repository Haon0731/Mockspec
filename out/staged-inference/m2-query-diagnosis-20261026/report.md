# M2 action reliability diagnosis — 2026-10-26

Scope: one Scenario Agent, the existing three read-only tools, and the unchanged
five research stages. No semantic prompt improvement, new input admission, target
test execution, dependency, or production code change. New phase terminology:
M1 engineering; M2 real execution; M3 semantic review; M4 method comparison.

## Applied references

- [Anthropic: Building effective agents](https://www.anthropic.com/research/building-effective-agents): preserve the simple workflow and use actual environment feedback.
- [OpenAI: Function calling](https://developers.openai.com/api/docs/guides/function-calling): validate the model/application protocol and associate observations with requests. The existing host-dispatched QueryResult protocol remains in place.
- [SWE-agent: ACI](https://github.com/SWE-agent/SWE-agent/blob/main/docs/background/aci.md): keep task-specific actions and useful bounded feedback; no generic shell or broader repository access.
- [OpenAI: Trace grading](https://developers.openai.com/api/docs/guides/trace-grading) and [SDK tracing](https://openai.github.io/openai-agents-python/tracing/): inspect request/observation/failure trajectories before evaluating research quality. All evidence remains local; no exporter enabled.

## Reproduction and fix

The saved valid EXTRACT result and the same reviewed flask_308 input were replayed
into QUERY once. `request.json`, `response.json` and `outcome.json` show HTTP 400:
`Invalid json schema: field anyOf: missing field type` (provider request ID in
the message: `f0af4d3f-032c-4376-ba2d-742a558b292b`; response header ID absent).
The provider rejected the requested output schema before a usable QUERY response.
Two `Any | None` fields generated `anyOf: [{}, {type: null}]`.

`QueryAttempt` and existing `QueryAssessment` now supply explicit nullable wire
schemas through Pydantic `WithJsonSchema`. Runtime values remain raw: QueryExecutor
still registers malformed attempts before validation. The five-stage policy,
Prompt text, three tools and shared budgets are unchanged.

The real same-input fixed QUERY returned HTTP 200 and `finish_queries`, using
5,650 tokens (rate estimate $0.004152). It considered existing evidence sufficient.
This proves the reproduced schema rejection was resolved, not autonomous tool use.
The earlier historical error lacked raw diagnostics; the matched reproduction is
strong evidence for its cause, not a recovered HTTP response from that old run.

Two local probe preparation errors (httpx vs installed httpx2; UnifiedTask uses
to_dict rather than model_dump) occurred before any provider request. They were
corrected in the diagnostic harness. An HTTP MockTransport regression harness
stalled locally and was replaced with a deterministic SDK provider-call boundary;
that test reproduces the captured 400 rule and parses a typed response after repair.

## Trace changes

`inference_record.json.model_calls` retains each invocation's immutable payload,
payload hash/size, instructions, output schema, model settings, elapsed time,
response references and sanitized exception class/status/code/request ID/cause types.
It distinguishes pre-call budget rejection, SDK-hook request accounting, response,
timeout and cancellation. SDK-hook accounting is not proof of network transmission.
Existing `query_log`, model responses and stage results provide the tool/observation
links. Headers and credentials are not recorded in the application trace. Calls
are published with the existing record/manifest on handled completion; abrupt
process death before artifact publication is not a durable streaming trace guarantee.

## Real validation outcomes

1. `fixed-query/`: same QUERY input, HTTP 200, parsed `finish_queries`; no tool call.
2. `full-run/`: fresh original five-stage execution. EXTRACT model request completed,
   then host validation rejected a source quote mismatch: `ERROR/output_invalid`.
   It used 5,348 tokens (rate estimate $0.0043782), produced no final contract,
   and was not retried. This separate output/reference problem was not repaired
   by changing semantic prompts or weakening source validation.
3. `action-loop/`: explicitly directed engineering conformance check, using the
   existing QUERY stage, reviewed task and QueryExecutor. The tester specified
   the three tools; this is NOT spontaneous exploration or a full five-stage run.
   Four real model requests produced three queries: inspect_source,
   inspect_program_structure and inspect_dependency_usage. All returned `ok`;
   each receipt was assessed by the next real model response, then the model
   finished. 24,652 tokens; rate estimate $0.0127749. `trace.json` preserves
   the observations, associations and updated judgments.

Per-check preflights retain limits and identity. No automatic or quality retries.
The rejected 400 provided no usage; its usage remains unknown, so the estimates
above cannot establish a total invoice for the whole diagnosis session.

## Verification and remaining boundary

- Regression reproduced the malformed-schema rejection before the fix.
- Seven focused trace/schema tests passed; full offline suite: 408 passed,
  13 real-data tests deselected. Scoped Ruff passed.
- Production mypy remains 31 baseline diagnostics; research check remains 25,
  with no new diagnostics. Nonzero baseline typecheck exits are retained.
- The full-run record and input hashes match its manifest (`verification.json`).
- Stub-Tester and frozen input files were read only. All source material is the
  approved external frozen snapshot, not a live project checkout or raw dataset.

The concrete QUERY schema defect is fixed and a directed real tool-observation
loop is verified. Full M2 is still not passed: there is no successful complete
five-stage Scenario Card, and autonomous need-based exploration is not established.
The EXTRACT quote mismatch remains visible for a separately scoped output-quality
investigation. No M3 semantic or M4 method-effectiveness claim is made.

Reproduction helpers `query_probe.py` and `action_loop_probe.py` are bounded
diagnostic artifacts, not new production entry points. They require a fresh output
directory and the explicitly loaded local API environment; invoking them makes
paid requests. Historical request/response files are preserved without overwrite.
