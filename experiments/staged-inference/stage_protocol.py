"""Strict stage wire types, citation materialization, and result validation."""

from __future__ import annotations

from copy import deepcopy
from typing import Annotated, Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from mock_spec.scenario import (
    Claim, Hypothesis, Interaction, ScenarioContract, VerificationCandidate,
    validate_contract,
)
from mock_spec.schemas import UnifiedTask
from mock_spec.validation import SourceStore

from specification import (
    BranchDecision, Premise, PreservedRealLogic, ReplacementBoundary, SpecConflict,
    SpecSemantics,
)


Text = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]


class StageModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


StageResult = TypeVar("StageResult", bound=StageModel)


class SourceReference(StageModel):
    source_id: Text
    start_line: Annotated[int, Field(ge=1)]
    end_line: Annotated[int, Field(ge=1)]
    quote: Text


class SourceLocator(StageModel):
    source_id: Text
    start_line: Annotated[int, Field(ge=1)]
    end_line: Annotated[int, Field(ge=1)]


class EvidenceSelection(SourceLocator):
    id: Text
    rationale: Text


class CandidateDraft(StageModel):
    scenario_id: Text
    description: Text
    evidence: list[SourceReference]


class ExtractResult(StageModel):
    schema_version: Literal["staged-extract/v0.1"]
    result_status: Literal["CONTINUE", "INCONCLUSIVE"] = "CONTINUE"
    original_goals: list[SourceReference]
    candidates: list[CandidateDraft]
    unresolved: list[Text]


class CandidateSelection(StageModel):
    scenario_id: Text
    description: Text
    evidence: list[SourceLocator]


class ExtractUnknown(StageModel):
    question: Text
    goal_indexes: Annotated[list[Annotated[int, Field(ge=0)]], Field(min_length=1)]
    impact: Text


class ExtractSelection(StageModel):
    schema_version: Literal["staged-extract/v0.3"]
    result_status: Literal["CONTINUE", "INCONCLUSIVE"] = "CONTINUE"
    original_goals: list[SourceLocator]
    candidates: list[CandidateSelection]
    unresolved: list[ExtractUnknown]


class QueryAssessment(StageModel):
    attempt_sequence: Annotated[int, Field(ge=1)]
    impact: Literal["supports", "weakens", "no_new_information"]
    rationale: Text


class QueryAssessmentSelection(StageModel):
    """Model-facing assessment; the host owns receipt association."""

    impact: Literal["supports", "weakens", "no_new_information"]
    rationale: Text


class QueryAttempt(StageModel):
    """Provider-facing shape; host validation still follows attempt registration."""

    question: Text
    scenario_id: Text
    tool: Text
    arguments_json: Text


class InvestigationRequest(StageModel):
    question: Text
    goal_indexes: Annotated[list[Annotated[int, Field(ge=0)]], Field(min_length=1)]
    impact: Text


class QuestionAssessmentBase(StageModel):
    rationale: Text
    evidence: list[SourceLocator]


class OpenQuestionUpdateSelection(QuestionAssessmentBase):
    """Existing questions use null immutable fields; new questions provide them."""

    question_id: Text | None
    question: Text | None
    goal_indexes: list[Annotated[int, Field(ge=0)]] | None
    impact: Text | None
    state: Literal["open"]
    acquisition: Literal["available", "unavailable"]
    attempted_sequences: list[Annotated[int, Field(ge=1)]]


class ClosedQuestionUpdateSelection(QuestionAssessmentBase):
    """Model-facing update whose acquisition state is owned by the host."""

    question_id: Text | None
    question: Text | None
    goal_indexes: list[Annotated[int, Field(ge=0)]] | None
    impact: Text | None
    state: Literal["resolved", "nonblocking"]


QuestionUpdateSelection = (
    OpenQuestionUpdateSelection | ClosedQuestionUpdateSelection
)


class QuestionUpdate(InvestigationRequest, QuestionAssessmentBase):
    """Host-owned ledger update after projecting the model-facing selection."""

    question_id: Text | None
    state: Literal["open", "resolved", "nonblocking"]
    acquisition: Literal["available", "unavailable", "not_needed"]
    attempted_sequences: list[Annotated[int, Field(ge=1)]]


class RemainingUnknown(StageModel):
    question: Text
    impact: Text
    blocking: bool


class ExplorationStop(StageModel):
    reason: Literal["evidence_sufficient", "unknowns_nonblocking", "no_further_evidence"]
    rationale: Text
    evidence: list[SourceReference]
    remaining_unknowns: list[RemainingUnknown]


class ExplorationStopSelection(StageModel):
    reason: Literal["evidence_sufficient", "unknowns_nonblocking", "no_further_evidence"]
    rationale: Text
    evidence: list[SourceLocator]


class QueryResult(StageModel):
    schema_version: Literal["staged-query/v0.1"]
    decision: Literal["request_query", "finish_queries"]
    query_attempt: QueryAttempt | None
    previous_receipt_assessment: QueryAssessment | None = None
    impact: Text
    stop_explanation: ExplorationStop | None = None


class QuerySelection(StageModel):
    schema_version: Literal["staged-query/v0.6"]
    decision: Literal["request_query", "finish_queries"]
    query_attempt: QueryAttempt | None
    previous_receipt_assessment: QueryAssessmentSelection | None
    impact: Text
    stop_explanation: ExplorationStopSelection | None
    question_updates: list[QuestionUpdateSelection] = Field(default_factory=list)
    candidate_updates: list[CandidateSelection] = Field(default_factory=list)


class ConstructContent(StageModel):
    result_status: Literal["SELECTED", "ALTERNATIVES", "AMBIGUOUS", "INCONCLUSIVE"]
    selected_scenario_id: Text | None
    selection_rationale: Text
    allowed_variations: list[Text]
    unresolved: list[Text]
    nonblocking_unknowns: list[Text] = Field(default_factory=list)
    spec: SpecSemantics | None = None
    investigation_requests: list[InvestigationRequest] = Field(default_factory=list)


class ConstructResult(ConstructContent):
    schema_version: Literal["staged-construct/v0.1"]
    contract: ScenarioContract | None


class ReferencedClaim(Claim):
    ref: Text


class ReferencedContractSelection(StageModel):
    """Wire contract with stable semantic refs; refs are not published."""

    schema_version: Literal["staged-contract-selection/v0.1"]
    test_objectives: Annotated[list[ReferencedClaim], Field(min_length=1)]
    scenario_hypotheses: Annotated[list[Hypothesis], Field(min_length=1)]
    dependency_constraints: list[ReferencedClaim]
    expected_interactions: list[Interaction]
    verification_candidates: list[VerificationCandidate]
    evidence: list[EvidenceSelection]
    uncertainty: list[ReferencedClaim]


class FixedInputSelection(StageModel):
    statement: Text
    goal_refs: Annotated[list[Text], Field(min_length=1)]
    evidence_ids: Annotated[list[Text], Field(min_length=1)]


class ConstraintBasisSelection(StageModel):
    constraint_ref: Text
    premise_ids: Annotated[list[Text], Field(min_length=1)]


class GoalCoverageSelection(StageModel):
    goal_ref: Text
    disposition: Literal["constrained", "real_code", "blocked"]
    constraint_refs: list[Text]
    unknown_ids: list[Text]
    rationale: Text
    evidence_ids: Annotated[list[Text], Field(min_length=1)]
    premise_ids: list[Text]


class SpecUnknownSelection(StageModel):
    id: Text
    question: Text
    impact: Text
    disposition: Literal["blocking", "nonblocking", "out_of_scope"]
    goal_refs: list[Text]
    constraint_refs: list[Text]
    scenario_ids: list[Text]
    formal_uncertainty_refs: list[Text]
    rationale: Text


class AllowedVariationSelection(StageModel):
    id: Text
    statement: Text
    scenario_ids: Annotated[list[Text], Field(min_length=1)]
    constraint_refs: Annotated[list[Text], Field(min_length=1)]
    evidence_ids: list[Text]
    premise_ids: list[Text]


class SpecSemanticsSelection(StageModel):
    schema_version: Literal["staged-spec-semantics/v0.2"]
    delivery_status: Literal["ready", "partial", "conflict"]
    fixed_inputs: Annotated[list[FixedInputSelection], Field(min_length=1)]
    replacement_boundary: list[ReplacementBoundary]
    preserved_real_logic: list[PreservedRealLogic]
    premises: Annotated[list[Premise], Field(min_length=1)]
    constraint_basis: list[ConstraintBasisSelection]
    branches: Annotated[list[BranchDecision], Field(min_length=1)]
    goal_coverage: list[GoalCoverageSelection]
    unknowns: list[SpecUnknownSelection]
    allowed_variations: list[AllowedVariationSelection]
    conflicts: list[SpecConflict]


class ConstructFallbackUnknown(StageModel):
    question: Text
    blocking: bool


class ConstructSelection(StageModel):
    schema_version: Literal["staged-construct/v0.4"]
    result_status: Literal["SELECTED", "ALTERNATIVES", "AMBIGUOUS", "INCONCLUSIVE"]
    selected_scenario_id: Text | None
    selection_rationale: Text
    fallback_unknowns: list[ConstructFallbackUnknown]
    spec: SpecSemanticsSelection | None = None
    investigation_requests: list[InvestigationRequest] = Field(default_factory=list)
    contract: ReferencedContractSelection | None


class ContrastResult(StageModel):
    schema_version: Literal["staged-contrast/v0.3"]
    status: Literal["proposed", "none_identified"]
    alternative: Text | None
    bypass_behavior: Text | None
    evidence_ids: list[Text]
    distinguishing_observation: Text | None
    revision_requested: bool
    revision_reason: Text | None
    support: Literal["evidence", "assumption"] | None = None
    assumption_reason: Text | None = None
    semantic_role: Literal[
        "dependency_contract", "mut_path_condition", "test_objective"
    ] | None = None
    weakness_review: Text
    overconstraint_review: Text
    observation_limits: Text
    investigation_requests: list[InvestigationRequest] = Field(default_factory=list)


class ContrastSelection(StageModel):
    schema_version: Literal["staged-contrast/v0.4"]
    status: Literal["proposed", "none_identified"]
    alternative: Text | None
    bypass_behavior: Text | None
    evidence_ids: list[Text]
    distinguishing_observation: Text | None
    revision_requested: bool
    revision_reason: Text | None
    assumption_reason: Text | None
    semantic_role: Literal[
        "dependency_contract", "mut_path_condition", "test_objective"
    ] | None = None
    weakness_review: Text
    overconstraint_review: Text
    observation_limits: Text
    investigation_requests: list[InvestigationRequest] = Field(default_factory=list)


class RevisionResult(StageModel):
    schema_version: Literal["staged-revise/v0.1"]
    revised_construct: ConstructResult
    modified_objects: Annotated[list[Text], Field(min_length=1)]
    evidence_reason: Text
    evidence_ids: Annotated[list[Text], Field(min_length=1)]
    request_another_revision: bool = False


class RevisionSelection(StageModel):
    schema_version: Literal["staged-revise/v0.3"]
    revised_construct: ConstructSelection
    evidence_reason: Text
    evidence_ids: Annotated[list[Text], Field(min_length=1)]
    request_another_revision: bool = False


class DeliverResult(StageModel):
    schema_version: Literal["staged-deliver/v0.1"]
    result_status: Literal["SELECTED", "ALTERNATIVES", "AMBIGUOUS", "INCONCLUSIVE"]
    selected_scenario_id: Text | None
    contract: ScenarioContract | None


class CitationMaterializationError(ValueError):
    def __init__(
        self, message: str, *, field_path: str, locator: object = None,
    ) -> None:
        super().__init__(message)
        raw = locator.model_dump() if isinstance(locator, SourceLocator) else locator
        raw = raw if isinstance(raw, dict) else {}
        self.audit = {
            "field_path": field_path,
            "source_id": raw.get("source_id") if isinstance(raw.get("source_id"), str) else None,
            "start_line": raw.get("start_line") if type(raw.get("start_line")) is int else None,
            "end_line": raw.get("end_line") if type(raw.get("end_line")) is int else None,
            "error": str(message),
        }


def check_source_reference(reference: SourceReference, source_store: SourceStore) -> None:
    if reference.end_line < reference.start_line:
        raise ValueError("source reference has a reversed range")
    quote = source_store.quote(reference.source_id, reference.start_line, reference.end_line)
    if quote != reference.quote:
        raise ValueError(
            f"source reference quote mismatch: {reference.source_id}:"
            f"{reference.start_line}-{reference.end_line}"
        )


def materialize_reference(
    locator: SourceLocator, store: SourceStore, *, field_path: str,
) -> SourceReference:
    try:
        quote = store.quote(locator.source_id, locator.start_line, locator.end_line)
    except ValueError as exc:
        raise CitationMaterializationError(
            str(exc), field_path=field_path, locator=locator,
        ) from None
    return SourceReference(**locator.model_dump(), quote=quote)


def materialize_contract(
    raw: object, store: SourceStore, *, field_path: str = "contract",
) -> ScenarioContract | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("contract selection must be an object or null")
    selected = deepcopy(raw)
    evidence = selected.get("evidence")
    if not isinstance(evidence, list):
        raise ValueError("contract selection evidence must be a list")
    materialized = []
    for index, item in enumerate(evidence):
        item_path = f"{field_path}.evidence.{index}"
        try:
            locator = EvidenceSelection.model_validate(item)
        except ValidationError as exc:
            raise CitationMaterializationError(
                "invalid evidence locator", field_path=item_path, locator=item,
            ) from exc
        try:
            quote = store.quote(locator.source_id, locator.start_line, locator.end_line)
        except ValueError as exc:
            raise CitationMaterializationError(
                str(exc), field_path=item_path, locator=locator,
            ) from None
        materialized.append({**locator.model_dump(), "quote": quote})
    selected["evidence"] = materialized
    return ScenarioContract.model_validate(selected)


def _ref_indexes(refs: list[str], lookup: dict[str, int], label: str) -> list[int]:
    if len(refs) != len(set(refs)):
        raise ValueError(f"{label} contains duplicate stable refs")
    try:
        return [lookup[ref] for ref in refs]
    except KeyError as exc:
        raise ValueError(f"{label} references unknown stable ref: {exc.args[0]}") from None


def _materialize_referenced_contract(
    raw: ReferencedContractSelection, store: SourceStore, task: UnifiedTask, *, field_path: str,
) -> tuple[ScenarioContract, dict[str, dict[str, int]]]:
    selected = raw.model_dump(mode="json")
    selected["schema_version"] = "mock-spec.scenario/v0.1"
    selected["case_id"] = task.case_id
    selected["language"] = task.language
    refs: dict[str, dict[str, int]] = {}
    for name, items in (
        ("goal", selected["test_objectives"]),
        ("constraint", selected["dependency_constraints"]),
        ("uncertainty", selected["uncertainty"]),
    ):
        mapping: dict[str, int] = {}
        for index, item in enumerate(items):
            ref = item.pop("ref")
            if ref in mapping:
                raise ValueError(f"duplicate {name} stable ref")
            mapping[ref] = index
        refs[name] = mapping
    contract = materialize_contract(selected, store, field_path=field_path)
    assert contract is not None
    return contract, refs


def _materialize_spec(
    raw: SpecSemanticsSelection, refs: dict[str, dict[str, int]],
) -> SpecSemantics:
    selected = raw.model_dump(mode="json")
    selected["schema_version"] = "staged-spec-semantics/v0.1"
    for item in selected["fixed_inputs"]:
        item["goal_indexes"] = _ref_indexes(
            item.pop("goal_refs"), refs["goal"], "fixed input",
        )
    for item in selected["constraint_basis"]:
        item["constraint_index"] = _ref_indexes(
            [item.pop("constraint_ref")], refs["constraint"], "constraint basis",
        )[0]
    for item in selected["goal_coverage"]:
        item["goal_index"] = _ref_indexes(
            [item.pop("goal_ref")], refs["goal"], "goal coverage",
        )[0]
        item["constraint_indexes"] = _ref_indexes(
            item.pop("constraint_refs"), refs["constraint"], "goal coverage",
        )
    for item in selected["unknowns"]:
        item["goal_indexes"] = _ref_indexes(
            item.pop("goal_refs"), refs["goal"], "unknown",
        )
        item["constraint_indexes"] = _ref_indexes(
            item.pop("constraint_refs"), refs["constraint"], "unknown",
        )
        item["formal_uncertainty_indexes"] = _ref_indexes(
            item.pop("formal_uncertainty_refs"), refs["uncertainty"], "unknown",
        )
    for item in selected["allowed_variations"]:
        item["constraint_indexes"] = _ref_indexes(
            item.pop("constraint_refs"), refs["constraint"], "allowed variation",
        )
    return SpecSemantics.model_validate(selected)


def materialize_extract(wire: ExtractSelection, store: SourceStore) -> ExtractResult:
    return ExtractResult(
        schema_version="staged-extract/v0.1",
        result_status=wire.result_status,
        original_goals=[
            materialize_reference(item, store, field_path=f"original_goals.{index}")
            for index, item in enumerate(wire.original_goals)
        ],
        candidates=[CandidateDraft(
            scenario_id=item.scenario_id,
            description=item.description,
            evidence=[
                materialize_reference(
                    ref, store, field_path=f"candidates.{candidate_index}.evidence.{index}"
                )
                for index, ref in enumerate(item.evidence)
            ],
        ) for candidate_index, item in enumerate(wire.candidates)],
        unresolved=[item.question for item in wire.unresolved],
    )


def materialize_query(
    wire: QuerySelection, store: SourceStore, *, assessment_sequence: int | None = None,
) -> QueryResult:
    stop = None
    if wire.stop_explanation is not None:
        stop = ExplorationStop(
            reason=wire.stop_explanation.reason,
            rationale=wire.stop_explanation.rationale,
            evidence=[
                materialize_reference(
                    item, store, field_path=f"stop_explanation.evidence.{index}"
                )
                for index, item in enumerate(wire.stop_explanation.evidence)
            ],
            # Projected from the host question ledger after updates are applied.
            remaining_unknowns=[],
        )
    assessment = None
    if wire.previous_receipt_assessment is not None:
        if assessment_sequence is None:
            raise ValueError("QUERY assessment has no host-bound receipt")
        assessment = QueryAssessment(
            attempt_sequence=assessment_sequence,
            impact=wire.previous_receipt_assessment.impact,
            rationale=wire.previous_receipt_assessment.rationale,
        )
    return QueryResult(
        schema_version="staged-query/v0.1",
        decision=wire.decision,
        query_attempt=wire.query_attempt,
        previous_receipt_assessment=assessment,
        impact=wire.impact,
        stop_explanation=stop,
    )


def project_question_updates(
    updates: list[QuestionUpdateSelection],
    *, existing_questions: list[dict[str, Any]], attempts: list[dict[str, Any]],
    store: SourceStore,
) -> list[QuestionUpdate]:
    """Restore immutable ledger fields and observed receipt provenance."""
    existing = {item["question_id"]: item for item in existing_questions}
    observations = store.observations()
    projected: list[QuestionUpdate] = []
    for update in updates:
        values = update.model_dump(mode="json")
        question_id = values.get("question_id")
        prior: dict[str, Any] | None = None
        if question_id is not None:
            prior = existing.get(question_id)
            if prior is None:
                raise ValueError("question update references an unknown question")
            if any(values.get(key) is not None for key in ("question", "goal_indexes", "impact")):
                raise ValueError("existing question update must not repeat immutable fields")
            values.update(
                question=prior["question"], goal_indexes=prior["goal_indexes"],
                impact=prior["impact"],
            )
        elif any(values.get(key) is None for key in ("question", "goal_indexes", "impact")):
            raise ValueError("new question update requires question, goals, and impact")
        if values["state"] in {"resolved", "nonblocking"}:
            values["acquisition"] = "not_needed"
            response_ids = {
                observation.get("response_id")
                for locator in update.evidence for observation in observations
                if observation["source_id"] == locator.source_id
                and isinstance(observation["start_line"], int)
                and isinstance(observation["end_line"], int)
                and observation["start_line"] <= locator.end_line
                and observation["end_line"] >= locator.start_line
            }
            values["attempted_sequences"] = sorted(
                item["sequence"] for item in attempts
                if item.get("response_id") in response_ids
            )
        if prior is not None:
            values["attempted_sequences"] = sorted({
                *prior.get("attempted_sequences", []),
                *values.get("attempted_sequences", []),
            })
        projected.append(QuestionUpdate.model_validate(values))
    return projected


def materialize_construct(
    wire: ConstructSelection, store: SourceStore, task: UnifiedTask, *, field_path: str = "",
) -> ConstructResult:
    contract: ScenarioContract | None = None
    spec: SpecSemantics | None = None
    if wire.contract is not None:
        contract, refs = _materialize_referenced_contract(
            wire.contract, store, task, field_path=f"{field_path}.contract".lstrip("."),
        )
        if wire.spec is not None:
            spec = _materialize_spec(wire.spec, refs)
    elif wire.spec is not None:
        raise ValueError("specification semantics require a formal contract")
    if wire.spec is not None and wire.fallback_unknowns:
        raise ValueError("fallback unknowns are only allowed when spec is absent")
    unresolved = [item.question for item in wire.fallback_unknowns if item.blocking]
    nonblocking = [item.question for item in wire.fallback_unknowns if not item.blocking]
    allowed_variations: list[str] = []
    if spec is not None:
        unresolved = [
            item.question for item in spec.unknowns if item.disposition == "blocking"
        ]
        nonblocking = [
            item.question for item in spec.unknowns if item.disposition != "blocking"
        ]
        allowed_variations = [item.statement for item in spec.allowed_variations]
    return ConstructResult(
        schema_version="staged-construct/v0.1",
        result_status=wire.result_status,
        selected_scenario_id=wire.selected_scenario_id,
        selection_rationale=wire.selection_rationale,
        allowed_variations=allowed_variations,
        unresolved=unresolved,
        nonblocking_unknowns=nonblocking,
        spec=spec,
        investigation_requests=wire.investigation_requests,
        contract=contract,
    )


def materialize_revision(
    wire: RevisionSelection, store: SourceStore, task: UnifiedTask,
) -> RevisionResult:
    return RevisionResult(
        schema_version="staged-revise/v0.1",
        revised_construct=materialize_construct(
            wire.revised_construct, store, task, field_path="revised_construct"
        ),
        # The host computes exact changed paths after materialization.  A placeholder
        # is replaced before this object is recorded or consumed.
        modified_objects=["host_pending_diff"],
        evidence_reason=wire.evidence_reason,
        evidence_ids=wire.evidence_ids,
        request_another_revision=wire.request_another_revision,
    )


def materialize_contrast(wire: ContrastSelection) -> ContrastResult:
    has_evidence = bool(wire.evidence_ids)
    has_assumption = wire.assumption_reason is not None
    if has_evidence == has_assumption:
        raise ValueError(
            "contrast requires exactly one of evidence IDs or assumption reason"
        )
    return ContrastResult(
        schema_version="staged-contrast/v0.3",
        support="evidence" if has_evidence else "assumption",
        **wire.model_dump(mode="json", exclude={"schema_version"}),
    )


def validate_constructed(
    constructed: ConstructResult, task: UnifiedTask, source_store: SourceStore,
    *, strict_mapping: bool = False,
) -> ScenarioContract | None:
    """Validate state-specific delivery rules and return a revalidated contract."""
    contract = (
        None if constructed.contract is None
        else validate_contract(constructed.contract, task, source_store=source_store)
    )
    hypotheses = {} if contract is None else {
        item.id: item for item in contract.scenario_hypotheses
    }
    if constructed.result_status == "SELECTED":
        if contract is None:
            raise ValueError("SELECTED requires a complete contract")
        selected = constructed.selected_scenario_id
        if selected not in hypotheses:
            raise ValueError("selected scenario is not a current candidate")
        if hypotheses[selected].status != "candidate":
            raise ValueError("selected scenario must have candidate status")
        if constructed.unresolved:
            raise ValueError("SELECTED cannot have blocking unresolved items")
    elif constructed.result_status == "ALTERNATIVES":
        if contract is None or len(hypotheses) < 2 or constructed.selected_scenario_id is not None:
            raise ValueError("ALTERNATIVES requires multiple complete branches and no selected ID")
        if constructed.unresolved or constructed.spec is None:
            raise ValueError("ALTERNATIVES requires explicit supported choices without blocking unknowns")
        if constructed.spec.delivery_status != "ready" or any(
            branch.disposition != "allowed" for branch in constructed.spec.branches
        ):
            raise ValueError("ALTERNATIVES cannot hide unresolved branch legality")
    elif constructed.result_status == "AMBIGUOUS":
        if contract is None:
            raise ValueError("AMBIGUOUS requires a complete contract")
        if constructed.selected_scenario_id is not None:
            raise ValueError("AMBIGUOUS cannot select a scenario")
        if len(hypotheses) < 2 or any(
            item.status != "candidate" for item in hypotheses.values()
        ):
            raise ValueError("AMBIGUOUS requires at least two candidate scenarios")
        if not constructed.unresolved:
            raise ValueError("AMBIGUOUS must preserve the missing selection basis")
    else:
        if constructed.selected_scenario_id is not None:
            raise ValueError("INCONCLUSIVE cannot select a scenario")
        if not constructed.unresolved:
            raise ValueError("INCONCLUSIVE must identify missing material and its impact")

    # Evidence/support semantics live in the contract premises and spec.  The v0.4
    # wire no longer asks the model to maintain parallel claim/critical summaries.
    return contract


def validate_contrast(
    contrast: ContrastResult, contract: ScenarioContract, *, strict_mapping: bool = False,
) -> None:
    evidence_ids = {item.id for item in contract.evidence}
    if not set(contrast.evidence_ids) <= evidence_ids:
        raise ValueError("degeneracy contrast references unknown evidence")
    if contrast.status == "proposed":
        if not (contrast.evidence_ids or (
            contrast.support == "assumption" and contrast.assumption_reason
        )):
            raise ValueError("proposed contrast requires evidence or an explicit assumption")
        if any((contrast.alternative, contrast.bypass_behavior,
                contrast.distinguishing_observation)) and not all((
                    contrast.alternative, contrast.bypass_behavior,
                    contrast.distinguishing_observation,
                )):
            raise ValueError("an alternative contrast bundle must be complete when present")
    elif any((
        contrast.alternative,
        contrast.bypass_behavior,
        contrast.distinguishing_observation,
        contrast.revision_requested,
        contrast.revision_reason,
    )):
        raise ValueError("none_identified contrast cannot propose or revise a candidate")
    if contrast.revision_requested:
        if contrast.status != "proposed" or contrast.revision_reason is None:
            raise ValueError("a revision request requires a proposed contrast and reason")
    elif contrast.revision_reason is not None:
        raise ValueError("revision reason requires a revision request")
    if strict_mapping:
        if contrast.semantic_role is None:
            raise ValueError("contrast must declare its semantic role")
        if contrast.support == "evidence":
            if not contrast.evidence_ids or contrast.assumption_reason is not None:
                raise ValueError("evidence-supported contrast requires only evidence IDs")
        elif contrast.support == "assumption":
            if contrast.evidence_ids or contrast.assumption_reason is None:
                raise ValueError("assumption contrast requires only an explicit reason")
        else:
            raise ValueError("contrast must declare evidence or assumption support")


__all__ = [
    "CandidateDraft", "CandidateSelection", "CitationMaterializationError",
    "ConstructResult", "ConstructSelection", "ContrastResult", "ContrastSelection",
    "DeliverResult", "ExplorationStop", "ExtractResult",
    "ExtractSelection", "InvestigationRequest", "QueryAttempt", "QueryResult",
    "QuerySelection", "QuestionUpdate", "QuestionUpdateSelection", "RevisionResult", "RevisionSelection",
    "SourceLocator", "SourceReference", "StageModel",
    "StageResult", "check_source_reference",
    "materialize_construct", "materialize_contract",
    "materialize_contrast", "materialize_extract", "materialize_query", "materialize_reference",
    "project_question_updates",
    "materialize_revision", "validate_constructed", "validate_contrast",
]
