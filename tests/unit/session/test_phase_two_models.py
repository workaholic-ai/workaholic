"""Unit tests for Phase 2 Session requests and protocol boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from workaholic.session import (
    ContextRequest,
    ProjectBindRequest,
    ProjectCreateRequest,
    ProjectListRequest,
    StatusRequest,
    TaskCreateRequest,
    TaskGetRequest,
    TaskListRequest,
    UpRequest,
    WorkaholicSession,
)


def test_profile_aware_requests_accept_only_valid_named_profiles() -> None:
    """Profile selectors use the exact trusted-profile domain grammar."""
    requests = (
        UpRequest(project_key="ACME", profile="team_1"),
        StatusRequest(profile="team_1"),
        ContextRequest(profile="team_1"),
        ProjectCreateRequest(key="DOCS", name="Docs", profile="team_1"),
        ProjectBindRequest(project="DOCS", profile="team_1"),
        ProjectListRequest(profile="team_1"),
    )

    assert all(request.profile == "team_1" for request in requests)
    for request_type, values in (
        (UpRequest, {"project_key": "ACME"}),
        (StatusRequest, {}),
        (ContextRequest, {}),
        (ProjectCreateRequest, {"key": "DOCS", "name": "Docs"}),
        (ProjectBindRequest, {"project": "DOCS"}),
        (ProjectListRequest, {}),
    ):
        with pytest.raises(ValidationError):
            request_type.model_validate({**values, "profile": "Team"})


def test_project_requests_normalize_names_and_reject_unknown_fields() -> None:
    """Project requests expose validated intent without presentation values."""
    creation = ProjectCreateRequest(
        key="DOCS",
        name="  Cafe\u0301 docs  ",
        idempotency_key="project-docs-1",
    )
    bootstrap = UpRequest(
        project_key="ACME",
        project_name="  Acme  ",
    )

    assert creation.name == "Café docs"
    assert bootstrap.project_name == "Acme"
    assert bootstrap.profile is None
    with pytest.raises(ValidationError, match="Extra inputs"):
        ProjectCreateRequest.model_validate(
            {
                "key": "DOCS",
                "name": "Docs",
                "storage": "/unsafe/database",
            }
        )
    with pytest.raises(ValidationError):
        ProjectCreateRequest(key="docs", name="Docs")


def test_project_bind_requires_typed_path_and_strict_replacement_flag() -> None:
    """Binding requests keep filesystem intent at the Session boundary."""
    target = Path("/work/docs")
    request = ProjectBindRequest(
        project="DOCS",
        path=target,
        replace=True,
    )

    assert request.path is target
    assert request.replace is True
    with pytest.raises(ValidationError):
        ProjectBindRequest.model_validate({"project": "DOCS", "path": "/work/docs"})
    with pytest.raises(ValidationError):
        ProjectBindRequest.model_validate({"project": "DOCS", "replace": 1})


def test_project_scoped_requests_accept_one_optional_valid_key() -> None:
    """Status, context, and Task requests share one strict Project selector."""
    requests = (
        StatusRequest(project="DOCS"),
        ContextRequest(project="DOCS"),
        TaskCreateRequest(title="Task", project="DOCS"),
        TaskListRequest(project="DOCS"),
        TaskGetRequest(task="DOCS-1", project="DOCS"),
    )

    assert all(request.project == "DOCS" for request in requests)
    for request in requests:
        values = request.model_dump()
        values["project"] = "docs"
        with pytest.raises(ValidationError):
            type(request).model_validate(values)


def test_all_projects_is_exclusive_and_exists_only_on_task_list() -> None:
    """Only Task listing can request an authorized all-Project selection."""
    request = TaskListRequest(all_projects=True, limit=500)

    assert request.all_projects is True
    assert request.project is None
    with pytest.raises(ValidationError, match="mutually exclusive"):
        TaskListRequest(project="DOCS", all_projects=True)
    with pytest.raises(ValidationError):
        TaskListRequest.model_validate({"all_projects": 1})

    request_types = (
        UpRequest,
        StatusRequest,
        ContextRequest,
        ProjectCreateRequest,
        ProjectBindRequest,
        ProjectListRequest,
        TaskCreateRequest,
        TaskGetRequest,
    )
    assert all("all_projects" not in model.model_fields for model in request_types)


def test_session_requests_are_frozen_and_reject_coercion() -> None:
    """The Session boundary rejects mutable, extra, and coerced caller data."""
    request = ContextRequest(profile="local", project="ACME")

    with pytest.raises(ValidationError, match="frozen"):
        request.project = "DOCS"
    with pytest.raises(ValidationError, match="Extra inputs"):
        ContextRequest.model_validate({"profile": "local", "url": "https://invalid"})
    with pytest.raises(ValidationError):
        TaskListRequest.model_validate({"limit": "100"})
    with pytest.raises(ValidationError):
        TaskCreateRequest.model_validate({"title": "Task", "priority": False})


def test_workaholic_session_declares_every_phase_two_operation() -> None:
    """Presentation adapters can discover the complete cumulative interface."""
    operations = {
        "up",
        "status",
        "context",
        "create_project",
        "bind_project",
        "list_projects",
        "create_task",
        "list_tasks",
        "get_task",
    }

    assert all(
        callable(getattr(WorkaholicSession, operation, None))
        for operation in operations
    )
