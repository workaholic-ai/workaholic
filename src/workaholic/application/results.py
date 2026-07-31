"""Strict result models returned by cumulative application operations."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from workaholic.domain import (
    DomainValidationError,
    Instance,
    Project,
    ProjectGrant,
    ProjectRole,
    Subject,
    SubjectKind,
    Task,
    WorkspaceBinding,
    validate_profile_name,
)

_CURSOR_MAX_LENGTH = 2_048


class _ResultModel(BaseModel):
    """Shared strictness policy for application result models."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )


class BootstrapResult(_ResultModel):
    """Persisted entities and safe binding produced by local bootstrap."""

    instance: Instance
    project: Project
    subject: Subject
    grant: ProjectGrant
    workspace: WorkspaceBinding

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        """Validate all cross-entity bootstrap relationships.

        Returns:
            The internally consistent bootstrap result.

        Raises:
            ValueError: If the result combines unrelated or unauthorized entities.

        """
        _validate_identity_consistency(
            instance=self.instance,
            project=self.project,
            subject=self.subject,
            grant=self.grant,
        )
        if (
            self.workspace.instance_id != self.instance.id
            or self.workspace.project_id != self.project.id
            or self.workspace.project_key != self.project.key
        ):
            message = "Bootstrap workspace does not match its Instance and Project."
            raise ValueError(message)
        return self


class StatusResult(_ResultModel):
    """Current embedded local status for one authorized Project."""

    mode: Literal["embedded"] = "embedded"
    profile: str = "local"
    schema_version: Literal[2] = 2
    instance: Instance
    project: Project
    subject: Subject
    grant: ProjectGrant

    @field_validator("profile", mode="before")
    @classmethod
    def _validate_profile(cls, value: object) -> str:
        """Validate the trusted profile represented by status.

        Args:
            value: Candidate profile name.

        Returns:
            Validated trusted profile name.

        """
        return validate_profile_name(value)

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        """Validate all cross-entity status relationships.

        Returns:
            The internally consistent status result.

        Raises:
            ValueError: If the result combines unrelated or unauthorized entities.

        """
        _validate_identity_consistency(
            instance=self.instance,
            project=self.project,
            subject=self.subject,
            grant=self.grant,
        )
        return self


class ProjectCreationResult(_ResultModel):
    """Committed Project and creator Owner grant."""

    project: Project
    grant: ProjectGrant

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        """Validate the committed Project and grant relationship.

        Returns:
            The internally consistent Project creation result.

        Raises:
            ValueError: If the grant does not own the created Project.

        """
        if (
            self.grant.project_id != self.project.id
            or self.grant.role is not ProjectRole.OWNER
        ):
            message = "Project creation grant must own the created Project."
            raise ValueError(message)
        return self


class ContextResult(_ResultModel):
    """One effective embedded profile, identity, and safe Workspace selection."""

    mode: Literal["embedded"] = "embedded"
    profile: str
    schema_version: Literal[2] = 2
    instance: Instance
    project: Project
    subject: Subject
    grant: ProjectGrant
    workspace_root: Path | None
    context_source: Path | None

    @field_validator("profile", mode="before")
    @classmethod
    def _validate_profile(cls, value: object) -> str:
        """Validate the selected trusted profile name.

        Args:
            value: Candidate profile name.

        Returns:
            The validated profile name.

        """
        return validate_profile_name(value)

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        """Validate identity relationships and safe path disclosure.

        Returns:
            The internally consistent effective context.

        Raises:
            ValueError: If identities or optional context paths disagree.

        """
        _validate_identity_consistency(
            instance=self.instance,
            project=self.project,
            subject=self.subject,
            grant=self.grant,
        )
        paths = (self.workspace_root, self.context_source)
        if (paths[0] is None) != (paths[1] is None):
            message = "Context paths must both be present or both be null."
            raise ValueError(message)
        if self.workspace_root is None or self.context_source is None:
            return self
        if (
            not self.workspace_root.is_absolute()
            or not self.context_source.is_absolute()
        ):
            message = "Context paths must be absolute."
            raise ValueError(message)
        if self.context_source.name != ".workaholic.env":
            message = "Context source must identify .workaholic.env."
            raise ValueError(message)
        if not self.workspace_root.is_relative_to(self.context_source.parent):
            message = "Workspace root must remain within its context directory."
            raise ValueError(message)
        return self


class TaskPage(_ResultModel):
    """One deterministic Project- or Instance-scoped ascending Task page."""

    tasks: tuple[Task, ...]
    next_cursor: str | None

    @field_validator("next_cursor", mode="before")
    @classmethod
    def _validate_next_cursor(cls, value: object) -> str | None:
        """Validate a returned opaque cursor.

        Args:
            value: Candidate cursor.

        Returns:
            The validated cursor or ``None``.

        Raises:
            ValueError: If the cursor is malformed.

        """
        if value is None:
            return None
        if not isinstance(value, str):
            message = "Next cursor must be a string or null."
            raise DomainValidationError(message)
        if (
            not value
            or value != value.strip()
            or len(value) > _CURSOR_MAX_LENGTH
            or any(
                character.isspace() or not character.isprintable()
                for character in value
            )
        ):
            message = (
                "Next cursor must contain 1 through 2048 characters without "
                "whitespace or control characters."
            )
            raise ValueError(message)
        return value

    @model_validator(mode="after")
    def _validate_task_order(self) -> Self:
        """Require strict Project-key and task-number ordering.

        Returns:
            The validated deterministic Task page.

        Raises:
            ValueError: If Tasks are not strictly ascending.

        """
        previous_position: tuple[str, int] | None = None
        for task in self.tasks:
            project_key, separator, _number = task.key.rpartition("-")
            if separator != "-":
                message = "Task page contains an invalid stable Task key."
                raise ValueError(message)
            position = (project_key, task.number)
            if previous_position is not None and position <= previous_position:
                message = (
                    "Task page must be ordered by Project key and task number "
                    "ascending."
                )
                raise ValueError(message)
            previous_position = position
        return self


def _validate_identity_consistency(
    *,
    instance: Instance,
    project: Project,
    subject: Subject,
    grant: ProjectGrant,
) -> None:
    """Validate the embedded bootstrap-Human and Owner relationship.

    Args:
        instance: Selected local Instance.
        project: Selected local Project.
        subject: Selected local Human.
        grant: Subject's ProjectGrant.

    Raises:
        ValueError: If any entity or authorization relationship is inconsistent.

    """
    if project.instance_id != instance.id:
        message = "Project does not belong to the selected Instance."
        raise ValueError(message)
    subject_kind: object = subject.kind
    if (
        subject_kind is not SubjectKind.HUMAN
        or not subject.enabled
        or not subject.is_instance_admin
    ):
        message = "Embedded context requires an enabled Human administrator."
        raise ValueError(message)
    if (
        grant.subject_id != subject.id
        or grant.project_id != project.id
        or grant.role is not ProjectRole.OWNER
    ):
        message = "Embedded context requires the selected Human to own the Project."
        raise ValueError(message)
