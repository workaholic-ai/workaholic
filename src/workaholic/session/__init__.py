"""Transport-neutral Session interfaces, requests, and local implementation."""

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    ExitCategory,
)
from workaholic.session.base import (
    LocalActorSelector,
    LocalIdentity,
    LocalRuntimeOpener,
    ProfileResolver,
    TaskSession,
    WorkaholicSession,
    WorkspaceContextGateway,
    WorkspaceContextSelection,
)
from workaholic.session.local import LocalRuntime, LocalSession
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
    "LocalIdentity",
    "LocalRuntime",
    "LocalRuntimeOpener",
    "LocalSession",
    "ProfileResolver",
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
    "WorkspaceContextSelection",
]
