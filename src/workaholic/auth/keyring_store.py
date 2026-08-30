"""Operating-system keyring adapter for profile-scoped Human credentials."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

import keyring
from keyring.errors import KeyringError

from workaholic.application import CredentialUnavailableError, InvalidInputError
from workaholic.auth.credentials import (
    HumanCredential,
    parse_credential_json,
    serialize_credential_json,
    validate_credential_profile,
)

if TYPE_CHECKING:
    from keyring.backend import KeyringBackend

_SERVICE_NAME = "workaholic-ai"
_USERNAME_PREFIX = "profile:"


@runtime_checkable
class KeyringProvider(Protocol):
    """Small testable subset of the Python keyring backend contract."""

    @property
    def priority(self) -> float:
        """Return positive backend priority when an implementation exists."""
        ...

    def get_password(self, service: str, username: str) -> str | None:
        """Return one protected value if present."""
        ...

    def set_password(self, service: str, username: str, password: str) -> None:
        """Create or replace one protected value."""
        ...

    def delete_password(self, service: str, username: str) -> None:
        """Delete one protected value."""
        ...


class KeyringCredentialStore:
    """CredentialStore backed by one selected operating-system keyring."""

    def __init__(self, provider: KeyringProvider) -> None:
        """Bind one provider without performing a keyring operation.

        Args:
            provider: Selected keyring backend.

        Raises:
            InvalidInputError: If the provider violates the runtime protocol.

        """
        if not isinstance(provider, KeyringProvider):
            raise InvalidInputError
        self._provider = provider

    @classmethod
    def system(cls) -> KeyringCredentialStore:
        """Construct a store from the process-selected system backend.

        Returns:
            Keyring credential adapter, which may report unavailable.

        Raises:
            CredentialUnavailableError: If backend discovery fails operationally.

        """
        try:
            provider: KeyringBackend = keyring.get_keyring()
        except KeyringError as error:
            raise CredentialUnavailableError from error
        return cls(provider)

    def is_available(self) -> bool:
        """Return whether a positive-priority system backend exists.

        Returns:
            ``True`` only for an installed non-failing backend.

        Raises:
            CredentialUnavailableError: If availability inspection fails.

        """
        try:
            priority: object = self._provider.priority
        except KeyringError as error:
            raise CredentialUnavailableError from error
        if not isinstance(priority, int | float):
            raise CredentialUnavailableError
        return priority > 0

    def load(self, profile: str) -> HumanCredential | None:
        """Load and validate one profile credential from the keyring.

        Args:
            profile: Trusted selected profile name.

        Returns:
            Stored credential or ``None``.

        Raises:
            CredentialUnavailableError: If the keyring or value is unusable.

        """
        name = validate_credential_profile(profile)
        try:
            value = self._provider.get_password(
                _SERVICE_NAME,
                _username(name),
            )
        except KeyringError as error:
            raise CredentialUnavailableError from error
        if value is None:
            return None
        credential = parse_credential_json(value)
        if credential.profile != name:
            raise CredentialUnavailableError
        return credential

    def replace(self, credential: HumanCredential) -> None:
        """Create or replace one protected profile credential.

        Args:
            credential: Validated credential and expected identity metadata.

        Raises:
            CredentialUnavailableError: If the keyring write fails.
            InvalidInputError: If the credential is malformed.

        """
        if not isinstance(credential, HumanCredential):
            raise InvalidInputError
        serialized = serialize_credential_json(credential)
        try:
            self._provider.set_password(
                _SERVICE_NAME,
                _username(credential.profile),
                serialized,
            )
        except KeyringError as error:
            raise CredentialUnavailableError from error

    def delete(self, profile: str) -> None:
        """Delete one profile entry and tolerate a missing credential.

        Args:
            profile: Trusted selected profile name.

        Raises:
            CredentialUnavailableError: If a present credential cannot be deleted.

        """
        name = validate_credential_profile(profile)
        if self.load(name) is None:
            return
        try:
            self._provider.delete_password(_SERVICE_NAME, _username(name))
        except KeyringError as error:
            raise CredentialUnavailableError from error


def _username(profile: str) -> str:
    """Build a non-secret stable account key for one trusted profile.

    Args:
        profile: Validated profile name.

    Returns:
        Keyring username scoped by Workaholic profile.

    """
    return f"{_USERNAME_PREFIX}{profile}"
