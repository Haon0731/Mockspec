"""Independent tiny semantic references plus scripted orchestration evidence.

The answers below are deliberately handwritten. They validate the controller's
preservation and rejection behavior, not the reasoning quality of a live model.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import pytest

STAGED = Path(__file__).parents[1] / "experiments" / "staged-inference"
sys.path.insert(0, str(STAGED))

from controller import StagedRunConfig, execute_staged_run  # noqa: E402
from investigation import InvestigationLedger  # noqa: E402
from stage_protocol import InvestigationRequest, QuestionUpdate  # noqa: E402
from query import QueryExecutor  # noqa: E402
from scripted import FixedScriptModel, synthetic_task  # noqa: E402
from mock_spec.schemas import UnifiedTask  # noqa: E402
from mock_spec.source_repository import ReviewedRepository  # noqa: E402
from mock_spec.validation import SourceStore  # noqa: E402
from mock_spec.scenario import ScenarioContract  # noqa: E402
from specification import validate_semantics  # noqa: E402


QUESTION = "What does the real adjust do to the product?"
IMPACT = "The final goal depends on adjust; product=100 alone is not sufficient."


def product_reference(*, adjust=False, resolved=False, dynamic_id=None):
    task = synthetic_task()
    task["case_id"] = "synthetic_relational_product"
    mut = "def target(k):\n    p = left(k)\n    q = right(k)\n    if q == 0: return 0\n    return "
    mut += "adjust(p * q)" if adjust else "p * q"
    for source, text, line in (
        (task["mut_source"], mut, 1),
        (task["masked_test"], 'def test_target():\n    assert target("business") == 100', 1),
        (task["supporting_sources"][0],
         "# left(k): positive int p; right(k): nonnegative int q.\n"
         "# Only left/right responses are replaceable; no side effects or exceptions.\n"
         "# Same business key; no other cross-return relation; integers do not overflow.", 1),
    ):
        source.update(text=text, start_line=line, end_line=line + len(text.splitlines()) - 1)
    task["permitted_interfaces"] = []
    evidence = [
        {"id": "mut", "source_id": "mut:0", "start_line": 1, "end_line": 5,
         "rationale": "Real control flow and result computation."},
        {"id": "test", "source_id": "test:0", "start_line": 2, "end_line": 2,
         "rationale": "Fixed business key and original result requirement."},
        {"id": "api", "source_id": "support:0", "start_line": 1, "end_line": 3,
         "rationale": "Explicit dependency conditions and replacement scope."},
    ]
    partial = adjust and not resolved
    relation = "adjust(p * q) == 100" if partial else "p * q == 50" if adjust else "p * q == 100"
    statement = f"left(k) returns positive integer p; right(k) returns nonnegative integer q; q > 0 and {relation}; no exceptions or side effects."
    condition = "k is the fixed business key; only left/right are replaceable; real MUT and integer semantics are preserved."
    claim = {"statement": statement, "status": "required", "scenario_ids": ["product"],
             "condition": condition, "evidence_ids": ["mut", "test", "api"]}
    contract = {
        "schema_version": "mock-spec.scenario/v0.1", "case_id": task["case_id"], "language": "python",
        "test_objectives": [{**claim, "statement": "target('business') must normally return 100.",
                             "evidence_ids": ["test"]}],
        "scenario_hypotheses": [{"id": "product", "description": "Nonzero divisor branch.",
                                  "condition": condition, "status": "candidate", "evidence_ids": ["mut"]}],
        "dependency_constraints": [claim], "expected_interactions": [],
        "verification_candidates": [], "evidence": evidence,
        "uncertainty": ([{**claim, "statement": QUESTION, "status": "unknown",
                          "evidence_ids": ["mut"]}] if partial else []),
    }
    premises = [{"id": key, "kind": kind, "statement": text, "evidence_ids": [key],
                 "assumption_impact": None} for key, kind, text in (
        ("mut", "implementation_fact", "q=0 returns 0; otherwise the real computation consumes p*q."),
        ("test", "test_requirement", "The fixed key must produce 100."),
        ("api", "dependency_semantics", "p positive int; q nonnegative int; stated boundary and no additional relation."),
    )]
    if resolved:
        assert dynamic_id is not None
        evidence.append({"id": "adjust", "source_id": dynamic_id, "start_line": 1, "end_line": 2,
                         "rationale": "Actual observed adjust doubles its input."})
        claim["evidence_ids"].append("adjust")
        premises.append({"id": "adjust", "kind": "implementation_fact", "statement": "adjust(x)=2*x.",
                         "evidence_ids": ["adjust"], "assumption_impact": None})
    premise_ids = [p["id"] for p in premises]
    variation = "Different integer factor pairs satisfying the relationship are legal; no pair is uniquely required."
    semantics = {
        "schema_version": "staged-spec-semantics/v0.1", "delivery_status": "partial" if partial else "ready",
        "fixed_inputs": [{"goal_indexes": [0], "statement": "The same business key is passed to both dependencies.", "evidence_ids": ["test", "mut"]}],
        "replacement_boundary": [{"id": "ports", "target": "left(k), right(k) in target",
                                  "replaceable_behavior": "The two return values, within the given interface constraints.",
                                  "preserved_behavior": "Real branch, arithmetic, and adjust when present.",
                                  "status": "confirmed", "evidence_ids": ["api", "mut"],
                                  "premise_ids": ["api", "mut"], "unknown_ids": []}],
        "preserved_real_logic": [{"statement": "Keep target, its zero guard, arithmetic, and any adjust real.",
                                  "evidence_ids": ["mut"], "premise_ids": ["mut"]}],
        "premises": premises, "constraint_basis": [{"constraint_index": 0, "premise_ids": premise_ids}],
        "branches": [{"scenario_id": "product", "disposition": "unresolved" if partial else "allowed",
                      "rationale": "Whether values can satisfy the target is unknown." if partial else "API conditions and the real computation support the relation.",
                      "evidence_ids": ["mut", "test", "api"], "premise_ids": premise_ids,
                      "unknown_ids": ["adjust-gap"] if partial else []}],
        "goal_coverage": [{"goal_index": 0, "disposition": "blocked" if partial else "constrained",
                           "constraint_indexes": [0], "unknown_ids": ["adjust-gap"] if partial else [],
                           "rationale": IMPACT if partial else "Substitution into the real branch yields the original result.",
                           "evidence_ids": ["mut", "test"], "premise_ids": premise_ids}],
        "unknowns": ([{"id": "adjust-gap", "question": QUESTION, "impact": IMPACT,
                       "disposition": "blocking", "goal_indexes": [0], "constraint_indexes": [0],
                       "scenario_ids": ["product"], "formal_uncertainty_indexes": [0],
                       "rationale": "The real transformation has not been observed."}] if partial else []),
        "allowed_variations": ([] if partial else [{"id": "factor-pairs", "statement": variation,
                                                   "scenario_ids": ["product"], "constraint_indexes": [0],
                                                   "evidence_ids": ["api", "mut", "test"], "premise_ids": premise_ids}]),
        "conflicts": [],
    }
    construct = {
        "schema_version": "staged-construct/v0.3", "result_status": "INCONCLUSIVE" if partial else "SELECTED",
        "selected_scenario_id": None if partial else "product", "selection_rationale": "Preserve the original goal and the interface constraints.",
        "claim_basis": [{"claim": "The relation follows under the declared input and interface conditions.",
                         "basis": "scenario_inference"}], "critical_behaviors": [],
        "allowed_variations": [] if partial else [variation], "unresolved": [IMPACT] if partial else [],
        "nonblocking_unknowns": [], "contract": contract, "spec": semantics, "investigation_requests": [],
    }
    # Existing strict research mappings remain explicit in repository-backed runs.
    construct["claim_basis"] = [
        {"claim": text, "basis": "scenario_inference", "target": target, "target_index": index,
         "semantic_role": "dependency_contract", "evidence_ids": ["mut", "test", "api"],
         "support": "evidence", "assumption_reason": None}
        for target, index, text in [("selection_rationale", 0, construct["selection_rationale"]),
                                    *[("allowed_variation", i, text) for i, text in enumerate(construct["allowed_variations"])],
                                    *[("unresolved", i, text) for i, text in enumerate(construct["unresolved"])]]
    ]
    return task, construct


def extract(construct):
    return {"schema_version": "staged-extract/v0.2", "original_goals": [locator("test:0", 2, 2)],
            "candidates": [{"scenario_id": "product", "description": "Initial revisable product hypothesis.",
                            "evidence": [locator("mut:0", 1, 5)]}], "unresolved": []}


def locator(source, start, end):
    return {"source_id": source, "start_line": start, "end_line": end}


def finish(*, update=None, remaining=None, assessment=None):
    return {"schema_version": "staged-query/v0.4", "decision": "finish_queries", "query_attempt": None,
            "previous_receipt_assessment": assessment, "impact": "Assess the original goal and dependency relation.",
            "question_updates": [] if update is None else [update], "candidate_updates": [],
            "stop_explanation": {"reason": "no_further_evidence" if remaining else "evidence_sufficient",
                                 "rationale": "Evidence supports the declared conclusion and limits.",
                                 "evidence": [locator("test:0", 2, 2)], "remaining_unknowns": remaining or []}}


def question_update(*, state, evidence, acquisition, sequences=(), question_id="q1"):
    return {"question_id": question_id, "question": QUESTION, "goal_indexes": [0], "impact": IMPACT,
            "state": state, "rationale": "The original result depends on the real transformation.",
            "evidence": evidence, "acquisition": acquisition, "attempted_sequences": list(sequences)}


def contrast(*, investigate=False):
    return {"schema_version": "staged-contrast/v0.2", "status": "none_identified", "execution_status": "not_executed",
            "alternative": None, "bypass_behavior": None, "evidence_ids": [], "distinguishing_observation": None,
            "revision_requested": False, "revision_reason": None, "support": "assumption",
            "assumption_reason": "Static analysis only, no global proof.", "semantic_role": "dependency_contract",
            "weakness_review": "Wrong products and negative pairs violate the explicit relationship or interface.",
            "overconstraint_review": "Multiple factor pairs remain legal; concrete example values are not obligations.",
            "observation_limits": "No execution; inability to find a counterexample is not proof.",
            "investigation_requests": [{"question": QUESTION, "goal_indexes": [0], "impact": IMPACT}] if investigate else []}


def deliver(construct):
    return {"schema_version": "staged-deliver/v0.3", "result_status": construct["result_status"],
            "selected_scenario_id": construct["selected_scenario_id"], "contract": construct["contract"]}


def run(tmp_path, task, outputs, *, repository=None, max_model_calls=20):
    model = FixedScriptModel(outputs)
    result = asyncio.run(execute_staged_run(
        task if repository is None else None, output_dir=tmp_path / "run", model=model,
        config=StagedRunConfig(variant="C", max_model_calls=max_model_calls),
        task_loader=None if repository is None else lambda: (UnifiedTask.from_dict(task), repository),
    ))
    return result, model


def test_zero_query_product_spec_retains_multiple_legal_mocks(tmp_path):
    task, constructed = product_reference()
    result, model = run(tmp_path, task, [extract(constructed), finish(), constructed, contrast(), deliver(constructed)])
    assert result["record"]["error"] is None
    artifact = json.loads((tmp_path / "run" / "dependency_spec.json").read_text())
    assert artifact["semantics"]["delivery_status"] == "ready"
    assert result["record"]["query_accounting"]["observed_attempts"] == 0
    # Independent finite reference checks: this is mathematical fixture evidence,
    # not a general parser/evaluator of natural-language model output.
    emitted = artifact["contract"]["dependency_constraints"][0]["statement"]
    assert emitted == constructed["contract"]["dependency_constraints"][0]["statement"]
    assert "q > 0 and p * q == 100" in emitted
    accepted = {(p, q) for p, q in [(20, 5), (25, 4), (20, 4), (-20, -5), (10, 0)]
                if p > 0 and q >= 0 and q > 0 and p * q == 100}
    assert accepted == {(20, 5), (25, 4)}
    assert artifact["regression_reference"]["status"] == "reference_only"
    assert "query_log" not in artifact
    assert model.calls == 5


def test_unavailable_adjust_delivers_partial_without_false_product_equation(tmp_path):
    task, constructed = product_reference(adjust=True)
    unknown = {"question": QUESTION, "impact": IMPACT, "blocking": True}
    stopped = finish(update=question_update(state="open", evidence=[locator("mut:0", 1, 5)], acquisition="unavailable", question_id=None),
                     remaining=[unknown])
    result, _ = run(tmp_path, task, [extract(constructed), stopped, constructed, contrast(), deliver(constructed)])
    assert result["record"]["error"] is None
    spec = result["spec"]
    assert spec.semantics.delivery_status == "partial"
    assert spec.semantics.goal_coverage[0].disposition == "blocked"
    assert "adjust(p * q) == 100" in spec.contract.dependency_constraints[0].statement
    assert "q > 0 and p * q == 100" not in spec.contract.dependency_constraints[0].statement
    assert result["record"]["result_status"] == "INCONCLUSIVE"


def repository_for(tmp_path, task):
    root = tmp_path / "snapshot"
    (root / "tree").mkdir(parents=True)
    source = b"def adjust(value):\n    return value * 2\n"
    (root / "tree" / "helper.py").write_bytes(source)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        return ReviewedRepository(fd, task=UnifiedTask.from_dict(task), snapshot_id="synthetic-adjust",
                                  discovery_root="tree", files_by_logical_path={
                                      "helper.py": ("tree/helper.py", hashlib.sha256(source).hexdigest())})
    finally:
        os.close(fd)


@pytest.mark.parametrize("origin", ["CONSTRUCT", "CONTRAST"])
def test_material_gap_reopens_queries_and_changes_the_relation(tmp_path, origin):
    task, partial = product_reference(adjust=True)
    repository = repository_for(tmp_path, task)
    dynamic_id = repository.source_id_for("helper.py")
    _, final = product_reference(adjust=True, resolved=True, dynamic_id=dynamic_id)
    first = deepcopy(final)
    # The preliminary construct can be a proposal that discovers its own gap.
    # The CONTRAST case starts from a mechanically valid but semantically weak
    # proposal, demonstrating why mechanical validation cannot prove sufficiency.
    if origin == "CONSTRUCT":
        first = deepcopy(partial)
        first["investigation_requests"] = [{"question": QUESTION, "goal_indexes": [0], "impact": IMPACT}]
    else:
        _, first = product_reference()
        first["contract"]["case_id"] = task["case_id"]
    outputs = [extract(first), finish(), first]
    if origin == "CONTRAST":
        outputs.append(contrast(investigate=True))
    assessment = {"impact": "supports", "rationale": "Observed real adjust changes the required product."}
    for index, args in enumerate([
        {"operation": "search", "path_pattern": "helper.py"},
        {"operation": "read", "source_id": dynamic_id, "start_line": 1, "end_line": 2},
    ]):
        outputs.append({"schema_version": "staged-query/v0.4", "decision": "request_query",
                        "query_attempt": {"question": QUESTION, "scenario_id": "original_task",
                                          "tool": "inspect_source", "arguments_json": json.dumps(args)},
                        "previous_receipt_assessment": None if index == 0 else assessment,
                        "impact": IMPACT, "stop_explanation": None,
                        "question_updates": [], "candidate_updates": []})
    outputs += [finish(update=question_update(state="resolved", evidence=[locator(dynamic_id, 1, 2)],
                                             acquisition="not_needed", sequences=[1, 2]), assessment=assessment),
                final, contrast(), deliver(final)]
    result, _ = run(tmp_path, task, outputs, repository=repository)
    assert result["record"]["error"] is None, result["record"]["error"]
    assert result["spec"].semantics.delivery_status == "ready"
    assert "p * q == 50" in result["spec"].contract.dependency_constraints[0].statement
    assert [item["origin"] for item in result["record"]["continuation_log"]] == [origin]
    assert result["record"]["questions"][0]["state"] == "resolved"
    assert result["record"]["query_accounting"]["observed_attempts"] == 2
    assert result["record"]["format_repair_allowance"]["state"] == "available"


def test_cannot_erase_original_goal_question_to_stop(tmp_path):
    task, constructed = product_reference()
    initial = extract(constructed)
    initial["unresolved"] = [QUESTION]
    result, model = run(tmp_path, task, [initial, finish(), constructed])
    assert result["record"]["stop_reason"] == "output_invalid"
    assert model.calls == 2
    assert result["spec"] is None


def test_continuation_uses_remaining_budget_without_publishing_stale_spec(tmp_path):
    task, constructed = product_reference()
    result, model = run(tmp_path, task, [extract(constructed), finish(), constructed, contrast(investigate=True)],
                        max_model_calls=4)
    assert model.calls == 4
    assert result["record"]["stop_reason"] == "budget_exhausted"
    assert result["record"]["result_status"] == "INCONCLUSIVE"
    assert result["spec"] is None
    assert not (tmp_path / "run" / "dependency_spec.json").exists()


def test_scope_external_unknown_does_not_force_an_irrelevant_query(tmp_path):
    task, constructed = product_reference()
    question, impact = "What happens for other business keys?", "Other keys do not affect the fixed original goal."
    constructed["contract"]["uncertainty"] = [{
        "statement": question, "status": "unknown", "scenario_ids": ["product"],
        "condition": "Different keys, outside this specification.", "evidence_ids": ["test"],
    }]
    constructed["spec"]["unknowns"] = [{
        "id": "other-keys", "question": question, "impact": impact, "disposition": "out_of_scope",
        "goal_indexes": [], "constraint_indexes": [], "scenario_ids": [],
        "formal_uncertainty_indexes": [0], "rationale": "The original test fixes one business key.",
    }]
    stopped = finish(remaining=[{"question": question, "impact": impact, "blocking": False}])
    stopped["stop_explanation"]["reason"] = "unknowns_nonblocking"
    stopped["question_updates"] = [{
        "question_id": None, "question": question, "goal_indexes": [0], "impact": impact,
        "state": "nonblocking", "rationale": "The test's fixed input excludes other keys.",
        "evidence": [locator("test:0", 2, 2)], "acquisition": "not_needed", "attempted_sequences": [],
    }]
    result, model = run(tmp_path, task, [extract(constructed), stopped, constructed, contrast(), deliver(constructed)])
    assert result["record"]["error"] is None
    assert result["spec"].semantics.delivery_status == "ready"
    assert model.calls == 5
    assert not result["record"]["query_log"]


def test_supported_alternative_branches_are_not_conjoined_or_ambiguous(tmp_path):
    task, constructed = product_reference()
    task["mut_source"].update(
        text="def target(k):\n    value = cache.get(k)\n    if value is not None:\n        return value\n    return backend.fetch(k)")
    task["supporting_sources"][0]["text"] = (
        "# cache.get(k) returns int or None; backend.fetch(k) returns int.\n"
        "# Only these responses are replaceable; no side effects or exceptions.\n"
        "# A hit or miss is permitted; no requirement to fetch or refill."
    )
    contract, semantics = constructed["contract"], constructed["spec"]
    contract["scenario_hypotheses"] = [
        {"id": branch, "description": description, "condition": condition, "status": "candidate",
         "evidence_ids": ["mut", "api"]}
        for branch, description, condition in [
            ("hit", "Cache hit", "Fixed business key and cache response is not None."),
            ("miss", "Cache miss", "Fixed business key and cache response is None."),
        ]
    ]
    contract["test_objectives"][0]["scenario_ids"] = ["hit", "miss"]
    contract["dependency_constraints"] = [
        {"statement": statement, "status": "required", "scenario_ids": [branch],
         "condition": condition, "evidence_ids": ["mut", "test", "api"]}
        for branch, statement, condition in [
            ("hit", "cache.get(k) returns integer 100; no exception or side effects.", "Cache hit for the fixed business key."),
            ("miss", "cache.get(k) returns None and backend.fetch(k) returns integer 100; no exception or side effects.",
             "Cache miss for the fixed business key."),
        ]
    ]
    semantics["premises"][0]["statement"] = "Real code returns cache value on hit and backend value on miss."
    semantics["premises"][2]["statement"] = "Both hit and miss are permitted by the provided interface and scope; no forced fetch."
    semantics["replacement_boundary"][0].update(target="cache.get(k), backend.fetch(k)",
                                                replaceable_behavior="Responses under the supplied API conditions.",
                                                preserved_behavior="The real conditional and return logic.")
    semantics["preserved_real_logic"][0]["statement"] = "The real conditional selects a complete branch."
    semantics["constraint_basis"] = [{"constraint_index": index, "premise_ids": ["mut", "test", "api"]} for index in (0, 1)]
    semantics["branches"] = [
        {"scenario_id": branch, "disposition": "allowed", "rationale": "The explicit API and task allow this whole branch.",
         "evidence_ids": ["api", "mut", "test"], "premise_ids": ["api", "mut", "test"], "unknown_ids": []}
        for branch in ("hit", "miss")
    ]
    semantics["goal_coverage"][0]["constraint_indexes"] = [0, 1]
    variation = "Choose either complete allowed branch, honoring all of its applicable constraints."
    semantics["allowed_variations"][0].update(statement=variation, scenario_ids=["hit", "miss"], constraint_indexes=[0, 1])
    constructed.update(result_status="ALTERNATIVES", selected_scenario_id=None, allowed_variations=[variation])
    initial = extract(constructed)
    stopped = finish()
    stopped["candidate_updates"] = [
        {"scenario_id": branch, "description": f"Evidence-supported {branch} alternative.",
         "evidence": [locator("mut:0", 1, 5), locator("support:0", 1, 3)]}
        for branch in ("hit", "miss")
    ]
    result, _ = run(tmp_path, task, [initial, stopped, constructed, contrast(), deliver(constructed)])
    assert result["record"]["error"] is None
    assert result["record"]["result_status"] == "ALTERNATIVES"
    assert result["spec"].semantics.delivery_status == "ready"
    constraints = result["spec"].contract.dependency_constraints
    assert constraints[0].scenario_ids == ["hit"] and constraints[1].scenario_ids == ["miss"]
    assert all(branch.disposition == "allowed" for branch in result["spec"].semantics.branches)
    assert "returns None" not in constraints[0].statement


@pytest.mark.parametrize("mutation", ["duplicate", "rebind", "future_receipt", "unavailable_without_basis"])
def test_question_ledger_rejects_explicit_inconsistencies(tmp_path, mutation):
    task, constructed = product_reference()
    initial = extract(constructed)
    initial["unresolved"] = [QUESTION]
    updated = question_update(state="resolved", evidence=[locator("test:0", 2, 2)], acquisition="not_needed")
    if mutation == "duplicate":
        updated["question_id"] = None
    elif mutation == "rebind":
        updated["goal_indexes"] = [1]
    elif mutation == "future_receipt":
        updated["attempted_sequences"] = [1]
    else:
        updated.update(state="open", evidence=[], acquisition="unavailable")
    stopped = finish(update=updated)
    result, model = run(tmp_path, task, [initial, stopped, constructed])
    assert result["record"]["stop_reason"] == "output_invalid"
    assert model.calls == 2
    assert result["spec"] is None


def test_new_partial_construct_survives_budget_stop_as_limited_consumer_artifact(tmp_path):
    task, partial = product_reference(adjust=True)
    result, model = run(tmp_path, task, [extract(partial), finish(), partial], max_model_calls=3)
    assert model.calls == 3
    assert result["record"]["stop_reason"] == "budget_exhausted"
    assert result["contract"] is None  # The legacy completion artifact is not published.
    assert result["spec"].semantics.delivery_status == "partial"
    assert result["spec"].exploration.reason == "budget_exhausted"
    assert result["spec"].semantics.unknowns[0].question == QUESTION
    assert result["record"]["task_completion"] == "partial"
    assert result["record"]["partial_spec_checkpoint"]["validated_at_model_call"] == 3
    assert (tmp_path / "run" / "dependency_spec.json").is_file()


def test_revision_new_blocking_unknown_reopens_investigation_without_explicit_request(tmp_path):
    task, partial = product_reference(adjust=True)
    _, weak = product_reference()
    revision_check = contrast()
    revision_check.update(
        status="proposed", alternative="The currently stated product ignores real adjust.",
        bypass_behavior="The final result may differ after the unobserved transformation.",
        evidence_ids=["mut", "test"], distinguishing_observation="Observe adjust's actual mapping.",
        revision_requested=True, revision_reason="The product equation is not sufficient without adjust.",
        support="evidence", assumption_reason=None,
    )
    revision = {
        "schema_version": "staged-revise/v0.2", "revised_construct": partial,
        "modified_objects": ["contract", "spec", "result_status", "selected_scenario_id",
                             "unresolved", "allowed_variations", "claim_basis"],
        "evidence_reason": "Preserve the unobserved real computation's effect on the original objective.",
        "evidence_ids": ["mut", "test"], "request_another_revision": False,
    }
    result, model = run(tmp_path, task, [extract(weak), finish(), weak, revision_check, revision], max_model_calls=5)
    assert model.calls == 5
    assert result["record"]["stop_reason"] == "budget_exhausted", result["record"]["error"]
    assert result["record"]["continuation_log"][0]["origin"] == "REVISE"
    assert result["spec"].semantics.delivery_status == "partial"
    assert result["record"]["questions"][0]["state"] == "open"


def test_question_citing_combined_read_ranges_requires_both_receipts(tmp_path):
    task_data, _ = product_reference(adjust=True)
    task = UnifiedTask.from_dict(task_data)
    repository = repository_for(tmp_path, task_data)
    store = SourceStore(task, repository=repository)
    executor = QueryExecutor(task, source_store=store, max_attempts=4, max_output_chars=16000,
                             analysis_timeout_seconds=5, deadline=time.monotonic() + 15)

    async def read_parts():
        for line in (1, 2):
            attempt = executor.register({"question": QUESTION, "scenario_id": "original_task",
                                         "tool": "inspect_source", "arguments_json": json.dumps({
                                             "source_id": "helper.py", "start_line": line, "end_line": line})})
            receipt = await executor.execute_registered(attempt, candidate_ids={"original_task"})
            assert receipt["status"] == "ok"
    try:
        asyncio.run(read_parts())
        reference = locator(repository.source_id_for("helper.py"), 1, 2)
        update = question_update(state="resolved", evidence=[reference], acquisition="not_needed",
                                 sequences=[1], question_id=None)
        questions = InvestigationLedger(1, repository_available=True)
        with pytest.raises(ValueError, match="associated with its observed query receipt"):
            questions.apply_updates([QuestionUpdate.model_validate(update)],
                                    store=store, attempts=executor.attempts, task=task)
        assert questions.snapshot() == []
        update["attempted_sequences"] = [1, 2]
        questions.apply_updates([QuestionUpdate.model_validate(update)],
                                store=store, attempts=executor.attempts, task=task)
        assert questions.snapshot()[0]["state"] == "resolved"
    finally:
        repository.close()


@pytest.mark.parametrize("origin", ["CONSTRUCT", "CONTRAST"])
def test_reopening_question_cannot_erase_original_association(origin):
    task_data, _ = product_reference(adjust=True)
    task = UnifiedTask.from_dict(task_data)
    request = InvestigationRequest(question=QUESTION, goal_indexes=[0], impact=IMPACT)
    questions = InvestigationLedger(2, repository_available=True)
    questions.register([request], origin="EXTRACT")
    update = question_update(state="resolved", evidence=[locator("mut:0", 1, 5)],
                             acquisition="not_needed", sequences=[1], question_id="q1")
    questions.apply_updates(
        [QuestionUpdate.model_validate(update)], store=SourceStore(task), task=task,
        attempts=[{"sequence": 1, "status": "ok", "result": {}, "response_id": "read-1"}],
    )
    before = questions.snapshot()
    with pytest.raises(ValueError, match="cannot rebind"):
        questions.register([request.model_copy(update={"goal_indexes": [1]})], origin=origin)
    assert questions.snapshot() == before
    questions.register([request], origin=origin)
    reopened = questions.snapshot()[0]
    assert reopened["state"] == "open"
    assert reopened["evidence"] == before[0]["evidence"]
    assert reopened["attempted_sequences"] == [1]
    assert before[0]["state"] == "resolved"


def test_question_snapshot_cannot_mutate_the_ledger():
    questions = InvestigationLedger(1, repository_available=True)
    request = InvestigationRequest(question=QUESTION, goal_indexes=[0], impact=IMPACT)
    questions.register([request], origin="EXTRACT")
    before = questions.snapshot()
    edited = questions.snapshot()
    edited[0]["goal_indexes"].append(99)
    edited[0]["evidence"].append(locator("unobserved", 1, 1))
    edited[0]["state"] = "resolved"
    edited.clear()

    assert questions.snapshot() == before


@pytest.mark.parametrize("defect", ["constrained_while_blocked", "reverse_goal_missing"])
def test_partial_goal_coverage_and_unknown_impact_must_agree(defect):
    _, constructed = product_reference(adjust=True)
    semantics = constructed["spec"]
    if defect == "constrained_while_blocked":
        semantics["goal_coverage"][0].update(disposition="constrained", unknown_ids=[])
    else:
        semantics["unknowns"][0]["goal_indexes"] = []
    # Materialize the fixture's source quotes through the stage protocol.
    from stage_protocol import ConstructSelection, materialize_construct
    task, _ = product_reference(adjust=True)
    contract = materialize_construct(ConstructSelection.model_validate(constructed),
                                      SourceStore(UnifiedTask.from_dict(task))).contract
    assert isinstance(contract, ScenarioContract)
    with pytest.raises(ValueError, match="blocked coverage|affected goal indexes"):
        validate_semantics(semantics, contract)
