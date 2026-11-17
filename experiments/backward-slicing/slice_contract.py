"""Strict research contract for conservative backward-slice clues.

The contract validates identity and references.  It deliberately does not turn
well-formed relations into semantic proof, a complete execution explanation,
or a Mock behavior specification.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator


Text = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Status = Literal["grounded", "conditional"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Identity(ContractModel):
    case_id: Text
    dataset: Text
    language: Literal["python", "java"]
    snapshot_id: Text
    entry_source: Literal["automatic", "task_provided", "diagnostic_override"]
    analysis_version: Text


class SourceIdentity(ContractModel):
    source_id: Text
    logical_path: Text
    sha256: Digest
    start_line: Annotated[int, Field(ge=1)]
    end_line: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def valid_range(self) -> SourceIdentity:
        if self.end_line < self.start_line:
            raise ValueError("source range is reversed")
        return self


class SourceReference(ContractModel):
    source_id: Text
    source_sha256: Digest
    start_line: Annotated[int, Field(ge=1)]
    start_column: Annotated[int, Field(ge=0)]
    end_line: Annotated[int, Field(ge=1)]
    end_column: Annotated[int, Field(ge=0)]
    quote: Text

    @model_validator(mode="after")
    def valid_range(self) -> SourceReference:
        if self.end_line < self.start_line:
            raise ValueError("source reference range is reversed")
        if self.start_line == self.end_line and self.end_column <= self.start_column:
            raise ValueError("source reference columns are reversed")
        return self


class Context(ContractModel):
    context_id: Text
    label: Text
    input_facts: dict[str, JsonValue]
    evidence_ids: Annotated[list[Text], Field(min_length=1)]


class Evidence(ContractModel):
    evidence_id: Text
    kind: Literal["source", "rule", "binding"]
    source: SourceReference | None = None
    rule: Text | None = None
    provenance: Text

    @model_validator(mode="after")
    def matching_payload(self) -> Evidence:
        if self.kind == "source" and (self.source is None or self.rule is not None):
            raise ValueError("source evidence requires only a source reference")
        if self.kind in {"rule", "binding"} and (self.rule is None or self.source is not None):
            raise ValueError("rule and binding evidence require only a rule")
        return self


class Observation(ContractModel):
    observation_id: Text
    kind: Literal["value", "exception", "interaction", "state"]
    expression: Text
    evidence_id: Text


class SliceNode(ContractModel):
    node_id: Text
    kind: Literal["statement", "condition", "call", "return", "observation"]
    context_ids: Annotated[list[Text], Field(min_length=1)]
    evidence_id: Text
    relevance: Literal["criterion", "value_source", "control_guard", "call_boundary", "state_effect"]


class Premise(ContractModel):
    premise_id: Text
    kind: Literal["condition", "binding", "normal_completion", "object_identity"]
    expression: Text
    polarity: bool | None
    context_ids: Annotated[list[Text], Field(min_length=1)]
    evidence_ids: Annotated[list[Text], Field(min_length=1)]
    status: Status


class Relation(ContractModel):
    relation_id: Text
    kind: Literal["data", "control", "call", "state"]
    from_node_id: Text
    to_node_id: Text
    observation_ids: Annotated[list[Text], Field(min_length=1)]
    context_id: Text
    premise_ids: list[Text]
    evidence_ids: Annotated[list[Text], Field(min_length=1)]
    status: Status


class UnknownBoundary(ContractModel):
    unknown_id: Text
    kind: Text
    description: Text
    affects_observation_ids: Annotated[list[Text], Field(min_length=1)]
    context_ids: Annotated[list[Text], Field(min_length=1)]
    evidence_ids: Annotated[list[Text], Field(min_length=1)]


class Exclusion(ContractModel):
    exclusion_id: Text
    kind: Text
    description: Text
    evidence_ids: Annotated[list[Text], Field(min_length=1)]


class Coverage(ContractModel):
    status: Literal["partial", "closed_for_declared_scope"]
    declared_scope: Text
    included_context_ids: list[Text]
    unknown_ids: list[Text]


class Cost(ContractModel):
    wall_seconds: Annotated[float, Field(ge=0)] | None
    model_requests: Annotated[int, Field(ge=0)]
    target_executions: Annotated[int, Field(ge=0)]
    note: Text


class SliceClueContract(ContractModel):
    schema_version: Literal["mock-spec.slice-evidence/v1"]
    identity: Identity
    sources: Annotated[list[SourceIdentity], Field(min_length=1)]
    contexts: Annotated[list[Context], Field(min_length=1)]
    evidence: Annotated[list[Evidence], Field(min_length=1)]
    observations: Annotated[list[Observation], Field(min_length=1)]
    slice_nodes: Annotated[list[SliceNode], Field(min_length=1)]
    premises: list[Premise]
    relations: list[Relation]
    unknowns: list[UnknownBoundary]
    exclusions: list[Exclusion]
    coverage: Coverage
    cost: Cost
    semantic_claim: Literal["clues_only_not_semantic_proof"]

    @model_validator(mode="after")
    def references_exist(self) -> SliceClueContract:
        def unique(values: Sequence[str], label: str) -> None:
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {label}")

        def index(items: Sequence[Any], field: str) -> set[str]:
            values = [str(getattr(item, field)) for item in items]
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {field}")
            return set(values)

        source_ids = index(self.sources, "source_id")
        source_by_id = {source.source_id: source for source in self.sources}
        context_ids = index(self.contexts, "context_id")
        evidence_ids = index(self.evidence, "evidence_id")
        observation_ids = index(self.observations, "observation_id")
        node_ids = index(self.slice_nodes, "node_id")
        premise_ids = index(self.premises, "premise_id")
        unknown_ids = index(self.unknowns, "unknown_id")
        index(self.relations, "relation_id")
        index(self.exclusions, "exclusion_id")

        for item in self.evidence:
            if item.source is None:
                continue
            if item.source.source_id not in source_ids:
                raise ValueError("evidence references unknown source")
            source = source_by_id[item.source.source_id]
            if item.source.source_sha256 != source.sha256:
                raise ValueError("evidence source digest mismatch")
            if not (source.start_line <= item.source.start_line <= item.source.end_line <= source.end_line):
                raise ValueError("evidence range is outside source")
        source_spans = [
            (item.source.source_id, item.source.start_line, item.source.start_column,
             item.source.end_line, item.source.end_column)
            for item in self.evidence if item.source is not None
        ]
        if len(source_spans) != len(set(source_spans)):
            raise ValueError("duplicate source evidence span")

        for context in self.contexts:
            unique(context.evidence_ids, "context evidence reference")
            if not set(context.evidence_ids) <= evidence_ids:
                raise ValueError("context references unknown evidence")
        for observation in self.observations:
            if observation.evidence_id not in evidence_ids:
                raise ValueError("observation references unknown evidence")
        for node in self.slice_nodes:
            unique(node.context_ids, "slice node context reference")
            if node.evidence_id not in evidence_ids or not set(node.context_ids) <= context_ids:
                raise ValueError("slice node has an unknown reference")
        for premise in self.premises:
            unique(premise.context_ids, "premise context reference")
            unique(premise.evidence_ids, "premise evidence reference")
            if not set(premise.context_ids) <= context_ids or not set(premise.evidence_ids) <= evidence_ids:
                raise ValueError("premise has an unknown reference")
        for relation in self.relations:
            unique(relation.observation_ids, "relation observation reference")
            unique(relation.premise_ids, "relation premise reference")
            unique(relation.evidence_ids, "relation evidence reference")
            if relation.from_node_id not in node_ids or relation.to_node_id not in node_ids:
                raise ValueError("relation references unknown node")
            if relation.context_id not in context_ids:
                raise ValueError("relation references unknown context")
            if not set(relation.observation_ids) <= observation_ids:
                raise ValueError("relation references unknown observation")
            if not set(relation.premise_ids) <= premise_ids or not set(relation.evidence_ids) <= evidence_ids:
                raise ValueError("relation has an unknown evidence or premise reference")
            endpoint_contexts = {
                node.node_id: set(node.context_ids) for node in self.slice_nodes
            }
            if relation.context_id not in endpoint_contexts[relation.from_node_id]:
                raise ValueError("relation source is outside its context")
            if relation.context_id not in endpoint_contexts[relation.to_node_id]:
                raise ValueError("relation target is outside its context")
        for unknown in self.unknowns:
            unique(unknown.affects_observation_ids, "unknown observation reference")
            unique(unknown.context_ids, "unknown context reference")
            unique(unknown.evidence_ids, "unknown evidence reference")
            if not set(unknown.affects_observation_ids) <= observation_ids:
                raise ValueError("unknown references unknown observation")
            if not set(unknown.context_ids) <= context_ids or not set(unknown.evidence_ids) <= evidence_ids:
                raise ValueError("unknown boundary has an unknown reference")
        for exclusion in self.exclusions:
            unique(exclusion.evidence_ids, "exclusion evidence reference")
            if not set(exclusion.evidence_ids) <= evidence_ids:
                raise ValueError("exclusion references unknown evidence")
        if set(self.coverage.included_context_ids) != context_ids:
            raise ValueError("coverage must enumerate every declared context")
        if not set(self.coverage.unknown_ids) <= unknown_ids:
            raise ValueError("coverage references unknown boundary")
        unique(self.coverage.included_context_ids, "coverage context reference")
        unique(self.coverage.unknown_ids, "coverage unknown reference")
        return self


def validate_source_quotes(
    contract: SliceClueContract, source_texts: dict[str, str],
) -> None:
    """Authenticate source digests and exact quoted spans against immutable input."""
    declared = {source.source_id: source for source in contract.sources}
    if set(source_texts) != set(declared):
        raise ValueError("source text set differs from declared source scope")
    for source_id, source in declared.items():
        text = source_texts[source_id]
        if hashlib.sha256(text.encode()).hexdigest() != source.sha256:
            raise ValueError("source text digest mismatch")
        lines = text.splitlines()
        if len(lines) != source.end_line - source.start_line + 1:
            raise ValueError("source text line range mismatch")
    for item in contract.evidence:
        reference = item.source
        if reference is None:
            continue
        source = declared[reference.source_id]
        lines = source_texts[reference.source_id].splitlines()
        first = reference.start_line - source.start_line
        last = reference.end_line - source.start_line
        try:
            selected = [line.encode() for line in lines[first:last + 1]]
            if first == last:
                quote = selected[0][reference.start_column:reference.end_column].decode()
            else:
                selected[0] = selected[0][reference.start_column:]
                selected[-1] = selected[-1][:reference.end_column]
                quote = b"\n".join(selected).decode()
        except (IndexError, TypeError, UnicodeDecodeError) as exc:
            raise ValueError("source quote coordinates are invalid") from exc
        if quote != reference.quote:
            raise ValueError("source quote differs from authenticated text")


__all__ = ["SliceClueContract", "validate_source_quotes"]
