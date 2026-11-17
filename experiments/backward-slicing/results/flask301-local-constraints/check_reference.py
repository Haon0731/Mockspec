"""Independent acceptance checks from manual-reference.md, never analyzer input."""
import json
from pathlib import Path
import sys


def check(path: Path) -> dict:
    artifact = json.loads(path.read_text())
    assert artifact['identity']['case_id'] == 'flask_301'
    assert artifact['identity']['snapshot_id'] == (
        'c453c24d83b12ba90fa4ca4f38b149166d417b1ad04fcd778ff6ad93fe764c44'
    )
    result = artifact['analysis']
    assert result['binding']['origin'] == 'task_provided'
    assert [o['observation_id'] for o in result['observations']] == [
        'criterion:0', 'criterion:1', 'criterion:2',
    ]
    expected = [
        ('empty_args', 181, 'current_app.response_class', [], [True]),
        ('single_string_arg', 184, 'current_app.make_response', ['Hello World'], [False, True]),
        ('single_tuple_arg', 184, 'current_app.make_response', [['Success', 200]], [False, True]),
        ('multiple_args', 184, 'current_app.make_response', [['Not Found', 404]], [False, False]),
    ]
    assert len(result['branches']) == len(expected)
    for branch, (scenario, line, target, args, polarities) in zip(result['branches'], expected):
        assert branch['parameter_row']['scenario'] == scenario
        assert branch['return_site']['start_line'] == line
        assert [c['polarity'] for c in branch['conditions']] == polarities
        boundary = branch['boundary_return']
        assert boundary['target_expression'] == target
        assert boundary['arguments'] == args
        assert boundary['keyword_arguments'] == {}
        if scenario in ('single_tuple_arg', 'multiple_args'):
            assert len(boundary['argument_literals']) == 1
            assert boundary['argument_literals'][0]['kind'] == 'tuple'
        assert boundary['argument_status'] == 'implementation_derived_path_fact_not_test_requirement'
        assert boundary['requirements'] == [
            {'kind': 'return_non_null', 'observation_id': 'criterion:0'},
            {'kind': 'return_has_attribute', 'attribute': 'headers', 'observation_id': 'criterion:1'},
            {'kind': 'return_has_attribute', 'attribute': 'status_code', 'observation_id': 'criterion:2'},
        ]
        assert boundary['authorization'] == 'not_established'
        assert branch['status'] == {
            'explanation': 'partial', 'support': 'conditional', 'realization': 'conditional_only',
        }
        assert not branch['blockers']
    assert result['coverage']['status'] == 'partial'
    assert result['common_constraints'] == []
    assert artifact['cost']['model_requests'] == artifact['cost']['target_executions'] == 0
    return {'manual_reference_check': 'passed', 'parameter_rows': 4, 'original_observations': 3,
            'scope': 'local normal-return projection only; not runtime or full-path validity'}


if __name__ == '__main__':
    print(json.dumps(check(Path(sys.argv[1])), indent=2))
