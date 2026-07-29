"""Typed boundaries and assertions for golden-journey specifications."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal, Never, Protocol, TypeGuard

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from contextlib import AbstractContextManager
    from pathlib import Path
    from subprocess import CompletedProcess

type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)
type JsonObject = dict[str, JsonValue]
type StorageBackend = Literal["json", "sqlite", "postgres"]
type SubjectKind = Literal["human", "agent"]

_CLI_SCHEMA = "workaholic.cli/v1"


class GoldenInstance(Protocol):
    """A real isolated Workaholic AI instance owned by a test harness."""

    def environment_for(self, subject_name: str) -> Mapping[str, str]:
        """Return trusted client environment variables for one Subject.

        Args:
            subject_name: Subject provisioned when the Instance was created.

        Returns:
            Environment overrides selecting the Instance, Project, and Subject.

        Raises:
            KeyError: If the requested Subject was not provisioned.

        """
        ...


class GoldenJourneyRunner(Protocol):
    """Real-process operations required by the canonical journey tests."""

    def cli(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path,
        input_text: str | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> CompletedProcess[str]:
        """Run one fresh `workaholic` CLI process.

        Args:
            arguments: Arguments after the `workaholic` executable.
            cwd: Working directory for context discovery.
            input_text: Optional standard-input payload.
            environment: Optional trusted environment overrides.

        Returns:
            The completed process with decoded text streams.

        """
        ...

    def instance(
        self,
        *,
        backend: StorageBackend,
        project_key: str,
        remote: bool,
        root: Path,
        subjects: Mapping[str, SubjectKind],
    ) -> AbstractContextManager[GoldenInstance]:
        """Provision a real isolated Instance and its client identities.

        The fixture may use supported administrative commands or deployment
        setup, but domain operations exercised by a journey must still cross
        the public CLI boundary.

        Args:
            backend: Persistence adapter used by the Instance.
            project_key: Immutable key of the initially provisioned Project.
            remote: Whether to run through a real server and RemoteSession.
            root: Isolated filesystem root owned by the test.
            subjects: Subject names and kinds to provision.

        Returns:
            A context manager that tears down every allocated resource.

        """
        ...

    def published_package_spec(self) -> str:
        """Return the immutable registry package spec under acceptance.

        Returns:
            A version-pinned `workaholic-ai` package requirement.

        """
        ...

    def uvx(
        self,
        package_spec: str,
        arguments: Sequence[str],
        *,
        cwd: Path,
        input_text: str | None = None,
    ) -> CompletedProcess[str]:
        """Run a published package through uvx outside the source checkout.

        Args:
            package_spec: Version-pinned registry package requirement.
            arguments: Arguments after the `workaholic` executable.
            cwd: Clean working directory for the invocation.
            input_text: Optional standard-input payload.

        Returns:
            The completed uvx-hosted process with decoded text streams.

        """
        ...


def _reject_nonstandard_number(value: str) -> Never:
    """Reject JSON constants forbidden by the CLI contract.

    Args:
        value: Nonstandard JSON numeric token.

    Raises:
        ValueError: Always, because the token is outside interoperable JSON.

    """
    message = f"Nonstandard JSON number is forbidden: {value}"
    raise ValueError(message)


def _is_json_value(value: object) -> TypeGuard[JsonValue]:
    """Return whether a decoded value belongs to the JSON data model.

    Args:
        value: Decoded value to validate recursively.

    Returns:
        Whether the value is valid contract JSON.

    """
    if value is None or isinstance(value, bool | int | float | str):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_value(item) for key, item in value.items()
        )
    return False


def _decode_json_object(result: CompletedProcess[str]) -> JsonObject:
    """Decode one newline-terminated CLI JSON object.

    Args:
        result: Completed CLI process.

    Returns:
        Validated top-level JSON object.

    Raises:
        AssertionError: If stdout violates JSON-mode framing or serialization.

    """
    if not result.stdout.endswith("\n") or result.stdout.endswith("\n\n"):
        message = "JSON-mode stdout must end with exactly one newline."
        raise AssertionError(message)

    try:
        decoded: object = json.loads(
            result.stdout,
            parse_constant=_reject_nonstandard_number,
        )
    except (json.JSONDecodeError, ValueError) as error:
        message = "JSON-mode stdout must contain exactly one interoperable JSON value."
        raise AssertionError(message) from error

    if not _is_json_value(decoded) or not isinstance(decoded, dict):
        message = "JSON-mode stdout must contain one top-level JSON object."
        raise AssertionError(message)
    return decoded


def require_success(result: CompletedProcess[str]) -> JsonValue:
    """Validate a successful public CLI envelope and return its data.

    Args:
        result: Completed CLI process.

    Returns:
        The validated `data` value from the success envelope.

    Raises:
        AssertionError: If process status or envelope fields violate the
            public CLI contract.

    """
    if result.returncode != 0:
        message = "A successful CLI operation must exit with status zero."
        raise AssertionError(message)

    payload = _decode_json_object(result)
    if payload.get("schema") != _CLI_SCHEMA:
        message = "CLI success envelope has an unexpected schema identifier."
        raise AssertionError(message)
    if payload.get("ok") is not True:
        message = "CLI success envelope must set `ok` to true."
        raise AssertionError(message)
    if "data" not in payload or "error" in payload:
        message = "CLI success envelope must contain `data` and omit `error`."
        raise AssertionError(message)
    return payload["data"]


def require_error(
    result: CompletedProcess[str],
    *,
    expected_code: str,
) -> JsonObject:
    """Validate a failed public CLI envelope and return its error object.

    Args:
        result: Completed CLI process.
        expected_code: Machine-readable error code required by the journey.

    Returns:
        The validated `error` object.

    Raises:
        AssertionError: If process status or envelope fields violate the
            public CLI contract.

    """
    if result.returncode == 0:
        message = "A failed CLI operation must exit with a nonzero status."
        raise AssertionError(message)

    payload = _decode_json_object(result)
    if payload.get("schema") != _CLI_SCHEMA:
        message = "CLI error envelope has an unexpected schema identifier."
        raise AssertionError(message)
    if payload.get("ok") is not False:
        message = "CLI error envelope must set `ok` to false."
        raise AssertionError(message)
    if "data" in payload:
        message = "CLI error envelope must omit `data`."
        raise AssertionError(message)

    error = require_object(payload.get("error"), context="CLI error")
    if error.get("code") != expected_code:
        message = "CLI error envelope returned an unexpected error code."
        raise AssertionError(message)
    if not isinstance(error.get("message"), str) or not error["message"]:
        message = "CLI error message must be a nonempty string."
        raise AssertionError(message)
    if not isinstance(error.get("retryable"), bool):
        message = "CLI error retry guidance must be a boolean."
        raise AssertionError(message)  # noqa: TRY004 - contract assertion failure
    return error


def require_object(value: JsonValue, *, context: str) -> JsonObject:
    """Require a JSON object at a journey assertion boundary.

    Args:
        value: JSON value to validate.
        context: Human-readable boundary name used in failure output.

    Returns:
        The value narrowed to a JSON object.

    Raises:
        AssertionError: If the value is not an object.

    """
    if not isinstance(value, dict):
        message = f"{context} must be a JSON object."
        raise AssertionError(message)  # noqa: TRY004 - journey assertion failure
    return value


def require_array(value: JsonValue, *, context: str) -> list[JsonValue]:
    """Require a JSON array at a journey assertion boundary.

    Args:
        value: JSON value to validate.
        context: Human-readable boundary name used in failure output.

    Returns:
        The value narrowed to a JSON array.

    Raises:
        AssertionError: If the value is not an array.

    """
    if not isinstance(value, list):
        message = f"{context} must be a JSON array."
        raise AssertionError(message)  # noqa: TRY004 - journey assertion failure
    return value


def require_string(value: JsonValue, *, context: str) -> str:
    """Require a nonempty JSON string at a journey assertion boundary.

    Args:
        value: JSON value to validate.
        context: Human-readable boundary name used in failure output.

    Returns:
        The value narrowed to a nonempty string.

    Raises:
        AssertionError: If the value is not a nonempty string.

    """
    if not isinstance(value, str) or not value:
        message = f"{context} must be a nonempty JSON string."
        raise AssertionError(message)
    return value
