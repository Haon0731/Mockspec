"""Build the three preregistered generation inputs for issue #34.

Only retained, generation-visible evidence is transformed here. Reference
mocks, candidates, evaluator output, and mutant results are never inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from mock_spec.adapters.python import load_task_with_repository
from mock_spec.validation import SourceStore


HERE = Path(__file__).resolve().parent
EXPERIMENT = HERE.parent
REPOSITORY = EXPERIMENT.parent.parent
FLASK_RESULTS = EXPERIMENT / "results" / "flask"
STUDY = REPOSITORY / "out/mockbench-study/python-issue26-27-20261111-r11/observation-study.json"
GENERATION_SNAPSHOT = "241ef457f8d071c23e6acd658b684b11b54f13fa955e097d6bebe35dc755f47d"
ANALYSIS_SNAPSHOT = "723639bebf43aa4f8a5c007ee21c31b0647d61488945905921fb2351be69b78c"
SLOTS = (
    "mock_stub_001", "mock_stub_002", "mock_stub_003", "mock_stub_004",
    "mock_stub_005", "mock_stub_006", "verify_001", "verify_002",
    "verify_003", "verify_004",
)

PROMPT_TEMPLATE = """You are completing one masked Python pytest test from the frozen flask_308 case.

Use only the initial input, the condition evidence below, and source text returned by the audited source tool. Do not read developer reference mocks, historical candidates, evaluation results, mutation definitions, or external dependency implementations. Do not run the target test. You may make at most 10 source read or search calls.

Fill all ten marked slots in one response. Preserve every line outside the slots. Generate the Mock declarations, Stub behavior, and interaction assertions together. Base every generated behavior and assertion on visible evidence, and mark unresolved claims Unknown.

Return one JSON object with exactly one top-level field, "flask". Its value must contain "slots", an object with exactly the ten slot IDs below, and "rationale", a short array whose claims name a visible source location or are marked Unknown. Each slot value is replacement Python source without the slot's existing indentation.

Required slot IDs:
{slots_json}

Initial input:
{task_json}

Condition evidence:
{condition_evidence}
"""


def rendered(value: Any, *, indent: int | None = 2) -> str:
    return json.dumps(value, ensure_ascii=False, indent=indent)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_record(path: Path, logical_path: str) -> dict[str, Any]:
    data = path.read_bytes()
    return {"path": logical_path, "bytes": len(data), "sha256": digest(data)}


def line_index(sources: dict[str, Any]) -> dict[tuple[str, int], str]:
    result: dict[tuple[str, int], str] = {}
    for source in sources["sources"]:
        for offset, text in enumerate(source["text"].splitlines()):
            result[(source["logical_path"], source["start_line"] + offset)] = text
    return result


def relation_locations(relations: list[dict[str, Any]]) -> set[tuple[str, int]]:
    result: set[tuple[str, int]] = set()
    for relation in relations:
        for endpoint in (relation.get("from", {}), relation.get("to", {})):
            if "logical_path" not in endpoint:
                continue
            for line in range(endpoint["start_line"], endpoint["end_line"] + 1):
                result.add((endpoint["logical_path"], line))
    return result


def full_evidence(
    study: dict[str, Any], sources: dict[str, Any], verified_helper_digests: set[str],
) -> dict[str, Any]:
    case = study["case_result"]
    relations = case["relations"]
    if len(relations) != 13:
        raise ValueError("the frozen Flask analysis must contain all 13 relations")
    lines = line_index(sources)
    locations = relation_locations(relations)
    missing = sorted(location for location in locations if location not in lines)
    if missing:
        raise ValueError(f"associated source lines are unavailable: {missing}")
    helper_digest = next(
        source["sha256"] for source in case["source_scope"]
        if source["logical_path"] == "src/src/flask/helpers.py"
    )
    if helper_digest not in verified_helper_digests:
        raise ValueError("analysis and generation helpers.py differ")
    return {
        "schema": "mock-spec.issue34-full-slice/v1",
        "origin": "frozen observation-study case_result; host semantic-validation and cost fields excluded",
        "identity": case["identity"],
        "source_equivalence": {
            "analysis_snapshot": ANALYSIS_SNAPSHOT,
            "generation_snapshot": GENERATION_SNAPSHOT,
            "masked_test_sha256": case["identity"]["masked_test_sha256"],
            "product_source_sha256": helper_digest,
            "status": "masked test and helpers.py are byte-identical across the two audited snapshots",
        },
        "observations": case["observations"],
        "parameterization": case["parameterization"],
        "entry_point": case["entry_point"],
        "relations": relations,
        "relation_coverage": case["relation_coverage"],
        "analysis_boundaries": case["analysis_boundaries"],
        "completion": case["completion"],
        "unknown_value_constraints": study["unknown_value_constraints"],
        "associated_source_lines": [
            {"logical_path": path, "line": line, "text": lines[(path, line)]}
            for path, line in sorted(locations)
        ],
        "interpretation": [
            "Relations retain their stated certain or analysis_may modality.",
            "A visible dependency call is an interaction candidate, not an automatically required assertion.",
            "Exact exception type, text, code-to-message construction, and unmatched star arguments remain Unknown.",
        ],
    }


def compact_evidence(full: dict[str, Any]) -> dict[str, Any]:
    """Project the same frozen relations into three observation-oriented cards."""
    return {
        "schema": "mock-spec.issue34-compact-navigation/v1",
        "derived_from": {
            "schema": full["schema"],
            "analysis_snapshot": ANALYSIS_SNAPSHOT,
            "generation_snapshot": GENERATION_SNAPSHOT,
            "relation_count": len(full["relations"]),
            "source_equivalence": full["source_equivalence"],
            "covered_relation_indices": list(range(13)),
            "coverage_by_section": {
                "shared_entry": [0, 1],
                "exception_observation": [4, 5, 6],
                "app_text_observation": [2, 7, 9, 11],
                "werkzeug_text_observation": [3, 8, 10, 12],
            },
        },
        "shared_entry": {
            "observation_call": "tests/test_308abort.py:103 calls src/src/flask/helpers.py:265",
            "argument_fact": "actual argument 0 (code) is bound to formal argument 0; *args and **kwargs matching is Unknown",
        },
        "observations": [
            {
                "observation": "tests/test_308abort.py:102 expects an exception around abort(code, *args, **kwargs)",
                "path": [
                    "src/src/flask/helpers.py:282 evaluates product guard _cv_app.get(None) is not None",
                    "when that product guard is true, line 283 may call ctx.app.aborter(code, *args, **kwargs)",
                    "if line 283 completes normally, line 285 may call _wz_abort(code, *args, **kwargs)",
                    "an exception at lines 282, 283, or 285 prevents the corresponding normal continuation",
                ],
                "interaction_candidates": ["_cv_app.get", "ctx.app.aborter", "_wz_abort"],
                "unknown": "exact exception type, value, text, and which dependency supplies it are Unknown",
            },
            {
                "observation": "tests/test_308abort.py:120 checks app-context exception text",
                "test_guard": "tests/test_308abort.py:111 has_app_context true branch controls this assertion",
                "product_guard": "src/src/flask/helpers.py:282 independently checks _cv_app.get(None) is not None",
                "possible_influences": "exceptions from product lines 282, 283, and 285 may influence the captured exception and assertion",
                "unknown": "the analysis does not prove equal test and product guards or infer exact exception text",
            },
            {
                "observation": "tests/test_308abort.py:126 checks no-app-context Werkzeug exception text",
                "test_guard": "tests/test_308abort.py:111 has_app_context false branch controls this assertion",
                "product_guard": "src/src/flask/helpers.py:282 independently checks _cv_app.get(None) is not None",
                "possible_influences": "exceptions from product lines 282, 283, and 285 may influence the captured exception and assertion",
                "unknown": "the analysis does not infer exact Werkzeug behavior or exception text",
            },
        ],
        "assertion_boundary": "calls are visible interaction candidates; the navigation does not make each call a mandatory verification",
    }


def snapshot_segments(old_root: Path, study: dict[str, Any]) -> list[dict[str, Any]]:
    expected = {
        item["logical_path"]: item for item in study["case_result"]["source_scope"]
        if item["logical_path"] in {"tests/test_308abort.py", "src/src/flask/helpers.py"}
    }
    snapshot = (
        old_root / ".omx/developer-realistic-registry-max-fix23"
        / "formal-snapshots/python-benchmark-new" / GENERATION_SNAPSHOT
    )
    task, repository = load_task_with_repository(snapshot, case_id="flask_308")
    store = SourceStore(task, repository=repository)
    contents: dict[str, bytes] = {}
    try:
        for path, record in expected.items():
            source = store.analysis_source(path)
            candidates = (source.text.encode(), (source.text + "\n").encode())
            data = next((item for item in candidates if len(item) == record["bytes"] and digest(item) == record["sha256"]), None)
            if data is None:
                raise ValueError(f"generation snapshot source differs from frozen analysis scope: {path}")
            contents[path] = data
    finally:
        repository.close()
    records = []
    for path, record in sorted(expected.items()):
        generation = contents[path]
        records.append({
            "logical_path": path,
            "analysis_snapshot": ANALYSIS_SNAPSHOT,
            "generation_snapshot": GENERATION_SNAPSHOT,
            "analysis_bytes": record["bytes"],
            "generation_bytes": len(generation),
            "sha256": record["sha256"],
            "byte_identical": True,
            "proof": "generation bytes match the SHA-256 and byte count retained in the frozen analysis source_scope",
        })
    return records


def adapter_task(
    old_root: Path, retained_task: dict[str, Any], retained_sources: dict[str, Any],
) -> dict[str, Any]:
    snapshot = (
        old_root / ".omx/developer-realistic-registry-max-fix23"
        / "formal-snapshots/python-benchmark-new" / GENERATION_SNAPSHOT
    )
    task, repository = load_task_with_repository(snapshot, case_id="flask_308")
    try:
        loaded = task.to_dict()
    finally:
        repository.close()
    if loaded != retained_task:
        raise ValueError("retained task.json differs from the current Adapter output")
    projected_sources = [
        {
            "source_id": source["source_id"],
            "logical_path": source["logical_path"],
            "text": source["text"],
            "start_line": source["start_line"],
        }
        for source in (
            loaded["masked_test"], loaded["mut_source"], *loaded["supporting_sources"]
        )
    ]
    if retained_sources.get("sources") != projected_sources:
        raise ValueError("retained sources.json differs from the current Adapter-visible sources")
    if retained_sources.get("case_id") != loaded["case_id"] or retained_sources.get("snapshot") != GENERATION_SNAPSHOT:
        raise ValueError("retained source identity differs from the Adapter task")
    return loaded


def build(output: Path, old_root: Path) -> None:
    retained_task = json.loads((FLASK_RESULTS / "task.json").read_text(encoding="utf-8"))
    sources = json.loads((FLASK_RESULTS / "sources.json").read_text(encoding="utf-8"))
    study = json.loads(STUDY.read_text(encoding="utf-8"))
    task = adapter_task(old_root, retained_task, sources)
    if task["case_id"] != "flask_308" or sources["snapshot"] != GENERATION_SNAPSHOT:
        raise ValueError("unexpected generation input identity")
    if digest(task["masked_test"]["text"].encode()) != study["case_result"]["identity"]["masked_test_sha256"]:
        raise ValueError("analysis and generation masked tests differ")

    output.mkdir(parents=True, exist_ok=True)
    segments = snapshot_segments(old_root, study)
    helper_digest = next(item["sha256"] for item in segments if item["logical_path"] == "src/src/flask/helpers.py")
    full = full_evidence(study, sources, {helper_digest})
    evidence = {"a": None, "b": full, "c": compact_evidence(full)}
    task_input = {
        key: task[key] for key in (
            "case_id", "masked_test", "mut_source", "supporting_sources",
            "execution_context", "input_policy",
        )
    }
    task_input["snapshot_id"] = GENERATION_SNAPSHOT
    for condition in ("a", "b", "c"):
        directory = output / condition
        directory.mkdir(exist_ok=True)
        value = evidence[condition]
        (directory / "evidence.json").write_text(rendered(value) + "\n", encoding="utf-8")
        evidence_text = (
            "No slice evidence is supplied. Explore only through the common audited source tool."
            if value is None else rendered(value)
        )
        prompt = PROMPT_TEMPLATE.format(
            slots_json=rendered(list(SLOTS)), task_json=rendered(task_input),
            condition_evidence=evidence_text,
        )
        (directory / "prompt.txt").write_text(prompt, encoding="utf-8")

    manifest = {
        "schema": "mock-spec.issue34-generation-inputs/v1",
        "case_id": "flask_308",
        "conditions": {
            "a": "autonomous exploration with no slice evidence",
            "b": "all 13 frozen analysis relations and their associated visible lines",
            "c": "three observation-oriented navigation cards covering relation indices 0 through 12",
        },
        "generation_snapshot": GENERATION_SNAPSHOT,
        "analysis_snapshot": ANALYSIS_SNAPSHOT,
        "source_equivalence_verification": "adapter-loaded generation source hashes matched the frozen analysis source_scope",
        "snapshot_segment_identity": segments,
        "common_limits": {"wall_time_seconds": 600, "source_tool_calls": 10},
        "common_initial_input": {
            name: {"bytes": len(task[field]["text"].encode()),
                   "sha256": digest(task[field]["text"].encode())}
            for name, field in (("masked_test", "masked_test"), ("mut_source", "mut_source"))
        },
        "source_tool": {
            "entrypoint": "experiments/backward-slicing/issue34/source_tool.py",
            "policy": "reviewed fix23 snapshot through load_task_with_repository; identical read/search interface for all groups",
        },
        "prompt_template_sha256": digest(PROMPT_TEMPLATE.encode()),
        "excluded": [
            "developer reference mocks", "historical candidates", "evaluation results",
            "mutation definitions", "external dependency implementations", "host semantic validation",
        ],
        "files": [
            file_record(output / condition / filename, f"inputs/{condition}/{filename}")
            for condition in ("a", "b", "c")
            for filename in ("prompt.txt", "evidence.json")
        ],
    }
    (output / "manifest.json").write_text(rendered(manifest) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-root", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=HERE / "inputs")
    arguments = parser.parse_args()
    build(arguments.output, arguments.old_root)
