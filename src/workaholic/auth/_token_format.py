"""Shared canonical raw-Token grammar for secret-bearing auth value objects."""

from __future__ import annotations

import base64
import binascii
import re
from typing import Final

from workaholic.auth.errors import TokenFormatError
from workaholic.domain import DomainValidationError, TokenId

_TOKEN_ENTROPY_BYTES: Final = 32
_TOKEN_SECRET_LENGTH: Final = 43
_TOKEN_COMPONENT_COUNT: Final = 2
_SECRET_PATTERN: Final = re.compile(r"^[A-Za-z0-9_-]{43}$")


def validate_token_text(value: object) -> TokenId:
    """Validate exact Token grammar and return its typed public identity.

    Args:
        value: Candidate raw Token text.

    Returns:
        Parsed typed Token identity.

    Raises:
        TokenFormatError: If any raw component is malformed or noncanonical.

    """
    if not isinstance(value, str) or not value.isascii():
        raise TokenFormatError
    parts = value.split(".")
    if len(parts) != _TOKEN_COMPONENT_COUNT:
        raise TokenFormatError
    token_id_text, encoded_secret = parts
    if _SECRET_PATTERN.fullmatch(encoded_secret) is None:
        raise TokenFormatError
    try:
        token_id = TokenId(token_id_text)
        entropy = base64.b64decode(
            f"{encoded_secret}=",
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, DomainValidationError, ValueError) as error:
        raise TokenFormatError from error
    if (
        len(entropy) != _TOKEN_ENTROPY_BYTES
        or base64.urlsafe_b64encode(entropy).decode("ascii").rstrip("=")
        != encoded_secret
    ):
        raise TokenFormatError
    return token_id
