"""Command-specific Phase 1 CLI object serialization."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from workaholic.cli.envelopes import normalize_json_value

if TYPE_CHECKING:
    from workaholic.cli.envelopes import JsonValue
    from workaholic.domain import (
        Instance,
        Project,
        ProjectGrant,
        Subject,
        Task,
        WorkspaceBinding,
    )

_CONTEXT_FILENAME = ".workaholic.env"


def instance_data(instance: Instance) -> dict[str, JsonValue]:
    """Serialize one Instance using the closed Phase 1 CLI shape.

    Args:
        instance: Validated domain Instance.

    Returns:
        Public Instance data.

    """
    return {"id": str(instance.id)}


def project_data(project: Project) -> dict[str, JsonValue]:
    """Serialize one Project using the closed Phase 1 CLI shape.

    Args:
        project: Validated domain Project.

    Returns:
        Public Project data.

    """
    return {
        "id": str(project.id),
        "key": project.key,
    }


def subject_data(
    subject: Subject,
    grant: ProjectGrant,
) -> dict[str, JsonValue]:
    """Serialize one Subject and its selected-Project role.

    Args:
        subject: Validated selected Subject.
        grant: Subject's validated Project grant.

    Returns:
        Public Subject data with authorization context.

    """
    return {
        "id": str(subject.id),
        "kind": subject.kind.value,
        "display_name": subject.display_name,
        "is_instance_admin": subject.is_instance_admin,
        "project_role": grant.role.value,
    }


def task_data(task: Task) -> dict[str, JsonValue]:
    """Serialize one Task using the closed Phase 1 CLI shape.

    Args:
        task: Validated domain Task.

    Returns:
        Public Task data with all required fields.

    """
    return {
        "uid": str(task.uid),
        "project_id": str(task.project_id),
        "number": task.number,
        "key": task.key,
        "title": task.title,
        "objective": task.objective,
        "state": task.state.value,
        "priority": task.priority,
        "version": task.version,
        "created_by": str(task.created_by),
        "created_at": normalize_json_value(task.created_at),
        "updated_at": normalize_json_value(task.updated_at),
    }


def workspace_data(
    binding: WorkspaceBinding,
    *,
    current_directory: Path,
) -> dict[str, JsonValue]:
    """Serialize the exact-directory Workspace as absolute paths.

    The durable Phase 1 binding stores ``.`` so it remains location-safe. The
    CLI resolves that marker against the command's current directory only
    after bootstrap has durably completed.

    Args:
        binding: Validated Workspace binding returned by the Session.
        current_directory: Exact directory in which the command is running.

    Returns:
        Public absolute Workspace and context-file paths.

    Raises:
        TypeError: If ``current_directory`` is not a Path.

    """
    candidate_directory: object = current_directory
    if not isinstance(candidate_directory, Path):
        message = "CLI current directory must be a Path."
        raise TypeError(message)
    root = (candidate_directory / binding.workspace_root).resolve()
    return {
        "root": str(root),
        "context_file": str(root / _CONTEXT_FILENAME),
    }
