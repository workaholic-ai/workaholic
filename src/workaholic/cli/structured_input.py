"""Bounded, inert, and duplicate-safe JSON input for CLI commands."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import IO, TYPE_CHECKING, Never, cast

from workaholic.domain import parse_rfc3339_utc_timestamp, validate_json_value

if TYPE_CHECKING:
    from datetime import datetime

STRUCTURED_INPUT_MAX_BYTES = 1_048_576


class StructuredInputError(ValueError):
    """Signal that structured CLI input is unsafe or contract-invalid."""


def load_structured_object(source: str) -> dict[str, object]:
    """Load one bounded closed-candidate JSON object from a file or stdin.

    The loader does not interpret object fields. It establishes the common
    transport safety contract before command-specific Pydantic validation:
    bounded bytes and containers, strict UTF-8 without a BOM, no symlink or
    directory traversal, no duplicate keys, and no non-finite numbers.

    Args:
        source: Filesystem path, or exactly ``-`` for explicit stdin.

    Returns:
        Detached parsed JSON object for command-specific validation.

    Raises:
        StructuredInputError: If the source or JSON document is invalid.

    """
    candidate: object = source
    if not isinstance(candidate, str) or not candidate or "\x00" in candidate:
        raise StructuredInputError
    payload = _read_stdin() if candidate == "-" else _read_regular_file(Path(candidate))
    if not payload or len(payload) > STRUCTURED_INPUT_MAX_BYTES:
        raise StructuredInputError
    if payload.startswith(b"\xef\xbb\xbf"):
        raise StructuredInputError
    try:
        text = payload.decode("utf-8", errors="strict")
        parsed = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_nonfinite,
        )
        validate_json_value(parsed, label="Structured input")
    except StructuredInputError, UnicodeDecodeError, json.JSONDecodeError, ValueError:
        raise StructuredInputError from None
    if not isinstance(parsed, dict):
        raise StructuredInputError
    return parsed


def merge_structured_fields(
    *,
    file_values: dict[str, object],
    inline_values: dict[str, object],
    allowed_fields: frozenset[str],
) -> dict[str, object]:
    """Merge disjoint file and explicit inline fields under one closed schema.

    Args:
        file_values: Parsed file-owned values.
        inline_values: Only options explicitly supplied by the caller.
        allowed_fields: Complete command-specific file field inventory.

    Returns:
        New merged mapping with no shared mutable ownership.

    Raises:
        StructuredInputError: If a file field is unknown or supplied inline.

    """
    if not set(file_values) <= allowed_fields:
        raise StructuredInputError
    if set(file_values) & set(inline_values):
        raise StructuredInputError
    return {**file_values, **inline_values}


def parse_utc_timestamp_field(
    value: object,
    *,
    label: str,
    allow_none: bool,
) -> datetime | None:
    """Parse one CLI-owned RFC 3339 UTC field before strict Session validation.

    Args:
        value: Serialized timestamp, or explicit ``None`` for a clear operation.
        label: Safe field label used only by the domain parser.
        allow_none: Whether the command semantics permit explicit clearing.

    Returns:
        Parsed UTC datetime or the permitted explicit ``None`` value.

    Raises:
        StructuredInputError: If the serialized value violates the CLI contract.

    """
    if value is None:
        if allow_none:
            return None
        raise StructuredInputError
    try:
        return parse_rfc3339_utc_timestamp(value, label=label)
    except ValueError:
        raise StructuredInputError from None


def _read_regular_file(path: Path) -> bytes:
    """Read at most one bounded regular non-symlink file.

    Args:
        path: Caller-selected local input path.

    Returns:
        Raw bounded file bytes.

    Raises:
        StructuredInputError: If the path is unsafe, unreadable, or oversized.

    """
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    before: os.stat_result | None = None
    if nofollow == 0:
        try:
            before = path.lstat()
        except OSError, ValueError:
            raise StructuredInputError from None
        if stat.S_ISLNK(before.st_mode):
            raise StructuredInputError
    try:
        descriptor = os.open(path, flags | nofollow)
    except OSError, ValueError:
        raise StructuredInputError from None
    try:
        current = os.fstat(descriptor)
    except OSError:
        os.close(descriptor)
        raise StructuredInputError from None
    if not stat.S_ISREG(current.st_mode):
        os.close(descriptor)
        raise StructuredInputError
    if before is not None and (
        before.st_dev != current.st_dev or before.st_ino != current.st_ino
    ):
        os.close(descriptor)
        raise StructuredInputError
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            return _read_bounded(stream)
    except OSError, ValueError:
        raise StructuredInputError from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_stdin() -> bytes:
    """Read bounded bytes only after the caller explicitly selected stdin.

    Returns:
        Raw bounded stdin bytes.

    Raises:
        StructuredInputError: If stdin is unavailable or oversized.

    """
    binary_stream: object = getattr(sys.stdin, "buffer", None)
    if binary_stream is not None and callable(getattr(binary_stream, "read", None)):
        reader = cast("IO[bytes]", binary_stream)
        try:
            data = reader.read(STRUCTURED_INPUT_MAX_BYTES + 1)
        except OSError, UnicodeError, ValueError:
            raise StructuredInputError from None
        if not isinstance(data, bytes):
            raise StructuredInputError
    else:
        try:
            text = sys.stdin.read(STRUCTURED_INPUT_MAX_BYTES + 1)
        except OSError, UnicodeError, ValueError:
            raise StructuredInputError from None
        if not isinstance(text, str):
            raise StructuredInputError
        data = text.encode("utf-8")
    if len(data) > STRUCTURED_INPUT_MAX_BYTES:
        raise StructuredInputError
    return data


def _read_bounded(stream: IO[bytes]) -> bytes:
    """Read one byte past the public limit to detect oversized input.

    Args:
        stream: Open binary regular-file stream.

    Returns:
        Raw bytes within the public limit.

    Raises:
        StructuredInputError: If the stream exceeds the byte limit.

    """
    data = stream.read(STRUCTURED_INPUT_MAX_BYTES + 1)
    if len(data) > STRUCTURED_INPUT_MAX_BYTES:
        raise StructuredInputError
    return data


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build one JSON object while refusing duplicate member names.

    Args:
        pairs: Decoder-preserved object members in source order.

    Returns:
        Unique-key object preserving source insertion order.

    Raises:
        StructuredInputError: If one member name occurs more than once.

    """
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise StructuredInputError
        result[key] = value
    return result


def _reject_nonfinite(_value: str) -> Never:
    """Reject JSON constants outside the interoperable number grammar.

    Args:
        _value: Decoder-provided non-finite token.

    Raises:
        StructuredInputError: Always.

    """
    raise StructuredInputError
