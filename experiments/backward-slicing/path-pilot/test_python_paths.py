import json
from pathlib import Path
import subprocess
import sys


PROBE = Path(__file__).with_name("python_paths.py")


def run_probe(source: str, function: str = "target") -> dict:
    request = {
        "sources": [{"source_id": "mut", "logical_path": "mut.py", "start_line": 1, "text": source}],
        "entry": {"source_id": "mut", "function": function},
    }
    completed = subprocess.run(
        [sys.executable, str(PROBE)], input=json.dumps(request), text=True,
        capture_output=True, check=True,
    )
    return json.loads(completed.stdout)


def test_unknown_calls_produce_false_raise_and_normal_then_fallback_paths():
    result = run_probe("""
def target(code):
    if (ctx := lookup.get(None)) is not None:
        ctx.app.aborter(code)
    fallback(code)
""")
    paths = result["paths"]
    assert any(
        any("lookup.get" in e["target"] and e["outcome"] == "normal" for e in p["events"])
        and any("fallback" in e["target"] for e in p["events"])
        and not any("aborter" in e["target"] for e in p["events"])
        for p in paths
    )
    assert any(
        any("aborter" in e["target"] and e["outcome"] == "raise" for e in p["events"])
        and not any("fallback" in e["target"] for e in p["events"])
        for p in paths
    )
    assert any(
        any("aborter" in e["target"] and e["outcome"] == "normal" for e in p["events"])
        and any("fallback" in e["target"] for e in p["events"])
        for p in paths
    )
    assert all("#1" in event["event_id"] for path in paths for event in path["events"])
    assert any("is not None" in guard["expression"] for path in paths for guard in path["guards"])
    assert result["exploration_complete"] is True
    assert result["complete"] is False


def test_assignment_substitution_and_integer_contradiction_pruning():
    result = run_probe("""
def target(x):
    y = x
    if y > 3:
        if y < 2:
            return y + 1
        return y
    return 0
""")
    assert any(p["outcome"].get("value", {}).get("text") == "x" for p in result["paths"])
    assert any(p["outcome"].get("value", {}).get("const") == 0 for p in result["paths"])
    assert any("empty_integer_interval" in p["reason"] for p in result["pruned_paths"])
    assert any(r["target"] == "y" and r["value"] == "x" for p in result["paths"] for r in p["value_relations"])


def test_unsupported_control_is_preserved_as_unknown():
    result = run_probe("""
def target(items):
    for item in items:
        consume(item)
    try:
        finish()
    except Exception:
        recover()
    return 1
""")
    assert result["complete"] is False
    reasons = {u["reason"] for p in result["paths"] for u in p["unknowns"]}
    assert "unsupported_control_structure" in reasons
    assert all(p["precision"] == "bounded_with_unknowns" for p in result["paths"])


def test_call_arguments_use_local_value_relations():
    result = run_probe("""
def target(code, *args, **kwargs):
    forwarded = code
    dependency(forwarded, *args, **kwargs)
    return forwarded
""")
    normal = next(p for p in result["paths"] if p["outcome"]["kind"] == "return")
    assert normal["events"][0]["arguments"] == ["code", "*args"]
    assert normal["events"][0]["keyword_arguments"] == [{"name": "**", "value": "kwargs"}]
    assert normal["outcome"]["value"]["text"] == "code"


def test_annotated_assignment_is_tracked():
    result = run_probe("""
def target(x):
    copied: int = x
    return copied
""")
    assert result["paths"][0]["outcome"]["value"]["text"] == "x"
    assert result["paths"][0]["value_relations"][0]["target"] == "copied"


def test_if_elif_else_early_return_and_visible_raise_remain_separate():
    result = run_probe("""
def target(flag):
    if flag == 1:
        return "one"
    elif flag == 2:
        raise ValueError("two")
    else:
        return "other"
""")
    assert len(result["paths"]) == 3
    assert {p["outcome"]["kind"] for p in result["paths"]} == {"return", "raise"}
    raised = next(p for p in result["paths"] if p["outcome"]["kind"] == "raise")
    assert raised["outcome"]["origin"] == "visible_raise"
    assert len(raised["guards"]) == 2
