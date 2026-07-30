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
    InvalidInputError,
    NotInitializedError,
    PermissionDeniedError,
    ProjectKeyConflictError,
    TaskNotFoundError,
)
from workaholic.application.ports import Clock, IdentifierFactory, PhaseOneRepository
from workaholic.application.queries import QueryApplication
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
    "InvalidInputError",
    "ListProjects",
    "ListTasks",
    "NotInitializedError",
    "PermissionDeniedError",
    "PhaseOneRepository",
    "ProjectKeyConflictError",
    "QueryApplication",
    "StatusResult",
    "TaskApplication",
    "TaskCreationMutation",
    "TaskNotFoundError",
    "TaskPage",
]
