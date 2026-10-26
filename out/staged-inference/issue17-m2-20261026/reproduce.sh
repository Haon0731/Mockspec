#!/usr/bin/env bash
# Run from repository root with credentials already exported; output must not exist.
set -euo pipefail
export OPENAI_AGENTS_DISABLE_TRACING=1
PYTHONPATH=src .venv/bin/python experiments/staged-inference/run.py --mode live_inference --variant C --snapshot /home/haon/projects/Stub-Tester/.omx/developer-realistic-registry-max-fix23/formal-snapshots/python-benchmark-new/241ef457f8d071c23e6acd658b684b11b54f13fa955e097d6bebe35dc755f47d --case-id flask_308 --model deepseek-flash --repository-read --output-dir out/staged-inference/issue17-m2-20261026/run --max-model-calls 10 --max-output-tokens 8192 --max-total-tokens 200000 --timeout-seconds 600 --parsing-timeout-seconds 120 --max-cost-usd 1.00 --input-cost-per-million 0.30 --output-cost-per-million 1.20
