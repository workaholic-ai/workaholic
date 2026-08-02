"""Unit tests for bounded CLI structured-input transport."""

from __future__ import annotations

import io
import json
import sys
from typing import TYPE_CHECKING

import pytest

from workaholic.cli.structured_input import (
    STRUCTURED_INPUT_MAX_BYTES,
    StructuredInputError,
    load_structured_object,
    merge_structured_fields,
    parse_utc_timestamp_field,
)
from workaholic.domain import (
    JSON_MAX_ARRAY_ITEMS,
    JSON_MAX_DEPTH,
    JSON_MAX_OBJECT_ITEMS,
    JSON_MAX_STRING_LENGTH,
)

if TYPE_CHECKING:
    from pathlib import Path


class _TextOnlyInput:
    """Minimal text-only stdin used to exercise the portable fallback."""

    def __init__(self, value: object) -> None:
        """Store one candidate read result."""
        self._value = value

    def read(self, _size: int) -> object:
        """Return the configured candidate value."""
        return self._value


class _FailingInput:
    """Minimal stdin whose read operation fails safely."""

    def read(self, _size: int) -> str:
        """Raise one private operating-system read failure."""
        private_diagnostic = "private stdin diagnostic"
        raise OSError(private_diagnostic)


class _InputWithBuffer:
    """Minimal stdin exposing one configurable binary-like buffer."""

    def __init__(self, buffer: object) -> None:
        """Store the candidate buffer object."""
        self.buffer = buffer


class _CandidateBuffer:
    """Minimal buffer returning one runtime candidate value."""

    def __init__(self, value: object) -> None:
        """Store one candidate read result."""
        self._value = value

    def read(self, _size: int) -> object:
        """Return the configured candidate value."""
        return self._value


class _FailingBuffer:
    """Minimal binary-like buffer whose read operation fails."""

    def read(self, _size: int) -> bytes:
        """Raise one private operating-system read failure."""
        private_diagnostic = "private binary stdin diagnostic"
        raise OSError(private_diagnostic)


def test_load_structured_object_accepts_one_strict_regular_json_file(
    tmp_path: Path,
) -> None:
    """A bounded UTF-8 object is returned without schema interpretation."""
    source = tmp_path / "task.json"
    source.write_text(
        '{"priority":0,"acceptance":[{"id":"done","required":true}]}',
        encoding="utf-8",
    )

    assert load_structured_object(str(source)) == {
        "priority": 0,
        "acceptance": [{"id": "done", "required": True}],
    }


def test_load_structured_object_reads_stdin_only_for_explicit_dash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reserved dash source performs the sole implicit stream read."""
    stream = io.TextIOWrapper(io.BytesIO(b'{"context":[]}'), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", stream)

    assert load_structured_object("-") == {"context": []}


def test_load_structured_object_rejects_oversized_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stdin path uses the same one-megabyte transport limit as files."""
    payload = b" " * (STRUCTURED_INPUT_MAX_BYTES + 1)
    stream = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", stream)

    with pytest.raises(StructuredInputError):
        load_structured_object("-")


def test_load_structured_object_supports_text_only_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A platform without ``stdin.buffer`` still receives strict JSON input."""
    monkeypatch.setattr(sys, "stdin", _TextOnlyInput('{"priority":10}'))

    assert load_structured_object("-") == {"priority": 10}


@pytest.mark.parametrize(
    "stream",
    [
        _TextOnlyInput(1),
        _FailingInput(),
        _InputWithBuffer(_CandidateBuffer("not bytes")),
        _InputWithBuffer(_FailingBuffer()),
    ],
)
def test_load_structured_object_rejects_invalid_stdin_runtime_behaviour(
    monkeypatch: pytest.MonkeyPatch,
    stream: object,
) -> None:
    """Unexpected stream values and failures never escape the safe boundary."""
    monkeypatch.setattr(sys, "stdin", stream)

    with pytest.raises(StructuredInputError):
        load_structured_object("-")


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"[]",
        b"null",
        b'{"a":1}{"b":2}',
        b'{"a":1,"a":2}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b"\xef\xbb\xbf{}",
        b'{"value":"\xff"}',
    ],
)
def test_load_structured_object_rejects_ambiguous_or_nonportable_json(
    tmp_path: Path,
    payload: bytes,
) -> None:
    """Input must be one duplicate-free interoperable UTF-8 object."""
    source = tmp_path / "invalid.json"
    source.write_bytes(payload)

    with pytest.raises(StructuredInputError):
        load_structured_object(str(source))


@pytest.mark.parametrize(
    "payload",
    [
        b" " * (STRUCTURED_INPUT_MAX_BYTES + 1),
        json.dumps({"value": "x" * (JSON_MAX_STRING_LENGTH + 1)}).encode(),
        json.dumps(
            {str(index): index for index in range(JSON_MAX_OBJECT_ITEMS + 1)}
        ).encode(),
        json.dumps({"items": list(range(JSON_MAX_ARRAY_ITEMS + 1))}).encode(),
    ],
)
def test_load_structured_object_enforces_byte_and_collection_bounds(
    tmp_path: Path,
    payload: bytes,
) -> None:
    """Large transport and recursive values fail before command validation."""
    source = tmp_path / "oversized.json"
    source.write_bytes(payload)

    with pytest.raises(StructuredInputError):
        load_structured_object(str(source))


def test_load_structured_object_enforces_nesting_bound(tmp_path: Path) -> None:
    """Objects nested beyond the shared domain limit are rejected."""
    value: object = "leaf"
    for _index in range(JSON_MAX_DEPTH + 1):
        value = {"nested": value}
    source = tmp_path / "deep.json"
    source.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(StructuredInputError):
        load_structured_object(str(source))


def test_load_structured_object_rejects_missing_directory_and_symlink(
    tmp_path: Path,
) -> None:
    """Only the requested regular non-symlink file can supply input."""
    regular = tmp_path / "regular.json"
    regular.write_text("{}", encoding="utf-8")
    symlink = tmp_path / "linked.json"
    symlink.symlink_to(regular)

    for source in (tmp_path / "missing.json", tmp_path, symlink):
        with pytest.raises(StructuredInputError):
            load_structured_object(str(source))


def test_load_structured_object_redacts_regular_file_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operating-system open failures become one inert structured-input error."""
    source = tmp_path / "unreadable.json"
    source.write_text("{}", encoding="utf-8")

    def fail_open(*_arguments: object, **_keywords: object) -> int:
        """Simulate an unreadable caller-owned path."""
        private_diagnostic = "private filesystem diagnostic"
        raise OSError(private_diagnostic)

    monkeypatch.setattr("workaholic.cli.structured_input.os.open", fail_open)

    with pytest.raises(StructuredInputError) as captured:
        load_structured_object(str(source))
    assert "private filesystem diagnostic" not in str(captured.value)


def test_load_structured_object_uses_lstat_when_nofollow_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback rejects symlinks before opening on older platforms."""
    regular = tmp_path / "regular.json"
    regular.write_text("{}", encoding="utf-8")
    symlink = tmp_path / "linked.json"
    symlink.symlink_to(regular)
    monkeypatch.setattr("workaholic.cli.structured_input.os.O_NOFOLLOW", 0)

    assert load_structured_object(str(regular)) == {}
    with pytest.raises(StructuredInputError):
        load_structured_object(str(symlink))


def test_load_structured_object_closes_descriptor_when_fstat_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A metadata race failure closes the already-open descriptor."""
    source = tmp_path / "task.json"
    source.write_text("{}", encoding="utf-8")

    def fail_fstat(_descriptor: int) -> object:
        """Simulate an operating-system descriptor metadata failure."""
        private_diagnostic = "private fstat diagnostic"
        raise OSError(private_diagnostic)

    monkeypatch.setattr("workaholic.cli.structured_input.os.fstat", fail_fstat)

    with pytest.raises(StructuredInputError):
        load_structured_object(str(source))


def test_load_structured_object_closes_descriptor_when_fdopen_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stream-wrapper failure closes the raw descriptor exactly once."""
    source = tmp_path / "task.json"
    source.write_text("{}", encoding="utf-8")

    def fail_fdopen(*_arguments: object, **_keywords: object) -> object:
        """Simulate an operating-system stream-wrapper failure."""
        private_diagnostic = "private fdopen diagnostic"
        raise OSError(private_diagnostic)

    monkeypatch.setattr("workaholic.cli.structured_input.os.fdopen", fail_fdopen)

    with pytest.raises(StructuredInputError):
        load_structured_object(str(source))


@pytest.mark.parametrize("source", ["", "bad\x00path"])
def test_load_structured_object_rejects_invalid_source(source: str) -> None:
    """Empty and NUL-containing paths never reach filesystem APIs."""
    with pytest.raises(StructuredInputError):
        load_structured_object(source)


def test_merge_structured_fields_accepts_only_disjoint_closed_fields() -> None:
    """Command-owned allowlists and explicit option precedence stay unambiguous."""
    file_values: dict[str, object] = {"acceptance": []}
    inline_values: dict[str, object] = {"priority": 10}

    merged = merge_structured_fields(
        file_values=file_values,
        inline_values=inline_values,
        allowed_fields=frozenset(("acceptance", "priority")),
    )

    assert merged == {"acceptance": [], "priority": 10}
    assert merged is not file_values
    assert merged is not inline_values


@pytest.mark.parametrize(
    ("file_values", "inline_values"),
    [
        ({"unknown": True}, {}),
        ({"priority": 20}, {"priority": 10}),
    ],
)
def test_merge_structured_fields_rejects_unknown_or_overlapping_ownership(
    file_values: dict[str, object],
    inline_values: dict[str, object],
) -> None:
    """Unknown fields and file/scalar collisions cannot be silently resolved."""
    with pytest.raises(StructuredInputError):
        merge_structured_fields(
            file_values=file_values,
            inline_values=inline_values,
            allowed_fields=frozenset(("priority",)),
        )


def test_parse_utc_timestamp_field_accepts_timestamp_and_explicit_clear() -> None:
    """The CLI adapter converts serialized timestamps and preserves allowed null."""
    parsed = parse_utc_timestamp_field(
        "2026-08-01T10:30:00Z",
        label="available_at",
        allow_none=False,
    )

    assert parsed is not None
    assert parsed.isoformat() == "2026-08-01T10:30:00+00:00"
    assert (
        parse_utc_timestamp_field(
            None,
            label="available_at",
            allow_none=True,
        )
        is None
    )


@pytest.mark.parametrize("value", [None, "2026-08-01T10:30:00+00:00", 1])
def test_parse_utc_timestamp_field_rejects_invalid_serialized_value(
    value: object,
) -> None:
    """Creation nulls, offsets, and non-strings fail as structured input."""
    with pytest.raises(StructuredInputError):
        parse_utc_timestamp_field(
            value,
            label="available_at",
            allow_none=False,
        )
