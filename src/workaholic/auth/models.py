"""Secret-safe value objects owned by the authentication boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from workaholic.auth._token_format import validate_token_text
from workaholic.auth.errors import TokenFormatError
from workaholic.domain import TokenId


class CredentialBackend(StrEnum):
    """Trusted Human credential-store backend selection."""

    AUTO = "auto"
    KEYRING = "keyring"
    FILE = "file"


class RawToken:
    """Opaque canonical bearer credential with redacted presentation.

    The stored string is deliberately available only through
    :meth:`get_secret_value`, making every escape from this boundary explicit.
    Construction validates canonical form through the shared parser injected by
    :mod:`workaholic.auth.tokens`.
    """

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        """Store one parser-validated raw Token.

        Args:
            value: Canonical raw Token text.

        Raises:
            TokenFormatError: If the value is not canonical.

        """
        validate_token_text(value)
        self.__value = value

    def get_secret_value(self) -> str:
        """Return the exact credential to a trusted authentication adapter.

        Returns:
            The original canonical string without copying it.

        """
        return self.__value

    def __repr__(self) -> str:
        """Return a representation that cannot disclose the credential."""
        return "RawToken(<redacted>)"

    def __str__(self) -> str:
        """Return a redacted string for logging and common formatting."""
        return "<redacted>"

    def __format__(self, format_spec: str) -> str:
        """Keep formatted output redacted for every format specification.

        Args:
            format_spec: Caller-supplied formatting specification.

        Returns:
            A redacted marker.

        Raises:
            ValueError: If a nonempty format specification is supplied.

        """
        if format_spec:
            message = "RawToken does not support format specifications."
            raise ValueError(message)
        return str(self)


@dataclass(frozen=True, slots=True)
class ParsedToken:
    """Canonical Token identity paired with its opaque raw credential."""

    token_id: TokenId
    raw_token: RawToken = field(repr=False)

    def __post_init__(self) -> None:
        """Validate runtime field types at the authentication boundary."""
        candidate_token_id: object = self.token_id
        candidate_raw_token: object = self.raw_token
        if not isinstance(candidate_token_id, TokenId) or not isinstance(
            candidate_raw_token,
            RawToken,
        ):
            raise TokenFormatError

    def __repr__(self) -> str:
        """Return metadata without rendering the raw credential."""
        return f"ParsedToken(token_id={self.token_id!r}, raw_token=<redacted>)"
