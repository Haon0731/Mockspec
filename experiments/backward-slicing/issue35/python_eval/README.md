# Python evaluation registration

This directory freezes the evaluation-side references for `ansible_119`,
`click_402`, `click_423`, and `langflow_189`. Each case contains the slot
envelope, the reconstructed reference test, and a registration that separates
ordinary assertions, interaction assertions, F_stub items, applicable rows,
and mutation identities.

`ansible_119` has one disclosed normalization. The processed reference used an
absolute host source path while the frozen Agent test uses the project-relative
path. The evaluation reference restores that frozen ordinary-test line before
extracting the Mock slots. The registration records both digests and the exact
replacement.

Run preparation and static checks with:

```bash
PYTHONPATH=src:experiments/backward-slicing:experiments/backward-slicing/issue35 \
  .venv/bin/python experiments/backward-slicing/issue35/python_eval.py prepare \
  --output experiments/backward-slicing/issue35/python_eval
```

Run one frozen envelope after the candidate-freeze gate opens with:

```bash
PYTHONPATH=src:experiments/backward-slicing:experiments/backward-slicing/issue35 \
  .venv/bin/python experiments/backward-slicing/issue35/python_eval.py run \
  --case click_402 \
  --envelope /path/to/frozen-envelope.json \
  --variant reference --mutant original \
  --output /new/output/directory
```

Docker executions acquire `/tmp/mockspec-issue35-docker.lock`. The runner mounts
the exact frozen snapshot tree and its runtime dependency bundle, disables the
network, verifies the staged file hashes, and observes the actual MUT frame,
filename, and code variant. Candidate and mutant staging files are deleted after
the container exits. Mutation and candidate evaluation remains gated until all
54 generated candidates are frozen.
