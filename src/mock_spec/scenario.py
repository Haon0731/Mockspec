"""Scenario output shape and source-reference checks, not semantic proof."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .schemas import UnifiedTask
from .validation import SourceStore, decode_json


Text = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
Status = Literal["required", "candidate", "unknown"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Evidence(ContractModel):
    id: Text
    source_id: Text
    start_line: Annotated[int, Field(ge=1)]
    end_line: Annotated[int, Field(ge=1)]
    quote: Text
    rationale: Text


class Hypothesis(ContractModel):
    id: Text
    description: Text
    condition: Text
    status: Literal["candidate", "unknown"]
    evidence_ids: list[Text]


class Claim(ContractModel):
    statement: Text
    status: Status
    scenario_ids: list[Text]
    condition: Text | None
    evidence_ids: list[Text]

    @model_validator(mode="after")
    def check_grounding(self) -> Claim:
        if self.status != "unknown" and (not self.evidence_ids or not self.scenario_ids):
            raise ValueError("concrete claims require evidence and scenario references")
        if self.status == "required" and self.condition is None:
            raise ValueError("required claims must declare their applicable condition")
        return self


class Interaction(Claim):
    assertion_policy: Literal["not_implied"]


class VerificationCandidate(Claim):
    status: Literal["candidate", "unknown"]


class ScenarioContract(ContractModel):
    schema_version: Literal["mock-spec.scenario/v0.1"]
    case_id: Text
    language: Literal["java", "python"]
    test_objectives: Annotated[list[Claim], Field(min_length=1)]
    scenario_hypotheses: Annotated[list[Hypothesis], Field(min_length=1)]
    dependency_constraints: list[Claim]
    expected_interactions: list[Interaction]
    verification_candidates: list[VerificationCandidate]
    evidence: list[Evidence]
    uncertainty: list[Claim]

    @model_validator(mode="after")
    def check_references(self) -> ScenarioContract:
        evidence_ids = [item.id for item in self.evidence]
        scenario_ids = [item.id for item in self.scenario_hypotheses]
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("duplicate evidence id")
        if len(set(scenario_ids)) != len(scenario_ids):
            raise ValueError("duplicate scenario id")
        for hypothesis in self.scenario_hypotheses:
            if hypothesis.status != "unknown" and not hypothesis.evidence_ids:
                raise ValueError("candidate hypotheses require evidence")
            if not set(hypothesis.evidence_ids) <= set(evidence_ids):
                raise ValueError("unknown hypothesis evidence reference")
        claims = (
            *self.test_objectives, *self.dependency_constraints,
            *self.expected_interactions, *self.verification_candidates, *self.uncertainty,
        )
        for claim in claims:
            if not set(claim.evidence_ids) <= set(evidence_ids):
                raise ValueError("unknown claim evidence reference")
            if not set(claim.scenario_ids) <= set(scenario_ids):
                raise ValueError("unknown claim scenario reference")
        if any(item.status != "unknown" for item in self.uncertainty):
            raise ValueError("uncertainty entries must have unknown status")
        return self


def validate_contract(
    output: object, task: UnifiedTask, *, source_store: SourceStore | None = None
) -> ScenarioContract:
    """Check shape, task identity, source ranges and exact quotes.

    The caller must obtain task through a reviewed adapter. This function does
    not approve new inputs or prove that a cited passage entails a conclusion.
    """
    if isinstance(output, ScenarioContract):
        # Revalidate even constructed/mutated model instances.
        output = output.model_dump()
    if isinstance(output, str):
        output = decode_json(output)
    contract = ScenarioContract.model_validate(output)
    if (contract.case_id, contract.language) != (task.case_id, task.language):
        raise ValueError("contract task identity mismatch")
    store = source_store or SourceStore(task)
    store.validate_task(task)
    for evidence in contract.evidence:
        try:
            quote = store.quote(
                evidence.source_id, evidence.start_line, evidence.end_line
            )
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if evidence.quote != quote:
            raise ValueError("evidence quote does not match source range")
    return contract
