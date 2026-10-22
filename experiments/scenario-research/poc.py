"""Offline cvc5 API experiment. Only trusted handwritten terms are executed."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

import cvc5
from cvc5 import Kind as K
from models import java_image, java_entrypoint, python_flow

HERE = Path(__file__).resolve().parent


def solver(model, abduct=False):
    s = cvc5.Solver(model.tm)
    s.setLogic('ALL')
    s.setOption('produce-models', 'true')
    s.setOption('tlimit-per', '3000')
    if abduct:
        s.setOption('produce-abducts', 'true')
        s.setOption('incremental', 'true')
    return s


def smt2(model, terms):
    # cvc5 serializes all terms; this is not an expression parser or translator.
    lines = ['(set-logic ALL)']
    for name, term in model.symbols.items():
        lines.append(f'(declare-fun {name} () {term.getSort()})')
    lines.extend(f'(assert {term})' for term in terms)
    return '\n'.join(lines) + '\n'


def query(model, terms):
    s = solver(model)
    start = time.perf_counter()
    try:
        for term in terms:
            s.assertFormula(term)
        result = s.checkSat()
        status = 'sat' if result.isSat() else ('unsat' if result.isUnsat() else 'unknown')
        record = {'status': status, 'raw_result': str(result), 'duration_ms': round((time.perf_counter()-start)*1000, 3)}
        if result.isSat():
            record['model'] = {name: str(s.getValue(t)) for name, t in model.symbols.items()}
        elif result.isUnknown():
            record['reason'] = str(result.getUnknownExplanation())
        return record
    except RuntimeError as exc:
        return {'status': 'error', 'error': str(exc), 'duration_ms': round((time.perf_counter()-start)*1000, 3)}


def check_candidate(model, candidate, guard=None):
    check_allowed(model, candidate)
    g = model.guard if guard is None else guard
    base = [*model.background, g, candidate]
    consistency = query(model, base)
    observation = query(model, [*base, model.goal])
    counterexample = query(model, [*base, model.neg(model.goal)])
    sufficient = (consistency['status'] == 'sat' and observation['status'] == 'sat'
                  and counterexample['status'] == 'unsat')
    return {'kind': 'candidate_checking', 'candidate': str(candidate), 'guard': str(g),
            'consistency': consistency, 'observation_reachability': observation,
            'counterexample': counterexample, 'locally_sufficient': sufficient,
            'whole_test_verified': False}


def free_constants(term):
    found = set()
    pending = [term]
    while pending:
        t = pending.pop()
        if t.getKind() == K.CONSTANT:
            found.add(str(t))
        pending.extend(t)
    return found


def check_allowed(model, term):
    illegal = free_constants(term) - model.controls
    if illegal:
        raise ValueError('noncontrollable symbols in H: ' + ', '.join(sorted(illegal)))


def grammar_for(model, s):
    b = model.tm.mkVar(model.tm.getBooleanSort(), 'H')
    g = s.mkGrammar([], [b])
    atoms = [model.symbols[n] for n in sorted(model.controls)
             if model.symbols[n].getSort().isBoolean()]
    if 'version' in model.controls:
        atoms.append(model.op(K.LEQ, model.symbols['version'], model.integer(8)))
    for atom in atoms:
        check_allowed(model, atom)
    g.addRules(b, atoms + [model.neg(b), model.op(K.AND, b, b), model.op(K.OR, b, b)])
    return g, [str(a) for a in atoms]


def abduction(model):
    s = solver(model, abduct=True)
    for term in [*model.background, model.guard]:
        s.assertFormula(term)
    grammar, atoms = grammar_for(model, s)
    record = {'kind': 'native_abduction', 'api': 'getAbduct/getAbductNext',
              'grammar_atoms': atoms, 'allowed_symbols': sorted(model.controls),
              'background': [str(t) for t in model.background], 'guard': str(model.guard),
              'goal': str(model.goal), 'candidates': [], 'calls': []}
    record['background_consistency'] = query(model, [*model.background, model.guard])
    if record['background_consistency']['status'] != 'sat':
        record['status'] = 'blocked_background_not_sat'
        return record
    start = time.perf_counter()
    previous = None
    try:
        for index in range(2):
            call_start = time.perf_counter()
            term = s.getAbduct(model.goal, grammar) if index == 0 else s.getAbductNext()
            record['calls'].append({'api': 'getAbduct' if index == 0 else 'getAbductNext',
                'duration_ms': round((time.perf_counter()-call_start)*1000, 3),
                'returned': str(term), 'is_null': term.isNull()})
            if term.isNull():
                record['status'] = 'candidate_found_next_unavailable' if record['candidates'] else 'no_abduct_returned'
                record['reason'] = 'null term; API does not distinguish exhaustion from resource limits here'
                break
            check_allowed(model, term)
            candidate = check_candidate(model, term)
            candidate['origin'] = 'cvc5_native_abduction'
            if previous is not None:
                candidate['equivalence_to_previous'] = query(model, [*model.background, model.guard,
                    model.op(K.XOR, previous, term)])
            record['candidates'].append(candidate)
            previous = term
        else:
            record['status'] = 'completed'
    except RuntimeError as exc:
        record['status'] = 'error'
        record['error'] = str(exc)
    record['duration_ms'] = round((time.perf_counter()-start)*1000, 3)
    return record


def bounded_abduction(name, timeout=12):
    """A wall-clock bound independent of the solver's internal query limit."""
    start = time.perf_counter()
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                 '--abduct', name], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {'status': 'timeout', 'kind': 'native_abduction', 'candidates': [],
                'duration_ms': round((time.perf_counter()-start)*1000, 3), 'wall_limit_s': timeout}
    if result.returncode:
        return {'status': 'error', 'kind': 'native_abduction', 'candidates': [],
                'exit_code': result.returncode, 'stderr': result.stderr}
    return json.loads(result.stdout)


def round_trip(model):
    source = smt2(model, [*model.background, model.guard, model.candidate])
    s = cvc5.Solver()
    s.setOption('tlimit-per', '3000')
    parser = cvc5.InputParser(s)
    parser.setStringInput(cvc5.InputLanguage.SMT_LIB_2_6, source, 'trusted-generated.smt2')
    count = 0
    while True:
        command = parser.nextCommand()
        if command.isNull():
            break
        # Guard command surface before invocation; input is locally generated only.
        if command.getCommandName() not in {'set-logic', 'declare-fun', 'assert'}:
            raise ValueError('unsupported command')
        command.invoke(s, parser.getSymbolManager())
        count += 1
    parsed = str(s.checkSat())
    original = query(model, [*model.background, model.guard, model.candidate])['status']
    return {'status': 'completed', 'original': original, 'round_trip': parsed,
            'matches': parsed == original, 'commands': count,
            'sha256': hashlib.sha256(source.encode()).hexdigest()}, source


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    report = {'authoring_mode': 'handcrafted_from_visible_input',
              'automatic_source_translation': False, 'automatic_agent_inference': False,
              'tool': {'cvc5': cvc5.__version__, 'solver_version': cvc5.Solver().getVersion().decode(),
                       'python': platform.python_version(),
                       'platform': platform.platform(), 'solver_query_limit_ms': 3000},
              'models': {}}
    for make in (java_image, java_entrypoint, python_flow):
        m = make()
        trip, source = round_trip(m)
        (output / f'{m.name}.smt2').write_text(source)
        report['models'][m.name] = {'round_trip': trip,
            'manual_candidate': check_candidate(m, m.candidate), 'abduction': bounded_abduction(m.name)}
    report['implementation_sha256'] = {name: hashlib.sha256((HERE/name).read_bytes()).hexdigest()
                                       for name in ('models.py', 'poc.py')}
    (output / 'solver-results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({name: {'checking': r['manual_candidate']['locally_sufficient'],
                           'abduction': r['abduction']['status']}
                      for name, r in report['models'].items()}, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=HERE/'results')
    parser.add_argument('--abduct', choices=['java_image', 'java_entrypoint', 'python_flow'])
    args = parser.parse_args()
    if args.abduct:
        make = {'java_image': java_image, 'java_entrypoint': java_entrypoint, 'python_flow': python_flow}[args.abduct]
        print(json.dumps(abduction(make())))
    else:
        run(args.output)
