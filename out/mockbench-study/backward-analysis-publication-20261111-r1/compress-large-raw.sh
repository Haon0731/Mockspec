#!/usr/bin/env bash
set -euo pipefail

repo=${1:-$(git rev-parse --show-toplevel)}
cd "$repo"

rg --files --hidden --no-ignore out/mockbench-study out/issue29-* \
  | sort -u | while IFS= read -r source; do
    case "$source" in *.gz|*.publication-compression.json) continue ;; esac
    [ "$(stat -c %s "$source")" -gt 95000000 ] || continue
    compressed="$source.gz"
    receipt="$source.publication-compression.json"
    if [ -e "$compressed" ] || [ -e "$receipt" ]; then
      continue
    fi
    gzip -n -9 -c -- "$source" >"$compressed"
    original_sha=$(sha256sum "$source" | cut -d' ' -f1)
    compressed_sha=$(sha256sum "$compressed" | cut -d' ' -f1)
    roundtrip_sha=$(gzip -dc -- "$compressed" | sha256sum | cut -d' ' -f1)
    [ "$roundtrip_sha" = "$original_sha" ] || { echo "round-trip mismatch: $source" >&2; exit 1; }
    printf '{\n  "schema": "mockspec.publication-compression/v1",\n  "source": "%s",\n  "original_bytes": %s,\n  "original_sha256": "%s",\n  "compressed": "%s",\n  "compressed_bytes": %s,\n  "compressed_sha256": "%s",\n  "round_trip_verified": true,\n  "restore": "gzip -dc %s > %s"\n}\n' \
      "$source" "$(stat -c %s "$source")" "$original_sha" "$compressed" \
      "$(stat -c %s "$compressed")" "$compressed_sha" "$compressed" "$source" >"$receipt"
  done
