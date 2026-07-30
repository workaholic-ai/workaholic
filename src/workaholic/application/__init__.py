"""Use-case contracts independent of transports and concrete storage."""

from workaholic.application.bootstrap import BootstrapApplication
from workaholic.application.commands import (
    BootstrapLocalProjectInput,
    BootstrapMutation,
    CreateTaskInput,
    GetLocalStatus,
    GetTask,
    ListProjects,
    ListTasks,
    TaskCreationMutation,
)
from workaholic.application.errors import (
    ApplicationError,
    ApplicationErrorCode,
    ExitCategory,
    IdempotencyConflictError,
    PermissionDeniedError,
    ProjectKeyConflictError,
)
from workaholic.application.ports import Clock, IdentifierFactory, PhaseOneRepository
from workaholic.application.results import (
    BootstrapResult,
    StatusResult,
    TaskPage,
)
from workaholic.application.tasks import TaskApplication

__all__ = [
    "ApplicationError",
    "ApplicationErrorCode",
    "BootstrapApplication",
    "BootstrapLocalProjectInput",
    "BootstrapMutation",
    "BootstrapResult",
    "Clock",
    "CreateTaskInput",
    "ExitCategory",
    "GetLocalStatus",
    "GetTask",
    "IdempotencyConflictError",
    "IdentifierFactory",
    "ListProjects",
    "ListTasks",
    "PermissionDeniedError",
    "PhaseOneRepository",
    "ProjectKeyConflictError",
    "StatusResult",
    "TaskApplication",
    "TaskCreationMutation",
    "TaskPage",
]
