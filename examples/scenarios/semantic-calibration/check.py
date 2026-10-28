"""Bounded structural replay of reference drafts; no semantic or model execution."""
import argparse
import json
from pathlib import Path

from mock_spec.adapters.python import load_task
from mock_spec.scenario import validate_contract
from mock_spec.schemas import UnifiedTask


ROOT = Path(__file__).resolve().parent
DEFAULT_SNAPSHOT = Path(
    '/home/haon/projects/Stub-Tester/.omx/developer-realistic-registry-max-fix23/'
    'formal-snapshots/python-benchmark-new/'
    '241ef457f8d071c23e6acd658b684b11b54f13fa955e097d6bebe35dc755f47d'
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real', action='store_true')
    parser.add_argument('--snapshot', type=Path, default=DEFAULT_SNAPSHOT)
    args = parser.parse_args()
    if args.real:
        task = load_task(args.snapshot, case_id='flask_308')
        validate_contract((ROOT / 'flask.contract.json').read_text(), task)
        print('flask_308: reviewed Adapter + structure/identity/source/quote PASS')
    else:
        bundle = json.loads((ROOT / 'synthetic.json').read_text())
        for entry in bundle['references']:
            task = UnifiedTask.from_dict(entry['task'])
            validate_contract(entry['contract'], task)
            print(f"{entry['reference']}: synthetic structure/identity/source/quote PASS")
    print('Semantic sufficiency is a separate review; no target test was executed.')


if __name__ == '__main__':
    main()
