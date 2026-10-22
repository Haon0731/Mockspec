"""Host evidence linking; does not translate source or accept external SMT programs."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import cvc5
from build_examples import JAVA_ORACLE_IDS, PY_ROWS
from inputs import load_java, load_python
from schema_draft import validate_contract
from models import java_image, java_entrypoint, python_flow
from poc import check_candidate, smt2

HERE = Path(__file__).resolve().parent


def _parse_formulas_in_process(contract):
    """Syntax/type validation using cvc5, not semantic validation of source mapping."""
    symbols = {s['id']: s for s in contract['symbols']}
    results = {}
    for f in contract['formulas']:
        if f['support_status'] != 'modeled':
            results[f['id']] = {'status': 'unsupported', 'semantic_check': 'not_run'}
            continue
        solver = cvc5.Solver()
        parser = cvc5.InputParser(solver)
        declarations = '\n'.join(f'(declare-fun {name} () {symbols[name]["sort"]})'
                                 for name in f['symbol_refs'])
        text = '(set-logic ALL)\n' + declarations + '\n' + f['text']
        parser.setStringInput(cvc5.InputLanguage.SMT_LIB_2_6, text, 'trusted-contract-formula')
        while True:
            cmd = parser.nextCommand()
            if cmd.isNull():
                break
            if cmd.getCommandName() not in {'set-logic', 'declare-fun', 'assert'}:
                raise ValueError('unsupported command')
            cmd.invoke(solver, parser.getSymbolManager())
        results[f['id']] = {'status': 'parsed_and_typechecked', 'semantic_check': 'not_run'}
    return results


class FormulaParseError(ValueError):
    """Rejected input or failed native parser; never a semantic UNSAT result."""


def parse_formulas(contract):
    # The installed native parser segfaulted on an undeclared-symbol error path.
    # Keep all parsing (including teardown) outside the host process.
    try:
        process = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--parse-worker'],
                                 input=json.dumps(contract), capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired as exc:
        raise FormulaParseError('parser timeout; input not admitted') from exc
    if process.returncode:
        raise FormulaParseError(f'parser failure exit={process.returncode}; input not admitted; {process.stderr[-2000:]}')
    return json.loads(process.stdout)


def run(output=HERE/'results'):
    output.mkdir(parents=True, exist_ok=True)
    result = {'performed_by': 'offline_host', 'scope': 'handcrafted finite model only',
              'whole_test_verified': False, 'contracts': {}, 'python_rows': {}}
    contracts = {}
    for language, loader, expected_ids, expected_rows in [
        ('java', load_java, JAVA_ORACLE_IDS, set()),
        ('python', load_python, {f'O_{r[0]}' for r in PY_ROWS}, {r[0] for r in PY_ROWS})]:
        path = HERE/'examples'/f'{language}.contract.json'
        c = json.loads(path.read_text())
        contracts[language] = c
        validate_contract(c, loader(), expected_oracle_ids=expected_ids, expected_parameter_rows=expected_rows)
        result['contracts'][language] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'source_identity': c['sources'], 'formula_parsing': parse_formulas(c),
            'symbol_map': {s['id']: s['model_symbol'] for s in c['symbols'] if s['model_symbol'] is not None}}
    result['java_queries'] = {
        'java_image': {'formula_refs': ['F_J_DEFAULT_IMAGE_GUARD', 'F_J_VERSION_JAVA8', 'F_J_DEFAULT_BASE_TARGET'],
                       'check_ref':'K_J_LOCAL_CONSISTENCY', 'scenario_ref':'SC_J_IMAGE_DEFAULT',
                       'result_ref':'solver-results.json#/models/java_image/manual_candidate',
                       'source_refs': [{'source_id':'mut','lines':[60,69,70]}],
                       'oracle_ref':'O_J_BASE_IMAGE', 'relation_to_oracle':'local precursor only; final builder semantics unformalized'},
        'java_entrypoint': {'formula_refs':['F_J_CUSTOM_ENTRY_GUARD','F_J_CUSTOM_ENTRY_EQUALS_TARGET','F_J_ENTRY_TARGET'],
                           'check_ref':'K_J_ENTRY_LOCAL', 'scenario_ref':'SC_J_ENTRY_CUSTOM',
                           'result_ref':'solver-results.json#/models/java_entrypoint/manual_candidate',
                           'source_refs':[{'source_id':'mut','lines':[75,77,78,79]}],
                           'relation_to_oracle':'intermediate list equality only; final builder semantics unformalized'}}
    for row, scenario_name, has_context, code, args, kwargs, message, start, end in PY_ROWS:
        model = python_flow()
        x = model.symbols
        candidates = {
            'lookup_none': model.conjunction(x['ctx_none'], x['fallback_throws'], x['fallback_message_matches']),
            'aborter_throw': model.conjunction(model.neg(x['ctx_none']), x['abort_throws'], x['abort_message_matches']),
            'aborter_return': model.conjunction(model.neg(x['ctx_none']), model.neg(x['abort_throws']), x['fallback_throws'], x['fallback_message_matches'])}
        candidates = {name:term for name,term in candidates.items()
                      if (name == 'lookup_none') != has_context}
        checks = {}
        for name, candidate in candidates.items():
            scenario_suffix = {'lookup_none':'LOOKUP_NONE', 'aborter_throw':'ABORTER_THROW', 'aborter_return':'ABORTER_RETURN'}[name]
            matching = [s for s in contracts['python']['scenarios']
                        if f'O_{row}' in s['oracle_refs'] and s['id'].endswith(scenario_suffix)]
            if len(matching) != 1:
                raise ValueError(f'missing or ambiguous row scenario: {row}/{name}')
            scenario = matching[0]
            proposed = [k for k in contracts['python']['checks'] if scenario['id'] in k['scenario_refs']]
            if not proposed:
                raise ValueError('missing proposed check')
            checks[name] = {**check_candidate(model,candidate), 'scenario_ref':scenario['id'],
                           'check_refs':[k['id'] for k in proposed],
                           'formula_refs':[scenario['condition']['formula_ref'], 'F_PY_NONVACUOUS_OBSERVATION'],
                           'result_ref':f'bound-results.json#/python_rows/{row}/checks/{name}'}
        result['python_rows'][row] = {'oracle_ref': f'O_{row}', 'row_source': {'source_id':'test','start_line':start,'end_line':end},
            'fixed_input': {'scenario':scenario_name,'has_app_context':has_context,'code':code,'args':args,'kwargs':kwargs},
            'message_substring':message, 'exception_requirement':'Exception; not an exact subclass',
            'translation_assumption':'message-match predicates denote the displayed row substring; args forwarding is structural only',
            'formula_refs':['F_PY_ALL_ORACLES','F_PY_NONVACUOUS_OBSERVATION'],
            'checks': checks}
        for name, candidate in candidates.items():
            (output/f'{row}.{name}.smt2').write_text(smt2(model,[*model.background,model.guard,candidate]))
    result['artifact_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in [HERE/'models.py', HERE/'poc.py', HERE/'bind_results.py', HERE/'build_examples.py', HERE/'schema_draft.py', HERE/'results'/'solver-results.json']}
    (output/'bound-results.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__ == '__main__':
    if sys.argv[1:] == ['--parse-worker']:
        print(json.dumps(_parse_formulas_in_process(json.load(sys.stdin))))
    else:
        r = run()
        print(f"Validated {len(r['contracts'])} contracts; checked {sum(len(row['checks']) for row in r['python_rows'].values())} scoped paths over {len(r['python_rows'])} rows")
