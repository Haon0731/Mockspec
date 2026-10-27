#!/usr/bin/env bash
# One preregistered paid run; use repository root and exported credentials. Output must not exist.
set -euo pipefail
export OPENAI_AGENTS_DISABLE_TRACING=1
PYTHONPATH=src .venv/bin/python experiments/staged-inference/run.py --mode live_inference --variant C --snapshot /home/haon/projects/Stub-Tester/.omx/developer-realistic-registry-max/formal-snapshots/java-bechmark-strong/ff6812732f1e2779e09005d721f464cebed6b587925130e6a2555a154097214a --case-id 'de.codecentric.boot.admin.client.registration.CloudFoundryApplicationFactoryTest#should_use_application_uri@d0085edf' --model deepseek-flash --repository-read --output-dir out/staged-inference/issue17-query-recovery-20261027/live/run --max-output-tokens 8192 --max-total-tokens 200000 --timeout-seconds 600 --parsing-timeout-seconds 120 --max-cost-usd 1.00 --input-cost-per-million 0.30 --output-cost-per-million 1.20
