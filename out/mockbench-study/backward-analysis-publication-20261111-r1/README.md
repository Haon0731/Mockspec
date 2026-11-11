# Backward-analysis publication manifest

This directory separates reviewable research evidence from local raw and rebuildable files.
It covers the issue #19 repair line through the eventual issue #31 formal run. Run
`out/mockbench-study/backward-analysis-publication-20261111-r1/refresh.sh` from the repository root after the formal run and any required compression
sidecars are complete. The refresh refuses to include its own previous manifest outputs.

`publishable-artifacts.txt` is the exact artifact staging list. Its companion SHA256 file
binds every listed file. `raw-local-only.sha256` records uncompressed raw files that remain
on the host because their lossless `.gz` sidecars are published. No raw file is deleted.
`compression-receipts.sha256` binds both the original run compression receipts and the
publication compression receipts added for previously uncompressed files.

Rebuildable cache entries, compiled classes and analysis databases are listed in
`rebuildable-exclusions.txt`. They are not evidence inputs and are not staged.

Restore a raw file using the exact command in its compression receipt. For older run
receipts, use `gzip -dc PATH.json.gz > PATH.json`, then compare the restored SHA256 with
the receipt. Publication sidecars use deterministic `gzip -n -9`; decompression is
byte-for-byte verified before a receipt is written.

`compress-large-raw.sh` creates a deterministic sidecar only for an uncompressed raw file
larger than 95 MB. It refuses to overwrite any existing `.gz` or receipt. Run it after a
new formal experiment, then run `refresh.sh` again. The raw file remains local and intact.

The manifest does not classify a candidate graph as semantic success. Read each case's
result contract, validation status, unknowns and backend support status.
