"""Recover field-specific may evidence for Jib Optional method references."""
from __future__ import annotations

from collections import defaultdict
import re
from typing import Any, Iterable, Mapping, Sequence

from jib_control_evidence import (
    ControlEvidenceError,
    MethodGraph,
    _declared_target,
    _definitions,
    _endpoint,
    _reachable,
    _uses,
    normalize_method_ssa_facts,
)


SCHEMA = "mockspec.jib-optional-evidence/v1"
MUT_PREFIX = "com.google.cloud.tools.jib.cli.jar.JarFiles.toJibContainerBuilder("
IF_PRESENT = "java.util.Optional.ifPresent(Ljava/util/function/Consumer;)V"
BUILDER_SETTER_PREFIX = (
    "com.google.cloud.tools.jib.api.buildplan.ContainerBuildPlan$Builder.set"
)
OPTIONAL_API_SOURCE = (
    "https://docs.oracle.com/en/java/javase/11/docs/api/java.base/java/util/"
    "Optional.html#ifPresent(java.util.function.Consumer)"
)
FIELD_PATTERN = re.compile(
    r"putfield\s+\d+\.<\s*[^,]+,\s*[^,]+,\s*([A-Za-z_$][A-Za-z0-9_$]*),"
)


def _pick(mapping: Mapping[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _as_int_list(value: Any) -> list[int] | None:
    if not isinstance(value, list):
        return None
    if not all(isinstance(item, int) and not isinstance(item, bool) for item in value):
        return None
    return value


def _parameter_descriptor(method_descriptor: str) -> str | None:
    start = method_descriptor.find("(")
    end = method_descriptor.find(")", start + 1)
    if start < 0 or end < 0:
        return None
    return method_descriptor[start + 1:end]


def _lambda_references(raw: Any, mut: MethodGraph) -> dict[int, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    rows = raw.get("lambda_method_references", [])
    result: dict[int, dict[str, Any]] = {}
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            index = _pick(row, ("factory_instruction_index", "instruction_index"))
            if isinstance(index, int) and not isinstance(index, bool):
                if index in result:
                    raise ControlEvidenceError("duplicate lambda method-reference metadata")
                result[index] = row
    for index, fact in mut.facts.items():
        nested = _pick(fact, ("lambda_method_reference", "bootstrap_method_reference"))
        if isinstance(nested, dict):
            if index in result:
                raise ControlEvidenceError("duplicate lambda metadata sources")
            result[index] = nested
    return result


def _unique_definition(graph: MethodGraph, value: int) -> tuple[int, dict[str, Any]] | None:
    matches = [
        (index, fact)
        for index, fact in graph.facts.items()
        if value in _definitions(fact)
    ]
    return matches[0] if len(matches) == 1 else None


def _normal_reaches(graph: MethodGraph, source: int, target: int) -> bool:
    return target in _reachable(graph, graph.normal_successors(source))


def _normal_returns(graph: MethodGraph, source: int) -> list[int]:
    returns = {
        index
        for index, fact in graph.facts.items()
        if "return" in str(fact.get("instruction_type", fact.get("type", ""))).lower()
        or str(fact.get("instruction", "")).lstrip().startswith("return")
    }
    return sorted(_reachable(graph, graph.normal_successors(source)) & returns)


def _method_graph(graphs: Sequence[MethodGraph], method: str) -> MethodGraph | None:
    matches = [graph for graph in graphs if graph.method == method]
    return matches[0] if len(matches) == 1 else None


def _field_write(
    graph: MethodGraph, parameters: Sequence[int]
) -> tuple[int, str] | None:
    candidates = []
    required_parameters = set(parameters)
    for index, fact in graph.facts.items():
        instruction = str(fact.get("instruction", ""))
        if "putfield" not in instruction:
            continue
        match = FIELD_PATTERN.search(instruction)
        if match and required_parameters and required_parameters <= _uses(fact):
            candidates.append((index, match.group(1)))
    return candidates[0] if len(candidates) == 1 else None


def _builder_field_chain(
    graphs: Sequence[MethodGraph], implementation_target: str,
    parameters_by_method: Mapping[str, Sequence[int]],
    bytecode_by_target: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    implementation = _method_graph(graphs, implementation_target)
    if implementation is None:
        bytecode = bytecode_by_target.get(implementation_target)
        return dict(bytecode) if bytecode is not None else None
    parameters = parameters_by_method.get(implementation_target, ())
    if len(parameters) < 2:
        return None
    payload_parameter = parameters[-1]
    candidates = [
        (index, target)
        for index, fact in implementation.facts.items()
        if (target := _declared_target(fact)) is not None
        and target.startswith(BUILDER_SETTER_PREFIX)
        and payload_parameter in _uses(fact)
    ]
    if len(candidates) != 1:
        return None
    call_index, builder_target = candidates[0]
    builder = _method_graph(graphs, builder_target)
    if builder is None:
        return None
    field_write = _field_write(builder, parameters_by_method.get(builder_target, ()))
    if field_write is None:
        return None
    write_index, field = field_write
    return {
        "implementation": _endpoint(implementation, call_index),
        "builder_setter": builder_target,
        "field_write": _endpoint(builder, write_index),
        "field": field,
    }


def _method_section(text: str, method_name: str) -> str | None:
    header = re.compile(
        rf"^  (?:public|private|protected) .+\s{re.escape(method_name)}\([^\n]*\);$",
        re.MULTILINE,
    )
    matches = list(header.finditer(text))
    if len(matches) != 1:
        return None
    next_header = re.search(
        r"^  (?:public|private|protected) .+\([^\n]*\);$",
        text[matches[0].end():],
        re.MULTILINE,
    )
    end = matches[0].end() + next_header.start() if next_header else len(text)
    return text[matches[0].start():end]


def extract_setter_bytecode_evidence(
    jib_container_builder_javap: str,
    plan_builder_javap: str,
    implementation_targets: Sequence[str],
) -> list[dict[str, Any]]:
    """Extract exact setter-to-field chains from saved ``javap -c -p`` output."""
    results = []
    for implementation_target in implementation_targets:
        prefix = implementation_target.split("(", 1)[0]
        method_name = prefix.rsplit(".", 1)[-1]
        implementation_section = _method_section(jib_container_builder_javap, method_name)
        if implementation_section is None:
            continue
        calls = re.findall(
            r"// Method (com/google/cloud/tools/jib/api/buildplan/"
            r"ContainerBuildPlan\$Builder\.[^:]+):([^\s]+)",
            implementation_section,
        )
        if len(calls) != 1:
            continue
        builder_owner_and_name, builder_descriptor = calls[0]
        builder_target = (
            builder_owner_and_name.replace("/", ".") + builder_descriptor
        )
        builder_name = builder_owner_and_name.rsplit(".", 1)[-1]
        builder_section = _method_section(plan_builder_javap, builder_name)
        if builder_section is None:
            continue
        fields = re.findall(r"// Field ([A-Za-z_$][A-Za-z0-9_$]*):([^\s]+)", builder_section)
        putfield_lines = [
            line for line in builder_section.splitlines()
            if "putfield" in line and "// Field " in line
        ]
        if len(fields) != 1 or len(putfield_lines) != 1:
            continue
        field, field_descriptor = fields[0]
        results.append({
            "implementation_target": implementation_target,
            "implementation": {
                "method": implementation_target,
                "evidence": calls[0][0] + ":" + builder_descriptor,
            },
            "builder_setter": builder_target,
            "field_write": {
                "method": builder_target,
                "instruction": putfield_lines[0].strip(),
            },
            "field": field,
            "field_descriptor": field_descriptor,
            "provenance": "saved javap bytecode for JibContainerBuilder and ContainerBuildPlan$Builder",
        })
    return results


def _observations_by_field(
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    for observation in observations:
        observation_id = observation.get("observation_id", observation.get("id"))
        field = observation.get("field")
        if isinstance(observation_id, str) and isinstance(field, str):
            result[field].append(observation_id)
    return dict(result)


def _unsupported(if_present: MethodGraph, index: int, reason: str) -> dict[str, Any]:
    return {
        "if_present": _endpoint(if_present, index),
        "status": "unknown_callback",
        "reason": reason,
        "observation_ids": [],
    }


def map_optional_evidence(
    observations: Sequence[Mapping[str, Any]], raw_facts: Any,
    setter_bytecode_evidence: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Bind verified Optional receivers and method references to exact fields."""
    graphs = normalize_method_ssa_facts(raw_facts)
    mut_matches = [graph for graph in graphs if graph.method.startswith(MUT_PREFIX)]
    if len(mut_matches) != 1:
        raise ControlEvidenceError(f"MUT body is not unique: {len(mut_matches)} matches")
    mut = mut_matches[0]
    raw_methods = raw_facts.get("method_ssa_facts", []) if isinstance(raw_facts, dict) else []
    parameters_by_method = {
        item["method"]: item["parameter_value_numbers"]
        for item in raw_methods
        if isinstance(item, dict)
        and isinstance(item.get("method"), str)
        and isinstance(item.get("parameter_value_numbers"), list)
        and all(isinstance(value, int) for value in item["parameter_value_numbers"])
    }
    references = _lambda_references(raw_facts, mut)
    observations_by_field = _observations_by_field(observations)
    bytecode_by_target = {
        row["implementation_target"]: row
        for row in setter_bytecode_evidence
        if isinstance(row.get("implementation_target"), str)
    }
    relations = []
    unsupported = []

    for index, fact in sorted(mut.facts.items()):
        if _declared_target(fact) != IF_PRESENT:
            continue
        uses = list(fact.get("uses", []))
        if len(uses) != 2 or not all(isinstance(value, int) for value in uses):
            unsupported.append(_unsupported(mut, index, "ifPresent receiver/consumer SSA is invalid"))
            continue
        receiver_value, consumer_value = uses
        receiver_definition = _unique_definition(mut, receiver_value)
        consumer_definition = _unique_definition(mut, consumer_value)
        if receiver_definition is None or consumer_definition is None:
            unsupported.append(_unsupported(mut, index, "receiver or consumer definition is not unique"))
            continue
        getter_index, getter = receiver_definition
        factory_index, factory = consumer_definition
        getter_target = _declared_target(getter)
        factory_target = _declared_target(factory)
        if getter_target is None or not getter_target.endswith("()Ljava/util/Optional;"):
            unsupported.append(_unsupported(mut, index, "ifPresent receiver is not an exact Optional getter result"))
            continue
        if factory_target is None or not factory_target.startswith("java.lang.invoke.LambdaMetafactory."):
            unsupported.append(_unsupported(mut, index, "consumer is not an exact lambda factory result"))
            continue
        reference = references.get(factory_index)
        if reference is None:
            unsupported.append(_unsupported(mut, index, "lambda bootstrap method reference is missing"))
            continue
        metadata_callsite = _pick(reference, ("factory_call_site", "call_site"))
        if metadata_callsite != factory.get("call_site"):
            unsupported.append(_unsupported(mut, index, "lambda metadata callsite mismatch"))
            continue
        reference_kind = _pick(
            reference, ("implementation_reference_kind", "reference_kind")
        )
        implementation_target = _pick(
            reference, ("implementation_target", "method_handle_target")
        )
        instantiated_method_type = reference.get("instantiated_method_type")
        captured = _as_int_list(
            _pick(reference, ("captured_value_numbers", "captured_values"))
        )
        if reference_kind not in {5, "REF_invokeVirtual"} or not isinstance(
            implementation_target, str
        ):
            unsupported.append(_unsupported(mut, index, "unsupported lambda implementation handle"))
            continue
        if not isinstance(instantiated_method_type, str) or _parameter_descriptor(
            instantiated_method_type
        ) != _parameter_descriptor(implementation_target):
            unsupported.append(
                _unsupported(mut, index, "lambda instantiated/implementation type mismatch")
            )
            continue
        if captured is None or captured != list(factory.get("uses", [])):
            unsupported.append(_unsupported(mut, index, "captured receiver SSA mismatch"))
            continue
        if not _normal_reaches(mut, getter_index, index) or not _normal_reaches(
            mut, factory_index, index
        ):
            unsupported.append(_unsupported(mut, index, "getter/factory lacks normal path to ifPresent"))
            continue
        mut_returns = _normal_returns(mut, index)
        if not mut_returns:
            unsupported.append(_unsupported(mut, index, "ifPresent lacks normal continuation to MUT return"))
            continue
        if len(captured) != 1 or not all(
            captured[0] in _uses(mut.facts[return_index]) for return_index in mut_returns
        ):
            unsupported.append(
                _unsupported(mut, index, "captured receiver is not the returned MUT builder")
            )
            continue
        field_chain = _builder_field_chain(
            graphs, implementation_target, parameters_by_method, bytecode_by_target
        )
        if field_chain is None:
            unsupported.append(_unsupported(mut, index, "method reference to exact builder field is unproved"))
            continue
        instantiated_parameter = _parameter_descriptor(instantiated_method_type)
        field_descriptor = field_chain.get("field_descriptor")
        builder_setter = field_chain.get("builder_setter")
        if field_descriptor is not None and (
            field_descriptor != instantiated_parameter
            or not isinstance(builder_setter, str)
            or _parameter_descriptor(builder_setter) != instantiated_parameter
        ):
            unsupported.append(
                _unsupported(mut, index, "setter bytecode payload/field type mismatch")
            )
            continue
        field = field_chain["field"]
        observation_ids = observations_by_field.get(field, [])
        if not observation_ids:
            unsupported.append(_unsupported(mut, index, "exact field has no registered observation"))
            continue
        relations.append({
            "kind": "state_flow",
            "modality": "analysis_may",
            "from": _endpoint(mut, getter_index),
            "to": field_chain["field_write"],
            "provenance": "WALA SSA plus JVM BootstrapMethods method-reference handle",
            "precision": (
                "field-specific conditional API model; payload value and trigger remain unknown"
            ),
            "observation_ids": observation_ids,
            "assumptions": [
                "getter and ifPresent complete normally",
                "Optional contains a value; presence is not established",
            ],
            "if_present": _endpoint(mut, index),
            "consumer_factory": _endpoint(mut, factory_index),
            "implementation_target": implementation_target,
            "implementation_reference_kind": "REF_invokeVirtual",
            "instantiated_method_type": instantiated_method_type,
            "captured_receiver_values": captured,
            "setter_chain": field_chain,
            "evidence_components": {
                "receiver_and_consumer": "exact same-method SSA definitions and uses",
                "method_reference": "exact JVM BootstrapMethods implementation handle",
                "payload_to_action": "Java 11 Optional.ifPresent conditional API model",
                "field_write": "exact implementation-to-builder-setter call and putfield chain",
            },
            "effect": {
                "status": "may_write",
                "field": field,
                "payload": "unknown",
                "must_write": False,
                "default_overwrite": "not_established",
            },
            "normal_continuation": {
                "mut_return_endpoints": [
                    _endpoint(mut, return_index) for return_index in mut_returns
                ]
            },
        })

    return {
        "schema": SCHEMA,
        "status": "verified_incomplete" if relations else "unknown",
        "relations": relations,
        "unsupported_callbacks": unsupported,
        "support": {
            "if_present_count": sum(
                _declared_target(fact) == IF_PRESENT for fact in mut.facts.values()
            ),
            "field_specific_mapping_count": len(relations),
            "unsupported_callback_count": len(unsupported),
        },
        "optional_api_semantics": {
            "source": OPTIONAL_API_SOURCE,
            "rule": "if a value is present, perform the action with it; otherwise do nothing",
            "role": "bounded library semantic summary",
            "runtime_version": "OpenJDK 11.0.33",
            "does_not_establish": [
                "that a value is present",
                "that the consumer executes",
                "the contained payload value",
                "the consumer's exception behavior",
                "the final field state",
            ],
        },
        "boundaries": {
            "optional_presence": "unknown",
            "payload_values": "unknown",
            "mock_behavior": "not inferred",
            "callback_exceptions": "unknown",
            "must_write_or_default_overwrite": "not claimed",
            "unsupported_callbacks": "fail closed without an exact bootstrap handle and field chain",
        },
    }
