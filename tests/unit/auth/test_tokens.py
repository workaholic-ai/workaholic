"""Unit tests for canonical bearer Token primitives."""

from __future__ import annotations

import base64
import hashlib

import pytest

from workaholic.auth import (
    RawToken,
    TokenFormatError,
    TokenGenerationError,
    generate_token,
    hash_token,
    parse_token,
    verify_token_digest,
)
from workaholic.domain import TokenId


class _EntropySource:
    """Deterministic entropy source recording the requested byte count."""

    def __init__(self, value: object) -> None:
        """Store one value returned from every call.

        Args:
            value: Candidate entropy source result.

        """
        self.value = value
        self.requested: list[int] = []

    def __call__(self, count: int) -> bytes:
        """Return the configured value and record the request.

        Args:
            count: Requested entropy byte count.

        Returns:
            Configured value cast to the callable protocol for hostile tests.

        """
        self.requested.append(count)
        return self.value  # type: ignore[return-value]


def _canonical_text(public_id: str = "tok_example") -> str:
    """Build deterministic canonical Token text.

    Args:
        public_id: Public Token identity prefix component.

    Returns:
        Canonical raw Token using 32 zero bytes.

    """
    secret = base64.urlsafe_b64encode(bytes(32)).decode("ascii").rstrip("=")
    return f"{public_id}.{secret}"


def test_generate_token_requests_exact_entropy_and_uses_canonical_base64() -> None:
    """Generation uses exactly 256 bits and never emits base64 padding."""
    entropy = bytes(range(32))
    source = _EntropySource(entropy)

    raw_token = generate_token(
        TokenId("tok_generated"),
        random_bytes=source,
    )

    encoded = base64.urlsafe_b64encode(entropy).decode("ascii").rstrip("=")
    assert source.requested == [32]
    assert raw_token.get_secret_value() == f"tok_generated.{encoded}"
    assert "=" not in raw_token.get_secret_value()
    assert parse_token(raw_token).token_id == TokenId("tok_generated")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "tok_example",
        "tok_example.",
        "." + "A" * 43,
        "bad_example." + "A" * 43,
        "tok_example." + "A" * 42,
        "tok_example." + "A" * 44,
        "tok_example." + "A" * 42 + "=",
        "tok_example." + "A" * 42 + "+",
        "tok_example." + "A" * 43 + ".extra",
        " tok_example." + "A" * 43,
        "tok_example." + "A" * 42 + "\n",
        "tok_example." + "é" * 43,
    ],
)
def test_parse_token_rejects_malformed_or_noncanonical_input(value: str) -> None:
    """Parsing rejects separator, identifier, alphabet, length, and text errors."""
    with pytest.raises(TokenFormatError) as captured:
        parse_token(value)

    if value:
        assert value not in str(captured.value)


@pytest.mark.parametrize(
    "source_value",
    [b"", b"x" * 31, b"x" * 33, bytearray(32), "x" * 32],
)
def test_generate_token_rejects_invalid_entropy_source_results(
    source_value: object,
) -> None:
    """Injected generators must return exactly 32 immutable bytes."""
    source = _EntropySource(source_value)
    with pytest.raises(TokenGenerationError):
        generate_token(TokenId("tok_invalid"), random_bytes=source)


def test_generate_token_maps_entropy_failures_without_secret_details() -> None:
    """Entropy-source exceptions are chained behind one safe public message."""

    def fail(_count: int) -> bytes:
        """Raise a private entropy-provider failure.

        Args:
            _count: Requested byte count.

        Raises:
            RuntimeError: Always.

        """
        message = "private provider state"
        raise RuntimeError(message)

    with pytest.raises(TokenGenerationError) as captured:
        generate_token(TokenId("tok_invalid"), random_bytes=fail)

    assert "private provider state" not in str(captured.value)


def test_hash_and_verify_cover_the_complete_canonical_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Digesting includes Token ID and verification uses constant-time compare."""
    raw_token = RawToken(_canonical_text())
    expected = hashlib.sha256(raw_token.get_secret_value().encode("ascii")).hexdigest()
    calls: list[tuple[str, str]] = []

    def compare(left: str, right: str) -> bool:
        """Record the constant-time comparison arguments.

        Args:
            left: Computed digest.
            right: Persisted expected digest.

        Returns:
            Whether the values match.

        """
        calls.append((left, right))
        return left == right

    monkeypatch.setattr("workaholic.auth.tokens.hmac.compare_digest", compare)

    assert hash_token(raw_token) == expected
    assert verify_token_digest(raw_token, expected)
    assert calls == [(expected, expected)]
    changed_id = RawToken(_canonical_text("tok_other"))
    assert hash_token(changed_id) != expected


@pytest.mark.parametrize("digest", ["", "a" * 63, "A" * 64, "g" * 64])
def test_verify_rejects_noncanonical_digest(digest: str) -> None:
    """Verification does not normalize malformed persisted digests."""
    with pytest.raises(TokenFormatError):
        verify_token_digest(RawToken(_canonical_text()), digest)


def test_runtime_type_checks_do_not_trust_annotations() -> None:
    """Every public primitive rejects untyped boundary values at runtime."""
    with pytest.raises(TokenGenerationError):
        generate_token("tok_wrong")  # type: ignore[arg-type]
    with pytest.raises(TokenFormatError):
        parse_token(7)  # type: ignore[arg-type]
    with pytest.raises(TokenFormatError):
        hash_token(_canonical_text())  # type: ignore[arg-type]
