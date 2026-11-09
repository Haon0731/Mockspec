import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from candidate_bindings import extract  # noqa: E402


def common(path):
    path.write_text(json.dumps({"tasks": {"cloudfoundry": {"masked_test": {"text": """
package p;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import org.springframework.boot.actuate.endpoint.EndpointId;
import org.springframework.boot.actuate.endpoint.web.PathMappedEndpoints;
class OriginalTest {}
"""}}}}), encoding="utf-8")


def candidate(path, stub, verify):
    path.write_text(json.dumps({"cloudfoundry": {"mock_stub": stub, "verify": verify}}),
                    encoding="utf-8")


def test_extracts_sequential_responses_and_verification(tmp_path):
    common_path = tmp_path / "common.json"
    common(common_path)
    source = tmp_path / "candidate.json"
    candidate(source, """PathMappedEndpoints endpoints = mock(PathMappedEndpoints.class);
when(endpoints.getPath(EndpointId.of(\"health\"))).thenReturn(null, \"actuator/health\");""",
              "verify(endpoints, times(2)).getPath(EndpointId.of(\"health\"));")
    result = extract(common_path, [source])
    item = result["candidates"][0]
    assert item["bindings"] == [{
        "receiver": "endpoints", "name": "getPath",
        "arguments": [{"kind": "call",
                       "owner": "org.springframework.boot.actuate.endpoint.EndpointId",
                       "name": "of", "target": None,
                       "arguments": [{"kind": "literal", "value": "health"}]}],
        "returns": [None, "actuator/health"],
        "location": item["bindings"][0]["location"],
    }]
    assert item["verifications"] == [{
        "receiver": "endpoints", "name": "getPath",
        "arguments": item["bindings"][0]["arguments"], "count": 2,
        "location": item["verifications"][0]["location"],
    }]
    assert item["unsupported"] == []


def test_preserves_unsupported_side_effect_and_duplicate_stub(tmp_path):
    common_path = tmp_path / "common.json"
    common(common_path)
    source = tmp_path / "candidate.json"
    candidate(source, """PathMappedEndpoints endpoints = mock(PathMappedEndpoints.class);
when(endpoints.getPath(EndpointId.of(\"health\"))).thenReturn(\"health\");
when(endpoints.getPath(EndpointId.of(\"health\"))).thenReturn(\"other\");
webEndpoint.setBasePath(\"/actuator\");""", "")
    item = extract(common_path, [source])["candidates"][0]
    assert len(item["bindings"]) == 1
    reasons = [entry["reason"] for entry in item["unsupported"]]
    assert "duplicate stub key has unsupported Mockito precedence" in reasons
    assert "call is outside the supported Mockito subset" in reasons
    assert any("setBasePath" in entry["text"] for entry in item["unsupported"])
