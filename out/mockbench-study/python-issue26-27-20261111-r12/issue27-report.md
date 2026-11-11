# Issue #27 closure report — unified state observation result

## Result

`verdict.json` and `state_boundary_evidence` are `passed`; all 17 state checks are true.
`state-observation-study.json` exposes nine registered state observations and nine `state_flow`
relations through `mock-spec.backward-analysis-case/v1`. The fixture case identity is explicitly
`python-state-mechanism-probe`; its evidence is not attached to the real Flask observations.

## Accepted evidence

- Generic visible write line 59 reaches `ready` line 60 without case function, method, global-name
  or field-name selectors in the accepted query.
- Direct argument (80→81), returned alias (85→87), receiver (131→132), receiver loop (149→154),
  resolved module global (158→159), explicit escape (176→177), handler (48→51), and finally
  (48→53) state paths are present.
- Definite same-region/same-field overwrites kill earlier effects. A loop pre-write is not mistaken
  for a later kill. Only trusted fresh objects allocated after opaque calls are excluded.
- Distinct parameters are retained as `parameter_alias` UNKNOWN; module-global rebinding is retained
  as `module_global_rebind` UNKNOWN. These boundaries now have exact scope-specific metadata:
  `unresolved_alias_relation` / `backend parameter-alias boundary` /
  `runtime parameter alias unresolved`, versus the corresponding global-rebind metadata.
- UNKNOWN paths require exact relation, scope, function, field, path ID, region identity and ordered
  call/region/observation lines. Multiple exceptional CFG routes collapse only when frozen-source
  AST evidence proves a unique call at the emitted line.

## Identity, cost and reproduction

- Final directory: `out/mockbench-study/python-issue26-27-20261111-r12`
- Command is recorded in `issue26-report.md`.
- `state-pre-registration.json`: `9395faaf13b52da6710d26aa726b39b9f4d769fa5d171964bf1122213be1dc70`
- `state-observation-study.json`: `f11d91377e4d866990538c93b4749bfe8a1ffbc9b57edbbb89cad90308b0675c`
- `verdict.json`: `818563c8f7955a722c342062ab1a9c94ebaebc82776204e3d30c86b6cc9e13b2`
- Pre-registration binds source `3c1c0cb361a103ce11eefc8042923030a6cbbda06bc8bf83deb84f39e27d5e4a`,
  boundary query `19ac61aa61f20e1a76960d83c424560aa17c8c4d632daa34dfeea481554240e1`,
  generic-visible query `f1b8cb5af4c6b7935050d1e99e9e355b89968fc849bd4589f271dc08f32cb1ef`,
  and mapper `3eabd95cbdeb02f741c3faa035c100f6e02a3c9f5fe945bf6e59c8696273752b`.
- Probe database and queries: 70.31 seconds; maximum recorded per-process max RSS: 618,708,992
  bytes (not a simultaneous whole-run peak).

Completion remains `partial` / `verified_incomplete` for concrete values, dynamic attributes,
arbitrary transitive heap reachability, implicit escapes, path satisfiability, visible-write
parameter aliasing and post-rebind identity. No target test or 165-case batch was run. r11 is
preserved; r12 differs because the mapper corrected parameter-alias provenance and precision.

