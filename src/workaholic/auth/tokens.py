"""Canonical generation, parsing, hashing, and verification of bearer Tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from typing import TYPE_CHECKING, Final

from workaholic.auth._token_format import validate_token_text
from workaholic.auth.errors import TokenFormatError, TokenGenerationError
from workaholic.auth.models import ParsedToken, RawToken
from workaholic.domain import TokenId

if TYPE_CHECKING:
    from collections.abc import Callable

_TOKEN_ENTROPY_BYTES: Final = 32
_TOKEN_SECRET_LENGTH: Final = 43
_DIGEST_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")


def generate_token(
    token_id: TokenId,
    *,
    random_bytes: Callable[[int], bytes] = secrets.token_bytes,
) -> RawToken:
    """Generate one canonical raw Token from 256 bits of entropy.

    Args:
        token_id: Preallocated non-secret Token identity.
        random_bytes: Injectable cryptographic byte generator.

    Returns:
        Opaque canonical raw Token.

    Raises:
        TokenGenerationError: If inputs or entropy violate the contract.

    """
    candidate_token_id: object = token_id
    candidate_random_bytes: object = random_bytes
    if not isinstance(candidate_token_id, TokenId) or not callable(
        candidate_random_bytes
    ):
        raise TokenGenerationError
    try:
        entropy = random_bytes(_TOKEN_ENTROPY_BYTES)
    except Exception as error:
        raise TokenGenerationError from error
    candidate_entropy: object = entropy
    if (
        not isinstance(candidate_entropy, bytes)
        or len(candidate_entropy) != _TOKEN_ENTROPY_BYTES
    ):
        raise TokenGenerationError
    encoded = base64.urlsafe_b64encode(entropy).decode("ascii").rstrip("=")
    if len(encoded) != _TOKEN_SECRET_LENGTH:
        raise TokenGenerationError
    return RawToken(f"{token_id}.{encoded}")


def parse_token(value: str | RawToken) -> ParsedToken:
    """Parse canonical raw Token text without normalizing caller input.

    Args:
        value: Raw string from a trusted credential source or existing wrapper.

    Returns:
        Parsed non-secret Token ID and opaque credential.

    Raises:
        TokenFormatError: If the raw Token is malformed or noncanonical.

    """
    raw_token = value if isinstance(value, RawToken) else RawToken(value)
    token_id = validate_token_text(raw_token.get_secret_value())
    return ParsedToken(token_id=token_id, raw_token=raw_token)


def hash_token(raw_token: RawToken) -> str:
    """Hash the complete canonical raw Token with SHA-256.

    Args:
        raw_token: Validated opaque raw Token.

    Returns:
        Lowercase SHA-256 hexadecimal digest.

    Raises:
        TokenFormatError: If the runtime input is not a ``RawToken``.

    """
    if not isinstance(raw_token, RawToken):
        raise TokenFormatError
    return hashlib.sha256(raw_token.get_secret_value().encode("ascii")).hexdigest()


def verify_token_digest(raw_token: RawToken, expected_digest: str) -> bool:
    """Compare a raw Token with one canonical digest in constant time.

    Args:
        raw_token: Validated opaque raw Token.
        expected_digest: Expected lowercase SHA-256 hexadecimal digest.

    Returns:
        Whether the complete canonical raw Token matches the digest.

    Raises:
        TokenFormatError: If either runtime input violates its contract.

    """
    candidate_digest: object = expected_digest
    if (
        not isinstance(candidate_digest, str)
        or _DIGEST_PATTERN.fullmatch(candidate_digest) is None
    ):
        raise TokenFormatError
    return hmac.compare_digest(hash_token(raw_token), expected_digest)
