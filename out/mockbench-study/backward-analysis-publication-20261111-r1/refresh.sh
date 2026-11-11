#!/usr/bin/env bash
set -euo pipefail

repo=${1:-$(git rev-parse --show-toplevel)}
publication=out/mockbench-study/backward-analysis-publication-20261111-r1
cd "$repo"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

{
  git ls-files --others --exclude-standard out/mockbench-study
  git ls-files --others --exclude-standard 'out/issue29-*'
} | rg -v "^$publication/" | sort -u >"$tmp/candidates"

rg '(^|/)(fresh-cache|cache|classes|database|db|generated-classes)(/|$)|/issue28-java-demand-(budget-)?cache-[^/]+/|\.class$' \
  "$tmp/candidates" >"$publication/rebuildable-exclusions.txt" || true

while IFS= read -r path; do
  [ -f "$path.gz" ] && printf '%s\n' "$path"
done <"$tmp/candidates" >"$tmp/raw-with-gzip"

while IFS= read -r path; do
  [ -f "$path" ] || continue
  [ "$(stat -c %s "$path")" -gt 95000000 ] && printf '%s\n' "$path"
done <"$tmp/candidates" >"$tmp/oversize"

comm -23 "$tmp/candidates" \
  <(cat "$publication/rebuildable-exclusions.txt" "$tmp/raw-with-gzip" "$tmp/oversize" | sort -u) \
  >"$publication/publishable-artifacts.txt"

while IFS= read -r path; do
  [ -f "$path" ] || continue
  printf '%s  %s\n' "$(sha256sum "$path" | cut -d' ' -f1)" "$path"
done <"$publication/publishable-artifacts.txt" >"$publication/publishable-artifacts.sha256"

while IFS= read -r source; do
  [ -f "$source" ] || continue
  printf '%s  %s\n' "$(sha256sum "$source" | cut -d' ' -f1)" "$source"
done < <(cat "$tmp/raw-with-gzip" "$tmp/oversize" | sort -u) >"$publication/raw-local-only.sha256"

rg --files --hidden --no-ignore out/mockbench-study \
  -g 'analysis-compression.json' -g '*.publication-compression.json' \
  | rg -v "^$publication/" | sort \
  | while IFS= read -r receipt; do
      printf '%s  %s\n' "$(sha256sum "$receipt" | cut -d' ' -f1)" "$receipt"
    done >"$publication/compression-receipts.sha256"

printf 'artifact_candidates=%s\npublishable_artifacts=%s\nraw_local_only=%s\nrebuildable_exclusions=%s\n' \
  "$(wc -l <"$tmp/candidates")" \
  "$(wc -l <"$publication/publishable-artifacts.txt")" \
  "$(wc -l <"$publication/raw-local-only.sha256")" \
  "$(wc -l <"$publication/rebuildable-exclusions.txt")" \
  >"$publication/counts.txt"
