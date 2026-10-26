# Issue #17 — M2 acceptance attempt and diagnosis

Date: 2026-10-26. Status: **NOT PASSED; #17 remains open; #18 remains blocked.**
This is a bounded research validation record, not a production capability claim.

## Diagnosis and engineering changes

The saved historical EXTRACT was replayed through the actual `_check_source_reference`
validator. Two references failed: `test:101–103` omitted the whitespace-only first
line (its text belongs to 102–103), and `imports:11` quoted line 12. The recorded
model payload equals the canonical input snapshot; serialization and host coordinates
were unchanged. See `diagnosis.json`. Historical outputs were not edited.

The research controller now supplies a `source_line_index` alongside the unchanged
visible task. Every row is an absolute line coordinate and exact already-visible
text; it adds no source access, answer path, tool sequence or suggested constraint.
Quote errors include the source ID and range. Strict equality, original input,
production V0.1 code/Prompt, masking and admission rules remain unchanged.

The first new run passed EXTRACT, then exposed another provider schema incompatibility:
the new nullable `ExplorationStop` used a bare `$ref` inside `anyOf`. DeepSeek returned
HTTP 400, `Invalid json schema: field anyOf: missing field type`. The prior provider
regression accepted `$ref` branches and did not catch this shape. A stricter boundary
regression reproduces the observed rejection (`schema-red.txt`).

`_StageOutputSchema` uses the installed Agents SDK output-schema extension to inline
local union references. It retains the SDK's original strict Pydantic validator.
Nullable construct/deliver/revision objects are covered too. The trace now records
the actual provider schema rather than the untransformed Pydantic schema.
DeepSeek officially documents both `anyOf` and `$ref`, but not this composition
restriction; the repair is based on the actual error and boundary reproduction.

## Recorded attempts

| Attempt | Result | What it establishes |
| --- | --- | --- |
| `run/` | EXTRACT passed; QUERY HTTP 400; zero tool attempts | Coordinate metadata can support valid extraction, but does not guarantee it. Schema incompatibility is a separate failure. |
| `run-schema-fixed/` | EXTRACT rejected `mut:282–283`; zero tool attempts | Model omitted four leading spaces from the first quoted line despite correct coordinates. No final contract. |
| `schema-provider-check/` | One isolated QUERY replay, HTTP 200 and typed `finish_queries` | Repaired schema is accepted by the real provider. This reuses a validated EXTRACT, does not dispatch tools, and is not a complete autonomous run. |

The second full run followed a concrete engineering repair; it is not a repeated
sample from unchanged code to select a favorable result. After its new output-quality
failure, no further full run was made. The final isolated check validates the schema
repair only and is explicitly excluded from M2 acceptance.

The QUERY conformance response judged the initial `flask_308` material sufficient
and requested no tools. Therefore this case has not demonstrated a need for newly
observed repository evidence. No forced tool calls or withheld initial context were
used to manufacture that need.

## Identity, commands and budget

`preflight.json` and `preflight-schema-fixed.json` retain reviewed snapshot identity,
input hash, controller hashes, model, provider, command and explicit limits. Both
full runs use variant C, `deepseek-flash`, repository-read mode, 200,000 total tokens,
8,192 output tokens, 600 seconds overall, 120 seconds per stage and a US$1 cost gate.
There is no fixed query-count limit. Automatic SDK retries are disabled.

The Adapter rechecked the existing frozen Python snapshot and masking before each
run. Repository exploration relies on the preparation-side premise documented in
`docs/input-audit.md` and #12, not a new whole-repository audit. No input admission
record, frozen source, target test, or dataset was modified or executed.

From repository root, with credentials securely exported and a **fresh output path**:

- `bash out/staged-inference/issue17-m2-20261026/reproduce.sh`
- `bash out/staged-inference/issue17-m2-20261026/reproduce-schema-fixed.sh`
- `PYTHONPATH=src OPENAI_AGENTS_DISABLE_TRACING=1 .venv/bin/python out/staged-inference/issue17-m2-20261026/schema_probe.py <new-output-directory>`

These commands make paid requests. The saved run directories must not be overwritten.
Each full-run manifest records the code identity used at execution. The final source
also has a type-narrowing assertion added after the second run; that assertion has
no model-input or validation-policy effect, and the isolated provider check uses it.
`artifact-checks.json` verifies both full-run input/record hashes and absence of a
published final contract. The probe inherits the earlier bounded diagnostic helper;
it is an evidence-reproduction script, not a production entry point.

Known usage: first EXTRACT 6,466 tokens ($0.0035796 estimate); second full run 8,596
($0.0061356); isolated QUERY 8,938 ($0.0047748). Known subtotal: 24,000 tokens and
$0.0144900. The rejected HTTP 400 supplied no usage, so the overall total is unknown.
Peak cache-miss input $0.30/M and output $1.20/M were verified on the official
[pricing page](https://api-docs.deepseek.com/quick_start/pricing/); these are host
estimates, not invoices or hard remote caps. Responses compatibility is documented
[here](https://api-docs.deepseek.com/guides/responses_api/).

## Acceptance gaps and next work

- The strict quote gate still blocks a complete five-stage run. Coordinate metadata
  is an input usability improvement, not a proven solution to model copying errors.
  Do not trim, replace, fuzzy-match or silently repair generated citations.
- No autonomous multi-round repository discovery or newly observed relevant evidence
  was obtained. This case's isolated QUERY response considered initial evidence
  sufficient. Choosing a genuinely context-dependent reviewed case is still needed;
  the current live research CLI admits only `flask_308`.
- No valid final Scenario Card, complete five-stage trace or M3 semantic evaluation
  was produced. #17 cannot be closed as completed and #18 cannot start.

Follow-up must address exact-citation generation and case suitability explicitly,
with a new bounded validation plan, before another full live attempt. Do not repeatedly
change semantic prompts or sample until success. Existing target-test and experiment
exclusions remain in force.

## Final verification

- `pytest-final.txt`: 530 passed, 13 real-data tests deselected. No target-project
  test or new dataset experiment was executed.
- `ruff-final.txt`: changed controller and regression tests pass scoped Ruff.
- `mypy-research-final-checked.txt`: research controller passes with untyped imports
  skipped (`--follow-imports=silent --ignore-missing-imports`). Production check
  reports 18 diagnostics in unchanged production files (`mypy-production.txt`);
  this is not a claim of a clean full-project typecheck.
- Regression tests preserve whitespace and non-one-based source coordinates, reject
  incomplete/wrong-coordinate/unindented quotes, require explicit wire union types,
  preserve malformed-attempt accounting and strict parsing, and compare captured
  schemas with actual SDK provider request bodies.
- Independent read-only code review found no blocker in the coordinate or Schema
  changes. Review and offline checks do not establish live M2 acceptance.
