"""Strict Pydantic parsing for model-authored structured output."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

from agents import AgentOutputSchema, AgentOutputSchemaBase, ModelBehaviorError
from pydantic import BaseModel, ValidationError

from .validation import InputValidationError, decode_json


ModelT = TypeVar("ModelT", bound=BaseModel)
ResultT = TypeVar("ResultT")

_WHOLE_JSON_FENCE = re.compile(
    r"\A\s*```json[ \t]*\r?\n(?P<body>.*?)(?:\r?\n)?```[ \t]*\s*\Z",
    re.IGNORECASE | re.DOTALL,
)


def strip_json_fence(text: str) -> str:
    """Remove one outer ``json`` fence without extracting embedded JSON."""
    match = _WHOLE_JSON_FENCE.fullmatch(text)
    return match.group("body") if match is not None else text


def parse_pydantic_json(model_type: type[ModelT], text: str) -> ModelT:
    """Parse one complete JSON document and reject ambiguous duplicate fields.

    Pydantic is the authoritative parser. ``decode_json`` is called afterwards
    only as an envelope ambiguity guard; its decoded value is never consumed.
    """
    if not isinstance(text, str):
        raise TypeError("structured output must be text")
    unfenced = strip_json_fence(text)
    parsed = model_type.model_validate_json(unfenced, strict=True)
    decode_json(unfenced)
    return parsed


class StructuredOutputValidationError(ModelBehaviorError):
    """A provider response failed the declared Pydantic output contract."""

    def __init__(self, validation_error: ValidationError):
        self.validation_error = validation_error
        super().__init__("model output failed Pydantic validation")


class StructuredOutputEnvelopeError(ModelBehaviorError):
    """The JSON envelope was ambiguous despite satisfying the model shape."""


class PydanticOutputSchema(AgentOutputSchemaBase, Generic[ModelT]):
    """SDK output schema using ``BaseModel.model_validate_json`` at the boundary."""

    def __init__(self, model_type: type[ModelT]):
        if not isinstance(model_type, type) or not issubclass(model_type, BaseModel):
            raise TypeError("model_type must be a Pydantic BaseModel class")
        self.output_type = model_type
        self._sdk_schema = AgentOutputSchema(model_type, strict_json_schema=True)

    def is_plain_text(self) -> bool:
        return False

    def name(self) -> str:
        return self.output_type.__name__

    def json_schema(self) -> dict:
        return self._sdk_schema.json_schema()

    def is_strict_json_schema(self) -> bool:
        return True

    def validate_json(self, json_str: str) -> ModelT:
        try:
            return parse_pydantic_json(self.output_type, json_str)
        except ValidationError as exc:
            raise StructuredOutputValidationError(exc) from exc
        except InputValidationError as exc:
            raise StructuredOutputEnvelopeError("model output has duplicate JSON fields") from exc


def validation_error_summary(error: StructuredOutputValidationError) -> str:
    """Produce a bounded repair hint without echoing model-authored values."""
    parts: list[str] = []
    for item in error.validation_error.errors(include_input=False, include_url=False)[:8]:
        location = ".".join(str(part)[:64] for part in item["loc"])[:192] or "$"
        parts.append(f"{location}: {item['type']}")
    return "; ".join(parts)[:512]


async def retry_validation_errors(
    operation: Callable[[int], Awaitable[ResultT]],
    *,
    max_retries: int = 1,
) -> ResultT:
    """Retry only output-contract validation failures, with a strict bound."""
    if type(max_retries) is not int or max_retries < 0:
        raise ValueError("max_retries must be a non-negative integer")
    for attempt in range(max_retries + 1):
        try:
            return await operation(attempt)
        except StructuredOutputValidationError as exc:
            validation_error = exc.validation_error
            if any(
                item["type"] in {"value_error", "assertion_error"}
                for item in validation_error.errors(include_input=False)
            ):
                raise
            if attempt >= max_retries:
                raise
    raise AssertionError("validation retry loop terminated unexpectedly")


__all__ = [
    "PydanticOutputSchema",
    "StructuredOutputEnvelopeError",
    "StructuredOutputValidationError",
    "parse_pydantic_json",
    "retry_validation_errors",
    "strip_json_fence",
    "validation_error_summary",
]
