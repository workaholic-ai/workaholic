"""Typed boundaries and assertions for golden-journey specifications."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Never, Protocol, TypeGuard

if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from subprocess import CompletedProcess

type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)
type JsonObject = dict[str, JsonValue]
type StorageBackend = Literal["json", "sqlite", "postgres"]
type SubjectKind = Literal["human", "agent"]

_CLI_SCHEMA = "workaholic.cli/v1"
_CLI_TIMEOUT_SECONDS = 30
_TRUSTED_CLI_ENVIRONMENT_KEYS = frozenset({"WORKAHOLIC_DATA_DIR"})


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


@dataclass(frozen=True, slots=True)
class SubprocessGoldenJourneyRunner:
    """Run supported journeys in fresh processes over isolated local state."""

    data_directory: Path

    def __post_init__(self) -> None:
        """Validate the owned data root without creating it.

        Raises:
            TypeError: If the data directory is not an absolute Path.

        """
        candidate: object = self.data_directory
        if not isinstance(candidate, Path) or not candidate.is_absolute():
            message = "Golden data_directory must be an absolute Path."
            raise TypeError(message)

    def cli(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path,
        input_text: str | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run one fresh installed-package CLI process.

        Args:
            arguments: Arguments after the ``workaholic`` executable.
            cwd: Existing exact Workspace working directory.
            input_text: Optional UTF-8 standard-input payload.
            environment: Optional documented trusted environment overrides.

        Returns:
            Completed process with UTF-8 text streams.

        Raises:
            TypeError: If a boundary value has an unsupported runtime type.
            ValueError: If an override is undocumented or escapes owned state.
            subprocess.TimeoutExpired: If the CLI does not finish promptly.

        """
        validated_arguments = _validate_cli_arguments(arguments)
        validated_cwd = _validate_cli_cwd(cwd)
        validated_input = _validate_input_text(input_text)
        process_environment = _isolated_cli_environment(
            self.data_directory,
            environment,
        )
        return subprocess.run(
            [sys.executable, "-m", "workaholic", *validated_arguments],
            check=False,
            cwd=validated_cwd,
            env=process_environment,
            input=validated_input,
            capture_output=True,
            encoding="utf-8",
            timeout=_CLI_TIMEOUT_SECONDS,
        )

    def instance(
        self,
        *,
        backend: StorageBackend,
        project_key: str,
        remote: bool,
        root: Path,
        subjects: Mapping[str, SubjectKind],
    ) -> AbstractContextManager[GoldenInstance]:
        """Reject future Instance orchestration before its enabling phase.

        Args:
            backend: Requested persistence backend.
            project_key: Requested initial Project key.
            remote: Whether a real remote server is requested.
            root: Requested isolated resource root.
            subjects: Requested Subject inventory.

        Raises:
            NotImplementedError: Always in the Phase 1 harness.

        """
        del backend, project_key, remote, root, subjects
        message = "Golden Instance orchestration is not implemented in Phase 1."
        raise NotImplementedError(message)

    def published_package_spec(self) -> str:
        """Reject registry selection before release-candidate acceptance.

        Raises:
            NotImplementedError: Always in the Phase 1 harness.

        """
        message = "Published-package selection is not implemented in Phase 1."
        raise NotImplementedError(message)

    def uvx(
        self,
        package_spec: str,
        arguments: Sequence[str],
        *,
        cwd: Path,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Reject registry execution before release-candidate acceptance.

        Args:
            package_spec: Requested immutable registry package specifier.
            arguments: Requested public CLI arguments.
            cwd: Requested clean working directory.
            input_text: Optional requested standard-input payload.

        Raises:
            NotImplementedError: Always in the Phase 1 harness.

        """
        del package_spec, arguments, cwd, input_text
        message = "Golden uvx execution is not implemented in Phase 1."
        raise NotImplementedError(message)


def _validate_cli_arguments(value: object) -> tuple[str, ...]:
    """Validate one immutable CLI argument sequence.

    Args:
        value: Candidate argument sequence.

    Returns:
        Copied tuple of CLI arguments.

    Raises:
        TypeError: If the sequence or any element is invalid.

    """
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        message = "Golden CLI arguments must be a sequence of strings."
        raise TypeError(message)
    arguments = tuple(value)
    if not all(isinstance(argument, str) for argument in arguments):
        message = "Golden CLI arguments must contain only strings."
        raise TypeError(message)
    return arguments


def _validate_cli_cwd(value: object) -> Path:
    """Validate one exact existing CLI working directory.

    Args:
        value: Candidate working directory.

    Returns:
        Validated absolute directory.

    Raises:
        TypeError: If the value is not an absolute Path.
        ValueError: If the path is not an existing directory.

    """
    if not isinstance(value, Path) or not value.is_absolute():
        message = "Golden CLI cwd must be an absolute Path."
        raise TypeError(message)
    try:
        is_directory = value.is_dir()
    except OSError as error:
        message = "Golden CLI cwd is unavailable."
        raise ValueError(message) from error
    if not is_directory:
        message = "Golden CLI cwd must be an existing directory."
        raise ValueError(message)
    return value


def _validate_input_text(value: object) -> str | None:
    """Validate optional CLI standard input without coercion.

    Args:
        value: Candidate input payload.

    Returns:
        Input string or ``None``.

    Raises:
        TypeError: If the payload is not text.

    """
    if value is not None and not isinstance(value, str):
        message = "Golden CLI input_text must be a string or None."
        raise TypeError(message)
    return value


def _isolated_cli_environment(
    data_directory: Path,
    overrides: Mapping[str, str] | None,
) -> dict[str, str]:
    """Build an environment that cannot select developer Workaholic state.

    Args:
        data_directory: Harness-owned trusted data directory.
        overrides: Optional documented trusted overrides.

    Returns:
        Process environment pinned to harness-owned storage.

    Raises:
        TypeError: If overrides are not a string mapping.
        ValueError: If an override is undocumented or changes the data root.

    """
    candidate_overrides: object = overrides
    if candidate_overrides is None:
        supplied: dict[str, str] = {}
    elif not isinstance(candidate_overrides, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in candidate_overrides.items()
    ):
        message = "Golden CLI environment overrides must map strings to strings."
        raise TypeError(message)
    else:
        supplied = dict(candidate_overrides)

    undocumented = supplied.keys() - _TRUSTED_CLI_ENVIRONMENT_KEYS
    if undocumented:
        message = "Golden CLI environment contains an undocumented override."
        raise ValueError(message)
    expected_data_directory = str(data_directory)
    if supplied.get("WORKAHOLIC_DATA_DIR", expected_data_directory) != (
        expected_data_directory
    ):
        message = "Golden CLI cannot override its harness-owned data directory."
        raise ValueError(message)

    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("WORKAHOLIC_")
    }
    environment.update(
        {
            "NO_COLOR": "1",
            "WORKAHOLIC_DATA_DIR": expected_data_directory,
        }
    )
    return environment


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
    if payload.keys() != {"schema", "ok", "data"}:
        message = "CLI success envelope must contain exactly schema, ok, and data."
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
    if payload.keys() != {"schema", "ok", "error"}:
        message = "CLI error envelope must contain exactly schema, ok, and error."
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
