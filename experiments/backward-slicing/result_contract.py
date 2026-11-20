"""Compatibility import for the promoted backward-analysis result contract."""

from mock_spec.backward_result import (
    BINDING_ORIGINS,
    BINDING_STATUSES,
    RELATION_KINDS,
    RELATION_MODALITIES,
    RESULT_SCHEMA_VERSION,
    VALIDATION_STATUSES,
    ResultContractError,
    attach_result_contract,
    build_result_contract,
    contract_digest,
    validate_result_contract,
)

__all__ = [
    "BINDING_ORIGINS",
    "BINDING_STATUSES",
    "RELATION_KINDS",
    "RELATION_MODALITIES",
    "RESULT_SCHEMA_VERSION",
    "VALIDATION_STATUSES",
    "ResultContractError",
    "attach_result_contract",
    "build_result_contract",
    "contract_digest",
    "validate_result_contract",
]
