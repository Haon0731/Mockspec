"""Load the six frozen #35 contracts and full-cohort Agent source inputs."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tarfile
from typing import Any, BinaryIO

from mock_spec.adapters.frozen_dataset import FrozenDataset
import result_contract


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parent.parent.parent
MANIFEST = HERE / "frozen-sources.json"
DEFAULT_REGISTRY_ROOT = Path(
    "/home/haon/projects/Stub-Tester/.omx/developer-realistic-registry-max"
)


class FrozenInputError(ValueError):
    """The registered frozen input is absent or differs from its receipt."""


class _MultipartReader(io.RawIOBase):
    def __init__(self, paths: list[Path]) -> None:
        self._paths = iter(paths)
        self._current: BinaryIO | None = None

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray) -> int:
        view = memoryview(buffer)
        total = 0
        while total < len(view):
            if self._current is None:
                try:
                    self._current = next(self._paths).open("rb")
                except StopIteration:
                    break
            count = self._current.readinto(view[total:])
            if count:
                total += count
                continue
            self._current.close()
            self._current = None
        return total

    def close(self) -> None:
        if self._current is not None:
            self._current.close()
        super().close()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _archive_member(manifest: dict[str, Any], member_path: str) -> bytes:
    archive = manifest["archive"]
    directory = REPOSITORY / archive["directory"]
    parts = [directory / name for name in archive["parts"]]
    missing = [str(path) for path in parts if not path.is_file()]
    if missing:
        raise FrozenInputError(f"missing archive parts: {missing}")
    with _MultipartReader(parts) as raw:
        with tarfile.open(fileobj=io.BufferedReader(raw), mode="r|gz") as bundle:
            for member in bundle:
                if member.name.lstrip("./") != member_path:
                    continue
                extracted = bundle.extractfile(member)
                if extracted is None:
                    break
                return extracted.read()
    raise FrozenInputError(f"archive member is missing: {member_path}")


def _analysis_bytes(manifest: dict[str, Any], registration: dict[str, Any]) -> bytes:
    analysis = registration["analysis"]
    if analysis["storage"] == "file":
        path = REPOSITORY / analysis["path"]
        if not path.is_file():
            raise FrozenInputError(f"analysis artifact is missing: {analysis['path']}")
        return path.read_bytes()
    if analysis["storage"] == "multipart_tar_gz":
        return _archive_member(manifest, analysis["path"])
    raise FrozenInputError(f"unsupported analysis storage: {analysis['storage']}")


def _visible_sources(
    registration: dict[str, Any], contract: dict[str, Any], registry_root: Path,
) -> dict[str, dict[str, Any]]:
    if registration["visible_sources"]["status"] != "frozen_agent_cohort":
        raise FrozenInputError("unsupported visible source admission policy")
    dataset = FrozenDataset(
        registry_root, dataset=registration["dataset"],
        inventory_revision=registration["inventory_revision"],
    )
    scope = {source["source_id"]: source for source in contract["source_scope"]}
    result: dict[str, dict[str, Any]] = {}
    with dataset.load_case(registration["case_id"]) as case:
        identity = contract["identity"]
        if (
            case.snapshot_id != identity["snapshot_id"]
            or case.target_selector != identity["target_selector"]
            or case.language != identity["language"]
        ):
            raise FrozenInputError("cohort Adapter identity differs from frozen analysis")
        for source_id, expected in scope.items():
            source = (
                case.masked_test if source_id == "test"
                else case.read_source(expected["logical_path"])
            )
            text = source.text
            if source.logical_path != expected["logical_path"]:
                raise FrozenInputError(f"visible source path differs for {source_id}")
            if _digest(text.encode("utf-8")) != expected["sha256"]:
                raise FrozenInputError(f"visible source digest differs for {source_id}")
            result[source_id] = {
                "source_id": source_id,
                "logical_path": source.logical_path,
                "start_line": source.start_line,
                "end_line": source.end_line,
                "text": text,
            }
    return result


def load_case(
    case_key: str, registry_root: Path = DEFAULT_REGISTRY_ROOT,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Return one validated frozen contract and its admitted source-id mapping.

    The full-cohort Adapter authenticates every source read against the frozen
    snapshot index and tree manifest.  The returned mapping covers exactly the
    source scope recorded by the analysis contract.
    """

    manifest = _manifest()
    try:
        registration = manifest["cases"][case_key]
    except KeyError as error:
        raise FrozenInputError(f"unknown #35 case key: {case_key}") from error
    analysis = registration["analysis"]
    data = _analysis_bytes(manifest, registration)
    if _digest(data) != analysis["sha256"]:
        raise FrozenInputError(f"analysis artifact digest differs for {case_key}")
    payload = json.loads(data)
    field = analysis["contract_field"]
    contract = payload if field is None else payload.get(field)
    if not isinstance(contract, dict):
        raise FrozenInputError(f"analysis artifact lacks {field!r} for {case_key}")
    if contract.get("identity", {}).get("case_id") != registration["case_id"]:
        raise FrozenInputError(f"analysis artifact has the wrong case_id for {case_key}")
    result_contract.validate_result_contract(contract)
    return contract, _visible_sources(registration, contract, Path(registry_root))


def load_prompt_sources(
    case_key: str, registry_root: Path = DEFAULT_REGISTRY_ROOT,
) -> dict[str, dict[str, Any]]:
    """Return the masked test and uniquely bound MUT, when one is established."""

    task_sources = load_task_sources(case_key, registry_root)
    test = task_sources["masked_test"]
    selected = {test["source_id"]: test}
    mut = task_sources["mut_source"]
    if mut is not None:
        selected[mut["source_id"]] = mut
    return selected


def load_task_sources(
    case_key: str, registry_root: Path = DEFAULT_REGISTRY_ROOT,
) -> dict[str, dict[str, Any] | None]:
    """Return separate masked-test and uniquely justified MUT source records.

    ``mut_source`` is ``None`` when the frozen contract records an unresolved
    binding.  Callers can use the audited source tool for further exploration
    without treating a source-selection candidate as the actual MUT.
    """

    contract, sources = load_case(case_key, registry_root)
    mut_binding = contract["entry_point"]["mut_binding"]
    mut_path = None
    if mut_binding["status"] == "unique":
        candidate_paths = {
            candidate.get("logical_path")
            for candidate in mut_binding["candidates"]
            if isinstance(candidate, dict) and candidate.get("logical_path")
        }
        if len(candidate_paths) == 1:
            mut_path = candidate_paths.pop()
        else:
            product_sources = [
                source for source in contract["source_scope"]
                if source["selection_role"] in {"candidate_product", "visible_product"}
            ]
            if len(product_sources) == 1:
                mut_path = product_sources[0]["logical_path"]
    path_index = {source["logical_path"]: source for source in sources.values()}
    if mut_path is not None and mut_path not in path_index:
        raise FrozenInputError(f"bound MUT is outside the frozen source scope: {mut_path}")
    return {"masked_test": sources["test"], "mut_source": path_index.get(mut_path)}


def case_keys() -> tuple[str, ...]:
    return tuple(_manifest()["cases"])
