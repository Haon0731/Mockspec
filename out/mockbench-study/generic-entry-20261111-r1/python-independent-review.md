# Python generic-backend independent review

Status: **passed_bounded for the targeted intake gate**. Semantic validation remains
`not_evaluated` for every new relation.

The final `python-final-r4` run used stable implementation hashes through the
scrubbed outer dispatcher. All three non-original cases prepared admitted frozen
input, created and queried a CodeQL database, uniquely mapped the original
assertion, automatically bound a product function, and produced one
observation-linked cross-source relation. Neither the target nor a visible loader
was executed.

| Case | Project and test form | Original root | Product scope | Review |
| --- | --- | --- | --- | --- |
| `click_402` | Click; direct imported call assigned to a local result | `tests/test_402format_filename.py:100` | `src/click/utils.py:407-446`, `format_filename` | Source-grounded composed flow |
| `stable-diffusion-webui_419` | Stable Diffusion WebUI; visible loader plus multiline keyword call | `tests/test_419load_file_from_url.py:58` | `src/modules/modelloader.py:20-41`, `load_file_from_url` | Source-grounded composed flow |
| `langflow_186` | Langflow; sliced-source loader plus visible function-global helper injection | `tests/test_186get_best_access_host.py:130` | `src/src/backend/base/langflow/__main__.py:487-508`, `get_best_access_host` | Source-grounded composed flow |

The accepted relation has a deliberately narrow meaning: the backend combines a
native or authenticated visible-loader function binding with CodeQL test-local
flow from that call result to the exact original assertion expression. The
function span is provenance scope. It is not an internal product return-site edge
and does not validate a concrete result.

Unknowns remain visible. Every contract retains incomplete return, exception, and
reachable-state effects plus unevaluated path feasibility. Stable Diffusion keeps
the rejected empty package source boundary. Langflow records fourteen source
budget exclusions; its test also visibly replaces `is_loopback_address` and
`can_connect` in the loaded function globals, so no internal branch or helper
behavior is inferred. All three contracts remain `partial` with
`semantic_validation.status = not_evaluated`.

Therefore the three-case Python targeted gate holds for this bounded composed-flow
scope across three projects and three test forms. It does not establish semantic
correctness, complete slicing, full import coverage, exception/state influence,
or Mock behavior.

Evidence: `python-final-r4/targeted-results.json` SHA-256
`42b312817153bb3d7a58767dcbc93f4aa1c262ad47b403214a650d9d1ddf57dc`.
Machine-readable case hashes and endpoints are in
[`python-independent-review.json`](python-independent-review.json).
