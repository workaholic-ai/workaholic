"""Validated `workaholic.cli/v1` JSON envelopes and value normalization."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Annotated, Final, Literal, TypeGuard, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from workaholic.domain import (
    AttemptId,
    InstanceId,
    ProjectId,
    RequestId,
    ResultId,
    SubjectId,
    TaskEventId,
    TaskId,
)

type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)

CLI_SCHEMA: Final[Literal["workaholic.cli/v1"]] = "workaholic.cli/v1"
_IDENTIFIER_TYPES: Final = (
    AttemptId,
    InstanceId,
    ProjectId,
    ResultId,
    SubjectId,
    TaskId,
    TaskEventId,
    RequestId,
)
_STRING_BOUNDARY_TYPES: Final = (*_IDENTIFIER_TYPES, Path)
_ERROR_CODE = Annotated[
    str,
    Field(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Z][A-Z0-9_]*$",
    ),
]
_ERROR_MESSAGE = Annotated[str, Field(min_length=1, max_length=500)]


class _EnvelopeModel(BaseModel):
    """Shared strictness policy for public CLI envelope models."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )


class JsonSuccess(_EnvelopeModel):
    """One successful non-streaming CLI response envelope."""

    schema_: Literal["workaholic.cli/v1"] = Field(
        default=CLI_SCHEMA,
        alias="schema",
    )
    ok: Literal[True] = True
    data: JsonValue

    @field_validator("data", mode="before")
    @classmethod
    def _normalize_data(cls, value: object) -> JsonValue:
        """Normalize one supported result into the strict JSON data model.

        Args:
            value: Candidate command-specific result.

        Returns:
            Recursively validated JSON value.

        """
        return normalize_json_value(value)


class JsonErrorDetail(_EnvelopeModel):
    """Stable machine-readable and Human-readable CLI failure detail."""

    code: _ERROR_CODE
    message: _ERROR_MESSAGE
    retryable: bool

    @field_validator("message", mode="before")
    @classmethod
    def _validate_message(cls, value: object) -> str:
        """Require one trimmed printable public message.

        Args:
            value: Candidate safe error message.

        Returns:
            Validated public diagnostic.

        Raises:
            ValueError: If the message is unsafe or malformed.

        """
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or any(not character.isprintable() for character in value)
        ):
            message = "CLI error message must be nonempty, trimmed, and printable."
            raise ValueError(message)
        return value


class JsonError(_EnvelopeModel):
    """One failed non-streaming CLI response envelope."""

    schema_: Literal["workaholic.cli/v1"] = Field(
        default=CLI_SCHEMA,
        alias="schema",
    )
    ok: Literal[False] = False
    error: JsonErrorDetail


def normalize_json_value(value: object) -> JsonValue:
    """Normalize supported boundary values into interoperable JSON.

    Pydantic models and standard dataclasses are traversed explicitly before
    primitive validation so no custom string representation can leak into the
    public automation contract.

    Args:
        value: Candidate result value.

    Returns:
        Recursively validated JSON value.

    Raises:
        TypeError: If a value or mapping key has an unsupported type.
        ValueError: If a value is non-finite, non-UTC, or malformed.

    """
    if value is None or type(value) in (bool, int, float, str):
        return _normalize_json_scalar(value)
    return _normalize_json_composite(value)


def _normalize_json_scalar(value: object) -> None | bool | int | float | str:
    """Normalize one candidate primitive JSON scalar.

    Args:
        value: Candidate known to have a supported scalar runtime type.

    Returns:
        Validated JSON scalar.

    Raises:
        ValueError: If a floating-point value is non-finite.

    """
    if type(value) is float and not math.isfinite(value):
        message = "CLI JSON numbers must be finite."
        raise ValueError(message)
    return cast("None | bool | int | float | str", value)


def _normalize_json_composite(value: object) -> JsonValue:
    """Normalize one non-primitive supported boundary value.

    Args:
        value: Candidate datetime, model, dataclass, mapping, or sequence.

    Returns:
        Recursively validated JSON value.

    Raises:
        TypeError: If the value has an unsupported type.
        ValueError: If any nested value is malformed.

    """
    if isinstance(value, datetime):
        normalized: JsonValue = _serialize_utc_datetime(value)
    elif isinstance(value, _STRING_BOUNDARY_TYPES):
        normalized = str(value)
    elif isinstance(value, Enum):
        normalized = normalize_json_value(value.value)
    elif isinstance(value, BaseModel):
        normalized = normalize_json_value(
            value.model_dump(
                mode="python",
                by_alias=True,
                exclude_none=False,
            )
        )
    elif is_dataclass(value) and not isinstance(value, type):
        normalized = {
            field.name: normalize_json_value(getattr(value, field.name))
            for field in fields(value)
        }
    elif isinstance(value, Mapping):
        normalized_mapping: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                message = "CLI JSON object keys must be strings."
                raise TypeError(message)
            normalized_mapping[key] = normalize_json_value(item)
        normalized = normalized_mapping
    elif isinstance(value, list | tuple):
        normalized = [normalize_json_value(item) for item in value]
    else:
        message = f"Unsupported CLI JSON value type: {type(value).__name__}."
        raise TypeError(message)
    return normalized


def is_json_value(value: object) -> TypeGuard[JsonValue]:
    """Return whether a value is already in the strict JSON data model.

    Args:
        value: Candidate normalized value.

    Returns:
        Whether every nested value is valid interoperable JSON.

    """
    if value is None or type(value) in (bool, int, str):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if isinstance(value, list):
        return all(is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and is_json_value(item) for key, item in value.items()
        )
    return False


def _serialize_utc_datetime(value: datetime) -> str:
    """Serialize one timezone-aware UTC datetime with an explicit ``Z``.

    Args:
        value: Candidate timestamp.

    Returns:
        RFC 3339 UTC text.

    Raises:
        ValueError: If the timestamp is naive or has a nonzero UTC offset.

    """
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        message = "CLI JSON timestamps must be timezone-aware UTC datetimes."
        raise ValueError(message)
    return value.isoformat().replace("+00:00", "Z")
