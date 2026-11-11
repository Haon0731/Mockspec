import copy

import pytest

from langflow_mapping import ROW_FIELDS, expected_backend_rows, map_result
from prepare_langflow import invalid_token_identity_binding, validate_binding


EXPECTED = {
    "product_path": "p.py", "function": {"name": "f", "start_line": 4, "end_line": 8},
    "loader": {"name": "load", "return_index": 0, "test_binding_line": 10, "call_line": 12},
    "observations": [], "unknown": [],
}
BINDING = {
    "status": "resolved", "loader": "load", "path": "p.py", "function_key": "f", "return_index": 0,
    "function_definition": {"logical_path": "p.py", "name": "f", "start_line": 4, "end_line": 8},
    "test_bindings": [{"name": "f", "line": 10}], "call_sites": [{"name": "f", "line": 12}],
}
IDENTITY_SOURCE = '''
def load():
    class LocalInvalidToken(Exception):
        pass
    namespace = {"InvalidToken": LocalInvalidToken}
    exec("code", namespace)
    return namespace["f"], LocalInvalidToken

f, invalid_token_cls = load()
result = f(value)
assert result
assert invalid_token_cls is f.__globals__["InvalidToken"]
'''.lstrip()


def identity_expectations():
    expected = copy.deepcopy(EXPECTED)
    expected["loader"]["test_binding_line"] = 8
    expected["observations"] = [{"id": "criterion:1", "line": 11}]
    expected["invalid_token_identity"] = {
        "status": "resolved", "loader_class": "LocalInvalidToken", "namespace": "namespace",
        "namespace_key": "InvalidToken", "namespace_assignment_line": 4, "loader_return_line": 6,
        "return_slots": [{"index": 0, "test_name": "f"},
                         {"index": 1, "test_name": "invalid_token_cls"}],
        "test_binding_line": 8, "observation_line": 11,
        "comparison": "invalid_token_cls is f.__globals__['InvalidToken']",
        "precision": "strict_supported_python_ast",
    }
    return expected


@pytest.mark.parametrize("mutation", ["path", "function", "call", "rebind"])
def test_loader_origin_rejects_wrong_source_function_call_or_rebinding(mutation):
    binding = copy.deepcopy(BINDING)
    if mutation == "path":
        binding["path"] = "other.py"
    elif mutation == "function":
        binding["function_definition"]["name"] = "other"
    elif mutation == "call":
        binding["call_sites"] = [{"name": "f", "line": 99}]
    else:
        binding["test_bindings"].append({"name": "f", "line": 11})
    with pytest.raises(ValueError):
        validate_binding(binding, EXPECTED)


def test_invalid_token_tuple_namespace_and_identity_assertion_are_authenticated():
    expected = identity_expectations()
    result = invalid_token_identity_binding(IDENTITY_SOURCE, expected)
    assert result["return_slots"][1] == {"index": 1, "test_name": "invalid_token_cls"}
    assert result["comparison"].startswith("invalid_token_cls is")


@pytest.mark.parametrize("old,new", [
    ('return namespace["f"], LocalInvalidToken', 'return LocalInvalidToken, namespace["f"]'),
    ('{"InvalidToken": LocalInvalidToken}', '{"Other": LocalInvalidToken}'),
    ('assert invalid_token_cls is f.__globals__["InvalidToken"]',
     'assert invalid_token_cls == f.__globals__["InvalidToken"]'),
    ('result = f(value)', 'f = replacement\nresult = f(value)'),
    ('exec("code", namespace)', 'exec("code", namespace)\n    namespace["InvalidToken"] = Other'),
    ('exec("code", namespace)', 'exec("code", namespace)\n    LocalInvalidToken = Other'),
    ('result = f(value)', 'f.__globals__["InvalidToken"] = Other\nresult = f(value)'),
    ('result = f(value)', 'f.__globals__.update({"InvalidToken": Other})\nresult = f(value)'),
])
def test_invalid_token_provenance_rejects_tuple_namespace_assertion_or_rebind(old, new):
    expected = identity_expectations()
    with pytest.raises(ValueError):
        invalid_token_identity_binding(IDENTITY_SOURCE.replace(old, new), expected)


def test_mapper_requires_backend_facts_and_keeps_bridge_distinct():
    expected = copy.deepcopy(EXPECTED)
    expected.update({"product_path": "p.py", "function": {"name": "f"},
                     "loader": {"call_line": 104}, "observations": [], "unknown": ["unknown"]})
    mapped_binding = copy.deepcopy(BINDING)
    mapped_binding["call_sites"] = [{"name": "f", "line": 104}]
    expected["invalid_token_identity"] = identity_expectations()["invalid_token_identity"]
    manifest = {"case_id": "c", "dataset": "d", "snapshot_id": "s", "target_selector": "t",
                "masking_contract": "m", "dynamic_loader_binding": mapped_binding,
                "invalid_token_identity_binding": expected["invalid_token_identity"],
                "files": [{"logical_path": "t.py", "role": "masked_test"}]}
    rows = [dict(zip(ROW_FIELDS, row)) for row in expected_backend_rows("t.py", "p.py", "f")]
    result = map_result(manifest, expected, rows)
    assert result["status"] == "verified_incomplete"
    assert any(row["modality"] == "authenticated_external_bridge"
               for row in result["raw_backend_relations"])
    assert "unknown" in result["unknowns"]
    with pytest.raises(ValueError, match="evidence contract"):
        map_result(manifest, expected, rows[1:])


@pytest.mark.parametrize("field,value", [
    ("targetLine", "999"), ("sourcePath", "wrong.py"),
    ("detail", "wrong"), ("modality", "certain"),
])
def test_mapper_rejects_mutated_backend_endpoint_detail_or_modality(field, value):
    expected = copy.deepcopy(EXPECTED)
    expected.update({"product_path": "p.py", "function": {"name": "f"},
                     "loader": {"call_line": 104}, "observations": [], "unknown": [],
                     "invalid_token_identity": identity_expectations()["invalid_token_identity"]})
    binding = copy.deepcopy(BINDING)
    binding["call_sites"] = [{"name": "f", "line": 104}]
    manifest = {"case_id": "c", "dataset": "d", "snapshot_id": "s", "target_selector": "t",
                "masking_contract": "m", "dynamic_loader_binding": binding,
                "invalid_token_identity_binding": copy.deepcopy(expected["invalid_token_identity"]),
                "files": [{"logical_path": "t.py", "role": "masked_test"}]}
    rows = [dict(zip(ROW_FIELDS, row)) for row in expected_backend_rows("t.py", "p.py", "f")]
    guard = next(row for row in rows if row["relation"] == "guard_controls_product_return")
    guard[field] = value
    with pytest.raises(ValueError, match="evidence contract"):
        map_result(manifest, expected, rows)


def test_mapper_rejects_mutated_frontend_identity_proof():
    expected = identity_expectations()
    expected.update({"product_path": "p.py", "function": {"name": "f"},
                     "loader": {"call_line": 104}, "unknown": []})
    binding = copy.deepcopy(BINDING)
    binding["call_sites"] = [{"name": "f", "line": 104}]
    proof = copy.deepcopy(expected["invalid_token_identity"])
    proof["namespace_key"] = "Other"
    manifest = {"case_id": "c", "dataset": "d", "snapshot_id": "s", "target_selector": "t",
                "masking_contract": "m", "dynamic_loader_binding": binding,
                "invalid_token_identity_binding": proof,
                "files": [{"logical_path": "t.py", "role": "masked_test"}]}
    rows = [dict(zip(ROW_FIELDS, row)) for row in expected_backend_rows("t.py", "p.py", "f")]
    with pytest.raises(ValueError, match="identity provenance"):
        map_result(manifest, expected, rows)
