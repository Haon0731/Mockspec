"""Strict parsing and bounded repair for model-authored JSON."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from mock_spec.structured_output import (
    PydanticOutputSchema,
    StructuredOutputEnvelopeError,
    StructuredOutputValidationError,
    parse_pydantic_json,
    retry_validation_errors,
    validation_error_summary,
)


class Example(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str
    count: int


class SemanticExample(Example):
    @model_validator(mode="after")
    def check_semantics(self):
        if self.count == 0:
            raise ValueError("zero has domain meaning and is not format damage")
        return self


def test_parser_accepts_one_whole_json_fence() -> None:
    parsed = parse_pydantic_json(
        Example, '  ```json\n{"name":"item","count":2}\n```  '
    )
    assert parsed == Example(name="item", count=2)


@pytest.mark.parametrize(
    "text",
    [
        'prefix ```json\n{"name":"item","count":2}\n```',
        '```\n{"name":"item","count":2}\n```',
        '{"name":"item","count":"2"}',
    ],
)
def test_parser_does_not_extract_or_coerce_invalid_output(text: str) -> None:
    with pytest.raises(ValidationError):
        parse_pydantic_json(Example, text)


def test_output_schema_rejects_duplicate_fields_after_pydantic_validation() -> None:
    schema = PydanticOutputSchema(Example)
    with pytest.raises(StructuredOutputEnvelopeError):
        schema.validate_json('{"name":"first","name":"second","count":2}')


def test_output_schema_exposes_native_strict_schema() -> None:
    schema = PydanticOutputSchema(Example)
    assert schema.is_strict_json_schema() is True
    assert schema.json_schema()["additionalProperties"] is False
    assert schema.validate_json('{"name":"item","count":2}').count == 2


def test_validation_retry_is_bounded_and_does_not_retry_other_errors() -> None:
    calls: list[int] = []

    async def operation(attempt: int) -> Example:
        calls.append(attempt)
        if attempt == 0:
            PydanticOutputSchema(Example).validate_json('{"name":"item"}')
        return Example(name="item", count=2)

    assert asyncio.run(retry_validation_errors(operation, max_retries=1)).count == 2
    assert calls == [0, 1]

    async def unrelated(_attempt: int) -> Example:
        raise RuntimeError("transport failed")

    with pytest.raises(RuntimeError, match="transport failed"):
        asyncio.run(retry_validation_errors(unrelated, max_retries=1))

    validation_calls: list[int] = []

    async def unrelated_validation(attempt: int) -> Example:
        validation_calls.append(attempt)
        return Example.model_validate_json('{"name":"item"}')

    with pytest.raises(ValidationError):
        asyncio.run(retry_validation_errors(unrelated_validation, max_retries=1))
    assert validation_calls == [0]


def test_validation_retry_does_not_repair_domain_validator_failures() -> None:
    calls: list[int] = []

    async def invalid(attempt: int) -> SemanticExample:
        calls.append(attempt)
        PydanticOutputSchema(SemanticExample).validate_json(
            '{"name":"item","count":0}'
        )
        raise AssertionError("unreachable")

    with pytest.raises(StructuredOutputValidationError):
        asyncio.run(retry_validation_errors(invalid, max_retries=1))
    assert calls == [0]


def test_validation_retry_raises_after_limit() -> None:
    calls: list[int] = []

    async def invalid(attempt: int) -> Example:
        calls.append(attempt)
        PydanticOutputSchema(Example).validate_json('{"name":"item"}')
        raise AssertionError("unreachable")

    with pytest.raises(StructuredOutputValidationError):
        asyncio.run(retry_validation_errors(invalid, max_retries=1))
    assert calls == [0, 1]


def test_validation_summary_never_echoes_values_and_is_bounded() -> None:
    schema = PydanticOutputSchema(Example)
    attacker_value = "secret-model-output"
    attacker_field = "x" * 1_000
    with pytest.raises(StructuredOutputValidationError) as caught:
        schema.validate_json(
            '{"name":"' + attacker_value + '","count":"bad","' + attacker_field + '":1}'
        )
    summary = validation_error_summary(caught.value)
    assert attacker_value not in summary
    assert len(summary) <= 512
    assert "string_type" not in summary
    assert "int_type" in summary
