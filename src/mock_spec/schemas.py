"""Strict, language-neutral schemas for reviewed MockBench task inputs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
import re
from typing import Any, ClassVar, Mapping


class SchemaValidationError(ValueError):
    """Raised when task data violates the public input contract."""


def _require_mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise SchemaValidationError(f"{path} must be an object")
    if any(type(key) is not str for key in value):
        raise SchemaValidationError(f"{path} field names must be strings")
    return value


def _require_fields(
    data: Mapping[str, Any], expected: frozenset[str], path: str
) -> None:
    unknown = sorted(set(data) - expected)
    missing = sorted(expected - set(data))
    if unknown:
        raise SchemaValidationError(f"{path} has unknown fields: {', '.join(unknown)}")
    if missing:
        raise SchemaValidationError(f"{path} is missing fields: {', '.join(missing)}")


def _require_string(value: object, path: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if type(value) is not str:
        raise SchemaValidationError(f"{path} must be a string")
    if not value.strip():
        raise SchemaValidationError(f"{path} must not be empty")
    return value


def _require_int(value: object, path: str) -> int:
    if type(value) is not int:
        raise SchemaValidationError(f"{path} must be an integer")
    return value


def _require_choice(value: object, choices: frozenset[str], path: str) -> str:
    parsed = _require_string(value, path)
    assert parsed is not None
    if parsed not in choices:
        expected = ", ".join(sorted(choices))
        raise SchemaValidationError(f"{path} must be one of: {expected}")
    return parsed


def _require_list(value: object, path: str) -> list[Any]:
    if type(value) is not list:
        raise SchemaValidationError(f"{path} must be an array")
    return value


_SOURCE_ID_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]*\Z")


def _validate_source_id(value: object, path: str) -> str:
    parsed = _require_string(value, path)
    assert parsed is not None
    if _SOURCE_ID_PATTERN.fullmatch(parsed) is None:
        raise SchemaValidationError(
            f"{path} must match [A-Za-z][A-Za-z0-9_.:-]*"
        )
    return parsed


def _validate_logical_path(value: object, path: str) -> str:
    parsed = _require_string(value, path)
    assert parsed is not None
    if "\x00" in parsed:
        raise SchemaValidationError(f"{path} must not contain NUL")
    if "\\" in parsed:
        raise SchemaValidationError(f"{path} must use POSIX separators")
    if re.match(r"^[A-Za-z]:", parsed):
        raise SchemaValidationError(f"{path} must not use a Windows drive prefix")
    candidate = PurePosixPath(parsed)
    if parsed == "." or candidate.is_absolute() or candidate.as_posix() != parsed:
        raise SchemaValidationError(f"{path} must be a normalized relative POSIX path")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise SchemaValidationError(f"{path} must not contain traversal components")
    return parsed


def _validate_line_range(start_line: int, end_line: int, path: str) -> None:
    if start_line < 1:
        raise SchemaValidationError(f"{path}.start_line must be positive")
    if end_line < start_line:
        raise SchemaValidationError(f"{path}.end_line must be >= start_line")


@dataclass(frozen=True)
class Source:
    source_id: str
    logical_path: str
    text: str
    symbol: str | None
    start_line: int
    end_line: int
    resolution_status: str
    target_precision: str

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "source_id",
            "logical_path",
            "text",
            "symbol",
            "start_line",
            "end_line",
            "resolution_status",
            "target_precision",
        }
    )
    RESOLUTION_STATUSES: ClassVar[frozenset[str]] = frozenset(
        {"resolved", "partial", "unresolved"}
    )
    TARGET_PRECISIONS: ClassVar[frozenset[str]] = frozenset(
        {"method", "function", "class", "file"}
    )

    def __post_init__(self) -> None:
        _validate_source_id(self.source_id, "Source.source_id")
        _validate_logical_path(self.logical_path, "Source.logical_path")
        text = _require_string(self.text, "Source.text")
        _require_string(self.symbol, "Source.symbol", nullable=True)
        start = _require_int(self.start_line, "Source.start_line")
        end = _require_int(self.end_line, "Source.end_line")
        _validate_line_range(start, end, "Source")
        assert text is not None
        if len(text.splitlines()) != end - start + 1:
            raise SchemaValidationError(
                "Source line range must match the number of lines in Source.text"
            )
        _require_choice(
            self.resolution_status,
            self.RESOLUTION_STATUSES,
            "Source.resolution_status",
        )
        _require_choice(
            self.target_precision, self.TARGET_PRECISIONS, "Source.target_precision"
        )

    @classmethod
    def from_dict(cls, value: object) -> Source:
        data = _require_mapping(value, "Source")
        _require_fields(data, cls.FIELDS, "Source")
        return cls(
            source_id=_validate_source_id(data["source_id"], "Source.source_id"),
            logical_path=_validate_logical_path(
                data["logical_path"], "Source.logical_path"
            ),
            text=_require_string(data["text"], "Source.text"),
            symbol=_require_string(data["symbol"], "Source.symbol", nullable=True),
            start_line=_require_int(data["start_line"], "Source.start_line"),
            end_line=_require_int(data["end_line"], "Source.end_line"),
            resolution_status=_require_choice(
                data["resolution_status"],
                cls.RESOLUTION_STATUSES,
                "Source.resolution_status",
            ),
            target_precision=_require_choice(
                data["target_precision"],
                cls.TARGET_PRECISIONS,
                "Source.target_precision",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Interface:
    symbol: str
    signature: str
    source_id: str
    start_line: int
    end_line: int
    resolution_status: str

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"symbol", "signature", "source_id", "start_line", "end_line", "resolution_status"}
    )

    def __post_init__(self) -> None:
        _require_string(self.symbol, "Interface.symbol")
        _require_string(self.signature, "Interface.signature")
        _validate_source_id(self.source_id, "Interface.source_id")
        start = _require_int(self.start_line, "Interface.start_line")
        end = _require_int(self.end_line, "Interface.end_line")
        _validate_line_range(start, end, "Interface")
        _require_choice(
            self.resolution_status,
            Source.RESOLUTION_STATUSES,
            "Interface.resolution_status",
        )

    @classmethod
    def from_dict(cls, value: object) -> Interface:
        data = _require_mapping(value, "Interface")
        _require_fields(data, cls.FIELDS, "Interface")
        return cls(
            symbol=_require_string(data["symbol"], "Interface.symbol"),
            signature=_require_string(data["signature"], "Interface.signature"),
            source_id=_validate_source_id(
                data["source_id"], "Interface.source_id"
            ),
            start_line=_require_int(data["start_line"], "Interface.start_line"),
            end_line=_require_int(data["end_line"], "Interface.end_line"),
            resolution_status=_require_choice(
                data["resolution_status"],
                Source.RESOLUTION_STATUSES,
                "Interface.resolution_status",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionContext:
    runtime_name: str
    runtime_version: str | None
    test_framework: str
    test_framework_version: str | None

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"runtime_name", "runtime_version", "test_framework", "test_framework_version"}
    )

    def __post_init__(self) -> None:
        _require_string(self.runtime_name, "ExecutionContext.runtime_name")
        _require_string(
            self.runtime_version, "ExecutionContext.runtime_version", nullable=True
        )
        _require_string(self.test_framework, "ExecutionContext.test_framework")
        _require_string(
            self.test_framework_version,
            "ExecutionContext.test_framework_version",
            nullable=True,
        )

    @classmethod
    def from_dict(cls, value: object) -> ExecutionContext:
        data = _require_mapping(value, "ExecutionContext")
        _require_fields(data, cls.FIELDS, "ExecutionContext")
        return cls(
            runtime_name=_require_string(
                data["runtime_name"], "ExecutionContext.runtime_name"
            ),
            runtime_version=_require_string(
                data["runtime_version"],
                "ExecutionContext.runtime_version",
                nullable=True,
            ),
            test_framework=_require_string(
                data["test_framework"], "ExecutionContext.test_framework"
            ),
            test_framework_version=_require_string(
                data["test_framework_version"],
                "ExecutionContext.test_framework_version",
                nullable=True,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InputPolicy:
    policy_id: str
    protocol_version: str
    masking_contract: str
    visibility: str

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"policy_id", "protocol_version", "masking_contract", "visibility"}
    )
    VISIBILITY: ClassVar[str] = "reviewed_frozen_sources"

    def __post_init__(self) -> None:
        _require_string(self.policy_id, "InputPolicy.policy_id")
        _require_string(self.protocol_version, "InputPolicy.protocol_version")
        _require_string(self.masking_contract, "InputPolicy.masking_contract")
        visibility = _require_string(self.visibility, "InputPolicy.visibility")
        if visibility != self.VISIBILITY:
            raise SchemaValidationError(
                f"InputPolicy.visibility must be {self.VISIBILITY!r}"
            )

    @classmethod
    def from_dict(cls, value: object) -> InputPolicy:
        data = _require_mapping(value, "InputPolicy")
        _require_fields(data, cls.FIELDS, "InputPolicy")
        visibility = _require_string(data["visibility"], "InputPolicy.visibility")
        if visibility != cls.VISIBILITY:
            raise SchemaValidationError(
                f"InputPolicy.visibility must be {cls.VISIBILITY!r}"
            )
        return cls(
            policy_id=_require_string(data["policy_id"], "InputPolicy.policy_id"),
            protocol_version=_require_string(
                data["protocol_version"], "InputPolicy.protocol_version"
            ),
            masking_contract=_require_string(
                data["masking_contract"], "InputPolicy.masking_contract"
            ),
            visibility=visibility,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceIndex:
    source_id: str
    logical_path: str
    role: str

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"source_id", "logical_path", "role"}
    )
    ROLES: ClassVar[frozenset[str]] = frozenset(
        {"mut", "masked_test", "supporting"}
    )

    def __post_init__(self) -> None:
        _validate_source_id(self.source_id, "SourceIndex.source_id")
        _validate_logical_path(self.logical_path, "SourceIndex.logical_path")
        _require_choice(self.role, self.ROLES, "SourceIndex.role")

    @classmethod
    def from_dict(cls, value: object) -> SourceIndex:
        data = _require_mapping(value, "SourceIndex")
        _require_fields(data, cls.FIELDS, "SourceIndex")
        return cls(
            source_id=_validate_source_id(
                data["source_id"], "SourceIndex.source_id"
            ),
            logical_path=_validate_logical_path(
                data["logical_path"], "SourceIndex.logical_path"
            ),
            role=_require_choice(data["role"], cls.ROLES, "SourceIndex.role"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UnifiedTask:
    schema_version: str
    language: str
    case_id: str
    mut_source: Source
    masked_test: Source
    permitted_interfaces: tuple[Interface, ...]
    supporting_sources: tuple[Source, ...]
    execution_context: ExecutionContext
    input_policy: InputPolicy
    sources: tuple[SourceIndex, ...]

    SCHEMA_VERSION: ClassVar[str] = "mock-spec.task/v1"
    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "schema_version",
            "language",
            "case_id",
            "mut_source",
            "masked_test",
            "permitted_interfaces",
            "supporting_sources",
            "execution_context",
            "input_policy",
            "sources",
        }
    )
    LANGUAGES: ClassVar[frozenset[str]] = frozenset({"java", "python"})

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise SchemaValidationError(
                f"UnifiedTask.schema_version must be {self.SCHEMA_VERSION!r}"
            )
        _require_choice(self.language, self.LANGUAGES, "UnifiedTask.language")
        _require_string(self.case_id, "UnifiedTask.case_id")
        if not isinstance(self.mut_source, Source):
            raise SchemaValidationError("UnifiedTask.mut_source must be a Source")
        if not isinstance(self.masked_test, Source):
            raise SchemaValidationError("UnifiedTask.masked_test must be a Source")
        if type(self.permitted_interfaces) is not tuple or any(
            not isinstance(item, Interface) for item in self.permitted_interfaces
        ):
            raise SchemaValidationError(
                "UnifiedTask.permitted_interfaces must be a tuple of Interface values"
            )
        if type(self.supporting_sources) is not tuple or any(
            not isinstance(item, Source) for item in self.supporting_sources
        ):
            raise SchemaValidationError(
                "UnifiedTask.supporting_sources must be a tuple of Source values"
            )
        if not isinstance(self.execution_context, ExecutionContext):
            raise SchemaValidationError(
                "UnifiedTask.execution_context must be an ExecutionContext"
            )
        if not isinstance(self.input_policy, InputPolicy):
            raise SchemaValidationError("UnifiedTask.input_policy must be an InputPolicy")
        if type(self.sources) is not tuple or any(
            not isinstance(item, SourceIndex) for item in self.sources
        ):
            raise SchemaValidationError(
                "UnifiedTask.sources must be a tuple of SourceIndex values"
            )
        self._validate_references()

    def _validate_references(self) -> None:
        expected: list[tuple[Source, str]] = [
            (self.mut_source, "mut"),
            (self.masked_test, "masked_test"),
            *((source, "supporting") for source in self.supporting_sources),
        ]
        source_ids = [source.source_id for source, _ in expected]
        if len(source_ids) != len(set(source_ids)):
            raise SchemaValidationError("UnifiedTask source IDs must be unique")

        indexes_by_id: dict[str, SourceIndex] = {}
        for index in self.sources:
            if index.source_id in indexes_by_id:
                raise SchemaValidationError("UnifiedTask source index IDs must be unique")
            indexes_by_id[index.source_id] = index
        if set(indexes_by_id) != set(source_ids):
            raise SchemaValidationError(
                "UnifiedTask.sources must index every task source exactly once"
            )

        sources_by_id: dict[str, Source] = {}
        for source, role in expected:
            sources_by_id[source.source_id] = source
            index = indexes_by_id[source.source_id]
            if index.logical_path != source.logical_path or index.role != role:
                raise SchemaValidationError(
                    f"source index for {source.source_id!r} does not match its source"
                )

        for interface in self.permitted_interfaces:
            source = sources_by_id.get(interface.source_id)
            if source is None:
                raise SchemaValidationError(
                    f"interface {interface.symbol!r} cites an unknown source ID"
                )
            if not (
                source.start_line <= interface.start_line <= interface.end_line <= source.end_line
            ):
                raise SchemaValidationError(
                    f"interface {interface.symbol!r} cites lines outside its source"
                )

    @classmethod
    def from_dict(cls, value: object) -> UnifiedTask:
        data = _require_mapping(value, "UnifiedTask")
        _require_fields(data, cls.FIELDS, "UnifiedTask")
        interfaces = _require_list(
            data["permitted_interfaces"], "UnifiedTask.permitted_interfaces"
        )
        supporting = _require_list(
            data["supporting_sources"], "UnifiedTask.supporting_sources"
        )
        indexes = _require_list(data["sources"], "UnifiedTask.sources")
        schema_version = _require_string(
            data["schema_version"], "UnifiedTask.schema_version"
        )
        if schema_version != cls.SCHEMA_VERSION:
            raise SchemaValidationError(
                f"UnifiedTask.schema_version must be {cls.SCHEMA_VERSION!r}"
            )
        return cls(
            schema_version=schema_version,
            language=_require_choice(
                data["language"], cls.LANGUAGES, "UnifiedTask.language"
            ),
            case_id=_require_string(data["case_id"], "UnifiedTask.case_id"),
            mut_source=Source.from_dict(data["mut_source"]),
            masked_test=Source.from_dict(data["masked_test"]),
            permitted_interfaces=tuple(
                Interface.from_dict(item) for item in interfaces
            ),
            supporting_sources=tuple(Source.from_dict(item) for item in supporting),
            execution_context=ExecutionContext.from_dict(data["execution_context"]),
            input_policy=InputPolicy.from_dict(data["input_policy"]),
            sources=tuple(SourceIndex.from_dict(item) for item in indexes),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "language": self.language,
            "case_id": self.case_id,
            "mut_source": self.mut_source.to_dict(),
            "masked_test": self.masked_test.to_dict(),
            "permitted_interfaces": [
                interface.to_dict() for interface in self.permitted_interfaces
            ],
            "supporting_sources": [
                source.to_dict() for source in self.supporting_sources
            ],
            "execution_context": self.execution_context.to_dict(),
            "input_policy": self.input_policy.to_dict(),
            "sources": [index.to_dict() for index in self.sources],
        }


__all__ = [
    "ExecutionContext",
    "InputPolicy",
    "Interface",
    "SchemaValidationError",
    "Source",
    "SourceIndex",
    "UnifiedTask",
]
