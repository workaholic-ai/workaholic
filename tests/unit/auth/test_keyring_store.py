"""Unit tests for the operating-system keyring credential adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from keyring.errors import KeyringError

from tests.unit.auth.test_credentials import _credential
from workaholic.application import CredentialUnavailableError, InvalidInputError
from workaholic.auth import HumanCredential, KeyringCredentialStore
from workaholic.auth.credentials import serialize_credential_json

if TYPE_CHECKING:
    from workaholic.auth.keyring_store import KeyringProvider


class _FakeKeyring:
    """Deterministic keyring backend with injectable operation failures."""

    def __init__(self, *, priority: float = 1) -> None:
        """Initialize empty protected state.

        Args:
            priority: Backend availability priority.

        """
        self.priority = priority
        self.values: dict[tuple[str, str], str] = {}
        self.failure: str | None = None

    def get_password(self, service: str, username: str) -> str | None:
        """Return one stored value or fail as configured."""
        self._raise_if("get")
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        """Store one exact value or fail as configured."""
        self._raise_if("set")
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        """Delete one exact value or fail as configured."""
        self._raise_if("delete")
        self.values.pop((service, username), None)

    def _raise_if(self, operation: str) -> None:
        """Raise a keyring error for one configured operation."""
        if self.failure == operation:
            message = "private keyring failure"
            raise KeyringError(message)


def test_keyring_profile_isolation_replacement_and_delete() -> None:
    """Each trusted profile owns one independently replaceable keyring value."""
    provider = _FakeKeyring()
    store = KeyringCredentialStore(provider)
    local = _credential("local")
    staging = _credential("staging")

    assert store.is_available()
    assert store.load("local") is None
    store.replace(local)
    store.replace(staging)
    assert store.load("local") == local
    assert store.load("staging") == staging

    replacement = _credential("local")
    store.replace(replacement)
    assert store.load("local") == replacement
    store.delete("local")
    store.delete("local")
    assert store.load("local") is None
    assert store.load("staging") == staging


def test_zero_priority_backend_is_unavailable() -> None:
    """The standard fail backend is recognized before any credential operation."""
    assert not KeyringCredentialStore(_FakeKeyring(priority=0)).is_available()


@pytest.mark.parametrize("operation", ["get", "set", "delete"])
def test_keyring_operation_errors_never_downgrade_or_leak(
    operation: str,
) -> None:
    """A selected keyring maps operational failures to one safe error."""
    provider = _FakeKeyring()
    store = KeyringCredentialStore(provider)
    credential = _credential()
    if operation == "delete":
        store.replace(credential)
    provider.failure = operation

    def execute_operation() -> HumanCredential | None:
        """Execute the selected failing keyring operation."""
        if operation == "get":
            return store.load("local")
        if operation == "set":
            store.replace(credential)
        else:
            store.delete("local")
        return None

    with pytest.raises(CredentialUnavailableError) as captured:
        execute_operation()

    assert "private keyring failure" not in captured.value.safe_message
    assert credential.raw_token.get_secret_value() not in repr(captured.value)


def test_malformed_or_cross_profile_keyring_value_fails_closed() -> None:
    """Protected values remain closed, typed, and bound to their username profile."""
    provider = _FakeKeyring()
    store = KeyringCredentialStore(provider)
    provider.values[("workaholic-ai", "profile:local")] = "not-json"
    with pytest.raises(CredentialUnavailableError):
        store.load("local")

    staging: HumanCredential = _credential("staging")
    provider.values[("workaholic-ai", "profile:local")] = serialize_credential_json(
        staging
    )
    with pytest.raises(CredentialUnavailableError):
        store.load("local")


def test_keyring_constructor_and_replace_validate_runtime_protocols() -> None:
    """Keyring boundaries reject incomplete providers and malformed credentials."""
    with pytest.raises(InvalidInputError):
        KeyringCredentialStore(cast("KeyringProvider", object()))
    with pytest.raises(InvalidInputError):
        KeyringCredentialStore(_FakeKeyring()).replace(
            cast("HumanCredential", object())
        )


def test_system_keyring_discovery_maps_backend_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """System discovery returns a valid provider or one stable safe error."""
    provider = _FakeKeyring()
    monkeypatch.setattr(
        "workaholic.auth.keyring_store.keyring.get_keyring", lambda: provider
    )
    assert KeyringCredentialStore.system().is_available()

    def fail_discovery() -> object:
        """Raise a simulated private keyring discovery failure."""
        message = "private discovery failure"
        raise KeyringError(message)

    monkeypatch.setattr(
        "workaholic.auth.keyring_store.keyring.get_keyring",
        fail_discovery,
    )
    with pytest.raises(CredentialUnavailableError):
        KeyringCredentialStore.system()


def test_keyring_availability_rejects_failure_and_invalid_priority() -> None:
    """Availability requires a readable numeric provider priority."""

    class _FailingPriority(_FakeKeyring):
        """Provider whose priority lookup fails operationally."""

        def __getattribute__(self, name: str) -> object:
            """Fail only the simulated priority lookup."""
            if name == "priority":
                message = "private priority failure"
                raise KeyringError(message)
            return super().__getattribute__(name)

    invalid_priority = _FakeKeyring()
    invalid_priority.priority = cast("float", "high")

    with pytest.raises(CredentialUnavailableError):
        KeyringCredentialStore(_FailingPriority()).is_available()
    with pytest.raises(CredentialUnavailableError):
        KeyringCredentialStore(invalid_priority).is_available()
