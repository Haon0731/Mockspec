"""Self-contained dependency specification for staged inference research.

The formal ``ScenarioContract`` remains unchanged.  This module adds the
consumer-facing semantics that the formal contract cannot express directly and
binds them to the complete reviewed task.  Its validators prove reference and
state consistency only; they do not prove that source evidence entails a claim.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from mock_spec.scenario import ScenarioContract
from mock_spec.schemas import UnifiedTask


Text = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
Index = Annotated[int, Field(ge=0)]


class SpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SourceLocation(SpecModel):
    source_id: Text
    start_line: Annotated[int, Field(ge=1)]
    end_line: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def check_range(self) -> SourceLocation:
        if self.end_line < self.start_line:
            raise ValueError("source location has a reversed range")
        return self


class FixedInput(SpecModel):
    statement: Text
    goal_indexes: Annotated[list[Index], Field(min_length=1)]
    evidence_ids: Annotated[list[Text], Field(min_length=1)]


class ReplacementBoundary(SpecModel):
    id: Text
    target: Text
    replaceable_behavior: Text
    preserved_behavior: Text
    status: Literal["confirmed", "assumed", "unresolved"]
    evidence_ids: list[Text]
    premise_ids: list[Text]
    unknown_ids: list[Text]


class PreservedRealLogic(SpecModel):
    statement: Text
    evidence_ids: list[Text]
    premise_ids: list[Text]


class Premise(SpecModel):
    id: Text
    kind: Literal[
        "implementation_fact", "test_requirement", "dependency_semantics", "assumption"
    ]
    statement: Text
    evidence_ids: list[Text]
    assumption_impact: Text | None

    @model_validator(mode="after")
    def check_basis(self) -> Premise:
        if self.kind == "assumption":
            if self.evidence_ids or self.assumption_impact is None:
                raise ValueError("assumption requires impact and cannot claim direct evidence")
        elif not self.evidence_ids or self.assumption_impact is not None:
            raise ValueError("observed premise requires evidence and no assumption impact")
        return self


class ConstraintBasis(SpecModel):
    constraint_index: Index
    premise_ids: Annotated[list[Text], Field(min_length=1)]


class BranchDecision(SpecModel):
    scenario_id: Text
    disposition: Literal["allowed", "unresolved"]
    rationale: Text
    evidence_ids: list[Text]
    premise_ids: list[Text]
    unknown_ids: list[Text]

    @model_validator(mode="after")
    def check_disposition(self) -> BranchDecision:
        if self.disposition == "allowed":
            if not self.evidence_ids or not self.premise_ids or self.unknown_ids:
                raise ValueError(
                    "allowed branch requires evidence and premises without unresolved unknowns"
                )
        elif not self.unknown_ids:
            raise ValueError("unresolved branch must identify its unknowns")
        return self


class GoalCoverage(SpecModel):
    goal_index: Index
    disposition: Literal["constrained", "real_code", "blocked"]
    constraint_indexes: list[Index]
    unknown_ids: list[Text]
    rationale: Text
    evidence_ids: Annotated[list[Text], Field(min_length=1)]
    premise_ids: list[Text]

    @model_validator(mode="after")
    def check_disposition(self) -> GoalCoverage:
        if self.disposition == "constrained":
            if not self.constraint_indexes or self.unknown_ids:
                raise ValueError("constrained goal requires constraints and no unknowns")
        elif self.disposition == "real_code":
            if self.constraint_indexes or self.unknown_ids or not self.premise_ids:
                raise ValueError("real-code goal requires premises without constraints or unknowns")
        elif not self.unknown_ids:
            raise ValueError("blocked goal must identify its unknowns")
        return self


class SpecUnknown(SpecModel):
    id: Text
    question: Text
    impact: Text
    disposition: Literal["blocking", "nonblocking", "out_of_scope"]
    goal_indexes: list[Index]
    constraint_indexes: list[Index]
    scenario_ids: list[Text]
    formal_uncertainty_indexes: list[Index]
    rationale: Text

    @model_validator(mode="after")
    def check_impact_mapping(self) -> SpecUnknown:
        if self.disposition != "out_of_scope" and not (
            self.goal_indexes or self.constraint_indexes or self.scenario_ids
        ):
            raise ValueError("in-scope unknown must identify the decision it can affect")
        return self


class AllowedVariation(SpecModel):
    id: Text
    statement: Text
    scenario_ids: Annotated[list[Text], Field(min_length=1)]
    constraint_indexes: Annotated[list[Index], Field(min_length=1)]
    evidence_ids: list[Text]
    premise_ids: list[Text]

    @model_validator(mode="after")
    def check_grounding(self) -> AllowedVariation:
        if not self.evidence_ids and not self.premise_ids:
            raise ValueError("allowed variation requires an explicit basis")
        return self


class SpecConflict(SpecModel):
    id: Text
    statement: Text
    impact: Text
    evidence_ids: Annotated[list[Text], Field(min_length=1)]
    premise_ids: Annotated[list[Text], Field(min_length=1)]


class SpecSemantics(SpecModel):
    schema_version: Literal["staged-spec-semantics/v0.1"]
    delivery_status: Literal["ready", "partial", "conflict"]
    fixed_inputs: Annotated[list[FixedInput], Field(min_length=1)]
    replacement_boundary: list[ReplacementBoundary]
    preserved_real_logic: list[PreservedRealLogic]
    premises: Annotated[list[Premise], Field(min_length=1)]
    constraint_basis: list[ConstraintBasis]
    branches: Annotated[list[BranchDecision], Field(min_length=1)]
    goal_coverage: list[GoalCoverage]
    unknowns: list[SpecUnknown]
    allowed_variations: list[AllowedVariation]
    conflicts: list[SpecConflict]


class RegressionReference(SpecModel):
    status: Literal["reference_only"]
    mut_source_id: Text
    statement: Text


class ExplorationSummary(SpecModel):
    reason: Literal[
        "evidence_sufficient", "unknowns_nonblocking", "no_further_evidence",
        "budget_exhausted", "access_limited", "conflict", "controller_stop",
    ]
    rationale: Text
    evidence_ids: list[Text]
    unknown_ids: list[Text]


class DependencySpec(SpecModel):
    schema_version: Literal["mock-spec.dependency/v0.1"]
    run_id: Text
    input_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    case_id: Text
    language: Literal["java", "python"]
    input_origin: Literal["synthetic_fixture", "reviewed_adapter"]
    consumption_rule: Text
    task: dict[str, Any]
    regression_reference: RegressionReference
    original_goal_locations: Annotated[list[SourceLocation], Field(min_length=1)]
    original_goal_mapping: dict[str, list[int]]
    exploration: ExplorationSummary
    semantics: SpecSemantics
    contract: ScenarioContract


def _duplicates(values: list[str]) -> bool:
    return len(values) != len(set(values))


def _check_indexes(values: list[int], size: int, label: str) -> None:
    if any(value >= size for value in values):
        raise ValueError(f"{label} index is outside the formal contract")


def validate_semantics(output: object, contract: ScenarioContract) -> SpecSemantics:
    """Validate references and delivery state, without claiming semantic proof."""
    contract = ScenarioContract.model_validate(contract.model_dump(mode="json"))
    raw = output.model_dump(mode="json") if isinstance(output, SpecSemantics) else output
    semantics = SpecSemantics.model_validate(raw)
    evidence_ids = {item.id for item in contract.evidence}
    scenario_ids = {item.id for item in contract.scenario_hypotheses}
    premise_ids = [item.id for item in semantics.premises]
    unknown_ids = [item.id for item in semantics.unknowns]
    boundary_ids = [item.id for item in semantics.replacement_boundary]
    variation_ids = [item.id for item in semantics.allowed_variations]
    conflict_ids = [item.id for item in semantics.conflicts]
    for values, label in (
        (premise_ids, "premise"), (unknown_ids, "unknown"),
        (boundary_ids, "replacement boundary"), (variation_ids, "allowed variation"),
        (conflict_ids, "conflict"),
    ):
        if _duplicates(values):
            raise ValueError(f"duplicate {label} ID")
    known_premises, known_unknowns = set(premise_ids), set(unknown_ids)

    def references(
        *, evidence: list[str] | None = None, premises: list[str] | None = None,
        unknowns: list[str] | None = None, scenarios: list[str] | None = None,
        label: str,
    ) -> None:
        if not set(evidence or ()) <= evidence_ids:
            raise ValueError(f"{label} references unknown evidence")
        if not set(premises or ()) <= known_premises:
            raise ValueError(f"{label} references unknown premise")
        if not set(unknowns or ()) <= known_unknowns:
            raise ValueError(f"{label} references unknown unresolved item")
        if not set(scenarios or ()) <= scenario_ids:
            raise ValueError(f"{label} references unknown scenario")

    fixed_goal_indexes: list[int] = []
    for index, fixed_input in enumerate(semantics.fixed_inputs):
        _check_indexes(
            fixed_input.goal_indexes, len(contract.test_objectives), "fixed-input goal"
        )
        if len(fixed_input.goal_indexes) != len(set(fixed_input.goal_indexes)):
            raise ValueError("fixed input cannot repeat a goal mapping")
        fixed_goal_indexes.extend(fixed_input.goal_indexes)
        references(evidence=fixed_input.evidence_ids, label=f"fixed input {index}")
    if set(fixed_goal_indexes) != set(range(len(contract.test_objectives))):
        raise ValueError("every test objective requires a fixed-input mapping")
    for index, premise in enumerate(semantics.premises):
        references(evidence=premise.evidence_ids, label=f"premise {index}")
    for index, boundary in enumerate(semantics.replacement_boundary):
        references(
            evidence=boundary.evidence_ids, premises=boundary.premise_ids,
            unknowns=boundary.unknown_ids, label=f"replacement boundary {index}",
        )
        if boundary.status == "confirmed":
            if not boundary.evidence_ids or not boundary.premise_ids:
                raise ValueError("confirmed replacement boundary requires evidence and premises")
            if boundary.unknown_ids:
                raise ValueError("confirmed replacement boundary cannot retain unknowns")
        if boundary.status == "assumed" and not any(
            next(p for p in semantics.premises if p.id == premise).kind == "assumption"
            for premise in boundary.premise_ids
        ):
            raise ValueError("assumed replacement boundary requires an assumption premise")
        if boundary.status == "assumed" and boundary.unknown_ids:
            raise ValueError("assumed replacement boundary cannot retain unknowns")
        if boundary.status == "unresolved" and not boundary.unknown_ids:
            raise ValueError("unresolved replacement boundary requires an unknown")
    for index, preserved in enumerate(semantics.preserved_real_logic):
        references(
            evidence=preserved.evidence_ids, premises=preserved.premise_ids,
            label=f"preserved real logic {index}",
        )
        if not preserved.evidence_ids and not preserved.premise_ids:
            raise ValueError("preserved real logic requires an explicit basis")

    constraint_indexes = [item.constraint_index for item in semantics.constraint_basis]
    if sorted(constraint_indexes) != list(range(len(contract.dependency_constraints))):
        raise ValueError("every dependency constraint requires exactly one basis mapping")
    for index, basis in enumerate(semantics.constraint_basis):
        references(premises=basis.premise_ids, label=f"constraint basis {index}")

    branch_ids = [item.scenario_id for item in semantics.branches]
    if _duplicates(branch_ids) or set(branch_ids) != scenario_ids:
        raise ValueError("every scenario requires exactly one branch decision")
    blocking_ids = {
        item.id for item in semantics.unknowns if item.disposition == "blocking"
    }
    unknown_by_id = {item.id: item for item in semantics.unknowns}
    for index, branch in enumerate(semantics.branches):
        references(
            evidence=branch.evidence_ids, premises=branch.premise_ids,
            unknowns=branch.unknown_ids, scenarios=[branch.scenario_id],
            label=f"branch {index}",
        )
        if branch.disposition == "unresolved" and not any(
            unknown_id in blocking_ids
            and branch.scenario_id in unknown_by_id[unknown_id].scenario_ids
            for unknown_id in branch.unknown_ids
        ):
            raise ValueError(
                "unresolved branch requires a blocking unknown mapped to that scenario"
            )

    goal_indexes = [item.goal_index for item in semantics.goal_coverage]
    if sorted(goal_indexes) != list(range(len(contract.test_objectives))):
        raise ValueError("every test objective requires exactly one coverage decision")
    for index, coverage in enumerate(semantics.goal_coverage):
        _check_indexes(
            coverage.constraint_indexes,
            len(contract.dependency_constraints),
            "goal constraint",
        )
        references(
            evidence=coverage.evidence_ids, premises=coverage.premise_ids,
            unknowns=coverage.unknown_ids, label=f"goal coverage {index}",
        )
        if coverage.disposition == "blocked" and not set(coverage.unknown_ids) <= blocking_ids:
            raise ValueError("blocked goal must refer only to blocking unknowns")

    allowed_scenarios = {
        item.scenario_id for item in semantics.branches if item.disposition == "allowed"
    }
    applicable_constraints = {
        scenario_id: {
            index for index, constraint in enumerate(contract.dependency_constraints)
            if scenario_id in constraint.scenario_ids
        }
        for scenario_id in allowed_scenarios
    }
    for coverage in semantics.goal_coverage:
        if coverage.disposition != "constrained":
            continue
        goal_scenarios = set(contract.test_objectives[coverage.goal_index].scenario_ids)
        for scenario_id in goal_scenarios & allowed_scenarios:
            if not set(coverage.constraint_indexes) & applicable_constraints[scenario_id]:
                raise ValueError(
                    "constrained goal must cover an applicable constraint for every allowed branch"
                )

    for index, unknown in enumerate(semantics.unknowns):
        _check_indexes(unknown.goal_indexes, len(contract.test_objectives), "unknown goal")
        _check_indexes(
            unknown.constraint_indexes,
            len(contract.dependency_constraints),
            "unknown constraint",
        )
        _check_indexes(
            unknown.formal_uncertainty_indexes,
            len(contract.uncertainty),
            "formal uncertainty",
        )
        references(scenarios=unknown.scenario_ids, label=f"unknown {index}")
    coverage_by_goal = {item.goal_index: item for item in semantics.goal_coverage}
    unknown_by_id = {item.id: item for item in semantics.unknowns}
    for unknown in semantics.unknowns:
        if unknown.disposition == "blocking":
            for goal_index in unknown.goal_indexes:
                coverage = coverage_by_goal[goal_index]
                if coverage.disposition != "blocked" or unknown.id not in coverage.unknown_ids:
                    raise ValueError("blocking unknown must appear in its goal's blocked coverage")
    for coverage in semantics.goal_coverage:
        if coverage.disposition == "blocked" and any(
            coverage.goal_index not in unknown_by_id[key].goal_indexes for key in coverage.unknown_ids
        ):
            raise ValueError("blocked coverage must match the unknown's affected goal indexes")
    mapped_uncertainty = [
        index
        for unknown in semantics.unknowns
        for index in unknown.formal_uncertainty_indexes
    ]
    if sorted(mapped_uncertainty) != list(range(len(contract.uncertainty))):
        raise ValueError("every formal uncertainty requires exactly one semantic mapping")
    for index, variation in enumerate(semantics.allowed_variations):
        _check_indexes(
            variation.constraint_indexes,
            len(contract.dependency_constraints),
            "variation constraint",
        )
        references(
            evidence=variation.evidence_ids, premises=variation.premise_ids,
            scenarios=variation.scenario_ids, label=f"allowed variation {index}",
        )
    for index, conflict in enumerate(semantics.conflicts):
        references(
            evidence=conflict.evidence_ids, premises=conflict.premise_ids,
            label=f"conflict {index}",
        )

    used_premises = {
        premise
        for values in (
            *(item.premise_ids for item in semantics.replacement_boundary),
            *(item.premise_ids for item in semantics.preserved_real_logic),
            *(item.premise_ids for item in semantics.constraint_basis),
            *(item.premise_ids for item in semantics.branches),
            *(item.premise_ids for item in semantics.goal_coverage),
            *(item.premise_ids for item in semantics.allowed_variations),
            *(item.premise_ids for item in semantics.conflicts),
        )
        for premise in values
    }
    if used_premises != known_premises:
        raise ValueError("every premise must support an explicit specification decision")

    unresolved_branch = any(item.disposition == "unresolved" for item in semantics.branches)
    unresolved_boundary = any(
        item.status == "unresolved" for item in semantics.replacement_boundary
    )
    if semantics.delivery_status == "ready" and (
        blocking_ids or semantics.conflicts or unresolved_branch or unresolved_boundary
    ):
        raise ValueError("ready specification cannot retain blocking, conflict, or unresolved state")
    if semantics.delivery_status == "conflict" and not semantics.conflicts:
        raise ValueError("conflict specification must describe its conflict")
    if semantics.conflicts and semantics.delivery_status != "conflict":
        raise ValueError("described conflicts require conflict delivery status")
    if semantics.delivery_status == "partial" and not (
        blocking_ids or unresolved_branch or unresolved_boundary
    ):
        raise ValueError("partial specification must describe its limitation")
    return semantics


def _task_sha256(task: UnifiedTask) -> str:
    payload = json.dumps(
        task.to_dict(), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def original_goal_mapping(contract: ScenarioContract, locations: list[dict]) -> dict[str, list[int]]:
    """Map original source anchors to formal goals, without inferring meaning."""
    evidence = {item.id: item for item in contract.evidence}
    mapping: dict[str, list[int]] = {}
    for index, raw in enumerate(locations):
        source = SourceLocation.model_validate(raw)
        matches = [goal_index for goal_index, goal in enumerate(contract.test_objectives)
                   if any(evidence[key].source_id == source.source_id
                          and evidence[key].start_line <= source.start_line
                          and evidence[key].end_line >= source.end_line for key in goal.evidence_ids)]
        if not matches:
            raise ValueError("formal goals omit an original goal source anchor")
        mapping[str(index)] = matches
    if {index for matches in mapping.values() for index in matches} != set(range(len(contract.test_objectives))):
        raise ValueError("formal goal lacks an original goal source anchor")
    return mapping


def build_dependency_spec(
    *,
    semantics: object,
    contract: ScenarioContract,
    task: UnifiedTask,
    run_id: str,
    input_hash: str,
    original_goal_locations: list[SourceLocation | dict[str, object]],
    exploration: ExplorationSummary | dict[str, object],
    input_origin: Literal["synthetic_fixture", "reviewed_adapter"] = "synthetic_fixture",
) -> DependencySpec:
    """Bind validated semantics to the reviewed task and formal contract."""
    contract = ScenarioContract.model_validate(contract.model_dump(mode="json"))
    if (contract.case_id, contract.language) != (task.case_id, task.language):
        raise ValueError("dependency specification task identity mismatch")
    if input_hash != _task_sha256(task):
        raise ValueError("dependency specification input hash mismatch")
    parsed = validate_semantics(semantics, contract)
    locations = [SourceLocation.model_validate(item) for item in original_goal_locations]
    task_sources = {
        source.source_id: source
        for source in (task.masked_test, task.mut_source, *task.supporting_sources)
    }
    for location in locations:
        source = task_sources.get(location.source_id)
        if source is None:
            raise ValueError("original goal location references an unknown task source")
        if location.start_line < source.start_line or location.end_line > source.end_line:
            raise ValueError("original goal location falls outside the reviewed source range")
    summary = ExplorationSummary.model_validate(exploration)
    evidence_ids = {item.id for item in contract.evidence}
    unknown_ids = {item.id for item in parsed.unknowns}
    if not set(summary.evidence_ids) <= evidence_ids:
        raise ValueError("exploration summary references unknown evidence")
    if not set(summary.unknown_ids) <= unknown_ids:
        raise ValueError("exploration summary references unknown unresolved item")
    if summary.reason == "evidence_sufficient" and summary.unknown_ids:
        raise ValueError("evidence-sufficient exploration cannot retain unknowns")
    return DependencySpec(
        schema_version="mock-spec.dependency/v0.1",
        run_id=run_id,
        input_sha256=input_hash,
        case_id=task.case_id,
        language=cast(Literal["java", "python"], task.language),
        input_origin=input_origin,
        consumption_rule=(
            "Only a ready spec supports instantiation under its stated premises. Choose one "
            "complete allowed branch and obey every applicable constraint; never conjoin "
            "mutually exclusive branches. Partial/conflict specs retain useful claims but "
            "do not establish a complete valid Mock. Evidence linkage is not a proof of entailment."
        ),
        task=task.to_dict(),
        regression_reference=RegressionReference(
            status="reference_only",
            mut_source_id=task.mut_source.source_id,
            statement=(
                "The frozen MUT execution chain is the regression reference for path analysis; "
                "it is not asserted to be a verified business specification."
            ),
        ),
        original_goal_locations=locations,
        original_goal_mapping=original_goal_mapping(
            contract, [location.model_dump() for location in locations]
        ),
        exploration=summary,
        semantics=parsed,
        contract=contract,
    )


__all__ = [
    "DependencySpec", "ExplorationSummary", "SourceLocation", "SpecSemantics",
    "build_dependency_spec", "validate_semantics",
]
