"""Behavior-based counterexamples and negative evidence, no case-ID dispatch."""
import json
from pathlib import Path
from cvc5 import Kind as K
from models import Model, java_image, java_entrypoint, python_flow
from poc import check_candidate, check_allowed, query, round_trip, solver, bounded_abduction


def run():
    out = {}
    m = java_image()
    v = m.symbols
    bad = m.conjunction(m.candidate, m.neg(m.candidate))
    out['contradictory_h'] = check_candidate(m, bad)
    out['nullable_java_integer'] = check_candidate(m, m.conjunction(v['version_null'], m.op(K.LEQ, v['version'], m.integer(8))))
    out['version_7_also_sufficient'] = check_candidate(m, m.conjunction(m.neg(v['version_null']), m.neg(v['version_throws']), m.eq(v['version'], m.integer(7))))
    out['unused_java_response'] = check_candidate(m, m.candidate, guard=v['from1_present'])
    out['default_not_proven_globally_necessary'] = query(m, [*m.background, m.external_goal, v['from1_present']])
    out['default_not_proven_globally_necessary']['interpretation'] = 'abstract_countermodel_only; depends on unknown external_image_matches, not executable source evidence'
    out['external_path_without_favorable_assumption'] = query(m, [*m.background, v['from1_present'], v['from2_present'], m.neg(m.external_goal)])
    out['getters_not_implicitly_stable'] = query(m, [*m.background, v['from1_present'], m.neg(v['from2_present'])])
    equivalent = m.neg(m.neg(m.candidate))
    out['syntactically_distinct_equivalent'] = query(m, [*m.background, m.guard, m.op(K.XOR, m.candidate, equivalent)])
    for name in ('java8_selected', 'external_image_matches', 'image_observation_reached'):
        try:
            check_allowed(m, v[name])
        except ValueError as exc:
            out['forbidden_h_'+name] = {'status': 'rejected', 'reason': str(exc)}
        else:
            out['forbidden_h_'+name] = {'status': 'accepted'}
    # A malformed semantic interpretation cannot be rescued by exact citations.
    out['citation_valid_but_wrong_version_constraint'] = check_candidate(m, m.conjunction(
        m.neg(v['version_null']), m.neg(v['version_throws']), m.op(K.GT, v['version'], m.integer(8))))
    e = java_entrypoint()
    out['custom_list_can_equal_expected'] = check_candidate(e, e.candidate)
    p = python_flow()
    x = p.symbols
    out['no_exception_observed'] = check_candidate(p, p.conjunction(p.neg(x['abort_throws']), p.neg(x['fallback_throws'])))
    out['none_reaches_fallback'] = query(p, [*p.background, x['ctx_none'], p.neg(x['fallback_called'])])
    out['abort_throw_blocks_fallback'] = query(p, [*p.background, p.neg(x['ctx_none']), x['abort_throws'], x['fallback_called']])
    out['abort_return_reaches_fallback'] = query(p, [*p.background, p.neg(x['ctx_none']), p.neg(x['abort_throws']), p.neg(x['fallback_called'])])
    out['uncalled_fallback_cannot_supply_message'] = check_candidate(p, p.conjunction(
        p.neg(x['ctx_none']), x['abort_throws'], p.neg(x['abort_message_matches']), x['fallback_throws'], x['fallback_message_matches']))
    out['same_message_from_fallback'] = check_candidate(p, p.conjunction(
        p.neg(x['ctx_none']), p.neg(x['abort_throws']), x['fallback_throws'], x['fallback_message_matches']))
    # Truthiness is independent from identity with None, not substituted for it.
    truthy = p.var('ctx_truthy')
    out['falsey_non_none_still_calls_aborter'] = query(p, [*p.background, p.neg(x['ctx_none']), p.neg(truthy), p.neg(x['abort_called'])])
    # Independent representation probe, never reported as a third benchmark.
    seq = Model('synthetic_representation_test')
    c1, c2 = seq.var('call1'), seq.var('call2')
    t1, t2 = seq.var('throw1', control=True), seq.var('throw2', control=True)
    r1, r2 = seq.var('return1', 'Int', True), seq.var('return2', 'Int', True)
    seq.background = [c1, seq.eq(c2, seq.conjunction(c1, seq.neg(t1)))]
    response = [seq.neg(t1), seq.neg(t2), seq.eq(r1, seq.integer(1)), seq.eq(r2, seq.integer(2))]
    out['synthetic_representation_test'] = {
        'status': 'representation_only',
        'calls': [{'occurrence': 1, 'reaction': 'Return(1)'}, {'occurrence': 2, 'reaction': 'Return(2)'}],
        'sequence_consistent': query(seq, [*seq.background, *response]),
        'second_call_after_first_throws': query(seq, [*seq.background, t1, c2]),
    }
    # Exercise official serialization/parser on UNSAT too.
    m.candidate = bad
    out['unsat_round_trip'], _ = round_trip(m)
    resource_solver = solver(m)
    resource_solver.setOption('rlimit', '1')
    for term in m.background:
        resource_solver.assertFormula(term)
    result = resource_solver.checkSat()
    out['solver_unknown'] = {'status': 'unknown' if result.isUnknown() else str(result),
        'reason': str(result.getUnknownExplanation()) if result.isUnknown() else None,
        'raw_result': str(result), 'limit': 'rlimit=1; deliberate resource-limit probe'}
    out['process_timeout'] = bounded_abduction('java_image', timeout=0.000001)
    out['process_timeout']['interpretation'] = 'deliberately forced subprocess deadline, not observed solver workload timeout'
    return out


if __name__ == '__main__':
    result = run()
    target = Path(__file__).parent/'results'/'negative-results.json'
    target.write_text(json.dumps(result, indent=2)+'\n')
    print(f'{len(result)} negative/representation checks saved to {target}')
