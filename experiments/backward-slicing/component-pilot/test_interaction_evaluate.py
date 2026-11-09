import hashlib
import json
from pathlib import Path
import sys

import pytest


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from interaction_evaluate import (  # noqa: E402
    MUTATIONS,
    classify_failure,
    mutate_source,
    validate_baseline,
)


SOURCE = """class DefaultApplicationFactory {
    String method() {
        String health = pathMappedEndpoints.getPath(EndpointId.of("health"));
        if (StringUtils.hasText(health)) {
            return health;
        }
        return "fallback";
    }
}
"""


@pytest.mark.parametrize("mutant", sorted(MUTATIONS))
def test_mutation_is_unique_and_preserves_all_other_source(mutant):
    changed, evidence = mutate_source(SOURCE, mutant)
    assert evidence["match_count"] == 1
    assert evidence["original_source_sha256"] == hashlib.sha256(SOURCE.encode()).hexdigest()
    assert evidence["mutated_source_sha256"] == hashlib.sha256(changed.encode()).hexdigest()
    assert changed != SOURCE
    assert "fallback" in changed
    added = [line for line in evidence["unified_diff"].splitlines()
             if line.startswith("+") and not line.startswith("+++")]
    assert len(added) == 1
    assert "pathMappedEndpoints.getPath" in added[0]


def test_mutation_rejects_zero_or_multiple_targets():
    with pytest.raises(ValueError, match="found 0"):
        mutate_source("class Empty {}\n", "duplicate_health_lookup")
    target = MUTATIONS["duplicate_health_lookup"][0]
    with pytest.raises(ValueError, match="found 2"):
        mutate_source(target + target, "duplicate_health_lookup")


def test_baseline_identity_and_junit_success_are_required(tmp_path):
    candidate = tmp_path / "candidate.json"
    candidate.write_text('{"cloudfoundry": {}}\n', encoding="utf-8")
    candidate_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()
    baseline = tmp_path / "result.json"
    baseline.write_text(json.dumps({
        "candidate_sha256": candidate_hash,
        "status": "passed",
        "test": {"junit_ok": True},
    }), encoding="utf-8")
    assert validate_baseline(candidate, baseline)["status"] == "passed"
    data = json.loads(baseline.read_text())
    data["candidate_sha256"] = "0" * 64
    baseline.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="identity"):
        validate_baseline(candidate, baseline)
    data["candidate_sha256"] = candidate_hash
    data["status"] = "test_failed"
    baseline.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="passing original baseline"):
        validate_baseline(candidate, baseline)


def test_failure_classifier_requires_mockito_evidence(tmp_path):
    (tmp_path / "test.stdout.txt").write_text(
        "org.mockito.exceptions.verification.TooManyActualInvocations\n", encoding="utf-8"
    )
    (tmp_path / "test.stderr.txt").write_text("", encoding="utf-8")
    receipt = {"status": "test_failed", "compile": {"exit_code": 0}}
    assert classify_failure(receipt, tmp_path)["kind"] == "mock_assertion_failure"
    (tmp_path / "test.stdout.txt").write_text("ComparisonFailure\n", encoding="utf-8")
    assert classify_failure(receipt, tmp_path)["kind"] == "other_test_failure"
