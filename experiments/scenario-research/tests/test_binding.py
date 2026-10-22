"""Cross-check the metadata boundary using the actual cvc5 parser."""
import json
import sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bind_results import FormulaParseError, parse_formulas, run
from build_examples import PY_ROWS


def test_unlisted_formula_symbol_is_rejected():
    contract = json.loads((ROOT/'examples'/'java.contract.json').read_text())
    formula = contract['formulas'][0]
    # It is declared in the contract, but absent from this formula's permissions.
    formula['text'] = '(assert S_J_FROM2_PRESENT)'
    with pytest.raises(FormulaParseError, match='input not admitted'):
        parse_formulas(contract)


def test_scoped_result_binding_covers_every_row(tmp_path):
    result = run(tmp_path)
    assert len(result['python_rows']) == 5
    assert sum(len(row['checks']) for row in result['python_rows'].values()) == 8
    for row_id, scenario_name, has_context, code, args, kwargs, message, _, _ in PY_ROWS:
        assert result['python_rows'][row_id]['fixed_input'] == {
            'scenario': scenario_name, 'has_app_context': has_context,
            'code': code, 'args': args, 'kwargs': kwargs}
        assert result['python_rows'][row_id]['message_substring'] == message
    for row in result['python_rows'].values():
        for check in row['checks'].values():
            assert check['scenario_ref'] and check['check_refs'] and check['formula_refs']
            assert check['locally_sufficient']
