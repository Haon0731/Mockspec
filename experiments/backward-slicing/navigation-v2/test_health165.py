from __future__ import annotations

import importlib.util
from pathlib import Path


_PATH = Path(__file__).with_name("health165.py")
_SPEC = importlib.util.spec_from_file_location("navigation_v2_health165", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
health = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(health)


def test_distribution_preserves_fractional_median_and_nearest_rank_p95() -> None:
    assert health._distribution([1, 2, 3, 100]) == {
        "median": 2.5,
        "p95_nearest_rank": 100,
        "max": 100,
    }


def test_unknown_reasons_include_null_effect_and_incomplete_analysis() -> None:
    contract = {
        "completion": {"status": "partial"},
        "relation_coverage": [{"kind": "value_flow", "status": "not_evaluated"}],
    }
    facts = {
        "entry_binding": {"status": "unique"},
        "calls": [
            {
                "structural_role": "outbound_call",
                "effects": [{"reaches_assertion": None}],
            }
        ],
        "analysis_boundaries": {
            "unknown_effects": [],
            "unanalysed_regions": [],
            "assumptions": [],
        },
    }

    assert health._unknown_reasons(contract, facts) == [
        "analysis_incomplete",
        "effect_reachability_unknown",
        "relation_coverage_incomplete",
    ]


def test_guard_count_separates_control_edges_annotations_and_unique_branches() -> None:
    repeated = {
        "expression": "enabled",
        "location": {"logical_path": "product.py", "start_line": 8, "end_line": 8},
    }
    facts = {
        "guards": [{"guard_id": "g:1"}, {"guard_id": "g:2"}],
        "calls": [
            {"guards": [repeated]},
            {"guards": [repeated, {**repeated, "expression": "not (enabled)"}]},
        ],
    }

    assert health._guard_count(facts) == (2, 3, 2)


def test_backend_error_preserves_structured_root_cause() -> None:
    row = {
        "semantic_backend": {
            "error": {
                "type": "input_constraint",
                "message": "deleted Java declaration types are not uniquely constrained",
            }
        },
        "result_contract": {"completion": {"failure_categories": ["fallback"]}},
    }

    assert health._backend_error(row) == (
        "input_constraint: deleted Java declaration types are not uniquely constrained"
    )


def test_candidate_error_removes_tool_noise_but_preserves_root_cause() -> None:
    candidate = {
        "error": {
            "type": "RuntimeError",
            "message": (
                "backend_exit_1: SLF4J warning\n"
                'Exception in thread "main" java.lang.IllegalStateException: node limit exceeded\n'
                "\tat SliceProbe.run(SliceProbe.java:85)"
            ),
        }
    }

    assert health._candidate_error(candidate) == (
        "RuntimeError: java.lang.IllegalStateException: node limit exceeded"
    )


def test_summary_keeps_failure_denominators_separate_and_overlapping() -> None:
    base = {
        "dataset": "d",
        "language": "java",
        "snapshot_id": "s",
        "backend_status": "complete",
        "analysis_status": "partial",
        "backend_error": None,
    }
    success = {
        **base,
        "case_id": "ok",
        "projection_status": "success",
        "facts_bytes": 9,
        "view_bytes": 10,
        "view_tokens": 4,
        "estimated_tokens": 3,
        "observation_count": 1,
        "control_guard_relation_count": 0,
        "lexical_guard_annotation_count": 0,
        "unique_lexical_guard_branch_count": 0,
        "entry_binding_status": "unresolved",
        "unknown_reasons": ["entry_not_unique"],
    }
    backend_failed_but_projected = {
        **success,
        "case_id": "backend-failed",
        "analysis_status": "failed",
        "backend_status": "failed",
        "backend_error": "compile failed",
    }
    input_rejected = {
        **base,
        "case_id": "rejected",
        "projection_status": "input_rejected",
        "error_type": "InputValidationError",
        "error": "identity differs",
    }
    python_rows = [
        {
            **success,
            "language": "python",
            "case_id": f"p{i}",
        }
        for i in range(2)
    ]

    summary = health._summarize(
        [success, backend_failed_but_projected, input_rejected, *python_rows]
    )

    assert summary["projection_success"] == 4
    assert summary["input_rejected"] == 1
    assert summary["projection_error"] == 0
    assert [row["case_id"] for row in summary["backend_failures"]] == [
        "backend-failed"
    ]
    assert summary["by_language"]["java"]["projection_success"] == 2
    assert summary["by_language"]["java"]["input_rejected"] == 1


def test_delivery_artifacts_count_the_actual_bytes_and_link_archive(tmp_path: Path) -> None:
    import hashlib
    import json

    facts = {
        "schema_version": "mock-spec.navigation-facts/v2",
        "identity": {"case_id": "test"},
        "entry_binding": {"status": "unresolved", "candidates": [], "assumptions": [], "evidence": []},
        "analysis_boundaries": {"unknown_effects": [], "assumptions": [], "unanalysed_regions": []},
        "observations": [], "calls": [], "guards": [], "relations": [], "evidence_sources": [],
        "endpoints": [], "projection": {},
    }
    metadata = health._write_delivery(tmp_path, "case", facts, len, "utf8-bytes")
    archive = (tmp_path / metadata["facts_path"]).read_bytes()
    view = (tmp_path / metadata["view_path"]).read_bytes()
    assert archive == health._encoded(facts)
    assert metadata["facts_tokens"] == len(archive)
    assert metadata["view_tokens"] == metadata["view_bytes"] == len(view)
    assert metadata["view_sha256"] == hashlib.sha256(view).hexdigest()
    ref = json.loads(view)["archive"]
    assert ref["sha256"] == hashlib.sha256(archive).hexdigest()
    assert (tmp_path / metadata["view_path"]).with_name(ref["locator"]).read_bytes() == archive


def test_completed_backend_still_reports_observation_timeout() -> None:
    boundaries = {"unanalysed_regions": [
        {"scope": "criterion:1", "reason": "WALA observation timeout\nWALA_STAGE sdg_initialized"},
        {"scope": "criterion:2", "reason": "statement_budget_frontier"},
        {"scope": "criterion:3", "reason": "generic Java backend total deadline exhausted before observation"},
    ]}
    assert health._observation_timeouts(boundaries) == ["criterion:1", "criterion:3"]
