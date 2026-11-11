from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
QUERY = HERE / "generic-observation-flow.ql"
QLPACK = HERE / "qlpack.yml"

TEST_SOURCE = """import asyncio
import pytest
from pkg.product import direct, async_raw, async_done

def test_flow(obj):
    value = direct(obj)
    assert value == 2
    raw = async_raw(obj)
    assert raw
    with pytest.raises(ValueError):
        async_raw(obj)
    invalid = asyncio.run(async_raw(obj), 1)
    assert invalid
    completed = asyncio.run(async_done(obj))
    assert completed == 4

class Other:
    def test_flow(self, obj):
        direct(obj)
"""

PRODUCT_SOURCE = """def opaque(value):
    return value

def direct(obj):
    if False:
        return 999
    opaque(obj)
    obj.value = 1
    obj.value = 2
    return obj.value

async def async_raw(obj):
    opaque(obj)
    obj.value = 3
    raise ValueError("not executed by a raw call")

async def async_done(obj):
    opaque(obj)
    obj.value = 4
    return obj.value
"""

UNRELATED_SOURCE = """def direct(obj):
    return 500
"""


def _write_rows(path: Path, rows: list[list[object]]) -> None:
    with path.open("w", newline="") as stream:
        csv.writer(stream).writerows(rows)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def run_fixture(codeql: Path, output: Path) -> dict[str, object]:
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    started = time.monotonic()
    version = json.loads(subprocess.check_output(
        [str(codeql), "version", "--format=json"], text=True, timeout=30))
    with tempfile.TemporaryDirectory(prefix="mockspec-python-query-fixture-") as temporary:
        work = Path(temporary)
        source = work / "source"
        query_root = work / "query"
        (source / "tests").mkdir(parents=True)
        (source / "pkg").mkdir(parents=True)
        (source / "other").mkdir(parents=True)
        (source / "tests/test_flow.py").write_text(TEST_SOURCE)
        (source / "pkg/__init__.py").write_text("")
        (source / "pkg/product.py").write_text(PRODUCT_SOURCE)
        (source / "other/product.py").write_text(UNRELATED_SOURCE)
        query_root.mkdir()
        shutil.copy2(QLPACK, query_root / "qlpack.yml")
        shutil.copy2(QUERY, query_root / QUERY.name)

        facts = {
            "selectedTestPathFact": [["tests/test_flow.py"]],
            "selectedTestFunctionFact": [["test_flow"]],
            "selectedTestStartLineFact": [[5]],
            "selectedObservationLineFact": [[7], [9], [10], [13], [15]],
            "selectedObservationExprFact": [
                [7, 7, 12, "assert"], [9, 9, 12, "assert"], [10, 10, 10, "call"],
                [13, 13, 12, "assert"], [15, 15, 12, "assert"],
            ],
            "selectedSupportPathFact": [],
            "visibleLoaderBindingFact": [],
            "visibleImportBindingFact": [
                [6, "direct", "pkg/product.py", 4, "direct", 10, 0, ""],
                [8, "async_raw", "pkg/product.py", 12, "async_raw", 15, -1, ""],
                [11, "async_raw", "pkg/product.py", 12, "async_raw", 15, -1, ""],
                [12, "async_raw", "pkg/product.py", 12, "async_raw", 15, -1, ""],
                [14, "async_done", "pkg/product.py", 17, "async_done", 20, 2, "asyncio"],
            ],
        }
        fact_paths: dict[str, Path] = {}
        for name, rows in facts.items():
            path = output / f"{name}.csv"
            _write_rows(path, rows)
            fact_paths[name] = path

        database = work / "database"
        bqrs = work / "result.bqrs"
        commands = [
            [str(codeql), "database", "create", str(database), "--language=python",
             f"--source-root={source}", "--threads=2", "--ram=4096", "--overwrite"],
            [str(codeql), "query", "run", f"--database={database}", "--threads=2", "--ram=4096",
             f"--output={bqrs}", *[f"--external={name}={path}"
                                    for name, path in fact_paths.items()],
             str(query_root / QUERY.name)],
            [str(codeql), "bqrs", "decode", "--format=csv",
             f"--output={output / 'relations.csv'}", str(bqrs)],
        ]
        timings: list[float] = []
        for index, command in enumerate(commands):
            phase_started = time.monotonic()
            completed = subprocess.run(command, cwd=work, text=True, capture_output=True,
                                       timeout=360, check=False)
            timings.append(time.monotonic() - phase_started)
            (output / f"phase-{index}.stdout").write_text(completed.stdout)
            (output / f"phase-{index}.stderr").write_text(completed.stderr)
            if completed.returncode:
                raise RuntimeError(f"fixture phase {index} failed: {completed.returncode}")

    with (output / "relations.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    raw_lines = {13, 14, 15}
    done_lines = {18, 19, 20}
    body_relations = {"product_return_to_observation", "product_guard_controls_return_to_observation",
                      "product_exception_exit_unknown", "product_call_unknown_effect",
                      "product_attribute_write_unknown_effect"}
    summary = {
        "status": "not_evaluated",
        "rows": len(rows),
        "timings_seconds": {"database": timings[0], "query": timings[1], "decode": timings[2],
                            "total": time.monotonic() - started},
        "provenance": {
            "codeql_version": version,
            "query_sha256": _sha256_bytes(QUERY.read_bytes()),
            "runner_sha256": _sha256_bytes(Path(__file__).read_bytes()),
            "fixture_source_sha256": {
                "tests/test_flow.py": _sha256_bytes(TEST_SOURCE.encode()),
                "pkg/product.py": _sha256_bytes(PRODUCT_SOURCE.encode()),
                "other/product.py": _sha256_bytes(UNRELATED_SOURCE.encode()),
            },
            "fact_sha256": {name: _sha256_bytes(path.read_bytes())
                            for name, path in fact_paths.items()},
            "relations_sha256": _sha256_bytes((output / "relations.csv").read_bytes()),
            "command_shape": [
                "codeql database create <database> --language=python --source-root=<source> "
                "--threads=2 --ram=4096 --overwrite",
                "codeql query run --database=<database> --threads=2 --ram=4096 "
                "--output=<bqrs> --external=<predicate>=<fact>... <query>",
                "codeql bqrs decode --format=csv --output=<relations.csv> <bqrs>",
            ],
        },
        "checks": {
            "dead_return_excluded": not any(row["sourcePath"] == "pkg/product.py"
                                             and row["sourceLine"] == "6"
                                             and row["relation"] == "product_return_to_observation"
                                             for row in rows),
            "unrelated_same_name_excluded": not any(row["sourcePath"] == "other/product.py"
                                                     for row in rows),
            "same_file_same_name_scope_excluded": not any(
                row["sourcePath"] == "tests/test_flow.py" and row["sourceLine"] == "19"
                for row in rows),
            "raw_async_body_excluded": not any(row["relation"] in body_relations
                                                and int(row["sourceLine"]) in raw_lines for row in rows),
            "completed_async_body_present": any(row["relation"] in body_relations
                                                 and int(row["sourceLine"]) in done_lines for row in rows),
            "raw_binding_present": any(row["relation"] == "resolved_raw_async_test_product_call"
                                       for row in rows),
            "raw_raises_and_invalid_run_bind_only": {11, 12}.issubset({
                int(row["sourceLine"]) for row in rows
                if row["relation"] == "visible_raw_async_import_product_call"
            }),
            "unknown_effect_present": any(row["relation"] == "product_call_unknown_effect"
                                          for row in rows),
            "overwritten_writes_remain_unknown": {8, 9}.issubset({
                int(row["sourceLine"]) for row in rows
                if row["relation"] == "product_attribute_write_unknown_effect"
            }),
            "no_typed_state_flow": not any("state_flow" in row["relation"] for row in rows),
        },
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    if not all(summary["checks"].values()):
        raise AssertionError(summary["checks"])
    return summary


if __name__ == "__main__":
    result = run_fixture(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
    print(json.dumps(result, sort_keys=True))
