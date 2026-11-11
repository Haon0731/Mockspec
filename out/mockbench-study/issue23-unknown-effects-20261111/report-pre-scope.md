# Issue 23 bounded unknown-effect study

Status: **blocked**. The bounded mechanism gate passes; the overall gate remains blocked.

## Verified relations

- `unknown_value_state_write`: **passed**
- `return_alias_with_fresh_alternative`: **passed**
- `normal_completion`: **passed**
- `write_before_throw_to_handler_state_read`: **passed**

Normal completion is a structured proof: a structural summary normal exit is paired with the exact invoke's normal SSA-CFG successor and selected observation. It is not presented as a nonexistent single SDG edge.

## Remaining unknowns

- pointer analysis merges the fresh and argument return alternatives
- receiver fields, reference fields, arrays, globals/statics, escaped regions, null, other arguments, and arbitrary exception types
- visible-signature library boundaries outside the configured Application carrier set

This model is not a general sound unknown-call havoc model and does not choose concrete Mock behavior. Jib was not rerun because this change does not address its known transitive-MOD `HeapReturnCaller` materialization pressure.

## Reproduce

```bash
python3 experiments/backward-slicing/wala-pilot/summarize_capability.py \
  --unknown-effects-root out/mockbench-study/issue23-unknown-effects-20261111 \
  --output out/mockbench-study/issue23-unknown-effects-20261111/report.json
```
