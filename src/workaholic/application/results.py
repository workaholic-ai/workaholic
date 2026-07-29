"""Strict result models returned by Phase 1 application operations."""

from __future__ import annotations

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

    mode: Literal["local"] = "local"
    schema_version: Literal[1] = 1
    instance: Instance
    project: Project
    subject: Subject
    grant: ProjectGrant

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


class TaskPage(_ResultModel):
    """One deterministic ascending page of Tasks."""

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
        """Require one Project and strict task-number ordering.

        Returns:
            The validated deterministic Task page.

        Raises:
            ValueError: If Tasks cross Projects or are not strictly ascending.

        """
        if not self.tasks:
            return self
        project_id = self.tasks[0].project_id
        previous_number = 0
        for task in self.tasks:
            if task.project_id != project_id:
                message = "Task page must not combine Projects."
                raise ValueError(message)
            if task.number <= previous_number:
                message = "Task page must be ordered by task number ascending."
                raise ValueError(message)
            previous_number = task.number
        return self


def _validate_identity_consistency(
    *,
    instance: Instance,
    project: Project,
    subject: Subject,
    grant: ProjectGrant,
) -> None:
    """Validate the Phase 1 local identity and Owner relationship.

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
        message = "Phase 1 requires one enabled Human Instance administrator."
        raise ValueError(message)
    if (
        grant.subject_id != subject.id
        or grant.project_id != project.id
        or grant.role is not ProjectRole.OWNER
    ):
        message = "Phase 1 requires the selected Human to own the Project."
        raise ValueError(message)
