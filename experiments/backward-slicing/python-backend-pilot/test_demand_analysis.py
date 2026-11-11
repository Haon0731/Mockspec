import importlib.util
import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("demand_analysis", HERE / "demand_analysis.py")
demand = importlib.util.module_from_spec(spec)
sys.modules["demand_analysis"] = demand
spec.loader.exec_module(demand)
roots_spec = importlib.util.spec_from_file_location("demand_roots", HERE / "demand_roots.py")
roots = importlib.util.module_from_spec(roots_spec)
roots_spec.loader.exec_module(roots)


def test_observation_plan_executes_only_relationship_queries():
    result = demand.plan(
        ["flask-observations", "state-observations"], max_units=10, max_depth=3
    )
    assert result["complete"] is True
    assert result["selected_units"] == [
        "flask-observation-flow", "flask-abort", "exceptions",
        "pytest-exception-flow",
        "visible-state-flow", "unknown-effect-flow", "state-boundary-flow",
        "unknown-exception-state",
    ]
    assert "local-flow" not in result["selected_units"]


def test_budget_and_recursive_boundaries_are_preserved():
    units = {
        "root": demand.Unit("root", None, None, ("query", "cycle")),
        "query": demand.Unit("query", "probe", "visible-state-flow.ql"),
        "cycle": demand.Unit("cycle", None, None, ("root",)),
    }
    result = demand.plan(["root"], max_units=0, max_depth=3, units=units)
    assert result["complete"] is False
    assert {row["reason"] for row in result["unexpanded_boundaries"]} == {
        "unit_budget", "recursive_dependency",
    }


def test_cache_identity_invalidates_source_context_and_summary(tmp_path):
    unit = demand.UNITS["visible-state-flow"]
    base = dict(unit=unit, source_sha256="source-a", codeql_version="2.27.4",
                context="probe:visible")
    first = demand.cache_key(**base)
    assert first == demand.cache_key(**base)
    assert first != demand.cache_key(**{**base, "source_sha256": "source-b"})
    assert first != demand.cache_key(**{**base, "context": "probe:other"})
    assert first != demand.cache_key(**base, summary_version="next-summary")
    assert first != demand.cache_key(**base, config_sha256="new-config")
    assert first != demand.cache_key(**base, plan_sha256="new-observation-plan")
    assert first != demand.cache_key(**base, budget_sha256="new-budget")


def test_warm_cache_skips_runner_and_preserves_result(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "probe.py").write_text("value = 1\n")
    plan = demand.plan(["state-observations"], max_units=10, max_depth=3)
    cache = tmp_path / "cache"
    calls = []

    def runner(unit, target):
        calls.append(unit.name)
        target.write_text(f"relation\n{unit.name}\n")
        return {"status": "complete", "seconds": 0.1}

    source_ids = {"probe": demand.source_identity(source), "flask": "unused"}
    cold = demand.execute_plan(
        plan, source_ids=source_ids, codeql_version="2.27.4", context="state-probe",
        cache=cache, output=tmp_path / "cold", runner=runner,
    )
    assert len(calls) == 4
    calls.clear()
    warm = demand.execute_plan(
        plan, source_ids=source_ids, codeql_version="2.27.4", context="state-probe",
        cache=cache, output=tmp_path / "warm", runner=runner,
    )
    assert calls == []
    assert {row["status"] for row in cold["attempts"]} == {"executed"}
    assert {row["status"] for row in warm["attempts"]} == {"cache_hit"}
    for unit in plan["selected_units"]:
        assert (tmp_path / "cold" / f"probe-{unit}.csv").read_bytes() == \
            (tmp_path / "warm" / f"probe-{unit}.csv").read_bytes()


def test_failed_query_does_not_populate_reusable_cache(tmp_path):
    plan = demand.plan(["state-observations"], max_units=1, max_depth=3)
    cache = tmp_path / "cache"

    def failed(unit, target):
        target.write_text("partial\n")
        return {"status": "timeout", "seconds": 1.0,
                "unexpanded_boundary": "query budget exhausted"}

    result = demand.execute_plan(
        plan, source_ids={"probe": "source", "flask": "unused"},
        codeql_version="2.27.4", context="state-probe", cache=cache,
        output=tmp_path / "failed", runner=failed,
    )
    assert result["attempts"][0]["status"] == "timeout"
    assert result["complete"] is False
    assert result["unexpanded_boundaries"][-1]["reason"] == "query_execution_failed"
    assert list(cache.glob("*.csv")) == []


def test_generated_roots_exclude_unregistered_function_and_change_cache_identity(tmp_path):
    flask = tmp_path / "flask"
    test_file = flask / "tests/test_case.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "def test_case():\n"
        "    with raises(Error):\n"
        "        target(1)\n"
        "    if flag:\n"
        "        assert captured.value\n"
        "    else:\n"
        "        assert captured.value\n"
        "\n"
        "def unrelated():\n"
        "    assert target(999)\n",
        encoding="utf-8",
    )
    observation = {
        "observations": [
            {"observation_id": "raises-context:2", "logical_path": "tests/test_case.py", "line": 2},
            {"observation_id": "assert:5", "logical_path": "tests/test_case.py", "line": 5,
             "guard_line": 4},
            {"observation_id": "assert:7", "logical_path": "tests/test_case.py", "line": 7,
             "guard_line": 4},
        ]
    }
    state = json.loads((HERE / "state_observation_expectations.json").read_text())
    recursive = json.loads((HERE / "recursive_observation_expectations.json").read_text())
    text, record = roots.generate(
        flask_source_root=flask, state_source=HERE / "mechanism_probe.py",
        recursive_source=HERE / "demand_recursive_probe.py",
        observation_expectations=observation, state_expectations=state,
        recursive_expectations=recursive,
    )
    assert record["test_call_line"] == 3
    assert "unrelated" not in text
    assert "999" not in text
    changed = json.loads(json.dumps(observation))
    changed["observations"][1]["line"] = 6
    changed_text, changed_record = roots.generate(
        flask_source_root=flask, state_source=HERE / "mechanism_probe.py",
        recursive_source=HERE / "demand_recursive_probe.py",
        observation_expectations=changed, state_expectations=state,
        recursive_expectations=recursive,
    )
    assert text != changed_text
    unit = demand.UNITS["flask-observation-flow"]
    common = dict(unit=unit, source_sha256="same-source", codeql_version="2.27.4",
                  context="same-context")
    assert demand.cache_key(**common, root_selector_sha256=record["module_sha256"]) != \
        demand.cache_key(**common, root_selector_sha256=changed_record["module_sha256"])
