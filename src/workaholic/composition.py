"""Explicit embedded local composition root for the Workaholic CLI."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

from workaholic.application import (
    BootstrapApplication,
    QueryApplication,
    TaskApplication,
)
from workaholic.cli.main import create_app
from workaholic.context import (
    ContextInvalidError,
    exclude_context_from_git,
    read_current_workspace_context,
    resolve_local_data_paths,
    write_current_workspace_context,
)
from workaholic.domain import (
    InstanceId,
    ProjectId,
    RequestId,
    SubjectId,
    TaskEventId,
    TaskId,
    WorkspaceBinding,
)
from workaholic.persistence.sqlite import (
    SQLiteLocalActorSelector,
    SQLitePhaseOneRepository,
)
from workaholic.session import LocalSession

if TYPE_CHECKING:
    from collections.abc import Mapping

    from workaholic.session import WorkaholicSession

_PROGRAM_NAME = "workaholic"
_UUID7_VERSION = 7
_IDENTIFIER_PREFIXES: Final = frozenset(
    ("ins_", "prj_", "sub_", "tsk_", "evt_", "req_")
)


@dataclass(frozen=True, slots=True)
class _ExactDirectoryWorkspaceContext:
    """Adapt one exact current directory to the Session context port."""

    directory: Path

    def read_current(self) -> WorkspaceBinding:
        """Read the exact current directory's context binding."""
        return read_current_workspace_context(self.directory)

    def write_current(self, binding: WorkspaceBinding) -> Path:
        """Durably write context and exclude it from conventional local Git.

        Args:
            binding: Committed bootstrap Workspace binding.

        Returns:
            Exact context-file path.

        """
        context_path = write_current_workspace_context(self.directory, binding)
        exclude_context_from_git(self.directory)
        return context_path


class _UtcSystemClock:
    """Supply authoritative timezone-aware UTC operation timestamps."""

    def now(self) -> datetime:
        """Return the current timezone-aware UTC datetime."""
        return datetime.now(UTC)


class _Uuid7IdentifierFactory:
    """Generate globally unique prefixed Python 3.14 UUID7 identifiers."""

    def new_instance_id(self) -> InstanceId:
        """Create a candidate Instance identifier."""
        return InstanceId(_new_uuid7_text("ins_"))

    def new_project_id(self) -> ProjectId:
        """Create a candidate Project identifier."""
        return ProjectId(_new_uuid7_text("prj_"))

    def new_subject_id(self) -> SubjectId:
        """Create a candidate Subject identifier."""
        return SubjectId(_new_uuid7_text("sub_"))

    def new_task_id(self) -> TaskId:
        """Create a candidate Task identifier."""
        return TaskId(_new_uuid7_text("tsk_"))

    def new_event_id(self) -> TaskEventId:
        """Create a candidate TaskEvent identifier."""
        return TaskEventId(_new_uuid7_text("evt_"))

    def new_request_id(self) -> RequestId:
        """Create a candidate request identifier."""
        return RequestId(_new_uuid7_text("req_"))


def create_local_session(
    *,
    cwd: Path,
    environment: Mapping[str, str],
) -> WorkaholicSession:
    """Compose one short-lived embedded local Session.

    Session construction resolves trusted paths but performs no database or
    Workspace writes. Only ``up`` may initialize storage and context.

    Args:
        cwd: Exact absolute current Workspace directory.
        environment: Trusted process environment used for local data paths.

    Returns:
        Fully composed embedded Phase 1 Session.

    Raises:
        ContextInvalidError: If ``cwd`` or trusted environment is invalid.

    """
    directory = _require_workspace_directory(cwd)
    data_paths = resolve_local_data_paths(environment)
    repository = SQLitePhaseOneRepository(data_paths.database_path)
    clock = _UtcSystemClock()
    identifiers = _Uuid7IdentifierFactory()
    return LocalSession(
        context=_ExactDirectoryWorkspaceContext(directory),
        actors=SQLiteLocalActorSelector(data_paths.database_path),
        bootstrap=BootstrapApplication(repository, clock, identifiers),
        queries=QueryApplication(repository),
        tasks=TaskApplication(repository, clock, identifiers),
    )


def main() -> None:
    """Compose and run the Workaholic command-line application."""
    application = create_app(_create_process_session)
    application(prog_name=_PROGRAM_NAME)


def _create_process_session() -> WorkaholicSession:
    """Compose one Session from the current trusted process boundary."""
    return create_local_session(
        cwd=Path.cwd(),
        environment=os.environ,
    )


def _require_workspace_directory(value: object) -> Path:
    """Require one existing absolute Workspace directory.

    Args:
        value: Candidate process current directory.

    Returns:
        Validated exact directory without upward discovery.

    Raises:
        ContextInvalidError: If the value is not an existing absolute Path.

    """
    if not isinstance(value, Path) or not value.is_absolute():
        message = "The process current directory must be an absolute Path."
        raise ContextInvalidError(message)
    try:
        is_directory = value.is_dir()
    except OSError as error:
        message = "The process current directory is unavailable."
        raise ContextInvalidError(message) from error
    if not is_directory:
        message = "The process current directory must already exist."
        raise ContextInvalidError(message)
    return value


def _new_uuid7_text(prefix: str) -> str:
    """Generate one prefixed canonical UUID7 string.

    Args:
        prefix: Validated identifier-type prefix.

    Returns:
        Prefix followed by a canonical RFC 9562 UUID7 string.

    Raises:
        ValueError: If ``prefix`` is not one of the domain identifier prefixes.
        RuntimeError: If the standard-library generator violates its contract.

    """
    candidate_prefix: object = prefix
    if (
        not isinstance(candidate_prefix, str)
        or candidate_prefix not in _IDENTIFIER_PREFIXES
    ):
        message = "UUID7 identifier prefix is unsupported."
        raise ValueError(message)
    generated = uuid.uuid7()
    if generated.version != _UUID7_VERSION or generated.variant != uuid.RFC_4122:
        message = "Python uuid7() returned an invalid UUID."
        raise RuntimeError(message)
    return f"{prefix}{generated}"
