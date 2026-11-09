import json
import os
from pathlib import Path
import sys

import pytest


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from library_oracle import _validate_request, evaluate  # noqa: E402


def literal(value):
    return {"kind": "literal", "value": value}


def call(owner, name, arguments=(), target=None):
    return {"kind": "call", "owner": owner, "name": name,
            "target": target, "arguments": list(arguments)}


def test_request_shape_is_checked_before_runtime_access():
    assert _validate_request({"queries": []}) == {"queries": []}
    with pytest.raises(ValueError, match="queries array"):
        _validate_request({"queries": "not-an-array"})


@pytest.mark.skipif(
    os.environ.get("MOCKSPEC_GOAL_RUNTIME") != "1",
    reason="set MOCKSPEC_GOAL_RUNTIME=1 for the frozen-runtime integration check",
)
def test_frozen_runtime_executes_allowed_chain_and_rejects_unknown_call(tmp_path):
    uri = "org.springframework.web.util.UriComponentsBuilder"
    target = call(uri, "path", [literal("health")],
                  call(uri, "path", [literal("/")],
                       call(uri, "fromHttpUrl", [literal("http://example/actuator")])))
    request = {
        "queries": [
            {"id": "uri", "expr": call(uri, "toUriString", target=target)},
            {"id": "blank", "expr": call(
                "org.springframework.util.StringUtils", "hasText", [literal("  ")])},
            {"id": "endpoint", "expr": call(
                "org.springframework.boot.actuate.endpoint.EndpointId", "of", [literal("health")])},
            {"id": "unknown", "expr": call("java.lang.System", "getenv")},
        ]
    }
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    output = tmp_path / "output"
    old_root = os.environ.get("MOCKSPEC_OLD_ROOT")
    if not old_root:
        pytest.fail("MOCKSPEC_OLD_ROOT is required with MOCKSPEC_GOAL_RUNTIME=1")
    result = evaluate(request_path, output, Path(old_root))
    assert result["status"] == "ok"
    assert result["dependency_count"] == 89
    by_id = {item["id"]: item for item in result["results"]}
    assert by_id["uri"] == {"id": "uri", "status": "value", "value": "http://example/actuator/health"}
    assert by_id["blank"] == {"id": "blank", "status": "value", "value": False}
    assert by_id["endpoint"] == {"id": "endpoint", "status": "value", "value": "health"}
    assert by_id["unknown"]["status"] == "unsupported"
    assert "owner is not allowed" in by_id["unknown"]["exception"]
    assert json.loads((output / "result.json").read_text()) == result
