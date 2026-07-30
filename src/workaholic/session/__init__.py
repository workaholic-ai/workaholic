"""Transport-neutral Session interfaces, requests, and local implementation."""

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    ExitCategory,
)
from workaholic.session.base import (
    LocalActorSelector,
    WorkaholicSession,
    WorkspaceContextGateway,
)
from workaholic.session.local import LocalSession
from workaholic.session.models import (
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
    "ExitCategory",
    "LocalActorSelector",
    "LocalSession",
    "ProjectListRequest",
    "StatusRequest",
    "TaskCreateRequest",
    "TaskGetRequest",
    "TaskListRequest",
    "UpRequest",
    "WorkaholicSession",
    "WorkspaceContextGateway",
]
