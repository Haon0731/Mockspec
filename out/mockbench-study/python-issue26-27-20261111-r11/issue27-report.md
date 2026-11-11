# Issue #27 closure report — unified state observation result

## Result

`verdict.json` and its `state_boundary_evidence` are `passed`; all 17 state checks are true.
`state-observation-study.json` exposes nine registered state observations and nine `state_flow`
relations through the same `mock-spec.backward-analysis-case/v1` contract used by real cases.
The fixture identity is explicitly `python-state-mechanism-probe`; this evidence is not attached
to the real Flask observations.

## Accepted evidence

- Generic visible field write: line 59 to `ready` read at line 60. The accepted query has no
  case function, method, global-variable, or field-name selector.
- Direct opaque argument effect: line 80 to `ready` read at line 81.
- Returned alias effect: line 85 to `ready` read at line 87.
- Receiver effects: line 131 to `configured` at line 132, and loop call line 149 to line 154;
  a pre-call loop write is not misclassified as a later kill.
- Backend-resolved module-global binding: line 158 to `active` at line 159.
- Explicit escape path: line 176 to retained-argument field `sealed` at line 177.
- Exceptional state: call line 48 to handler line 51 and finally line 53. Multiple CFG routes
  collapse only after frozen-source AST evidence proves a unique call at the emitted line.
- Definite same-object/same-field overwrites kill earlier unknown state effects. Trusted fresh
  objects allocated after opaque calls are excluded.
- Distinct parameters are not treated as disjoint: receiver/observed and retained/observed
  parameter cases are recorded as `parameter_alias` UNKNOWN. A module-global rebind is recorded
  as `module_global_rebind` UNKNOWN, not as proof of no influence.
- UNKNOWN evidence requires exact relation, scope, function, field, path ID, region identity and
  ordered call/region/observation lines; label-only evidence fails closed.

## Identity and reproducibility

- Final directory: `out/mockbench-study/python-issue26-27-20261111-r11`
- Run command is the same command recorded in `issue26-report.md`.
- `state-pre-registration.json` SHA-256:
  `7f8c32e7f8a53c15c0484ee5adeaadd52de0b72b4c993bfe547e54716310b719`
- `state-observation-study.json` SHA-256:
  `b662fb91fa055e38537a3b39bdf1292fb26984eb5e66f47d8e49942a8fb18f39`
- `verdict.json` SHA-256:
  `818563c8f7955a722c342062ab1a9c94ebaebc82776204e3d30c86b6cc9e13b2`
- The state pre-registration binds source SHA-256
  `3c1c0cb361a103ce11eefc8042923030a6cbbda06bc8bf83deb84f39e27d5e4a`,
  boundary-query SHA-256
  `19ac61aa61f20e1a76960d83c424560aa17c8c4d632daa34dfeea481554240e1`,
  generic-visible-query SHA-256
  `f1b8cb5af4c6b7935050d1e99e9e355b89968fc849bd4589f271dc08f32cb1ef`,
  and mapper SHA-256
  `824ba0eb697d98e9cb887ec462ab89aca175ef97de2dd3c28b980e596a574bf6`.

## Cost and boundary

The probe database plus probe queries took 71.83 seconds. The maximum recorded per-process max
RSS was 674,082,816 bytes. Completion remains `partial` / `verified_incomplete`: concrete values,
dynamic attributes, arbitrary transitive heap reachability, implicit escapes, path satisfiability,
visible-write parameter aliasing and post-rebind alias identity remain explicitly unknown. No
target test or 165-case batch was run.

