"""Replay the restricted local constraint rule on one authenticated frozen case.

No model, target execution, dataset copying, or backend rebuild. The product
entry is explicitly task-provided; a prior result is evidence for comparison,
never an oracle or an input to the constraint rule.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from mock_spec.adapters.frozen_dataset import FrozenDataset  # noqa: E402
from local_constraints import analyze_local_constraints  # noqa: E402


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def replay(args: argparse.Namespace) -> dict:
    started = time.monotonic()
    inventory = json.loads(args.inventory.read_text())
    entries = [row for row in inventory['cases'] if row['case_id'] == args.case_id]
    if len(entries) != 1:
        raise ValueError('inventory must select exactly one case')
    entry = entries[0]
    if entry['language'] != 'python':
        raise ValueError('local rule supports Python only')
    baseline_bytes = args.baseline.read_bytes()
    baseline = json.loads(baseline_bytes)
    cases = [row for row in baseline['cases'] if row['identity']['case_id'] == args.case_id]
    if len(cases) != 1 or any(cases[0]['identity'].get(k) != entry[k]
                              for k in ('case_id', 'dataset', 'snapshot_id', 'language')):
        raise ValueError('baseline case identity differs from admitted inventory')
    old = cases[0]
    dataset = FrozenDataset(Path(inventory['registry_root']), dataset=entry['dataset'],
                            inventory_revision=entry['inventory_revision'])
    with dataset.load_case(entry['case_id']) as case:
        if case.snapshot_id != entry['snapshot_id']:
            raise ValueError('admitted snapshot differs from inventory')
        test = case.masked_test
        product = case.read_source(args.product_path)
        selected = [test, product]
        for source in selected:
            prior = [s for s in old['sources'] if s['logical_path'] == source.logical_path]
            if len(prior) != 1 or prior[0]['sha256'] != digest(source.text):
                raise ValueError('selected source identity differs from baseline')
        # No historical relations or human answers are passed into this rule.
        analysis = analyze_local_constraints(
            test_source=test.text, product_source=product.text,
            test_function=case.target_selector.rsplit('::', 1)[-1],
            test_function_line=args.test_function_line,
            product_function=args.product_function, product_function_line=args.product_line,
            call_line=args.call_line,
        )
        # Preserve the original observation IDs only for unambiguous matches.
        old_observations = old['result_contract']['observations']
        observation_mapping = {}
        if len(analysis['observations']) != len(old_observations):
            raise ValueError('local projection does not preserve all original observations')
        for observation in analysis['observations']:
            line = observation['location']['start_line']
            matches = [o for o in old_observations if o['location']['start_line'] == line]
            if len(matches) != 1 or matches[0]['kind'] != 'value_assertion':
                raise ValueError('observation identity cannot be mapped uniquely')
            local_id = observation['observation_id']
            observation_mapping[local_id] = matches[0]['observation_id']
            observation['local_observation_id'] = local_id
            observation['observation_id'] = matches[0]['observation_id']
        if set(observation_mapping.values()) != {o['observation_id'] for o in old_observations}:
            raise ValueError('local projection changed the observation identity set')
        for branch in analysis['branches']:
            boundary = branch.get('boundary_return', {})
            for field in ('requirements', 'test_obligations'):
                if field in boundary:
                    boundary[field] = [
                        {**requirement, 'observation_id': observation_mapping[requirement['observation_id']]}
                        for requirement in boundary[field]
                    ]
        sources = [{'source_id': s.source_id, 'logical_path': s.logical_path,
                    'sha256': digest(s.text), 'start_line': s.start_line, 'end_line': s.end_line}
                   for s in selected]
    relations = old['result_contract']['relations']
    old_returns = [r for r in relations if r['kind'] == 'normal_return_dependence']
    return {
        'schema_version': 'mock-spec.local-constraint-replay/v1',
        'identity': {k: entry[k] for k in
                     ('case_id', 'dataset', 'language', 'snapshot_id', 'inventory_revision')},
        'sources': sources,
        'binding': {'origin': 'task_provided', 'product_function': args.product_function,
                    'product_line': args.product_line, 'test_call_line': args.call_line,
                    'limitation': 'not a new automatic import/call binding capability'},
        'baseline': {'sha256': hashlib.sha256(baseline_bytes).hexdigest(),
                     'observation_ids': [r['observation_id'] for r in old['result_contract']['observations']],
                     'return_candidates': [{'source': r['from'], 'observation_ids': r['observation_ids']}
                                           for r in old_returns],
                     'semantic_validation': old['result_contract'].get('semantic_validation')},
        'observation_identity_mapping': observation_mapping,
        'analysis': analysis,
        'cost': {'wall_seconds': time.monotonic() - started,
                 'includes': 'admission, two selected source checks, AST analysis, baseline comparison',
                 'backend_rebuilt': False, 'model_requests': 0, 'target_executions': 0},
        'implementation_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in (Path(__file__), HERE / 'local_constraints.py')},
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inventory', type=Path, required=True)
    p.add_argument('--baseline', type=Path, required=True)
    p.add_argument('--case-id', required=True)
    p.add_argument('--product-path', required=True)
    p.add_argument('--product-function', required=True)
    p.add_argument('--product-line', type=int, required=True)
    p.add_argument('--test-function-line', type=int, required=True)
    p.add_argument('--call-line', type=int, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        raise FileExistsError('refusing to replace an existing replay artifact')
    result = replay(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


if __name__ == '__main__':
    main()
