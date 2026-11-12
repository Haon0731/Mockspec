import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("generic_java_backend", HERE / "generic_backend.py")
subject = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subject)


def completed(code, stderr=""):
    return subprocess.CompletedProcess(["javac"], code, "", stderr)


def test_structural_harness_uses_only_source_attributed_parameters():
    source = "void selected() { Product.run(dependency); }"
    results = iter([
        (completed(1, "X.java:1: error: cannot find symbol\n  symbol: variable dependency\n"), 0.1),
        (completed(0), 0.1),
    ])
    written = []
    resolution = {"parameters": [{"name": "dependency", "type": "q.Service",
                                   "evidence": [{"source_path": "p/Test.java", "line": 1,
                                                 "kind": "call_argument"}]}],
                  "unresolved": []}
    harness, parameters, attempts = subject._prepare_harness(
        source, "selected", written.append, lambda: next(results),
        constraint_resolver=lambda missing: resolution)
    assert harness == "void selected(q.Service dependency) { Product.run(dependency); }"
    assert parameters == resolution["parameters"]
    assert attempts[-1]["kind"] == "source_attributed_structural_parameter_harness"
    assert len(attempts) == 2


def test_structural_harness_rejects_unconstrained_compile_failure():
    source = "void selected() { consume(dependency); }"
    results = iter([
        (completed(1, "X.java:1: error: cannot find symbol\n  symbol: variable dependency\n"), 0.1),
    ])
    recorded = []
    with pytest.raises(ValueError, match="not uniquely constrained"):
        subject._prepare_harness(source, "selected", lambda _: None, lambda: next(results),
                                 record_attempts=lambda attempts: recorded.append(list(attempts)),
                                 constraint_resolver=lambda missing: {
                                     "parameters": [], "unresolved": [{
                                         "name": "dependency", "reason": "no_structural_type_evidence"}]})
    assert len(recorded[-1]) == 1


def test_structural_harness_rejects_missing_constraint_resolver():
    source = "void selected() { consume(dependency); }"
    result = (completed(1, "X.java:1: error: cannot find symbol\n  symbol: variable dependency\n"), 0.1)
    with pytest.raises(ValueError, match="require source-attributed"):
        subject._prepare_harness(source, "selected", lambda _: None, lambda: result)


def test_criterion_query_uses_exact_candidate_call_and_owner_qualified_constructor():
    source = """new Random().nextBytes(data);
assertAppendRequestParameters(appendInvocations(client).get(0), expected);
"""
    assert subject._criterion_query(source, {
        "line": 1, "kind": "candidate_event", "call": "Random",
    }) == "SOURCE_LINE:1:-1:.Random.<init>("
    assert subject._criterion_query(source, {
        "line": 1, "kind": "candidate_event", "call": "nextBytes",
    }) == "SOURCE_LINE:1:-1:.nextBytes("
    assert subject._criterion_query(source, {
        "line": 2, "kind": "candidate_event", "call": "assertAppendRequestParameters",
    }) == "SOURCE_LINE:2:-1:.assertAppendRequestParameters("
    assert subject._criterion_query(source, {
        "line": 2, "kind": "candidate_event", "call": "appendInvocations",
    }) == "SOURCE_LINE:2:-1:.appendInvocations("
    assert subject._criterion_query(source, {
        "line": 2, "kind": "candidate_event", "call": "get",
    }) == "SOURCE_LINE:2:-1:.get("


def test_criterion_query_rejects_unsupported_and_repeated_seed_shapes():
    with pytest.raises(ValueError, match="supported observation"):
        subject._criterion_query("assert actual;", {
            "line": 1, "kind": "value_assertion",
        })
    with pytest.raises(ValueError, match="absent or repeated"):
        subject._criterion_query("target.run(); target.run();", {
            "line": 1, "kind": "candidate_event", "call": "run",
        })
    with pytest.raises(ValueError, match="qualified or nested"):
        subject._criterion_query("new Outer.Inner();", {
            "line": 1, "kind": "candidate_event", "call": "Inner",
        })


def test_query_preflight_excludes_every_duplicate_and_preserves_all_invalid():
    duplicate = [
        {"criterion_id": "criterion:0", "line": 1,
         "kind": "candidate_event", "call": "run"},
        {"criterion_id": "criterion:1", "line": 1,
         "kind": "candidate_event", "call": "run"},
    ]
    queries, query_by_text, failures, records = subject._preflight_criterion_queries(
        "target.run();", duplicate)
    assert queries == []
    assert query_by_text == {}
    assert [item["observation_id"] for item in failures] == ["criterion:0", "criterion:1"]
    assert [item["status"] for item in records] == ["duplicate", "duplicate"]

    invalid = [{"criterion_id": "criterion:0", "line": 1,
                "kind": "value_assertion"}]
    queries, query_by_text, failures, records = subject._preflight_criterion_queries(
        "consume(value);", invalid)
    assert queries == [] and query_by_text == {}
    assert failures[0]["error"].startswith("unsupported_seed_shape:")
    assert records[0]["status"] == "unsupported"
    merged = subject._merge_criterion_analyses(invalid, [], failures)
    assert [item["observation_id"] for item in merged] == ["criterion:0"]
    assert merged[0]["graph_created"] is False


def test_filtered_supported_roots_keep_request_indices_effect_demand_and_original_order(tmp_path):
    criteria = [
        {"criterion_id": "criterion:0"},
        {"criterion_id": "criterion:1"},
        {"criterion_id": "criterion:2"},
    ]
    queries = ["q1", "q2"]
    query_by_text = {"q1": criteria[1], "q2": criteria[2]}
    supported = [
        {"observation_id": "criterion:1", "raw": {
            "observation_effect_demand": {"status": "demanded_instance_field"}},
         "root_mapped": True, "checkpoint_root_index": 0, "checkpoint_query": "q1"},
        {"observation_id": "criterion:2", "raw": {
            "observation_effect_demand": {"status": "not_demanded"}},
         "root_mapped": True, "checkpoint_root_index": 1, "checkpoint_query": "q2"},
    ]
    demanded = subject._demanded_effect_roots(
        queries, query_by_text, supported,
        {"q1": tmp_path / "root-00000.json", "q2": tmp_path / "root-00001.json"})
    assert [(query, criterion["criterion_id"], analysis["checkpoint_root_index"])
            for query, criterion, analysis in demanded] == [("q1", "criterion:1", 0)]
    unsupported = [{"observation_id": "criterion:0", "raw": None,
                    "error": "unsupported_seed_shape", "root_mapped": False}]
    merged = subject._merge_criterion_analyses(criteria, supported, unsupported)
    assert [item["observation_id"] for item in merged] == [
        "criterion:0", "criterion:1", "criterion:2"]
    assert [item.get("checkpoint_root_index") for item in merged] == [None, 0, 1]
    assert [item.get("checkpoint_query") for item in merged] == [None, "q1", "q2"]


def test_java_compilation_disables_annotation_processors():
    source = Path(subject._compile.__code__.co_filename).read_text()
    assert '"-proc:none"' in source


def test_backend_source_keeps_legacy_and_unique_source_line_criteria():
    source = subject.BACKEND_SOURCE.read_text()
    assert 'criterion.startsWith("SOURCE_LINE:")' in source
    assert "requestedOrdinal == -1 && matches.size() != 1" in source
    assert 'if ("RETURN".equals(criterion)' in source
    assert "GENERIC_NO_HEAP" in source
    assert "genericNoHeap" in source
    assert "hashBackedBackwardSlice(sdg, seed, 50_000)" in source
    assert "Slicer.computeBackwardSlice(sdg, seed)" in source


def test_opaque_summary_skips_array_field_enumeration_but_keeps_arrays_unknown():
    source = subject.BACKEND_SOURCE.read_text()
    assert "argumentClass == null || argumentClass.isArrayClass()" in source
    assert '"arrays and collection contents"' in source


def test_bounded_source_closure_resolves_static_import_owner():
    class Source:
        text = "package p.internal; final class Resources {}"
        start_line = 1

    class Case:
        source_paths = ("src/main/java/p/Schema.java", "src/main/java/p/internal/Resources.java")

        def read_source(self, path):
            assert path == "src/main/java/p/internal/Resources.java"
            return Source()

    selected = [
        {"source_id": "test", "logical_path": "src/test/java/p/SchemaTest.java", "text": "", "start_line": 1},
        {"source_id": "source:1", "logical_path": "src/main/java/p/Schema.java",
         "text": "package p;\nimport static p.internal.Resources.load;\nfinal class Schema {}", "start_line": 1},
    ]
    rows, evidence = subject._expand_java_source_closure(
        Case(), selected, max_files=3, max_bytes=10_000)
    assert [row["logical_path"] for row in rows][-1] == "src/main/java/p/internal/Resources.java"
    assert evidence[-1]["status"] == "selected_unique_visible_type"


def test_typed_product_path_requires_homogeneous_sdg_labels():
    raw = {
        "seed_index": 2,
        "slice_statements": [
            {"method": "p.Product.value()I", "line": 7},
            {"method": "p.Product.value()I", "line": 8},
            {"method": "p.Test.test()V", "line": 20},
        ],
        "slice_edges": [
            {"from": 0, "to": 1, "labels": ["DATA_DEP"]},
            {"from": 1, "to": 2, "labels": ["HEAP_DATA_DEP"]},
        ],
    }
    paths, suppressed = subject._typed_product_paths(
        raw, {"p.Product": "src/main/java/p/Product.java"}, {})
    assert suppressed == []
    assert {(row["kind"], row["source_line"]) for row in paths} == {
        ("value_flow", 7), ("value_flow", 8)}
    raw["slice_edges"][1]["labels"] = ["CONTROL_DEP"]
    mixed, suppressed = subject._typed_product_paths(
        raw, {"p.Product": "src/main/java/p/Product.java"}, {})
    assert suppressed == []
    assert [(row["kind"], row["source_line"]) for row in mixed] == [("control_dependence", 8)]


def test_typed_product_path_through_opaque_placeholder_is_suppressed():
    raw = {
        "seed_index": 2,
        "slice_statements": [
            {"method": "p.Product.value()I", "line": 7},
            {"method": "p.Product.placeholder()I", "line": 12},
            {"method": "p.Test.test()V", "line": 20},
        ],
        "slice_edges": [
            {"from": 0, "to": 1, "labels": ["DATA_DEP"]},
            {"from": 1, "to": 2, "labels": ["DATA_DEP"]},
        ],
    }
    opaque = {"src/main/java/p/Product.java": [
        {"source_id": "source:1", "name": "placeholder", "start_line": 10, "end_line": 13}
    ]}
    paths, suppressed = subject._typed_product_paths(
        raw, {"p.Product": "src/main/java/p/Product.java"}, opaque)
    assert paths == []
    assert suppressed[0]["opaque_hits"][0]["name"] == "placeholder"


def test_only_observation_path_opaque_crossings_become_unknown_effects():
    boundaries = [{
        "scope": "criterion:0", "reason": "typed_path_crosses_opaque_placeholder",
        "details": {"kind": "value_flow", "source_path": "src/main/java/p/Product.java",
                    "source_line": 7, "opaque_hits": [{
                        "source_id": "source:1", "logical_path": "src/main/java/p/Product.java",
                        "name": "placeholder", "start_line": 10, "end_line": 13,
                        "statement_index": 1,
                    }]},
    }, {
        "scope": "source_closure", "reason": "opaque_placeholder_inventory",
        "details": {"opaque_hits": [{"source_id": "source:2", "name": "unrelated",
                                       "start_line": 30, "end_line": 31}]},
    }]
    effects = subject._observed_opaque_unknown_effects(boundaries)
    assert len(effects) == 1
    assert effects[0]["scope"]["observation_id"] == "criterion:0"
    assert effects[0]["scope"]["placeholder"]["name"] == "placeholder"
    assert "unrelated" not in json.dumps(effects)


def test_opaque_unknown_effect_deduplicates_statements_inside_same_placeholder():
    hit = {"source_id": "source:1", "logical_path": "src/main/java/p/Product.java",
           "name": "placeholder", "start_line": 10, "end_line": 13}
    boundaries = [{
        "scope": "criterion:0", "reason": "typed_path_crosses_opaque_placeholder",
        "details": {"kind": "value_flow", "source_path": "src/main/java/p/Product.java",
                    "source_line": 7, "opaque_hits": [
                        {**hit, "statement_index": 1}, {**hit, "statement_index": 2}]},
    }]
    effects = subject._observed_opaque_unknown_effects(boundaries)
    assert len(effects) == 1
    assert effects[0]["boundary_id"] == "opaque:criterion:0:source:1:10:placeholder"
    assert effects[0]["scope"]["observation_statement_indexes"] == [1, 2]


def test_product_body_paths_require_typed_segments_and_keep_state_write_boundary():
    raw = {
        "seed_index": 4,
        "slice_statements": [
            {"method": "p.Product.value()I", "context": "C", "instruction_index": 1, "line": 7},
            {"method": "p.Product.value()I", "context": "C", "instruction_index": 2, "line": 8},
            {"method": "p.Product.value()I", "context": "C", "instruction_index": 3, "line": 9},
            {"method": "p.Product.value()I", "context": "C", "instruction_index": 4, "line": 10},
            {"method": "p.Test.test()V", "context": "C", "instruction_index": 5, "line": 20},
        ],
        "slice_edges": [
            {"from": 0, "to": 1, "labels": ["CONTROL_DEP"]},
            {"from": 1, "to": 4, "labels": ["DATA_DEP"]},
            {"from": 2, "to": 4, "labels": ["DATA_DEP"]},
            {"from": 3, "to": 4, "labels": ["CONTROL_DEP"]},
        ],
        "product_body_evidence": [
            {"kind": "control_guard", "method": "p.Product.value()I", "context": "C",
             "instruction_index": 1, "retained_instruction_index": 2, "line": 7},
            {"kind": "value_return", "method": "p.Product.value()I", "context": "C",
             "instruction_index": 3, "retained_instruction_index": 3, "line": 9},
            {"kind": "retained_state_write_witness", "method": "p.Product.value()I", "context": "C",
             "instruction_index": 4, "retained_instruction_index": 4, "line": 10},
        ],
    }
    paths, boundaries = subject._product_body_paths(
        raw, {"p.Product": "src/main/java/p/Product.java"})
    assert {(row["kind"], row["source_line"]) for row in paths} == {
        ("control_dependence", 7), ("normal_return_dependence", 9)}
    assert boundaries[0]["reason"] == "retained_state_write_without_heap_dependency_path"


def test_product_body_paths_reject_self_guard_and_reconverged_branch():
    raw = {
        "seed_index": 2,
        "slice_statements": [
            {"method": "p.Product.value()I", "context": "C", "instruction_index": 1, "line": 7},
            {"method": "p.Product.value()I", "context": "C", "instruction_index": 2, "line": 8},
            {"method": "p.Test.test()V", "context": "C", "instruction_index": 3, "line": 20},
        ],
        "slice_edges": [{"from": 1, "to": 2, "labels": ["DATA_DEP"]}],
        "product_body_evidence": [
            {"kind": "control_guard", "method": "p.Product.value()I", "context": "C",
             "instruction_index": 1, "retained_instruction_index": 1, "line": 7},
            {"kind": "control_guard", "method": "p.Product.value()I", "context": "C",
             "instruction_index": 1, "retained_instruction_index": 2, "line": 7},
        ],
    }
    assert subject._product_body_paths(
        raw, {"p.Product": "src/main/java/p/Product.java"}) == ([], [])


def test_probe_and_driver_share_base_graph_then_isolate_demanded_effect_roots():
    probe = subject.BACKEND_SOURCE.read_text()
    driver = Path(subject.__file__).read_text()
    assert '"mockspec.wala-multi-root/v1"' in probe
    assert '"mockspec.wala-multi-root/v2"' in probe
    assert '"root_manifest"' in probe
    assert '"CRITERIA_FILE:"' in probe
    assert "rootIndex < criterionQueries.size()" in probe
    assert "visited.size() < 10_000" not in probe
    assert "while (!pending.isEmpty())" in probe
    assert '"criteria-request.json"' in driver
    assert "one shared NO_HEAP base process plus one failure-contained FULL process" in driver
    assert "criteria_file.write_text(json.dumps(queries))" in driver
    assert '"OBSERVATION_EFFECT_SCAN"' in driver
    assert '"OBSERVATION_EFFECTS"' in driver
    assert "for effect_index, (query, criterion, analysis) in enumerate(demanded)" in driver
    assert "if (observationEffectScan && !observationEffects)" in probe
    assert '"ROOT_OUTPUT_DIR:"' in probe


def test_root_checkpoint_reader_preserves_partial_prefix_and_rejects_duplicates(tmp_path):
    queries = ["q0", "q1"]
    (tmp_path / "root-00000.json").write_text(json.dumps({
        "schema_version": "mockspec.wala-root-checkpoint/v1",
        "root_index": 0, "criterion_query": "q0", "root": {"criterion": {"query": "q0"}},
    }))
    assert subject._read_root_checkpoints(tmp_path, queries) == {
        "q0": {"criterion": {"query": "q0"}}
    }
    (tmp_path / "root-00001.json").write_text(json.dumps({
        "schema_version": "mockspec.wala-root-checkpoint/v1",
        "root_index": 0, "criterion_query": "q0", "root": {"criterion": {"query": "q0"}},
    }))
    with pytest.raises(ValueError, match="duplicate WALA root checkpoint"):
        subject._read_root_checkpoints(tmp_path, queries)


def test_timeout_recovery_keeps_completed_root_and_marks_only_unfinished_frontier():
    queries = ["q0", "q1"]
    criteria = {"q0": {"criterion_id": "criterion:0"},
                "q1": {"criterion_id": "criterion:1"}}
    root = {"criterion": {"query": "q0"}, "slice_statement_count": 3}
    recovered = subject._recover_checkpoint_analyses(
        queries, criteria, {"q0": root}, missing_error="timeout frontier", graph_created=True)
    assert recovered == [
        {"observation_id": "criterion:0", "raw": root, "graph_created": True,
         "root_mapped": True, "checkpoint_recovered": True},
        {"observation_id": "criterion:1", "raw": None, "error": "timeout frontier",
         "graph_created": True, "root_mapped": False},
    ]


def test_root_manifest_authenticates_checkpoint_without_embedding_full_root(tmp_path):
    path = tmp_path / "root-00000.json"
    path.write_text(json.dumps({
        "schema_version": "mockspec.wala-root-checkpoint/v1",
        "root_index": 0, "criterion_query": "q0",
        "root": {"criterion": {"query": "q0"}, "slice_statements": [{"large": "payload"}]},
    }))
    manifest = [{
        "root_index": 0, "criterion_query": "q0", "checkpoint_file": path.name,
        "checkpoint_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "status": "complete",
    }]
    roots = subject._read_root_checkpoints(tmp_path, ["q0"], manifest)
    assert roots["q0"]["slice_statements"] == [{"large": "payload"}]
    manifest[0]["checkpoint_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash/status differs"):
        subject._read_root_checkpoints(tmp_path, ["q0"], manifest)


def test_lazy_checkpoint_frontier_reaches_boundary_and_bounded_projection(tmp_path):
    checkpoint = tmp_path / "root-00000.json"
    root = {
        "criterion": {"query": "q0"}, "seed_index": 2,
        "traversal_truncated": True, "traversal_frontier_count": 7,
        "slice_statement_count": 3, "slice_edge_count": 2,
        "slice_statements": [{"line": 1}, {"line": 2}, {"line": 3}],
        "slice_edges": [{"from": 0, "to": 1, "labels": ["DATA_DEP"]},
                        {"from": 1, "to": 2, "labels": ["DATA_DEP"]}],
    }
    checkpoint.write_text(json.dumps({
        "schema_version": "mockspec.wala-root-checkpoint/v1", "root_index": 0,
        "criterion_query": "q0", "root": root,
    }))
    analyses = subject._checkpoint_path_analyses(
        ["q0"], {"q0": {"criterion_id": "criterion:0"}}, {"q0": checkpoint},
        missing_error="missing", graph_created=True)
    assert "raw" not in analyses[0]
    assert analyses[0]["raw_path"] == str(checkpoint)
    assert subject._analysis_boundaries(analyses, []) == [{
        "scope": "criterion:0", "reason": "statement_budget_frontier", "frontier_count": 7,
    }]
    relation = {"kind": "value_flow", "observation_ids": ["criterion:0"],
                "provenance": {"source_statement": 0, "seed_statement": 2,
                               "dependency_path": root["slice_edges"]}}
    output = tmp_path / "projection.json"
    subject._write_root_evidence_projection(output, analyses[0], root, [relation], [])
    projection = json.loads(output.read_text())
    assert [row["original_index"] for row in projection["statements"]] == [0, 1, 2]
    assert projection["frontier"] == {"truncated": True, "frontier_count": 7}
    assert projection["omitted_counts"] == {"statements": 0, "edges": 0}


def test_effect_checkpoint_merge_preserves_base_root_and_failure_is_observation_scoped(tmp_path):
    base_path = tmp_path / "base.json"
    effect_path = tmp_path / "effect.json"
    query = "SOURCE_LINE:10:-1:.assertEquals("
    base_root = {
        "criterion": {"query": query}, "seed_index": 0,
        "slice_statements": [{"line": 10}], "slice_edges": [],
        "observation_effect_demand": {"status": "demanded_instance_field"},
    }
    effect_root = {
        "criterion": {"query": query},
        "observation_effect_demand": {"status": "analyzed_full_heap"},
        "observation_effect_evidence": [{"analysis": {"status": "linked"}}],
    }
    base_path.write_text(json.dumps({"root": base_root}))
    effect_path.write_text(json.dumps({"root": effect_root}))
    completed = {"observation_id": "criterion:0", "raw_path": str(base_path),
                 "checkpoint_query": query, "effect_raw_path": str(effect_path),
                 "effect_checkpoint_sha256": "a" * 64, "effect_seconds": 2.0}
    merged = subject._analysis_raw(completed)
    assert merged["slice_statements"] == [{"line": 10}]
    assert merged["observation_effect_evidence"] == effect_root["observation_effect_evidence"]
    assert merged["observation_effect_stage"]["status"] == "complete"

    failed = {"observation_id": "criterion:1", "raw_path": str(base_path),
              "checkpoint_query": query, "effect_status": "failed",
              "effect_error": "OutOfMemoryError", "effect_seconds": 3.0}
    preserved = subject._analysis_raw(failed)
    assert preserved["slice_statements"] == [{"line": 10}]
    assert preserved["observation_effect_error"] == "OutOfMemoryError"
    relations, unknowns, boundaries = subject._project_observation_effects(
        preserved, "criterion:1", {"logical_path": "p/Test.java"}, {})
    assert relations == [] and unknowns == []
    assert boundaries[0]["reason"] == "observation_effect_stage_frontier"
    assert boundaries[0]["details"]["base_root_preserved"] is True


def test_effect_failure_and_unattempted_frontiers_preserve_every_base_root(tmp_path):
    query_by_index = [f"SOURCE_LINE:{line}:-1:.assertEquals(" for line in (10, 20, 30)]
    analyses = []
    for index, query in enumerate(query_by_index):
        base_path = tmp_path / f"base-{index}.json"
        base_path.write_text(json.dumps({"root": {
            "criterion": {"query": query}, "seed_index": 0,
            "slice_statements": [{"line": (index + 1) * 10}], "slice_edges": [],
            "observation_effect_demand": {"status": "demanded_instance_field"},
        }}))
        analyses.append({
            "observation_id": f"criterion:{index}", "raw_path": str(base_path),
            "checkpoint_query": query, "root_mapped": True,
        })

    effect_path = tmp_path / "effect-0.json"
    effect_path.write_text(json.dumps({"root": {
        "criterion": {"query": query_by_index[0]},
        "observation_effect_demand": {"status": "analyzed_full_heap"},
        "observation_effect_evidence": [],
    }}))
    analyses[0].update({
        "effect_raw_path": str(effect_path), "effect_checkpoint_sha256": "a" * 64,
        "effect_status": "complete", "effect_seconds": 1.0,
    })
    analyses[1].update({
        "effect_status": "failed", "effect_error": "OutOfMemoryError", "effect_seconds": 2.0,
    })
    analyses[2].update({
        "effect_status": "not_attempted_total_deadline_frontier",
        "effect_error": "effect phase not attempted: total deadline reserved for projection",
        "effect_seconds": 0.0,
    })

    merged = [subject._analysis_raw(analysis) for analysis in analyses]
    assert [root["slice_statements"][0]["line"] for root in merged] == [10, 20, 30]
    assert merged[0]["observation_effect_stage"]["status"] == "complete"
    for index in (1, 2):
        _, _, boundaries = subject._project_observation_effects(
            merged[index], f"criterion:{index}", {"logical_path": "p/Test.java"}, {})
        assert boundaries[0]["reason"] == "observation_effect_stage_frontier"
        assert boundaries[0]["details"]["base_root_preserved"] is True
        assert boundaries[0]["details"]["stage"]["status"] == analyses[index]["effect_status"]


def test_effect_error_checkpoint_is_failure_frontier_not_complete_effect(tmp_path):
    query = "SOURCE_LINE:10:-1:.assertEquals("
    effect_path = tmp_path / "root-00000.json"
    effect_path.write_text(json.dumps({
        "schema_version": "mockspec.wala-root-checkpoint/v1",
        "root_index": 0,
        "criterion_query": query,
        "root": {"criterion_query": query, "error": "IllegalStateException: effect failed"},
    }))
    manifest = [{
        "root_index": 0, "criterion_query": query,
        "checkpoint_file": effect_path.name,
        "checkpoint_sha256": hashlib.sha256(effect_path.read_bytes()).hexdigest(),
        "status": "error",
    }]
    validated = subject._validated_root_checkpoint_paths(tmp_path, [query], manifest)
    analysis = {"checkpoint_query": query}
    status, error = subject._attach_effect_checkpoint(analysis, validated[query], query)
    assert status == "failed"
    assert error == "WALA effect root error: IllegalStateException: effect failed"
    assert "effect_raw_path" not in analysis
    assert "effect_checkpoint_sha256" not in analysis
    assert analysis["effect_failed_checkpoint_sha256"] == hashlib.sha256(
        effect_path.read_bytes()).hexdigest()

    base_path = tmp_path / "base.json"
    base_path.write_text(json.dumps({"root": {
        "criterion": {"query": query}, "slice_statements": [{"line": 10}],
        "observation_effect_demand": {"status": "demanded_instance_field"},
    }}))
    analysis.update({
        "raw_path": str(base_path), "effect_status": status,
        "effect_error": error, "effect_seconds": 0.5,
    })
    preserved = subject._analysis_raw(analysis)
    assert preserved["slice_statements"] == [{"line": 10}]
    _, _, boundaries = subject._project_observation_effects(
        preserved, "criterion:0", {"logical_path": "p/Test.java"}, {})
    assert boundaries[0]["reason"] == "observation_effect_stage_frontier"
    assert boundaries[0]["details"]["base_root_preserved"] is True
    assert boundaries[0]["details"]["stage"]["failed_authenticated_checkpoint_sha256"] == (
        analysis["effect_failed_checkpoint_sha256"])


def test_static_observation_field_is_explicitly_unmodeled_without_full_demand():
    raw = {"observation_effect_demand": {
        "status": "not_demanded_static_field_unmodeled", "static_read_count": 1,
        "instance_read_count": 0,
    }}
    relations, unknowns, boundaries = subject._project_observation_effects(
        raw, "criterion:0", {"logical_path": "p/Test.java"}, {})
    assert relations == [] and unknowns == []
    assert boundaries == [{
        "scope": "criterion:0", "reason": "static_or_global_observation_field_unmodeled",
        "details": raw["observation_effect_demand"],
    }]


def test_contract_writes_authenticated_success_and_failure_projections(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, "validate_result_contract", lambda contract: None)
    root = {
        "criterion": {"query": "q0"}, "seed_index": 0,
        "slice_statement_count": 1, "slice_edge_count": 0,
        "slice_statements": [{"method": "p.Test.selected()V", "line": 10}],
        "slice_edges": [],
    }
    criteria = [
        {"criterion_id": "criterion:0", "line": 10, "kind": "assertion_argument",
         "confidence": "explicit", "reason": "assertion argument"},
        {"criterion_id": "criterion:1", "line": 11, "kind": "assertion_argument",
         "confidence": "explicit", "reason": "assertion argument"},
    ]
    analyses = [
        {"observation_id": "criterion:0", "raw": root, "graph_created": True,
         "root_mapped": True, "raw_checkpoint_sha256": "a" * 64,
         "checkpoint_root_index": 0, "checkpoint_query": "q0"},
        {"observation_id": "criterion:1", "raw": None, "graph_created": True,
         "root_mapped": False, "error": "timeout frontier",
         "raw_checkpoint_sha256": "b" * 64,
         "checkpoint_root_index": 1, "checkpoint_query": "q1"},
    ]
    contract, _ = subject._contract(
        {"case_id": "case", "dataset": "dataset", "snapshot_id": "snapshot"},
        {"test_path": "src/test/java/p/Test.java",
         "entry_signature": "p.Test.selected()V", "masking_contract": "masked",
         "masked_test_sha256": "c" * 64, "target_selector": "selected",
         "target_scope": {"start_line": 9, "end_line": 12}},
        [{"source_id": "test", "logical_path": "src/test/java/p/Test.java",
          "sha256": "d" * 64, "bytes": 100}],
        criteria, analyses, {"wall_seconds": 1.0}, [], [], [{
            "source_id": "source:unrelated", "name": "unrelated_placeholder",
            "start_line": 30, "end_line": 31,
        }],
        evidence_output=tmp_path)
    assert "unrelated_placeholder" not in json.dumps(
        contract["analysis_boundaries"]["unknown_effects"])

    success = json.loads((tmp_path / "criterion:0.json").read_text())
    assert success["checkpoint_identity"] == {"root_index": 0, "query": "q0"}
    assert success["raw_checkpoint_sha256"] == "a" * 64
    failure = json.loads((tmp_path / "criterion:1.json").read_text())
    assert failure["status"] == "failed"
    assert failure["checkpoint_identity"] == {"root_index": 1, "query": "q1"}
    assert failure["raw_checkpoint_sha256"] == "b" * 64


def test_mapping_cost_finalization_uses_post_projection_clock(monkeypatch):
    costs = {"timings_seconds": {"wala_analysis": 7.0}, "total_seconds": 8.0,
             "total_scope": "provisional"}
    contract = {"cost": {"graph_size": {"nodes": 3, "edges": 2}}}
    monkeypatch.setattr(subject.time, "monotonic", lambda: 20.0)
    subject._finalize_mapping_costs(costs, contract, started=4.0, mapping_started=15.0)
    assert costs["timings_seconds"]["relationship_mapping_and_projection"] == 5.0
    assert costs["total_seconds"] == 16.0
    assert contract["cost"]["total_seconds"] == 16.0
    assert contract["cost"]["graph_size"] == {"nodes": 3, "edges": 2}
    assert "excludes shared and final result JSON serialization" in costs["total_scope"]


def test_observation_effect_projection_keeps_visible_writer_and_read_identity():
    call = {"method": "p.Test.selected()V", "instruction_index": 4, "line": 10}
    read_ready = {"method": "p.Test.selected()V", "instruction_index": 7,
                  "declared_field": "Lp/Box.ready I", "line": 11}
    read_other = {"method": "p.Test.selected()V", "instruction_index": 8,
                  "declared_field": "Lp/Box.other I", "line": 11}
    raw = {"observation_effect_demand": {"status": "demanded_full_heap"},
           "observation_effect_evidence": [{
               "read": read_ready, "authenticated_opaque_calls": [{"call": call}],
               "analysis": {"status": "linked", "visible_state": [{
                   "write": {"method": "p.Product.fill(Lp/Box;)V", "line": 5},
                   "read": read_ready, "heap_path": [{"incoming_dependency_labels": ["HEAP_DATA_DEP"]}],
               }], "opaque_state": [
                   {"relation": "opaque_argument_rooted_state_effect", "certainty": "potential",
                    "call": call, "read": read_ready},
                   {"relation": "opaque_argument_rooted_state_effect", "certainty": "potential",
                    "call": call, "read": read_other},
               ], "exceptions": [], "cost": {"frontier": None}},
           }]}
    relations, unknowns, boundaries = subject._project_observation_effects(
        raw, "criterion:0", {"logical_path": "p/Test.java", "start_line": 11, "end_line": 11},
        {"p.Product": "src/main/java/p/Product.java"})
    assert boundaries == []
    assert relations[0]["kind"] == "state_flow"
    assert relations[0]["from"]["logical_path"] == "src/main/java/p/Product.java"
    assert relations[0]["from"]["start_line"] == 5
    assert len(unknowns) == 2
    assert len({row["boundary_id"] for row in unknowns}) == 2
    assert all(row["observation_ids"] == ["criterion:0"] for row in unknowns)
    assert subject._is_cross_source_relation(relations[0], "p/Test.java")
    same_file = {**relations[0], "from": {"logical_path": "p/Test.java", "start_line": 5}}
    assert not subject._is_cross_source_relation(same_file, "p/Test.java")


def test_observation_unknown_projection_is_fairly_bounded_and_reports_omissions(monkeypatch):
    monkeypatch.setattr(subject, "MAX_UNKNOWN_EFFECTS", 5)
    effects = [{"boundary_id": f"effect:{index}"} for index in range(4)]
    retained, omitted, limit = subject._bounded_observation_unknowns(effects, analysis_count=2)
    assert limit == 2
    assert retained == effects[:2]
    assert omitted == 2


def test_excluded_opaque_effect_boundaries_are_aggregated_with_bounded_samples():
    excluded = [{"certainty": "excluded", "reason": "points_to_regions_disjoint",
                 "call": {"instruction_index": index}} for index in range(25)]
    raw = {"observation_effect_demand": {"status": "demanded_full_heap"},
           "observation_effect_evidence": [{"read": {}, "analysis": {
               "status": "linked", "visible_state": [], "opaque_state": excluded,
               "exceptions": []}}]}
    relations, unknowns, boundaries = subject._project_observation_effects(
        raw, "criterion:0", {"logical_path": "p/Test.java"}, {})
    assert relations == []
    assert unknowns == []
    assert boundaries == [{
        "scope": "criterion:0", "reason": "opaque_effect_candidates_excluded",
        "excluded_count": 25, "sample_limit": 10, "samples": excluded[:10],
        "affected_observation_ids": ["criterion:0"],
    }]


def test_large_path_projection_builds_indexes_once_and_honors_edge_budget(monkeypatch):
    count = 6_001
    raw = {
        "seed_index": count - 1,
        "slice_statements": [
            {"method": "p.Product.value()I", "line": index + 1}
            for index in range(count - 1)
        ] + [{"method": "p.Test.test()V", "line": count}],
        "slice_edges": [
            {"from": index, "to": index + 1, "labels": ["DATA_DEP"]}
            for index in range(count - 1)
        ],
    }
    monkeypatch.setattr(subject, "_homogeneous_dependency_path",
                        lambda *args, **kwargs: pytest.fail("per-source full-edge scan used"))
    budget = {"relations": 1_000, "edges": 50_000,
              "omitted_relations": 0, "omitted_path_edges": 0}
    paths, suppressed = subject._typed_product_paths(
        raw, {"p.Product": "src/main/java/p/Product.java"}, {},
        subject._path_indexes(raw), budget)
    assert suppressed == []
    assert sum(len(path["dependency_path"]) for path in paths) <= 50_000
    assert len(paths) <= 1_000
    assert budget["omitted_relations"] > 0
