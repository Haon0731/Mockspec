"""Strict stage wire types, citation materialization, and result validation."""

from __future__ import annotations

from copy import deepcopy
from typing import Annotated, Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from mock_spec.scenario import ScenarioContract, validate_contract
from mock_spec.schemas import UnifiedTask
from mock_spec.validation import SourceStore

from specification import SpecSemantics


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


class ExtractSelection(StageModel):
    schema_version: Literal["staged-extract/v0.2"]
    result_status: Literal["CONTINUE", "INCONCLUSIVE"] = "CONTINUE"
    original_goals: list[SourceLocator]
    candidates: list[CandidateSelection]
    unresolved: list[Text]


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


class QuestionUpdate(InvestigationRequest):
    question_id: Text | None
    state: Literal["open", "resolved", "nonblocking"]
    rationale: Text
    evidence: list[SourceLocator]
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
    remaining_unknowns: list[RemainingUnknown]


class QueryResult(StageModel):
    schema_version: Literal["staged-query/v0.1"]
    decision: Literal["request_query", "finish_queries"]
    query_attempt: QueryAttempt | None
    previous_receipt_assessment: QueryAssessment | None = None
    impact: Text
    stop_explanation: ExplorationStop | None = None


class QuerySelection(StageModel):
    schema_version: Literal["staged-query/v0.4"]
    decision: Literal["request_query", "finish_queries"]
    query_attempt: QueryAttempt | None
    previous_receipt_assessment: QueryAssessmentSelection | None
    impact: Text
    stop_explanation: ExplorationStopSelection | None
    question_updates: list[QuestionUpdate] = Field(default_factory=list)
    candidate_updates: list[CandidateSelection] = Field(default_factory=list)


class CriticalBehavior(StageModel):
    behavior: Text
    observation: Text
    evidence_ids: list[Text]
    semantic_role: Literal[
        "dependency_contract", "mut_path_condition", "test_objective"
    ] | None = None
    formal_claim_index: Annotated[int, Field(ge=0)] | None = None
    support: Literal["evidence", "assumption"] | None = None
    assumption_reason: Text | None = None


class ConstructContent(StageModel):
    result_status: Literal["SELECTED", "ALTERNATIVES", "AMBIGUOUS", "INCONCLUSIVE"]
    selected_scenario_id: Text | None
    selection_rationale: Text
    claim_basis: list["ClaimBasis"]
    critical_behaviors: Annotated[list[CriticalBehavior], Field(max_length=3)]
    allowed_variations: list[Text]
    unresolved: list[Text]
    nonblocking_unknowns: list[Text] = Field(default_factory=list)
    spec: SpecSemantics | None = None
    investigation_requests: list[InvestigationRequest] = Field(default_factory=list)


class ConstructResult(ConstructContent):
    schema_version: Literal["staged-construct/v0.1"]
    contract: ScenarioContract | None


class ScenarioContractSelection(ScenarioContract):
    """Formal contract wire DTO that changes only evidence representation."""

    evidence: list[EvidenceSelection]  # type: ignore[assignment]


class ConstructSelection(ConstructContent):
    schema_version: Literal["staged-construct/v0.3"]
    contract: ScenarioContractSelection | None


class ClaimBasis(StageModel):
    claim: Text
    basis: Literal["task_requirement", "implementation_fact", "scenario_inference"]
    target: Literal[
        "selection_rationale", "allowed_variation", "unresolved",
        "nonblocking_unknown", "formal_claim"
    ] | None = None
    target_index: Annotated[int, Field(ge=0)] | None = None
    semantic_role: Literal[
        "dependency_contract", "mut_path_condition", "test_objective"
    ] | None = None
    evidence_ids: list[Text] = Field(default_factory=list)
    support: Literal["evidence", "assumption"] | None = None
    assumption_reason: Text | None = None


class ContrastResult(StageModel):
    schema_version: Literal["staged-contrast/v0.2"]
    status: Literal["proposed", "none_identified"]
    execution_status: Literal["not_executed"]
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


class RevisionResult(StageModel):
    schema_version: Literal["staged-revise/v0.1"]
    revised_construct: ConstructResult
    modified_objects: Annotated[list[Text], Field(min_length=1)]
    evidence_reason: Text
    evidence_ids: Annotated[list[Text], Field(min_length=1)]
    request_another_revision: bool = False


class RevisionSelection(StageModel):
    schema_version: Literal["staged-revise/v0.2"]
    revised_construct: ConstructSelection
    modified_objects: Annotated[list[Text], Field(min_length=1)]
    evidence_reason: Text
    evidence_ids: Annotated[list[Text], Field(min_length=1)]
    request_another_revision: bool = False


class DeliverResult(StageModel):
    schema_version: Literal["staged-deliver/v0.1"]
    result_status: Literal["SELECTED", "ALTERNATIVES", "AMBIGUOUS", "INCONCLUSIVE"]
    selected_scenario_id: Text | None
    contract: ScenarioContract | None


class DeliverSelection(StageModel):
    schema_version: Literal["staged-deliver/v0.3"]
    result_status: Literal["SELECTED", "ALTERNATIVES", "AMBIGUOUS", "INCONCLUSIVE"]
    selected_scenario_id: Text | None
    contract: ScenarioContractSelection | None


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
    if isinstance(raw, ScenarioContractSelection):
        raw = raw.model_dump(mode="json")
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


def contract_selection(contract: ScenarioContract | None) -> dict | None:
    if contract is None:
        return None
    selected = contract.model_dump(mode="json")
    for evidence in selected["evidence"]:
        evidence.pop("quote")
    return selected


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
        unresolved=wire.unresolved,
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
            remaining_unknowns=wire.stop_explanation.remaining_unknowns,
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


def materialize_construct(
    wire: ConstructSelection, store: SourceStore, *, field_path: str = "",
) -> ConstructResult:
    return ConstructResult(
        schema_version="staged-construct/v0.1",
        result_status=wire.result_status,
        selected_scenario_id=wire.selected_scenario_id,
        selection_rationale=wire.selection_rationale,
        claim_basis=wire.claim_basis,
        critical_behaviors=wire.critical_behaviors,
        allowed_variations=wire.allowed_variations,
        unresolved=wire.unresolved,
        nonblocking_unknowns=wire.nonblocking_unknowns,
        spec=wire.spec,
        investigation_requests=wire.investigation_requests,
        contract=materialize_contract(
            wire.contract, store, field_path=f"{field_path}.contract".lstrip("."),
        ),
    )


def materialize_revision(wire: RevisionSelection, store: SourceStore) -> RevisionResult:
    return RevisionResult(
        schema_version="staged-revise/v0.1",
        revised_construct=materialize_construct(
            wire.revised_construct, store, field_path="revised_construct"
        ),
        modified_objects=wire.modified_objects,
        evidence_reason=wire.evidence_reason,
        evidence_ids=wire.evidence_ids,
        request_another_revision=wire.request_another_revision,
    )


def materialize_delivery(wire: DeliverSelection, store: SourceStore) -> DeliverResult:
    return DeliverResult(
        schema_version="staged-deliver/v0.1",
        result_status=wire.result_status,
        selected_scenario_id=wire.selected_scenario_id,
        contract=materialize_contract(wire.contract, store, field_path="contract"),
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
        if not constructed.claim_basis:
            raise ValueError("SELECTED requires claim basis")
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

    if contract is not None:
        evidence_ids = {item.id for item in contract.evidence}
        if any(
            not set(item.evidence_ids) <= evidence_ids
            for item in constructed.critical_behaviors
        ):
            raise ValueError("critical behavior references unknown evidence")
    elif constructed.critical_behaviors:
        raise ValueError("critical behaviors require a complete contract")
    if any(
        not item.evidence_ids and not (
            strict_mapping and item.support == "assumption"
        )
        for item in constructed.critical_behaviors
    ):
        raise ValueError("critical behavior requires evidence unless explicitly assumed")
    if strict_mapping:
        assert contract is not None or constructed.result_status == "INCONCLUSIVE"
        known_evidence = set() if contract is None else {
            item.id for item in contract.evidence
        }
        role_claims: dict[str, list[Any]] = {
            "dependency_contract": [] if contract is None else contract.dependency_constraints,
            "mut_path_condition": [] if contract is None else contract.scenario_hypotheses,
            "test_objective": [] if contract is None else contract.test_objectives,
        }

        def check_support(
            *, support: str | None, ids: list[str], reason: str | None, label: str
        ) -> None:
            if support == "evidence":
                if not ids or reason is not None:
                    raise ValueError(f"{label} evidence support requires only evidence IDs")
                if not set(ids) <= known_evidence:
                    raise ValueError(f"{label} references unknown evidence")
            elif support == "assumption":
                if ids or reason is None:
                    raise ValueError(f"{label} assumption requires only an explicit reason")
            else:
                raise ValueError(f"{label} must declare evidence or assumption support")

        for index, basis in enumerate(constructed.claim_basis):
            if basis.semantic_role is None or basis.target is None:
                raise ValueError("claim basis requires semantic role and target mapping")
            check_support(
                support=basis.support, ids=basis.evidence_ids,
                reason=basis.assumption_reason, label=f"claim basis {index}",
            )
            collection_size = {
                "selection_rationale": 1,
                "allowed_variation": len(constructed.allowed_variations),
                "unresolved": len(constructed.unresolved),
                "nonblocking_unknown": len(constructed.nonblocking_unknowns),
                "formal_claim": len(role_claims[basis.semantic_role]),
            }[basis.target]
            if basis.target_index is None or basis.target_index >= collection_size:
                raise ValueError("claim basis target index is outside its mapped collection")

        mapped = {(item.target, item.target_index) for item in constructed.claim_basis}
        required_mappings = {("selection_rationale", 0)}
        required_mappings.update(
            ("allowed_variation", index)
            for index in range(len(constructed.allowed_variations))
        )
        required_mappings.update(
            ("unresolved", index) for index in range(len(constructed.unresolved))
        )
        required_mappings.update(
            ("nonblocking_unknown", index)
            for index in range(len(constructed.nonblocking_unknowns))
        )
        if not required_mappings <= mapped:
            raise ValueError("important constructed judgments lack claim-basis mappings")

        for index, behavior in enumerate(constructed.critical_behaviors):
            if behavior.semantic_role is None or behavior.formal_claim_index is None:
                raise ValueError("critical behavior requires a formal claim mapping")
            if behavior.formal_claim_index >= len(role_claims[behavior.semantic_role]):
                raise ValueError("critical behavior formal claim index is outside its role")
            check_support(
                support=behavior.support, ids=behavior.evidence_ids,
                reason=behavior.assumption_reason, label=f"critical behavior {index}",
            )
    return contract


def validate_contrast(
    contrast: ContrastResult, contract: ScenarioContract, *, strict_mapping: bool = False,
) -> None:
    evidence_ids = {item.id for item in contract.evidence}
    if not set(contrast.evidence_ids) <= evidence_ids:
        raise ValueError("degeneracy contrast references unknown evidence")
    if contrast.status == "proposed":
        if not all((
            contrast.alternative,
            contrast.bypass_behavior,
            contrast.distinguishing_observation,
        )) or not (contrast.evidence_ids or (
            contrast.support == "assumption" and contrast.assumption_reason
        )):
            raise ValueError("proposed contrast requires an alternative, bypass, evidence, and observation")
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
    "ClaimBasis", "ConstructResult", "ConstructSelection", "ContrastResult",
    "DeliverResult", "DeliverSelection", "ExplorationStop", "ExtractResult",
    "ExtractSelection", "InvestigationRequest", "QueryAttempt", "QueryResult",
    "QuerySelection", "QuestionUpdate", "RevisionResult", "RevisionSelection",
    "ScenarioContractSelection", "SourceLocator", "SourceReference", "StageModel",
    "StageResult", "check_source_reference", "contract_selection",
    "materialize_construct", "materialize_contract", "materialize_delivery",
    "materialize_extract", "materialize_query", "materialize_reference",
    "materialize_revision", "validate_constructed", "validate_contrast",
]
