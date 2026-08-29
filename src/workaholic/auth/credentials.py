"""Adapter-neutral Human credential contracts and backend selection."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol, cast, runtime_checkable

from workaholic.application import CredentialUnavailableError, InvalidInputError
from workaholic.auth.models import CredentialBackend, RawToken
from workaholic.domain import (
    DomainValidationError,
    InstanceId,
    SubjectId,
    validate_profile_name,
)

_CREDENTIAL_KEYS = frozenset(("instance_id", "profile", "subject_id", "token"))


@dataclass(frozen=True, slots=True)
class HumanCredential:
    """One profile-scoped Human credential with expected durable identity."""

    profile: str
    instance_id: InstanceId
    subject_id: SubjectId
    raw_token: RawToken = field(repr=False)

    def __post_init__(self) -> None:
        """Validate profile, identity, and opaque secret runtime fields."""
        try:
            profile = validate_profile_name(self.profile)
        except ValueError as error:
            raise InvalidInputError from error
        candidate_instance: object = self.instance_id
        candidate_subject: object = self.subject_id
        candidate_token: object = self.raw_token
        if (
            not isinstance(candidate_instance, InstanceId)
            or not isinstance(candidate_subject, SubjectId)
            or not isinstance(candidate_token, RawToken)
        ):
            raise InvalidInputError
        object.__setattr__(self, "profile", profile)

    def __repr__(self) -> str:
        """Return identity metadata with a redacted credential marker."""
        return (
            "HumanCredential("
            f"profile={self.profile!r}, instance_id={self.instance_id!r}, "
            f"subject_id={self.subject_id!r}, raw_token=<redacted>)"
        )


@runtime_checkable
class CredentialStore(Protocol):
    """Profile-scoped replace/load/delete contract for Human credentials."""

    def load(self, profile: str) -> HumanCredential | None:
        """Load one profile credential if present.

        Args:
            profile: Trusted selected profile name.

        Returns:
            Stored credential or ``None``.

        """
        ...

    def replace(self, credential: HumanCredential) -> None:
        """Create or replace one profile credential.

        Args:
            credential: Validated credential and expected identity metadata.

        """
        ...

    def delete(self, profile: str) -> None:
        """Delete one profile credential if present.

        Args:
            profile: Trusted selected profile name.

        """
        ...


@runtime_checkable
class AvailableCredentialStore(CredentialStore, Protocol):
    """Credential store that can report backend presence before selection."""

    def is_available(self) -> bool:
        """Return whether this backend exists for the current account."""
        ...


def select_credential_store(
    backend: CredentialBackend,
    *,
    keyring_store: AvailableCredentialStore,
    file_store: CredentialStore,
) -> CredentialStore:
    """Select one backend without downgrading after an operation begins.

    Args:
        backend: Trusted exact backend choice.
        keyring_store: Operating-system keyring adapter.
        file_store: Protected-file fallback adapter.

    Returns:
        Exactly one authoritative credential store.

    Raises:
        CredentialUnavailableError: If explicit keyring selection is unavailable.
        InvalidInputError: If runtime inputs violate their protocols.

    """
    candidate_backend: object = backend
    if not isinstance(candidate_backend, CredentialBackend):
        raise InvalidInputError
    if not isinstance(keyring_store, AvailableCredentialStore):
        raise InvalidInputError
    if not isinstance(file_store, CredentialStore):
        raise InvalidInputError
    if candidate_backend is CredentialBackend.FILE:
        return file_store
    available = keyring_store.is_available()
    if available:
        return keyring_store
    if candidate_backend is CredentialBackend.KEYRING:
        raise CredentialUnavailableError
    return file_store


def serialize_credential_json(credential: HumanCredential) -> str:
    """Serialize one credential for an already-protected secret backend.

    Args:
        credential: Validated Human credential.

    Returns:
        Canonical compact JSON including the raw Token.

    Raises:
        InvalidInputError: If the runtime input is not a Human credential.

    """
    if not isinstance(credential, HumanCredential):
        raise InvalidInputError
    return json.dumps(
        credential_to_mapping(credential),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def parse_credential_json(value: object) -> HumanCredential:
    """Parse one closed protected-backend credential JSON value.

    Args:
        value: Candidate serialized credential.

    Returns:
        Validated Human credential.

    Raises:
        CredentialUnavailableError: If persisted protected state is malformed.

    """
    if not isinstance(value, str):
        raise CredentialUnavailableError
    try:
        decoded: object = json.loads(value)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise CredentialUnavailableError from error
    return credential_from_mapping(decoded)


def credential_to_mapping(credential: HumanCredential) -> dict[str, str]:
    """Convert one credential to a closed secret-bearing storage mapping.

    Args:
        credential: Validated Human credential.

    Returns:
        New mutable mapping for protected storage only.

    """
    if not isinstance(credential, HumanCredential):
        raise InvalidInputError
    return {
        "profile": credential.profile,
        "instance_id": str(credential.instance_id),
        "subject_id": str(credential.subject_id),
        "token": credential.raw_token.get_secret_value(),
    }


def credential_from_mapping(value: object) -> HumanCredential:
    """Construct one credential from exact protected storage fields.

    Args:
        value: Candidate decoded mapping.

    Returns:
        Validated Human credential.

    Raises:
        CredentialUnavailableError: If protected state is malformed.

    """
    if not isinstance(value, dict) or set(value) != _CREDENTIAL_KEYS:
        raise CredentialUnavailableError
    try:
        profile = validate_profile_name(value["profile"])
        instance_id = InstanceId(cast("str", value["instance_id"]))
        subject_id = SubjectId(cast("str", value["subject_id"]))
        raw_token = RawToken(cast("str", value["token"]))
    except (DomainValidationError, TypeError, ValueError) as error:
        raise CredentialUnavailableError from error
    return HumanCredential(
        profile=profile,
        instance_id=instance_id,
        subject_id=subject_id,
        raw_token=raw_token,
    )


def validate_credential_profile(value: object) -> str:
    """Validate a profile operand and map it to the public input error.

    Args:
        value: Candidate profile name.

    Returns:
        Canonical profile name.

    Raises:
        InvalidInputError: If the operand is malformed.

    """
    try:
        return validate_profile_name(value)
    except ValueError as error:
        raise InvalidInputError from error
