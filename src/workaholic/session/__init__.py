"""Transport-neutral Session interfaces, requests, and local implementation."""

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    ExitCategory,
)
from workaholic.session.base import (
    LocalActorSelector,
    TaskSession,
    WorkaholicSession,
    WorkspaceContextGateway,
)
from workaholic.session.local import LocalSession
from workaholic.session.models import (
    ContextRequest,
    ProjectBindRequest,
    ProjectCreateRequest,
    ProjectListRequest,
    StatusRequest,
    TaskCreateRequest,
    TaskGetRequest,
    TaskListRequest,
    UpRequest,
)

__all__ = [
    "ApplicationError",
    "ApplicationErrorCode",
    "ContextRequest",
    "ExitCategory",
    "LocalActorSelector",
    "LocalSession",
    "ProjectBindRequest",
    "ProjectCreateRequest",
    "ProjectListRequest",
    "StatusRequest",
    "TaskCreateRequest",
    "TaskGetRequest",
    "TaskListRequest",
    "TaskSession",
    "UpRequest",
    "WorkaholicSession",
    "WorkspaceContextGateway",
]
