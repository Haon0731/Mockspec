# Issue 23 WALA capability closure

Status: **passed**. This closes a bounded backend capability gate, not Jib's original-observation acceptance.

## Acceptance matrix

- `version_config_budget_and_cost_recorded`: **passed**
- `raw_masked_and_structure_only_attempts_preserved`: **passed**
- `real_input_mappable_result_with_diagnostic_entry_separated`: **passed**
- `value_control_field_alias_and_unknown_effect_mechanisms`: **passed**
- `receiver_argument_reachable_global_escaped_and_write_before_throw`: **passed**
- `failures_and_resource_boundaries_preserved`: **passed**

## Newly verified opaque-effect regions

- `receiver_direct_field`: **passed**
- `argument_one_hop_reachable_field`: **passed**
- `application_static_escaped_object`: **passed**
- `application_static_primitive`: **passed**

Unsupported deeper heap, arrays, library/runtime globals, unbounded escaped state, null, and arbitrary exception types remain explicit unknowns. No concrete Mock behavior is encoded.
