"""Map Jib SSA/CFG facts to typed execution-condition evidence.

The mapper deliberately consumes CFG edges rather than slice membership.  It
does not attempt path satisfiability and it never assigns an exception type to
WALA's exceptional edges.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


SCHEMA = "mockspec.jib-control-evidence/v1"
MUT_TARGET = "com.google.cloud.tools.jib.cli.jar.JarFiles.toJibContainerBuilder("
OBSERVATION_OWNER = "com.google.cloud.tools.jib.cli.jar.ObservationSink."
EXIT = -1


class ControlEvidenceError(ValueError):
    """Required identity or CFG evidence is absent or ambiguous."""


@dataclass(frozen=True, order=True)
class Node:
    method: str
    context: str
    instruction_index: int


@dataclass(frozen=True)
class Edge:
    target: int | None
    kind: str


@dataclass
class MethodGraph:
    method: str
    context: str
    facts: dict[int, dict[str, Any]]
    edges: dict[int, list[Edge]]
    phi_uses: dict[int, set[int]]

    def normal_successors(self, index: int) -> set[int]:
        return {
            edge.target
            for edge in self.edges.get(index, ())
            if edge.kind == "normal" and edge.target is not None
        }

    def has_exit(self, index: int, kind: str) -> bool:
        return any(
            edge.kind == kind and edge.target is None
            for edge in self.edges.get(index, ())
        )


def _as_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return None


def _pick(mapping: Mapping[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _block_id(block: Mapping[str, Any]) -> int | None:
    return _as_int(_pick(block, ("block", "block_id", "number", "id")))


def _block_members(block: Mapping[str, Any]) -> list[int]:
    members = _pick(block, ("instruction_indexes", "instructions", "members"))
    if isinstance(members, list):
        result = []
        for item in members:
            value = _as_int(item if not isinstance(item, dict) else item.get("instruction_index"))
            if value is not None:
                result.append(value)
        if result:
            return sorted(set(result))
    first = _as_int(_pick(block, ("first_instruction_index", "first_index", "first")))
    last = _as_int(_pick(block, ("last_instruction_index", "last_index", "last")))
    if first is None or last is None or last < first:
        return []
    return list(range(first, last + 1))


def _successor_ids(block: Mapping[str, Any], kind: str) -> list[int | None]:
    if kind == "normal":
        names = ("normal_successor_blocks", "normal_successors", "normalSuccessors")
    else:
        names = (
            "exceptional_successor_blocks",
            "exceptional_successors",
            "exceptionalSuccessors",
        )
    raw = _pick(block, names)
    if not isinstance(raw, list):
        return []
    result: list[int | None] = []
    for item in raw:
        if item is None:
            result.append(None)
        elif isinstance(item, dict):
            result.append(_block_id(item))
        else:
            result.append(_as_int(item))
    return result


def _edge_kind(value: Any) -> str:
    text = str(value or "normal").lower()
    return "exceptional" if "exception" in text else "normal"


def _instruction_edges(fact: Mapping[str, Any]) -> list[Edge]:
    raw = _pick(fact, ("cfg_successors", "successors"))
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        if isinstance(item, dict):
            target = _as_int(
                _pick(item, ("instruction_index", "target_index", "successor_index", "target"))
            )
            kind = _edge_kind(_pick(item, ("edge_kind", "kind", "type")))
        else:
            target = _as_int(item)
            kind = "normal"
        result.append(Edge(target, kind))
    return result


def _method_graph(method_fact: Mapping[str, Any]) -> MethodGraph:
    method = method_fact.get("method")
    context = method_fact.get("context")
    instructions = method_fact.get("instructions")
    if not isinstance(method, str) or not isinstance(context, str) or not isinstance(instructions, list):
        raise ControlEvidenceError("method_ssa_facts require method, context, and instructions")
    facts: dict[int, dict[str, Any]] = {}
    for raw in instructions:
        if not isinstance(raw, dict):
            continue
        index = _as_int(raw.get("instruction_index"))
        if index is None or index in facts:
            raise ControlEvidenceError(f"invalid or duplicate instruction index in {method} @ {context}")
        fact = dict(raw)
        fact.setdefault("method", method)
        fact.setdefault("context", context)
        facts[index] = fact
    if not facts:
        raise ControlEvidenceError(f"no instructions for {method} @ {context}")

    edges = {index: _instruction_edges(fact) for index, fact in facts.items()}
    blocks = method_fact.get("cfg_blocks", [])
    if not isinstance(blocks, list):
        raise ControlEvidenceError(f"cfg_blocks is not an array for {method} @ {context}")
    block_by_id = {
        block_id: block
        for block in blocks
        if isinstance(block, dict) and (block_id := _block_id(block)) is not None
    }
    members_by_id = {block_id: _block_members(block) for block_id, block in block_by_id.items()}

    def successor_instructions(block_id: int | None) -> set[int | None]:
        if block_id is None:
            return {None}
        pending = [block_id]
        seen: set[int] = set()
        resolved: set[int | None] = set()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            present = [index for index in members_by_id.get(current, ()) if index in facts]
            if present:
                resolved.add(present[0])
                continue
            block = block_by_id.get(current)
            if block is None or block.get("exit") is True:
                resolved.add(None)
                continue
            next_ids = _successor_ids(block, "normal")
            if not next_ids:
                resolved.add(None)
            else:
                pending.extend(item for item in next_ids if item is not None)
                if any(item is None for item in next_ids):
                    resolved.add(None)
        return resolved

    for block_id, members in members_by_id.items():
        present = [index for index in members if index in facts]
        for left, right in zip(present, present[1:]):
            if not edges[left]:
                edges[left].append(Edge(right, "normal"))
        if not present:
            continue
        last = present[-1]
        block = block_by_id[block_id]
        for kind in ("normal", "exceptional"):
            for successor_id in _successor_ids(block, kind):
                for target in successor_instructions(successor_id):
                    edges[last].append(Edge(target, kind))
    phi_uses: dict[int, set[int]] = {}
    raw_phis = method_fact.get("phi_instructions", [])
    if isinstance(raw_phis, list):
        for phi in raw_phis:
            if not isinstance(phi, dict):
                continue
            uses = _uses(phi)
            for definition in _definitions(phi):
                phi_uses[definition] = uses
    return MethodGraph(method, context, facts, edges, phi_uses)


def normalize_method_ssa_facts(value: Any) -> list[MethodGraph]:
    """Normalize the nested probe payload, rejecting identity collisions."""
    if isinstance(value, dict):
        value = value.get("method_ssa_facts")
    if not isinstance(value, list):
        raise ControlEvidenceError("method_ssa_facts must be an array")
    graphs = [_method_graph(item) for item in value if isinstance(item, dict)]
    identities = [(graph.method, graph.context) for graph in graphs]
    if len(set(identities)) != len(identities):
        raise ControlEvidenceError("duplicate method/context facts")
    return graphs


def _reachable(graph: MethodGraph, starts: Iterable[int]) -> set[int]:
    reached: set[int] = set()
    pending = deque(starts)
    while pending:
        index = pending.popleft()
        if index in reached or index not in graph.facts:
            continue
        reached.add(index)
        pending.extend(graph.normal_successors(index) - reached)
    return reached


def _postdominators(graph: MethodGraph) -> dict[int, set[int]]:
    nodes = set(graph.facts)
    universe = nodes | {EXIT}
    result = {node: set(universe) for node in nodes}
    result[EXIT] = {EXIT}
    changed = True
    while changed:
        changed = False
        for node in sorted(nodes, reverse=True):
            successors = graph.normal_successors(node) or {EXIT}
            common = set(universe)
            for successor in successors:
                common &= result[successor]
            updated = {node} | common
            if updated != result[node]:
                result[node] = updated
                changed = True
    return result


def _control_edges(graph: MethodGraph) -> list[tuple[int, int, int]]:
    postdominators = _postdominators(graph)
    result = []
    for branch in graph.facts:
        successors = sorted(graph.normal_successors(branch))
        if len(successors) < 2:
            continue
        for ordinal, successor in enumerate(successors):
            for controlled in postdominators[successor] - postdominators[branch]:
                if controlled != EXIT:
                    result.append((branch, controlled, ordinal))
    return sorted(set(result))


def _declared_target(fact: Mapping[str, Any]) -> str | None:
    value = _pick(fact, ("declared_target", "target", "call_target"))
    return value if isinstance(value, str) else None


def _is_invoke(fact: Mapping[str, Any]) -> bool:
    return _declared_target(fact) is not None


def _uses(fact: Mapping[str, Any]) -> set[int]:
    raw = fact.get("uses", [])
    return {value for item in raw if (value := _as_int(item)) is not None} if isinstance(raw, list) else set()


def _definitions(fact: Mapping[str, Any]) -> set[int]:
    raw = fact.get("definitions", fact.get("defs", []))
    return {value for item in raw if (value := _as_int(item)) is not None} if isinstance(raw, list) else set()


def _predicate_producers(graph: MethodGraph, branch: int) -> list[int]:
    definitions: dict[int, list[int]] = defaultdict(list)
    for index, fact in graph.facts.items():
        for value in _definitions(fact):
            definitions[value].append(index)
    pending = deque(_uses(graph.facts[branch]))
    seen_values: set[int] = set()
    seen_instructions: set[int] = set()
    while pending:
        value = pending.popleft()
        if value in seen_values:
            continue
        seen_values.add(value)
        pending.extend(graph.phi_uses.get(value, ()))
        for index in definitions.get(value, ()):
            if index in seen_instructions:
                continue
            seen_instructions.add(index)
            pending.extend(_uses(graph.facts[index]))
    return sorted(index for index in seen_instructions if _is_invoke(graph.facts[index]))


def _endpoint(graph: MethodGraph, index: int) -> dict[str, Any]:
    fact = graph.facts[index]
    endpoint: dict[str, Any] = {
        "method": graph.method,
        "context": graph.context,
        "instruction_index": index,
    }
    for source, target in (
        ("call_site", "call_site"),
        ("declared_target", "declared_target"),
        ("source_position", "source_position"),
    ):
        if fact.get(source) is not None:
            endpoint[target] = fact[source]
    return endpoint


def _observations(value: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in value:
        observation_id = item.get("observation_id", item.get("id"))
        criterion = item.get("criterion")
        if not isinstance(observation_id, str) or not isinstance(criterion, str):
            raise ControlEvidenceError("observations require observation_id/id and criterion")
        result.append({"observation_id": observation_id, "criterion": criterion})
    if len({item["observation_id"] for item in result}) != len(result):
        raise ControlEvidenceError("duplicate observation id")
    return result


def _find_unique_call(
    graphs: Sequence[MethodGraph], target_test: Any, label: str
) -> tuple[MethodGraph, int]:
    matches = [
        (graph, index)
        for graph in graphs
        for index, fact in graph.facts.items()
        if (target := _declared_target(fact)) is not None and target_test(target)
    ]
    if len(matches) != 1:
        raise ControlEvidenceError(f"{label} callsite is not unique: {len(matches)} matches")
    return matches[0]


def _normal_path_reaches(graph: MethodGraph, source: int, target: int) -> bool:
    return target in _reachable(graph, graph.normal_successors(source))


def _exception_prevents(graph: MethodGraph, source: int, target: int) -> bool:
    exceptional = [edge for edge in graph.edges.get(source, ()) if edge.kind == "exceptional"]
    if not exceptional:
        return False
    for edge in exceptional:
        if edge.target is None or target not in _reachable(graph, [edge.target]):
            return True
    return False


def map_control_evidence(
    observations: Sequence[Mapping[str, Any]], method_ssa_facts: Any
) -> dict[str, Any]:
    """Return verified CFG evidence for the 19 post-MUT Jib observations."""
    mapped_observations = _observations(observations)
    graphs = normalize_method_ssa_facts(method_ssa_facts)
    mut_graph, mut_call = _find_unique_call(
        graphs, lambda target: target.startswith(MUT_TARGET), "MUT"
    )
    mut_body_matches = [graph for graph in graphs if graph.method.startswith(MUT_TARGET)]
    if len(mut_body_matches) != 1:
        raise ControlEvidenceError(
            f"MUT body method/context is not unique: {len(mut_body_matches)} matches"
        )
    mut_body = mut_body_matches[0]

    observation_sites: dict[str, tuple[MethodGraph, int]] = {}
    for observation in mapped_observations:
        criterion = observation["criterion"]
        if not criterion.startswith(OBSERVATION_OWNER):
            raise ControlEvidenceError(f"criterion is not an ObservationSink call: {criterion}")
        graph, index = _find_unique_call(
            graphs, lambda target, criterion=criterion: target == criterion, criterion
        )
        if (graph.method, graph.context) != (mut_graph.method, mut_graph.context):
            raise ControlEvidenceError("MUT and observation callsites have different caller contexts")
        if not _normal_path_reaches(graph, mut_call, index):
            raise ControlEvidenceError(
                f"observation {observation['observation_id']} lacks a normal CFG path after MUT"
            )
        observation_sites[observation["observation_id"]] = (graph, index)

    relations: list[dict[str, Any]] = []
    for observation_id, (graph, index) in observation_sites.items():
        relations.append({
            "kind": "normal_return_dependence",
            "modality": "analysis_may",
            "from": _endpoint(mut_graph, mut_call),
            "to": _endpoint(graph, index),
            "provenance": "WALA local CFG normal successor reachability",
            "precision": (
                "exact method/context/instruction/callsite/target; a normal path exists, "
                "but dominance/necessity is not established"
            ),
            "observation_ids": [observation_id],
            "assumptions": [],
        })
        if _exception_prevents(graph, mut_call, index):
            relations.append({
                "kind": "exception_dependence",
                "modality": "analysis_may",
                "from": _endpoint(mut_graph, mut_call),
                "to": _endpoint(graph, index),
                "provenance": "WALA local CFG exceptional successor",
                "precision": "exceptional non-continuation; exception type and feasibility unknown",
                "observation_ids": [observation_id],
                "assumptions": ["no complete path satisfiability result"],
            })

    control_relations = []
    mut_returns = {
        index
        for index, fact in mut_body.facts.items()
        if "return" in str(fact.get("instruction_type", fact.get("type", ""))).lower()
        or str(fact.get("instruction", "")).lstrip().startswith("return")
    }
    for graph in (mut_body,):
        for branch, controlled, successor_ordinal in _control_edges(graph):
            controlled_fact = graph.facts[controlled]
            if not _is_invoke(controlled_fact):
                continue
            producers = _predicate_producers(graph, branch)
            producer_targets = [
                target
                for index in producers
                if (target := _declared_target(graph.facts[index])) is not None
            ]
            control_reached_returns = sorted(
                _reachable(graph, graph.normal_successors(controlled)) & mut_returns
            )
            has_exceptional_exit = graph.has_exit(controlled, "exceptional")
            observation_ids = (
                list(observation_sites)
                if control_reached_returns and has_exceptional_exit
                else []
            )
            attribution: dict[str, Any]
            if observation_ids:
                attribution = {
                    "status": "composed_execution_may",
                    "mechanism": (
                        "branch controls conditional call; the call has a normal CFG path to "
                        "MUT return and an exceptional exit; the entry MUT call has a normal "
                        "CFG continuation to each referenced observation"
                    ),
                    "mut_return_endpoints": [
                        _endpoint(graph, return_index)
                        for return_index in control_reached_returns
                    ],
                    "entry_mut_call": _endpoint(mut_graph, mut_call),
                    "exceptional_exit": {
                        "scope": "MUT exceptional exit before post-return observations",
                        "exception_type": "unknown",
                    },
                    "semantic_boundary": (
                        "does not claim that the branch directly controls a post-merge "
                        "observation or that the conditional call supplies its field value"
                    ),
                }
            else:
                attribution = {
                    "status": "not_attributed",
                    "reason": (
                        "controlled call lacks the required normal-to-MUT-return and explicit "
                        "exceptional-exit composition"
                    ),
                }
            control_relations.append({
                "kind": "control_dependence",
                "modality": "analysis_may",
                "from": _endpoint(graph, branch),
                "to": _endpoint(graph, controlled),
                "provenance": "WALA local CFG postdominators",
                "precision": "exact branch successor region; path satisfiability not solved",
                "observation_ids": observation_ids,
                "assumptions": [],
                "branch_successor_ordinal": successor_ordinal,
                "predicate_producers": [
                    _endpoint(graph, producer) for producer in producers
                ],
                "predicate_targets": producer_targets,
                "observation_attribution": attribution,
            })
    relations.extend(control_relations)

    internal_normal_relations = []
    internal_exception_relations = []
    for graph in graphs:
        if not graph.method.startswith("com.google.cloud.tools.jib.cli.jar.JarFiles."):
            continue
        normal_returns = {
            index
            for index, fact in graph.facts.items()
            if "return" in str(fact.get("instruction_type", fact.get("type", ""))).lower()
            or str(fact.get("instruction", "")).lstrip().startswith("return")
        }
        for index, fact in graph.facts.items():
            if not _is_invoke(fact):
                continue
            reached_returns = _reachable(graph, graph.normal_successors(index)) & normal_returns
            if normal_returns and not reached_returns:
                continue
            for return_index in sorted(reached_returns):
                internal_normal_relations.append({
                    "kind": "normal_return_dependence",
                    "modality": "analysis_may",
                    "from": _endpoint(graph, index),
                    "to": _endpoint(graph, return_index),
                    "provenance": "WALA local CFG normal successor reachability",
                    "precision": (
                        "call normal continuation may reach MUT return; call execution may be conditional"
                    ),
                    "observation_ids": list(observation_sites),
                    "assumptions": ["no complete path satisfiability result"],
                })
            if not graph.has_exit(index, "exceptional"):
                continue
            internal_exception_relations.append({
                "kind": "exception_dependence",
                "modality": "analysis_may",
                "from": _endpoint(graph, index),
                "to": {"scope": "MUT exceptional exit before all post-return observations"},
                "provenance": "WALA local CFG exceptional exit",
                "precision": "may prevent MUT normal return; exception type and feasibility unknown",
                "observation_ids": list(observation_sites),
                "assumptions": ["no complete path satisfiability result"],
            })
    relations.extend(internal_normal_relations)
    relations.extend(internal_exception_relations)

    unknown_execution_conditions = [
        {
            "callsite": _endpoint(graph, index),
            "status": "unknown_condition",
            "conditional_effect": "Optional callback may execute after a normal ifPresent call",
            "precision": (
                "Optional payload/emptiness and callback execution are not established by CFG"
            ),
            "observation_ids": [],
            "scope": "callback execution condition only",
            "data_field_attribution": "not_evaluated",
        }
        for graph in graphs
        for index, fact in graph.facts.items()
        if (_declared_target(fact) or "").startswith("java.util.Optional.ifPresent(")
    ]

    kinds = {relation["kind"] for relation in relations}
    coverage = {
        kind: ("verified_incomplete" if kind in kinds else "not_evaluated")
        for kind in ("control_dependence", "normal_return_dependence", "exception_dependence")
    }
    return {
        "schema": SCHEMA,
        "status": "verified_incomplete",
        "observations": mapped_observations,
        "relations": relations,
        "unknown_execution_conditions": unknown_execution_conditions,
        "relation_coverage": coverage,
        "support": {
            "method_context_count": len(graphs),
            "mapped_observation_count": len(observation_sites),
            "control_edge_count": len(control_relations),
            "internal_normal_continuation_count": len(internal_normal_relations),
            "internal_exception_edge_count": len(internal_exception_relations),
            "unknown_callback_condition_count": len(unknown_execution_conditions),
        },
        "boundaries": {
            "path_satisfiability": "not_evaluated",
            "exception_types": "unknown",
            "native_call_effects": "not inferred from CFG or slice membership",
            "source_positions": "reported only when supplied by the probe",
        },
    }
