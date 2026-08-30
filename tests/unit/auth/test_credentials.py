"""Unit tests for Human credential models and backend selection."""

from __future__ import annotations

import base64
from typing import cast

import pytest

from workaholic.application import CredentialUnavailableError, InvalidInputError
from workaholic.auth import (
    CredentialBackend,
    CredentialStore,
    HumanCredential,
    RawToken,
    select_credential_store,
)
from workaholic.auth.credentials import (
    credential_from_mapping,
    credential_to_mapping,
    parse_credential_json,
    serialize_credential_json,
    validate_credential_profile,
)
from workaholic.domain import InstanceId, SubjectId


def _credential(profile: str = "local") -> HumanCredential:
    """Build one deterministic Human credential.

    Args:
        profile: Trusted profile name.

    Returns:
        Validated credential fixture.

    """
    encoded = base64.urlsafe_b64encode(bytes(32)).decode().rstrip("=")
    return HumanCredential(
        profile=profile,
        instance_id=InstanceId(f"ins_{profile}"),
        subject_id=SubjectId(f"sub_{profile}"),
        raw_token=RawToken(f"tok_{profile}.{encoded}"),
    )


class _MemoryStore:
    """Small protocol-conforming credential store for selector tests."""

    def __init__(self, *, available: bool = True) -> None:
        """Initialize empty state and availability.

        Args:
            available: Backend-presence response.

        """
        self.available = available
        self.values: dict[str, HumanCredential] = {}

    def is_available(self) -> bool:
        """Return configured backend availability."""
        return self.available

    def load(self, profile: str) -> HumanCredential | None:
        """Load one in-memory credential."""
        return self.values.get(profile)

    def replace(self, credential: HumanCredential) -> None:
        """Replace one in-memory credential."""
        self.values[credential.profile] = credential

    def delete(self, profile: str) -> None:
        """Delete one in-memory credential if present."""
        self.values.pop(profile, None)


def test_human_credential_is_profile_scoped_and_secret_safe() -> None:
    """Expected identities remain visible while the raw Token stays redacted."""
    credential = _credential()
    raw_text = credential.raw_token.get_secret_value()

    assert credential.profile == "local"
    assert str(credential.instance_id) == "ins_local"
    assert str(credential.subject_id) == "sub_local"
    assert raw_text not in repr(credential)


def test_human_credential_runtime_validation_is_strict() -> None:
    """Profile, identity, and Token fields do not trust annotations."""
    credential = _credential()
    with pytest.raises(InvalidInputError):
        HumanCredential(
            profile="invalid profile",
            instance_id=credential.instance_id,
            subject_id=credential.subject_id,
            raw_token=credential.raw_token,
        )
    with pytest.raises(InvalidInputError):
        HumanCredential(
            profile="local",
            instance_id=cast("InstanceId", "ins_local"),
            subject_id=credential.subject_id,
            raw_token=credential.raw_token,
        )


@pytest.mark.parametrize(
    ("backend", "available", "expected"),
    [
        (CredentialBackend.AUTO, True, "keyring"),
        (CredentialBackend.AUTO, False, "file"),
        (CredentialBackend.KEYRING, True, "keyring"),
        (CredentialBackend.FILE, True, "file"),
        (CredentialBackend.FILE, False, "file"),
    ],
)
def test_store_selection_falls_back_only_before_auto_selection(
    backend: CredentialBackend,
    available: bool,  # noqa: FBT001 - parametrized contract dimension.
    expected: str,
) -> None:
    """Auto checks presence once while explicit file never probes keyring."""
    keyring_store = _MemoryStore(available=available)
    file_store = _MemoryStore()

    selected = select_credential_store(
        backend,
        keyring_store=keyring_store,
        file_store=file_store,
    )

    assert selected is (keyring_store if expected == "keyring" else file_store)


def test_explicit_missing_keyring_fails_without_file_downgrade() -> None:
    """Explicit keyring selection never falls back to a protected file."""
    with pytest.raises(CredentialUnavailableError):
        select_credential_store(
            CredentialBackend.KEYRING,
            keyring_store=_MemoryStore(available=False),
            file_store=_MemoryStore(),
        )


def test_selector_runtime_checks_store_protocols() -> None:
    """The selector rejects objects missing required CredentialStore methods."""
    store = _MemoryStore()
    assert isinstance(store, CredentialStore)
    with pytest.raises(InvalidInputError):
        select_credential_store(
            cast("CredentialBackend", "auto"),
            keyring_store=store,
            file_store=store,
        )
    with pytest.raises(InvalidInputError):
        select_credential_store(
            CredentialBackend.AUTO,
            keyring_store=cast("_MemoryStore", object()),
            file_store=store,
        )
    with pytest.raises(InvalidInputError):
        select_credential_store(
            CredentialBackend.AUTO,
            keyring_store=store,
            file_store=cast("CredentialStore", object()),
        )


def test_credential_serialization_round_trip_has_a_closed_schema() -> None:
    """Protected serialization round-trips only the exact credential fields."""
    credential = _credential()

    serialized = serialize_credential_json(credential)

    assert parse_credential_json(serialized) == credential
    assert credential_from_mapping(credential_to_mapping(credential)) == credential


@pytest.mark.parametrize(
    "operation",
    [
        lambda: serialize_credential_json(cast("HumanCredential", object())),
        lambda: credential_to_mapping(cast("HumanCredential", object())),
        lambda: validate_credential_profile("invalid profile"),
    ],
)
def test_credential_helpers_reject_invalid_runtime_input(operation: object) -> None:
    """Secret-bearing helper boundaries do not trust caller annotations."""
    assert callable(operation)
    with pytest.raises(InvalidInputError):
        operation()


@pytest.mark.parametrize(
    "value",
    [
        None,
        "not-json",
        "[]",
        '{"profile":"local"}',
        '{"instance_id":"ins_local","profile":"invalid profile",'
        '"subject_id":"sub_local","token":"invalid"}',
    ],
)
def test_credential_parser_rejects_malformed_protected_values(value: object) -> None:
    """Malformed, open, and invalid credential records fail closed."""
    with pytest.raises(CredentialUnavailableError):
        parse_credential_json(value)
