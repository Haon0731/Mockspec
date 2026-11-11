#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 CODEQL_CLI FROZEN_REGISTRY_ROOT OUTPUT_DIRECTORY" >&2
  exit 2
fi

codeql=$1
registry_root=$2
output=$3
pilot_dir=$(cd "$(dirname "$0")" && pwd)
repo_root=$(cd "$pilot_dir/../../.." && pwd)
python_bin="$repo_root/.venv/bin/python"
run_root=$(mktemp -d /tmp/mockspec-issue24-pilot-XXXXXX)
trap 'rm -rf -- "$run_root"' EXIT

if [[ -e "$output" ]]; then
  echo "refusing to overwrite existing output: $output" >&2
  exit 2
fi
mkdir -p "$output"

"$codeql" version --format=json >"$output/codeql-version.json"
"$python_bin" "$pilot_dir/verify_pytest_model_source.py" \
  --assumptions "$pilot_dir/pytest_model_assumptions.json" \
  --output "$output/pytest-model-source-receipt.json"
PYTHONPATH="$repo_root/src" "$python_bin" "$pilot_dir/prepare_flask.py" \
  --registry-root "$registry_root" --output "$run_root/flask-input" \
  >"$output/prepare-flask.stdout"
cp "$run_root/flask-input/input-manifest.json" "$output/input-manifest.json"
"$python_bin" - "$pilot_dir/observation_expectations.json" \
  "$pilot_dir/pytest_model_assumptions.json" "$output/input-manifest.json" \
  "$output/pre-registration.json" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

expectations, pytest_model, manifest, output = map(Path, sys.argv[1:])
output.write_text(json.dumps({
    "schema": "mock-spec.python-observation-pre-registration/v1",
    "expectations_sha256": hashlib.sha256(expectations.read_bytes()).hexdigest(),
    "pytest_model_sha256": hashlib.sha256(pytest_model.read_bytes()).hexdigest(),
    "input_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
}, indent=2) + "\n", encoding="utf-8")
PY
"$python_bin" - "$pilot_dir/issue26_expectations.json" \
  "$pilot_dir/observation_mapping.py" "$pilot_dir/pytest-exception-flow.ql" \
  "$pilot_dir/flask-observation-flow.ql" "$pilot_dir/mechanism_probe.py" \
  "$pilot_dir/verify_issue26.py" "$output/issue26-pre-registration.json" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

expectations, mapper, pytest_query, flask_query, source, verifier, output = map(
    Path, sys.argv[1:]
)
digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
output.write_text(json.dumps({
    "schema": "mock-spec.issue26-pre-registration/v1",
    "expectations_sha256": digest(expectations),
    "observation_mapper_sha256": digest(mapper),
    "pytest_query_sha256": digest(pytest_query),
    "flask_query_sha256": digest(flask_query),
    "mechanism_source_sha256": digest(source),
    "verifier_sha256": digest(verifier),
}, indent=2) + "\n", encoding="utf-8")
PY
"$python_bin" - "$pilot_dir/state_observation_expectations.json" \
  "$pilot_dir/mechanism_probe.py" "$pilot_dir/state-boundary-flow.ql" \
  "$pilot_dir/visible-state-flow.ql" \
  "$pilot_dir/state_observation_mapping.py" "$output/state-pre-registration.json" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

expectations, source, query, visible_query, mapper, output = map(Path, sys.argv[1:])
digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
output.write_text(json.dumps({
    "schema": "mock-spec.python-state-observation-pre-registration/v1",
    "expectations_sha256": digest(expectations),
    "source_sha256": digest(source),
    "query_sha256": digest(query),
    "visible_query_sha256": digest(visible_query),
    "mapper_sha256": digest(mapper),
}, indent=2) + "\n", encoding="utf-8")
PY
mkdir "$run_root/probe-input"
cp "$pilot_dir/mechanism_probe.py" "$run_root/probe-input/mechanism_probe.py"
cp "$pilot_dir/pytest_model_probe.py" "$run_root/probe-input/pytest_model_probe.py"

for kind in probe flask; do
  /usr/bin/time -v -o "$output/$kind-db.time" \
    timeout 180 "$codeql" database create "$run_root/$kind-db" \
      --language=python --source-root="$run_root/$kind-input" \
      --threads=2 --ram=4096 --overwrite \
      >"$output/$kind-db.stdout" 2>"$output/$kind-db.stderr"
done

run_query() {
  local database_kind=$1
  local query_name=$2
  local prefix="$database_kind-$query_name"
  /usr/bin/time -v -o "$output/$prefix.time" \
    timeout 180 "$codeql" query run \
      --database="$run_root/$database_kind-db" --threads=2 --ram=4096 \
      --output="$run_root/$prefix.bqrs" "$pilot_dir/$query_name.ql" \
      >"$output/$prefix.stdout" 2>"$output/$prefix.stderr"
  "$codeql" bqrs decode --format=csv --output="$output/$prefix.csv" \
    "$run_root/$prefix.bqrs"
}

for query in local-flow global-flow control-flow state-flow effects exceptions reachability unknown-effect-flow unknown-exception-state state-boundary-flow visible-state-flow pytest-exception-flow; do
  run_query probe "$query"
done
for query in control-flow state-flow exceptions flask-abort flask-observation-flow pytest-exception-flow; do
  run_query flask "$query"
done

(
  cd "$pilot_dir"
  sha256sum ./*.ql mechanism_probe.py pytest_model_probe.py prepare_flask.py verify_results.py \
    verify_pytest_model_source.py observation_mapping.py observation_expectations.json \
    pytest_model_assumptions.json state_observation_mapping.py \
    state_observation_expectations.json issue26_expectations.json verify_issue26.py run_pilot.sh
) >"$output/query-and-adapter-sha256.txt"
PYTHONPATH="$pilot_dir" "$python_bin" "$pilot_dir/verify_results.py" \
  --results "$output" --output "$output/verdict.json"
PYTHONPATH="$pilot_dir" "$python_bin" "$pilot_dir/observation_mapping.py" \
  --results "$output" --expectations "$pilot_dir/observation_expectations.json" \
  --output "$output/observation-study.json"
PYTHONPATH="$pilot_dir" "$python_bin" "$pilot_dir/state_observation_mapping.py" \
  --results "$output" --expectations "$pilot_dir/state_observation_expectations.json" \
  --output "$output/state-observation-study.json"
PYTHONPATH="$pilot_dir" "$python_bin" "$pilot_dir/verify_issue26.py" \
  --results "$output" --expectations "$pilot_dir/issue26_expectations.json" \
  --output "$output/issue26-verdict.json"
