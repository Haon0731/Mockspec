# WALA return-boundary regression fixtures

These small Java programs are compiled and statically analyzed, never executed.
`cross-caller` calls the same helper through two different methods; `same-caller`
calls it twice inside `Cut.second`. Only the second result feeds `observeValue`.
Both include a guard and two explicit returns. They are structural analysis
fixtures, not MockBench cases or evidence of Agent generation quality.

`raw.json` is the unmodified merged WALA shared/root export. `generation.json`
records commands, source/probe hashes and the checkpoint digest. Temporary paths
in these records identify the original capture, not required installation paths.
Tests locate nodes by semantic fields; no instruction/basic-block metadata is
invented for return boundaries. Synthetic graph changes in negative tests are
explicitly limited to those tests and do not alter the archived exports.

From the repository root, run the fast regression:

```bash
.venv/bin/python -m pytest -q experiments/backward-slicing/wala-pilot/test_return_boundary.py
```

To regenerate one fixture in a new temporary directory with an installed WALA
classpath (requires JDK and WALA dependencies, no target execution):

```bash
.venv/bin/python experiments/backward-slicing/wala-pilot/regenerate_return_boundary_fixture.py \
  --repo . \
  --fixture-dir experiments/backward-slicing/wala-pilot/fixtures/return-boundary/cross-caller \
  --wala-classpath-file /tmp/wala-cp.txt \
  --output /tmp/wala-cross-caller-check
```

Use `same-caller` and a separate output directory for the other fixture. A fresh
export must be projected through the same regression checks before claiming
export-to-projection validation; snapshot tests alone do not establish that.
