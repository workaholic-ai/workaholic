"""CLI command for structured Agent Task progress."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from workaholic.cli.errors import write_failure, write_invalid_input
from workaholic.cli.options import (  # noqa: TC001 - Typer resolves aliases
    AttemptOption,
    IdempotencyKeyOption,
    InputFileOption,
    JsonOption,
    NonInteractiveOption,
    ProjectOption,
    TaskSelectorArgument,
)
from workaholic.cli.rendering import write_success
from workaholic.cli.runtime import SessionProvider, acquire_session
from workaholic.cli.serialization import task_progress_data, task_progress_summary
from workaholic.cli.structured_input import (
    StructuredInputError,
    load_required_structured_object,
)
from workaholic.domain import AttemptId, DomainValidationError
from workaholic.session import AgentProgressRequest, TaskProgressResult

if TYPE_CHECKING:
    import typer


def register_task_execution_commands(
    application: typer.Typer,
    *,
    session_provider: SessionProvider,
) -> None:
    """Register Agent execution commands against one Session provider.

    Args:
        application: Task Typer command group.
        session_provider: Command-scoped Session factory.

    """

    @application.command("progress")
    def report_progress(  # noqa: PLR0913 - explicit public CLI contract
        task: TaskSelectorArgument,
        attempt: AttemptOption = None,
        input_file: InputFileOption = None,
        project: ProjectOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002 - Typer option
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Append bounded structured progress for one active Agent Attempt."""
        del non_interactive
        try:
            progress = load_required_structured_object(input_file)
            request = AgentProgressRequest.model_validate(
                {
                    "task": task,
                    "attempt": AttemptId(attempt or ""),
                    "progress": progress,
                    "project": project,
                    "idempotency_key": idempotency_key,
                }
            )
        except StructuredInputError, DomainValidationError, ValidationError:
            write_invalid_input("Task-progress input is invalid.", json_mode=json_mode)
        try:
            result = acquire_session(session_provider).report_progress(request)
            _write_progress_result(result, json_mode=json_mode)
        except Exception as error:  # noqa: BLE001 - redact every boundary failure
            write_failure(error, json_mode=json_mode)


def _write_progress_result(result: TaskProgressResult, *, json_mode: bool) -> None:
    """Render one validated Agent progress result.

    Args:
        result: Session-returned progress operation result.
        json_mode: Whether to emit the public JSON envelope.

    """
    write_success(
        task_progress_data(result) if json_mode else task_progress_summary(result),
        json_mode=json_mode,
    )
