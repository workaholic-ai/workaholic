"""Use-case contracts independent of transports and concrete storage."""

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
)
from workaholic.application.ports import Clock, IdentifierFactory, PhaseOneRepository
from workaholic.application.results import (
    BootstrapResult,
    StatusResult,
    TaskPage,
)

__all__ = [
    "ApplicationError",
    "ApplicationErrorCode",
    "BootstrapLocalProjectInput",
    "BootstrapMutation",
    "BootstrapResult",
    "Clock",
    "CreateTaskInput",
    "ExitCategory",
    "GetLocalStatus",
    "GetTask",
    "IdentifierFactory",
    "ListProjects",
    "ListTasks",
    "PhaseOneRepository",
    "StatusResult",
    "TaskCreationMutation",
    "TaskPage",
]
