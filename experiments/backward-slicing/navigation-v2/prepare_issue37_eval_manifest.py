"""Join the r2 navigation inventory to the frozen MockBench evaluation banks.

This is a metadata-only preflight. It does not execute tests, read reference-test
contents, apply mutants, or call a model. The Stub-Tester workspace is treated as
read-only and only its already-frozen manifests and CSV ledgers are consumed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


JAVA_IMAGE = "sha256:7268546607aefcf0ab62e58f9b58d9c890d7f3b50bd0bec1231314cbb357bd36"
PYTHON_IMAGE = "sha256:04a57de0d4ccb65b7f73a5880277b19c65ca23c5b677d89d3a4673706f55866b"
GENERATION_INVENTORY = Path("out/mockbench-study/frozen-agent-inventory-20261109.json")
CANDIDATE_VALIDATION = Path(
    "/home/haon/projects/Stub-Tester/outputs/RQ4/candidate_migration_20260913/validation.json"
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _java_navigation_id(row: dict[str, str]) -> str:
    commit = row["case_id"].rsplit("_", 1)[-1][:8]
    return f'{row["test_class"]}#{row["test_method"]}@{commit}'


def _mutants_by_case(rows: list[dict[str, str]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        result[row["case_id"]].append(row["mutant_id"])
    return {case_id: sorted(ids) for case_id, ids in result.items()}


def _manifest_entries(path: Path, prefix: str = "repo/") -> dict[str, tuple[str, int, str]]:
    manifest = _read_json(path)
    return {
        entry["relpath"]: (entry["kind"], entry["mode"], entry["sha256"])
        for entry in manifest["entries"]
        if entry["relpath"].startswith(prefix)
    }


def _entries_digest(entries: dict[str, tuple[str, int, str]]) -> str:
    payload = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _require_unique_mutants(
    rows: list[dict[str, str]], *, expected_count: int, language: str
) -> dict[str, list[dict[str, str]]]:
    if len(rows) != expected_count:
        raise ValueError(f"{language} mutant count changed: {len(rows)} != {expected_count}")
    for field in ("mutant_id", "comparison_key"):
        values = [row[field] for row in rows]
        if len(values) != len(set(values)):
            raise ValueError(f"{language} mutant {field} is not globally unique")
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["case_id"]].append(row)
    return grouped


def _snapshot_manifest(snapshot_root: str) -> Path:
    return Path(snapshot_root) / "control/tree-manifest.json"


def _python_snapshot_relpath(source: str, project: str) -> str:
    marker = f"/{project}/"
    if marker not in source:
        raise ValueError(f"Python source is outside its project root: {source}")
    return "repo/" + source.split(marker, 1)[1]


def _java_snapshot_relpath(case_root: str, source: str) -> str:
    relative = Path(source).resolve().relative_to(Path(case_root).resolve())
    return "repo/" + relative.as_posix()


def _single_inserted_line(old_path: Path, new_path: Path) -> tuple[int, str] | None:
    old = old_path.read_text(encoding="utf-8").splitlines()
    new = new_path.read_text(encoding="utf-8").splitlines()
    if len(new) != len(old) + 1:
        return None
    for index, line in enumerate(new):
        if old == new[:index] + new[index + 1 :]:
            return index + 1, line
    return None


def _target_coordinates(value: Any, logical_path: str) -> list[int]:
    coordinates: list[int] = []
    if isinstance(value, dict):
        location = value.get("location")
        if isinstance(location, dict) and location.get("logical_path") == logical_path:
            coordinates.extend(
                location[name]
                for name in ("start_line", "end_line")
                if isinstance(location.get(name), int)
            )
        for child in value.values():
            coordinates.extend(_target_coordinates(child, logical_path))
    elif isinstance(value, list):
        for child in value:
            coordinates.extend(_target_coordinates(child, logical_path))
    return coordinates


def _verify_python_patch(patch: dict[str, Any], benchmark_root: Path) -> bool:
    source = benchmark_root / patch["source_path"]
    if not source.is_file() or _sha256(source) != patch["source_sha256"]:
        return False
    lines = source.read_text(encoding="utf-8").splitlines()
    start = int(patch["start_line"]) - 1
    end = int(patch["end_line"])
    if lines[start:end] != patch["original_lines"]:
        return False
    mutated = lines[:start] + patch["replacement_lines"] + lines[end:]
    payload = ("\n".join(mutated) + "\n").encode()
    return hashlib.sha256(payload).hexdigest() == patch["mutated_source_sha256"]


def _verify_java_patch(patch: dict[str, Any], case_root: Path) -> bool:
    source = case_root / patch["source_path"]
    if not source.is_file() or _sha256(source) != patch["source_sha256"]:
        return False
    text = source.read_text(encoding="utf-8")
    start = int(patch["start"])
    end = int(patch["end"])
    return text[start:end] == patch["original"] and patch["replacement"] != patch["original"]


def build_manifest(
    stub_tester_root: Path,
    r2_root: Path,
    generation_inventory_path: Path = GENERATION_INVENTORY,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    python_freeze = (
        stub_tester_root
        / "agent_results/rq4/python_current_bank_20260912/final_freeze"
    )
    python_bank = python_freeze / "bank"
    java_bank = (
        stub_tester_root
        / "outputs/RQ4/java_curated120_docker_20260912/gold_revalidation_identity_fixed"
    )

    source_paths = {
        "r2_manifest": r2_root / "manifest.json",
        "generation_inventory": generation_inventory_path,
        "candidate_validation": CANDIDATE_VALIDATION,
        "python_immutable": python_freeze / "immutable_inputs.json",
        "python_cases": python_bank / "interaction_rq4_cases.csv",
        "python_mutants": python_bank / "interaction_mutants.csv",
        "python_bank_manifest": python_bank / "interaction_gold_manifest.json",
        "java_cases": java_bank / "interaction_rq4_cases.csv",
        "java_mutants": java_bank / "interaction_mutants.csv",
        "java_exact_patches": java_bank / "exact_patches.json",
        "java_bank_manifest": java_bank / "interaction_gold_manifest.json",
    }
    missing = [str(path) for path in source_paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing frozen inputs: " + ", ".join(missing))

    r2_manifest = _read_json(source_paths["r2_manifest"])
    generation_inventory = {
        row["case_id"]: row for row in _read_json(source_paths["generation_inventory"])["cases"]
    }
    candidate_validation = _read_json(source_paths["candidate_validation"])
    proof_root = Path(candidate_validation["proof_root"])
    launch_path = proof_root / "launch.sh"
    package_path = proof_root / "package.json"
    if not launch_path.is_file() or not package_path.is_file():
        raise FileNotFoundError("validated Candidate migration proof is unavailable")
    packaged_runs = _read_json(package_path).get("candidate_runs", [])
    if len(packaged_runs) != 30:
        raise ValueError("validated Candidate migration package no longer contains 30 runs")
    python_immutable = _read_json(source_paths["python_immutable"])
    python_cases = _read_csv(source_paths["python_cases"])
    java_cases = _read_csv(source_paths["java_cases"])
    python_mutant_rows = _read_csv(source_paths["python_mutants"])
    java_mutant_rows = _read_csv(source_paths["java_mutants"])
    java_patches = {
        row["mutant_id"]: row
        for row in _read_json(source_paths["java_exact_patches"])["mutants"]
    }
    python_mutants = _require_unique_mutants(
        python_mutant_rows, expected_count=262, language="python"
    )
    java_mutants = _require_unique_mutants(
        java_mutant_rows, expected_count=120, language="java"
    )
    if set(java_patches) != {row["mutant_id"] for row in java_mutant_rows}:
        raise ValueError("Java exact-patch membership differs from the 120-mutant bank")

    python_by_id = {row["case_id"]: row for row in python_cases}
    java_by_navigation_id = {_java_navigation_id(row): row for row in java_cases}

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for digest, navigation in sorted(r2_manifest.items(), key=lambda item: item[1]["case_id"]):
        case_id = navigation["case_id"]
        facts = _read_json(r2_root / navigation["facts_path"])
        generation = generation_inventory.get(case_id)
        if generation is None or generation["snapshot_id"] != navigation["snapshot_id"]:
            errors.append(f"{case_id}: r2 snapshot is absent from the generation inventory")
            continue
        generation_entries = _manifest_entries(
            _snapshot_manifest(generation["snapshot_root"])
        )
        blockers: list[str] = []
        common = {
            "case_id": case_id,
            "r2_artifact_key": digest,
            "language": navigation["language"],
            "dataset": navigation["dataset"],
            "generation_snapshot_id": navigation["snapshot_id"],
            "navigation_view_path": str((r2_root / navigation["view_path"]).resolve()),
            "navigation_view_sha256": navigation["view_sha256"],
            "navigation_view_tokens": navigation["view_tokens"],
            "reference_visibility": "evaluator_only",
            "generation_reference_access": "denied",
            "candidate_registration_status": "pending_new_two_arm_candidate_manifest",
            "candidate_baseline_status": "pending_candidate_execution",
            "evaluation_admission_status": "pending_candidate_manifest_and_clean_baseline",
            "validated_launch_path": str(launch_path.resolve()),
            "validated_launch_sha256": _sha256(launch_path),
            "masked_test_delta": "none",
            "r2_target_coordinate_range": "",
        }
        if navigation["language"] == "python":
            bank_row = python_by_id.get(case_id)
            identity = python_immutable["cases"].get(case_id)
            if bank_row is None or identity is None:
                errors.append(f"{case_id}: missing Python bank identity")
                continue
            mutant_rows = python_mutants.get(case_id, [])
            mutant_ids = sorted(row["mutant_id"] for row in mutant_rows)
            evaluation_entries = _manifest_entries(
                _snapshot_manifest(str(Path(identity["snapshot_tree"]).parent))
            )
            generation_repo_digest = _entries_digest(generation_entries)
            evaluation_repo_digest = _entries_digest(evaluation_entries)
            if generation_entries != evaluation_entries:
                blockers.append("generation_and_evaluation_repository_manifests_differ")
            target_path = facts["identity"]["target_selector"].split("::", 1)[0]
            target_relpath = "repo/" + target_path
            masked_hash = facts["identity"]["masked_test_sha256"]
            if generation_entries.get(target_relpath, (None, None, None))[2] != masked_hash:
                blockers.append("r2_masked_test_hash_not_in_generation_snapshot")
            if evaluation_entries.get(target_relpath, (None, None, None))[2] != masked_hash:
                blockers.append("masked_test_differs_in_evaluation_snapshot")
                generation_test = Path(generation["snapshot_root"]) / "tree" / target_relpath
                evaluation_test = Path(identity["snapshot_tree"]) / target_relpath
                inserted = _single_inserted_line(generation_test, evaluation_test)
                if inserted is not None:
                    common["masked_test_delta"] = (
                        f"insert_line={inserted[0]};text={inserted[1]}"
                    )
                coordinates = _target_coordinates(facts, target_path)
                if coordinates:
                    common["r2_target_coordinate_range"] = (
                        f"{min(coordinates)}-{max(coordinates)}"
                    )
            edited_source_hashes = {row["sha256"] for row in identity["edited_sources"]}
            for source in identity["edited_sources"]:
                relative = _python_snapshot_relpath(source["path"], identity["project"])
                actual_hash = _sha256(Path(source["path"]))
                hashes = {
                    actual_hash,
                    generation_entries.get(relative, (None, None, None))[2],
                    evaluation_entries.get(relative, (None, None, None))[2],
                    source["sha256"],
                }
                if hashes != {source["sha256"]}:
                    blockers.append(f"cut_hash_mismatch:{relative}")
            for mutant in mutant_rows:
                patch = json.loads(mutant["exact_patch"])
                if (
                    not patch.get("patch_digest")
                    or patch.get("source_sha256") not in edited_source_hashes
                    or not _verify_python_patch(
                        patch, Path("/home/haon/data/processed/python-benchmark-new")
                    )
                ):
                    blockers.append(f"invalid_frozen_patch:{mutant['mutant_id']}")
            reference_path = Path(identity["reference_path"])
            if _sha256(reference_path) != identity["reference_sha256"]:
                blockers.append("reference_test_hash_mismatch")
            if bank_row["selected_for_rq4"] != "True":
                blockers.append("case_not_admitted_to_frozen_bank")
            common.update(
                {
                    "evaluation_case_id": bank_row["case_uid"],
                    "case_selector": case_id,
                    "evaluation_snapshot_id": identity["snapshot_id"],
                    "generation_repository_manifest_sha256": generation_repo_digest,
                    "evaluation_repository_manifest_sha256": evaluation_repo_digest,
                    "controller_image": PYTHON_IMAGE,
                    "test_container_image": PYTHON_IMAGE,
                    "evaluation_entry": "launch.sh python-candidate RUN_ID CANDIDATE_RUN --case",
                    "evaluation_adapter": "scripts.migration.rq4.python_candidate",
                    "reference_test_locator": identity["reference_path"],
                    "reference_test_sha256": identity["reference_sha256"],
                    "runtime_lock_locator": identity["runtime_lock_path"],
                    "runtime_lock_sha256": identity["runtime_lock_sha256"],
                    "preregistered_mutant_count": len(mutant_ids),
                    "preregistered_mutant_ids": ";".join(mutant_ids),
                    "mutant_bank": str(source_paths["python_mutants"].resolve()),
                }
            )
        else:
            bank_row = java_by_navigation_id.get(case_id)
            if bank_row is None:
                errors.append(f"{case_id}: missing Java bank identity")
                continue
            evaluation_case_id = bank_row["case_id"]
            mutant_rows = java_mutants.get(evaluation_case_id, [])
            mutant_ids = sorted(row["mutant_id"] for row in mutant_rows)
            case_root = Path("/home/haon/data/raw/java-bechmark-strong") / evaluation_case_id
            reference_path = Path(bank_row["gold_test_path"])
            target_relpath = _java_snapshot_relpath(str(case_root), str(reference_path))
            masked_hash = facts["identity"]["masked_test_sha256"]
            if generation_entries.get(target_relpath, (None, None, None))[2] != masked_hash:
                blockers.append("r2_masked_test_hash_not_in_generation_snapshot")
            selected_sources: dict[str, str] = {}
            for mutant in mutant_rows:
                source = case_root / mutant["mutated_file"]
                relative = "repo/" + mutant["mutated_file"]
                actual_hash = _sha256(source)
                selected_sources[relative] = actual_hash
                if generation_entries.get(relative, (None, None, None))[2] != actual_hash:
                    blockers.append(f"cut_hash_mismatch:{relative}")
                if mutant["compilable"] != "True":
                    blockers.append(f"noncompilable_frozen_mutant:{mutant['mutant_id']}")
                patch = java_patches[mutant["mutant_id"]]
                if (
                    patch["comparison_key"] != mutant["comparison_key"]
                    or patch["case_id"] != evaluation_case_id
                    or not _verify_java_patch(patch, case_root)
                ):
                    blockers.append(f"inapplicable_frozen_edit:{mutant['mutant_id']}")
            focal_class = bank_row["focal_class"].split("$", 1)[0]
            focal_relpath = "repo/src/main/java/" + focal_class.replace(".", "/") + ".java"
            focal_path = case_root / focal_relpath.removeprefix("repo/")
            if not focal_path.is_file():
                blockers.append(f"focal_source_missing:{focal_relpath}")
            elif generation_entries.get(focal_relpath, (None, None, None))[2] != _sha256(focal_path):
                blockers.append(f"focal_source_hash_mismatch:{focal_relpath}")
            reference_hash = _sha256(reference_path)
            if any(
                bank_row[field] != "True"
                for field in ("compile_pass", "original_test_pass", "has_gold_verification", "selected_for_rq4")
            ):
                blockers.append("case_not_admitted_to_frozen_bank")
            common.update(
                {
                    "evaluation_case_id": evaluation_case_id,
                    "case_selector": case_id,
                    "evaluation_snapshot_id": "frozen-java-bank:" + evaluation_case_id,
                    "generation_repository_manifest_sha256": _entries_digest(generation_entries),
                    "evaluation_repository_manifest_sha256": "selected_sources:" + _entries_digest(
                        {key: ("file", 0, value) for key, value in selected_sources.items()}
                    ),
                    "controller_image": PYTHON_IMAGE,
                    "test_container_image": JAVA_IMAGE,
                    "evaluation_entry": "launch.sh java-candidate RUN_ID CANDIDATE_RUN --case",
                    "evaluation_adapter": "scripts.migration.rq4.java_candidate",
                    "reference_test_locator": bank_row["gold_test_path"],
                    "reference_test_sha256": reference_hash,
                    "runtime_lock_locator": "java-bank container runtime identity",
                    "runtime_lock_sha256": "f7baecb4116b3125f78efb90fef41b363104642967c3fb93fd78f65e23e5135a",
                    "preregistered_mutant_count": len(mutant_ids),
                    "preregistered_mutant_ids": ";".join(mutant_ids),
                    "mutant_bank": str(source_paths["java_mutants"].resolve()),
                }
            )
        if not mutant_ids:
            blockers.append("no_preregistered_interaction_mutant")
        blockers.extend(
            ["candidate_manifest_not_registered", "candidate_clean_baseline_not_executed"]
        )
        alignment_blockers = [
            reason
            for reason in blockers
            if reason
            not in {"candidate_manifest_not_registered", "candidate_clean_baseline_not_executed"}
        ]
        common["input_alignment_status"] = "matched" if not alignment_blockers else "blocked"
        common["preflight_status"] = (
            "ready_for_generation" if not alignment_blockers else "blocked_input_alignment"
        )
        common["blocking_reasons"] = ";".join(dict.fromkeys(blockers))
        rows.append(common)

    if errors:
        raise ValueError("evaluation join failed:\n" + "\n".join(errors))
    if len(rows) != 165:
        raise ValueError(f"expected 165 joined cases, got {len(rows)}")

    language_counts = Counter(row["language"] for row in rows)
    mutant_counts: Counter[str] = Counter()
    for row in rows:
        mutant_counts[row["language"]] += row["preregistered_mutant_count"]
    expected = {"python": 106, "java": 59}
    if dict(language_counts) != expected:
        raise ValueError(f"language membership changed: {dict(language_counts)} != {expected}")
    expected_mutants = {"python": 262, "java": 120}
    if dict(mutant_counts) != expected_mutants:
        raise ValueError(f"mutant membership changed: {dict(mutant_counts)} != {expected_mutants}")

    summary = {
        "schema_version": "mockspec-issue37-evaluation-preflight/v2",
        "case_count": len(rows),
        "language_counts": dict(language_counts),
        "preregistered_mutant_counts": dict(mutant_counts),
        "input_alignment_matched_count": sum(
            row["input_alignment_status"] == "matched" for row in rows
        ),
        "ready_for_generation_count": sum(
            row["preflight_status"] == "ready_for_generation" for row in rows
        ),
        "evaluation_admitted_count": sum(
            row["evaluation_admission_status"] == "admitted" for row in rows
        ),
        "evaluation_pending_candidate_count": sum(
            row["evaluation_admission_status"] == "pending_candidate_manifest_and_clean_baseline"
            for row in rows
        ),
        "reference_evaluator_only_count": sum(
            row["reference_visibility"] == "evaluator_only" for row in rows
        ),
        "generation_reference_denied_count": sum(
            row["generation_reference_access"] == "denied" for row in rows
        ),
        "container_images": {"python": PYTHON_IMAGE, "java": JAVA_IMAGE},
        "candidate_runner": {
            "proof_root": str(proof_root),
            "launch_path": str(launch_path),
            "launch_sha256": _sha256(launch_path),
            "package_sha256": _sha256(package_path),
            "historical_packaged_run_count": len(packaged_runs),
            "new_two_arm_runs_registered": 0,
            "python_case_selector": "plain case_id",
            "java_case_selector": "navigation case_id",
            "admission_gates": [
                "candidate manifest registration",
                "candidate byte binding",
                "actual clean baseline pass",
                "frozen bank compatibility",
            ],
        },
        "source_file_sha256": {
            name: _sha256(path) for name, path in source_paths.items()
        },
        "validation_scope": (
            "read-only identity join over frozen manifests plus targeted SHA-256 checks "
            "of reference tests and selected CUT files; no test execution, mutant "
            "application, dependency-tree rehash, or model calls"
        ),
    }
    return rows, summary


def write_outputs(rows: list[dict[str, Any]], summary: dict[str, Any], output_root: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "eval-manifest.csv"
    with manifest_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    blocked_rows = [row for row in rows if row["input_alignment_status"] != "matched"]
    blockers_path = output_root / "alignment-blockers.csv"
    with blockers_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(blocked_rows)
    (output_root / "evaluation-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    alignment_note = (
        "165 例的生成快照、导航证据与冻结评估输入已逐例闭合。"
        if not blocked_rows
        else f"尚有 {len(blocked_rows)} 例输入身份未闭合，具体原因见 `alignment-blockers.csv`。"
    )
    blocker_detail = (
        "无待解决的快照、masked test 或导航坐标差异。"
        if not blocked_rows
        else "\n".join(
            f"- `{row['case_id']}`: {row['blocking_reasons']}" for row in blocked_rows
        )
    )
    report = f"""# Issue #37 评估清单预检

## 结论

- 导航清单的 165 个案例已逐一联接到冻结评估银行，Python 106 例，Java 59 例。
- 输入身份闭合 {summary['input_alignment_matched_count']}/165。{alignment_note}
- 165 例都有固定的语言级 Docker 镜像与已验证 Candidate 评估适配器。当前新两组 run 尚未注册 Candidate manifest，且尚无候选 clean baseline 实测，因此评估准入为 0/165，不能预填为通过。
- 参考测试共 165 份，均记录实际 SHA-256 并标记为 `evaluator_only`。生成阶段访问为 `denied`，只在候选字节冻结后由隔离评估器使用。
- 预登记交互变异体共 382 个，Python 262 个，Java 120 个。mutant ID 与 comparison key 全局唯一，每例至少 1 个；精确补丁的原始源码 SHA-256、字节/行区间和 bank member 绑定均通过只读校验。

## 固定执行环境

| 语言 | 案例 | Docker image ID | 评估入口 | 变异体 |
|---|---:|---|---|---:|
| Python | 106 | `{PYTHON_IMAGE}` | `launch.sh python-candidate … --case` | 262 |
| Java | 59 | `{JAVA_IMAGE}` | `launch.sh java-candidate … --case` | 120 |

正式对照执行时，Base 与 Base + navigation-v2 每个 case-cell 必须绑定相同的 `evaluation_case_id`、镜像、runtime lock 和 mutant IDs。候选生成工作区不得挂载 `reference_test_locator` 或 mutant bank。评估器在候选输出冻结后读取这些控制输入。

实际入口为 `{summary['candidate_runner']['launch_path']}`，SHA-256 为 `{summary['candidate_runner']['launch_sha256']}`。Python 的 `--case` 接受普通 case ID，Java 接受 navigation case ID。每个新实验臂还需生成并注册完整 Candidate input manifest，绑定候选字节、benchmark result、input-view manifest 与 source-run metadata；评估适配器随后要求候选 clean baseline 实际通过，再执行冻结变异体。

## 输入对齐

{alignment_note}

{blocker_detail}

## 校验边界

本次执行的是零费用只读身份预检。脚本读取冻结 manifest 与 CSV 身份账本，并对参考测试和选定 CUT 做 SHA-256 校验。没有应用变异体，没有执行旧测试，没有重新扫描依赖树，也没有调用模型。Docker 镜像的本机可用性需由总报告中的环境探针单独记录。

## 产物

- `eval-manifest.csv`：165 行逐例执行清单。
- `alignment-blockers.csv`：未通过输入身份门禁的案例。
- `evaluation-summary.json`：数量、镜像与输入账本 SHA-256。
- 复现命令：`python experiments/backward-slicing/navigation-v2/prepare_issue37_eval_manifest.py --r2-root <navigation-cohort> --generation-inventory <mixed-inventory> --output-root <output>`
"""
    (output_root / "evaluation-report.md").write_text(report, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stub-tester-root", type=Path, default=Path("/home/haon/projects/Stub-Tester")
    )
    parser.add_argument(
        "--r2-root",
        type=Path,
        default=Path("out/mockbench-study/navigation-v2-health165-20261119-r2"),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("out/mockbench-study/navigation-v2-preflight165-issue37"),
    )
    parser.add_argument(
        "--generation-inventory",
        type=Path,
        default=GENERATION_INVENTORY,
    )
    args = parser.parse_args()
    rows, summary = build_manifest(
        args.stub_tester_root,
        args.r2_root,
        args.generation_inventory,
    )
    write_outputs(rows, summary, args.output_root)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
