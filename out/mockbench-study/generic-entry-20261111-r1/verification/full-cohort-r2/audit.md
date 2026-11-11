# Issue #32 independent full-cohort audit

- Intake acceptance: **passed**
- Cohort: 165 cases ({'java': 59, 'python': 106})
- Integrity errors: 0; warnings: 0
- Independent stages: `{"all_observations_mapped": 120, "attempted": 165, "backend_completed": 124, "database_created": 106, "database_or_graph_created": 123, "graph_created": 17, "input_prepared": 164, "observation_linked_cross_source_relation": 26, "product_call_binding_present": 24}`
- By language: `{"java": {"all_observations_mapped": 14, "attempted": 59, "backend_completed": 18, "database_created": 0, "database_or_graph_created": 17, "graph_created": 17, "input_prepared": 58, "observation_linked_cross_source_relation": 12, "product_call_binding_present": 6}, "python": {"all_observations_mapped": 106, "attempted": 106, "backend_completed": 106, "database_created": 106, "database_or_graph_created": 106, "graph_created": 0, "input_prepared": 106, "observation_linked_cross_source_relation": 14, "product_call_binding_present": 18}}`
- Semantic correctness: **not_evaluated** (zero verified cases)

## Outcomes and retained gaps

`{"ambiguous_observation_cases": 0, "budget_or_timeout_cases": 0, "failure_categories": {"duplicate_type_input": 1, "generic_interprocedural_effects_incomplete": 106, "mut_binding_unresolved": 100, "observation_mapping_incomplete": 4, "product_source_not_located": 2, "resource_limit": 4, "semantic_analysis_gap": 36, "semantic_backend_failed": 41, "semantic_validation_not_evaluated": 124, "source_budget": 20, "test_to_product_not_reached": 8, "unresolved_calls": 34}, "missing_observation_cases": 45, "semantic_backend": {"generic_codeql_python": 106, "generic_wala_java": 59}, "semantic_backend_status": {"complete": 124, "failed": 41}, "unknown_effect_cases": 70}`

Typed relations are evidence of generic analysis intake, not proof of correct slicing, Mock recovery, generation, execution, or scoring. All relation coverage and semantic validation must remain `not_evaluated`.

## Integrity findings

- No integrity errors.

## Scope limits

- python: Static CodeQL intake, including narrowly qualified visible dynamic-loader facts; only typed observation-linked cross-source relations count.
- java: Static WALA graph/SSA/slice intake with source provenance; only typed observation-linked cross-source relations count.
- shared: No target tests, loaders, Mock generation/scoring, hidden answers, or semantic validation are covered.
