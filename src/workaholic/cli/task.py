"""CLI commands for persistent Project-scoped Tasks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer
from pydantic import ValidationError

from workaholic.cli.errors import write_failure, write_invalid_input
from workaholic.cli.options import (
    AllProjectsOption,
    ApprovalOption,
    AvailableAtOption,
    CursorOption,
    IdempotencyKeyOption,
    InputFileOption,
    JsonOption,
    LimitOption,
    NonInteractiveOption,
    ProjectOption,
    TaskSelectorArgument,
    TaskViewOption,
    option_was_supplied,
)
from workaholic.cli.rendering import write_success
from workaholic.cli.runtime import SessionProvider, acquire_session
from workaholic.cli.serialization import (
    created_task_data,
    task_details_data,
    task_details_summary,
    task_page_data,
    task_summary,
)
from workaholic.cli.structured_input import (
    StructuredInputError,
    load_structured_object,
    merge_structured_fields,
    parse_utc_timestamp_field,
)
from workaholic.cli.task_events import register_task_event_commands
from workaholic.cli.task_mutations import register_task_mutation_commands
from workaholic.cli.task_results import register_task_result_commands
from workaholic.session import (
    TaskCreateRequest,
    TaskDetailsRequest,
    TaskListByViewRequest,
)

if TYPE_CHECKING:
    from workaholic.domain import Task

TaskTitleArgument = Annotated[
    str,
    typer.Argument(
        ...,
        help="Title of the desired Task outcome.",
        metavar="TITLE",
    ),
]
ObjectiveOption = Annotated[
    str | None,
    typer.Option(
        ...,
        "--objective",
        help="Detailed desired outcome; defaults to TITLE.",
        metavar="TEXT",
        prompt=False,
    ),
]
PriorityOption = Annotated[
    int,
    typer.Option(
        ...,
        "--priority",
        help="Task priority from 0 through 100.",
        metavar="INTEGER",
        prompt=False,
        show_default=True,
    ),
]

_TASK_CREATE_FILE_FIELDS = frozenset(
    (
        "objective",
        "priority",
        "available_at",
        "approval",
        "acceptance",
        "context",
    )
)


def register_task_commands(
    application: typer.Typer,
    *,
    session_provider: SessionProvider,
) -> None:
    """Register Task commands against an explicit Session provider.

    Args:
        application: Task Typer command group.
        session_provider: Command-scoped Session factory.

    """

    @application.command("add")
    def add_task(  # noqa: PLR0913 - explicit public CLI option contract
        ctx: typer.Context,
        title: TaskTitleArgument,
        objective: ObjectiveOption = None,
        priority: PriorityOption = 50,
        available_at: AvailableAtOption = None,
        approval: ApprovalOption = None,
        input_file: InputFileOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        project: ProjectOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002 - Typer option
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Create one attributable Task in the selected Project."""
        del non_interactive
        try:
            inline: dict[str, object] = {
                name: value
                for name, value in (
                    ("objective", objective),
                    ("priority", priority),
                    ("available_at", available_at),
                    ("approval", approval),
                )
                if option_was_supplied(ctx, name)
            }
            file_values = (
                {} if input_file is None else load_structured_object(input_file)
            )
            _require_add_file_availability(file_values)
            definition = merge_structured_fields(
                file_values=file_values,
                inline_values=inline,
                allowed_fields=_TASK_CREATE_FILE_FIELDS,
            )
            if "available_at" in definition:
                definition["available_at"] = parse_utc_timestamp_field(
                    definition["available_at"],
                    label="Task creation available_at",
                    allow_none=False,
                )
            request = TaskCreateRequest.model_validate(
                {
                    "title": title,
                    **definition,
                    "idempotency_key": idempotency_key,
                    "project": project,
                }
            )
        except StructuredInputError, ValidationError:
            write_invalid_input(
                "Task-create input is invalid.",
                json_mode=json_mode,
            )
        try:
            task = acquire_session(session_provider).create_task(request)
            data = {"task": created_task_data(task)}
            write_success(
                data if json_mode else task_summary(task),
                json_mode=json_mode,
            )
        except Exception as error:  # noqa: BLE001 - redact every boundary failure
            write_failure(error, json_mode=json_mode)

    @application.command("list")
    def list_tasks(  # noqa: PLR0913 - explicit public CLI option contract
        project: ProjectOption = None,
        all_projects: AllProjectsOption = False,  # noqa: FBT002 - Typer option
        view: TaskViewOption = "all",
        cursor: CursorOption = None,
        limit: LimitOption = 100,
        json_mode: JsonOption = False,  # noqa: FBT002 - Typer option
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """List one ascending page of Tasks in the selected Project."""
        del non_interactive
        try:
            request = TaskListByViewRequest.model_validate(
                {
                    "view": view,
                    "cursor": cursor,
                    "limit": limit,
                    "project": project,
                    "all_projects": all_projects,
                }
            )
        except ValidationError:
            write_invalid_input(
                "Task-list input is invalid.",
                json_mode=json_mode,
            )
        try:
            page = acquire_session(session_provider).list_tasks_by_view(request)
            data = task_page_data(page)
            human_result = _task_page_summary(
                page.tasks,
                next_cursor=page.next_cursor,
            )
            write_success(
                data if json_mode else human_result,
                json_mode=json_mode,
            )
        except Exception as error:  # noqa: BLE001 - redact every boundary failure
            write_failure(error, json_mode=json_mode)

    @application.command("show")
    def show_task(
        task: TaskSelectorArgument,
        project: ProjectOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002 - Typer option
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Show one Task by canonical UID or stable Human key."""
        del non_interactive
        try:
            request = TaskDetailsRequest(task=task, project=project)
        except ValidationError:
            write_invalid_input(
                "Task selector is invalid.",
                json_mode=json_mode,
            )
        try:
            details = acquire_session(session_provider).get_task_details(request)
            data = task_details_data(details)
            write_success(
                data if json_mode else task_details_summary(details),
                json_mode=json_mode,
            )
        except Exception as error:  # noqa: BLE001 - redact every boundary failure
            write_failure(error, json_mode=json_mode)

    register_task_mutation_commands(
        application,
        session_provider=session_provider,
    )
    register_task_result_commands(
        application,
        session_provider=session_provider,
    )
    register_task_event_commands(
        application,
        session_provider=session_provider,
    )


def _require_add_file_availability(file_values: dict[str, object]) -> None:
    """Reject explicit null availability, which is update-only.

    Args:
        file_values: Parsed Task-create definition fields.

    Raises:
        StructuredInputError: If availability is explicitly null.

    """
    if file_values.get("available_at", object()) is None:
        raise StructuredInputError


def _task_page_summary(
    tasks: tuple[Task, ...],
    *,
    next_cursor: str | None,
) -> str:
    """Render one deterministic Human Task page.

    Args:
        tasks: Ordered Tasks returned by the Session.
        next_cursor: Optional opaque continuation cursor.

    Returns:
        Stable newline-delimited page summary.

    """
    lines = [task_summary(task) for task in tasks]
    if not lines:
        lines.append("No tasks.")
    if next_cursor is not None:
        lines.append(f"Next cursor: {next_cursor}")
    return "\n".join(lines)
