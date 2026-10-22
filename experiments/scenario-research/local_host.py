"""Offline research boundary: proposals name host-owned facts, never formulas/paths.

Construction is trusted preparation code. Only ``propose`` and ``bind_contract``
are proposal-facing methods. A manually assembled ReviewedInput is not admission.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from inputs import load_java, load_python
from local_patterns import extract_patterns
from local_solver import check_local_model


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode()).hexdigest()


_INVERSE = {'le': 'gt', 'lt': 'ge', 'gt': 'le', 'ge': 'lt', 'eq': 'ne',
            'ne': 'eq', 'is_none': 'is_not_none', 'is_not_none': 'is_none'}


class LocalResearchHost:
    """Host-only registry over an Adapter-admitted input (or labeled fixture)."""

    @classmethod
    async def prepare(cls, language):
        """Admit only the two frozen inputs through the existing audited adapters."""
        if language not in {'java', 'python'}:
            raise ValueError('choose a reviewed language, not a path or supplied task')
        reviewed = (load_java if language == 'java' else load_python)()
        return await cls._prepare(reviewed, synthetic=False)

    @classmethod
    async def prepare_synthetic(cls, reviewed):
        """Explicit host-only mechanism fixture path; never claims audited admission."""
        return await cls._prepare(reviewed, synthetic=True)

    @classmethod
    async def _prepare(cls, reviewed, *, synthetic):
        actual_hashes = {sid: hashlib.sha256(reviewed.sources.get(sid).text.encode()).hexdigest()
                         for sid in reviewed.source_sha256}
        if actual_hashes != reviewed.source_sha256:
            raise ValueError('source identity does not match current source text')
        self = cls()
        self._input = {
            'case_id': reviewed.task.case_id,
            'language': reviewed.task.language,
            'task_sha256': identity(reviewed.task.to_dict()),
            'source_sha256': dict(reviewed.source_sha256),
            'input_policy': reviewed.task.input_policy.to_dict(),
            'admission': 'synthetic_mechanism_fixture' if synthetic else 'reviewed_adapter',
        }
        self._extraction = await extract_patterns(reviewed)
        self._patterns = {p['pattern_id']: deepcopy(p)
                          for p in self._extraction['patterns']}
        self._checks = {}
        return self

    def catalog(self):
        return deepcopy({'input': self._input, 'extraction': self._extraction})

    async def propose(self, proposal):
        """Check a local observation; this interface cannot configure a mock.

        Candidate choices are existing source-condition polarity, exact integer
        return, or None identity. No oracle/output/external axiom fields exist.
        """
        fields = {'source_id', 'pattern_id', 'question', 'goal_branch', 'candidate'}
        if not isinstance(proposal, dict) or set(proposal) != fields:
            raise ValueError('proposal fields must be exactly the allowed reference/question fields')
        if proposal['question'] not in {'check_local_branch', 'infer_local_branch'}:
            raise ValueError('only local returned-value questions are allowed; mock control unresolved')
        if type(proposal['goal_branch']) is not bool:
            raise ValueError('goal_branch must be boolean')
        if not isinstance(proposal['pattern_id'], str):
            raise ValueError('invalid pattern reference')
        pattern = self._patterns.get(proposal['pattern_id'])
        if pattern is None or proposal['source_id'] != self._extraction['source_id']:
            raise ValueError('unknown source/pattern reference')
        if pattern['support_status'] != 'supported':
            return {'status': pattern['support_status'], 'pattern': deepcopy(pattern),
                    'whole_test_verified': False, 'locally_sufficient': False}
        term = deepcopy(pattern['term'])
        choices = proposal['candidate']
        if not isinstance(choices, list) or len(choices) > 16:
            raise ValueError('candidate must be a bounded list')
        if proposal['question'] == 'infer_local_branch' and choices:
            raise ValueError('inference takes no submitted candidate')
        candidates = []
        for choice in choices:
            if not isinstance(choice, dict) or set(choice) != {'kind', 'value'}:
                raise ValueError('invalid candidate behavior fields')
            kind, value = choice['kind'], choice['value']
            if kind == 'condition' and type(value) is bool:
                candidates.append({'operator': term['operator'] if value else _INVERSE[term['operator']],
                                   'literal': term['literal']})
            elif kind == 'return_integer' and term['sort'] == 'Int' and type(value) is int:
                if not -(2**63) <= value < 2**63:
                    raise ValueError('integer candidate exceeds research limit')
                candidates.append({'operator': 'eq', 'literal': value})
            elif kind == 'return_is_none' and term['sort'] == 'Bool' and type(value) is bool:
                candidates.append({'operator': 'is_none' if value else 'is_not_none', 'literal': None})
            else:
                raise ValueError('unsupported candidate or noncontrollable symbol')
        model = {
            'input': self._input, 'source_id': proposal['source_id'],
            'pattern': pattern, 'term': term,
            'scenario': 'condition reached; dependency invocation returned normally',
            'goal': {'condition_branch': proposal['goal_branch']},
            'symbol': {'name': 'response', 'sort': term['sort'],
                       'meaning': 'return_is_none' if term['sort'] == 'Bool' else 'normal_nonnull_integer_return',
                       'origin': pattern['dependency_call'],
                       'controllability': pattern['controllability'],
                       'allowed_use': 'local observation constraint only; no mock configuration'},
            'assumptions': [pattern['normal_return_assumption'],
                            'local condition is reached; enclosing paths are not proved'],
            'not_formalized': ['exception propagation', 'test oracle', 'MUT final output',
                               'external library behavior', 'global necessity', 'mock realization'],
        }
        model_id = identity(model)
        result = await check_local_model(term, candidates,
                                        goal_branch=proposal['goal_branch'],
                                        infer=proposal['question'] == 'infer_local_branch')
        artifact = {'model_id': model_id, 'model': model, 'proposal': deepcopy(proposal),
                    'solver': result, 'whole_test_verified': False}
        check_id = identity(artifact)
        artifact['check_id'] = check_id
        self._checks[check_id] = deepcopy(artifact)
        return deepcopy(artifact)

    def contract(self, check_id):
        """Host-produced research contract; formulas are the solver's own output."""
        artifact = self._checks[check_id]
        return deepcopy({'model_id': artifact['model_id'],
                         'formulas': artifact['solver'].get('formulas'),
                         'formula_identity': artifact['solver'].get('formula_identity')})

    def bind_contract(self, check_id, contract):
        """Reject mismatches before attaching a host-owned check result."""
        if check_id not in self._checks or contract != self.contract(check_id):
            raise ValueError('contract/check formula or model mismatch')
        artifact = self._checks[check_id]
        if not artifact['solver'].get('formulas'):
            raise ValueError('no solver formula evidence to bind')
        return {'contract': deepcopy(contract), 'check': deepcopy(artifact),
                'binding': 'exact_host_model_and_solver_terms'}
