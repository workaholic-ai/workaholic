"""Optimistic Task definition, state, and dependency CLI mutations."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

import typer
from pydantic import BaseModel, ValidationError

from workaholic.cli.errors import (
    write_expected_task_version_required,
    write_failure,
    write_invalid_input,
)
from workaholic.cli.options import (
    ApprovalOption,
    AvailableAtOption,
    ClearAvailableAtOption,
    ExpectedVersionOption,
    IdempotencyKeyOption,
    InputFileOption,
    JsonOption,
    NonInteractiveOption,
    ProjectOption,
    TaskSelectorArgument,
    option_was_supplied,
)
from workaholic.cli.rendering import write_success
from workaholic.cli.runtime import SessionProvider, acquire_session
from workaholic.cli.serialization import task_mutation_data, task_summary
from workaholic.cli.structured_input import (
    StructuredInputError,
    load_structured_object,
    merge_structured_fields,
    parse_utc_timestamp_field,
)
from workaholic.session import (
    TaskAddDependencyRequest,
    TaskBlockRequest,
    TaskCancelRequest,
    TaskDetailsRequest,
    TaskMutationResult,
    TaskRemoveDependencyRequest,
    TaskUnblockRequest,
    TaskUpdateRequest,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from workaholic.session import WorkaholicSession

_UPDATE_FIELDS = frozenset(
    (
        "title",
        "objective",
        "priority",
        "available_at",
        "approval",
        "acceptance",
        "context",
    )
)
PrerequisiteArgument = Annotated[
    str,
    typer.Argument(
        ...,
        help="Canonical UID or stable key of the prerequisite Task.",
        metavar="PREREQUISITE",
    ),
]
TitleOption = Annotated[
    str | None,
    typer.Option(
        ...,
        "--title",
        help="Replace the Task title.",
        metavar="TEXT",
        prompt=False,
    ),
]
ObjectiveOption = Annotated[
    str | None,
    typer.Option(
        ...,
        "--objective",
        help="Replace the detailed desired outcome.",
        metavar="TEXT",
        prompt=False,
    ),
]
PriorityOption = Annotated[
    int | None,
    typer.Option(
        ...,
        "--priority",
        help="Replace Task priority with an integer from 0 through 100.",
        metavar="INTEGER",
        prompt=False,
    ),
]
BlockReasonOption = Annotated[
    str,
    typer.Option(
        ...,
        "--reason",
        help="Explain why the Task cannot currently proceed.",
        metavar="TEXT",
        prompt=False,
    ),
]
CancelReasonOption = Annotated[
    str | None,
    typer.Option(
        ...,
        "--reason",
        help="Optionally explain why the Task is being cancelled.",
        metavar="TEXT",
        prompt=False,
    ),
]


class ExpectedVersionRequiredError(ValueError):
    """Signal that an automation-safe mutation omitted its Task version."""


@dataclass(frozen=True, slots=True)
class PreparedMutation:
    """One command-scoped Session plus the exact version to mutate."""

    session: WorkaholicSession
    expected_version: int


def register_task_mutation_commands(
    application: typer.Typer,
    *,
    session_provider: SessionProvider,
) -> None:
    """Register optimistic Task mutation commands.

    Args:
        application: Task Typer command group.
        session_provider: Command-scoped Session factory.

    """

    @application.command("update")
    def update_task(  # noqa: PLR0913 - explicit public CLI contract
        ctx: typer.Context,
        task: TaskSelectorArgument,
        title: TitleOption = None,
        objective: ObjectiveOption = None,
        priority: PriorityOption = None,
        available_at: AvailableAtOption = None,
        clear_available_at: ClearAvailableAtOption = False,  # noqa: FBT002
        approval: ApprovalOption = None,
        input_file: InputFileOption = None,
        expected_version: ExpectedVersionOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        project: ProjectOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Replace one or more editable Task definition fields."""
        _require_version_mode_or_exit(
            expected_version,
            json_mode=json_mode,
            non_interactive=non_interactive,
        )
        try:
            _require_distinct_availability_options(
                available_at=available_at,
                clear_available_at=clear_available_at,
            )
            inline = _explicit_update_fields(
                ctx,
                title=title,
                objective=objective,
                priority=priority,
                available_at=available_at,
                clear_available_at=clear_available_at,
                approval=approval,
            )
            file_values = (
                {} if input_file is None else load_structured_object(input_file)
            )
            patch = merge_structured_fields(
                file_values=file_values,
                inline_values=inline,
                allowed_fields=_UPDATE_FIELDS,
            )
            if "available_at" in patch:
                patch["available_at"] = parse_utc_timestamp_field(
                    patch["available_at"],
                    label="Task update available_at",
                    allow_none=True,
                )
            provisional = TaskUpdateRequest.model_validate(
                {
                    "task": task,
                    "expected_version": (
                        1 if expected_version is None else expected_version
                    ),
                    "idempotency_key": idempotency_key,
                    "project": project,
                    "patch": patch,
                }
            )
        except StructuredInputError, ValidationError:
            write_invalid_input("Task-update input is invalid.", json_mode=json_mode)
        prepared = _prepare_or_exit(
            session_provider,
            task=task,
            project=project,
            expected_version=expected_version,
            action="update its definition",
            json_mode=json_mode,
        )
        if prepared is None:
            return
        request = _replace_expected_version(
            provisional,
            TaskUpdateRequest,
            prepared.expected_version,
        )
        _invoke_mutation(
            lambda: prepared.session.update_task(request),
            json_mode=json_mode,
        )

    @application.command("block")
    def block_task(  # noqa: PLR0913 - explicit public CLI contract
        task: TaskSelectorArgument,
        reason: BlockReasonOption,
        expected_version: ExpectedVersionOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        project: ProjectOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Block one open Task with an attributable Human reason."""
        values = {
            "task": task,
            "reason": reason,
            "expected_version": 1 if expected_version is None else expected_version,
            "idempotency_key": idempotency_key,
            "project": project,
        }
        _run_simple_mutation(
            session_provider,
            request_type=TaskBlockRequest,
            request_values=values,
            expected_version=expected_version,
            action="block it",
            invoke=lambda session, request: session.block_task(request),
            json_mode=json_mode,
            non_interactive=non_interactive,
        )

    @application.command("unblock")
    def unblock_task(  # noqa: PLR0913 - explicit public CLI contract
        task: TaskSelectorArgument,
        expected_version: ExpectedVersionOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        project: ProjectOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Return one blocked Task to the open state."""
        values = {
            "task": task,
            "expected_version": 1 if expected_version is None else expected_version,
            "idempotency_key": idempotency_key,
            "project": project,
        }
        _run_simple_mutation(
            session_provider,
            request_type=TaskUnblockRequest,
            request_values=values,
            expected_version=expected_version,
            action="unblock it",
            invoke=lambda session, request: session.unblock_task(request),
            json_mode=json_mode,
            non_interactive=non_interactive,
        )

    @application.command("cancel")
    def cancel_task(  # noqa: PLR0913 - explicit public CLI contract
        task: TaskSelectorArgument,
        reason: CancelReasonOption = None,
        expected_version: ExpectedVersionOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        project: ProjectOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Cancel one mutable Task, optionally recording a reason."""
        values = {
            "task": task,
            "reason": reason,
            "expected_version": 1 if expected_version is None else expected_version,
            "idempotency_key": idempotency_key,
            "project": project,
        }
        _run_simple_mutation(
            session_provider,
            request_type=TaskCancelRequest,
            request_values=values,
            expected_version=expected_version,
            action="cancel it",
            invoke=lambda session, request: session.cancel_task(request),
            json_mode=json_mode,
            non_interactive=non_interactive,
        )

    @application.command("add-dependency")
    def add_dependency(  # noqa: PLR0913 - explicit public CLI contract
        task: TaskSelectorArgument,
        prerequisite: PrerequisiteArgument,
        expected_version: ExpectedVersionOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        project: ProjectOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Add one same-Project prerequisite to a Task."""
        values = {
            "task": task,
            "prerequisite": prerequisite,
            "expected_version": 1 if expected_version is None else expected_version,
            "idempotency_key": idempotency_key,
            "project": project,
        }
        _run_simple_mutation(
            session_provider,
            request_type=TaskAddDependencyRequest,
            request_values=values,
            expected_version=expected_version,
            action=f"add prerequisite {prerequisite}",
            invoke=lambda session, request: session.add_task_dependency(request),
            json_mode=json_mode,
            non_interactive=non_interactive,
        )

    @application.command("remove-dependency")
    def remove_dependency(  # noqa: PLR0913 - explicit public CLI contract
        task: TaskSelectorArgument,
        prerequisite: PrerequisiteArgument,
        expected_version: ExpectedVersionOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        project: ProjectOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Remove one same-Project prerequisite from a Task."""
        values = {
            "task": task,
            "prerequisite": prerequisite,
            "expected_version": 1 if expected_version is None else expected_version,
            "idempotency_key": idempotency_key,
            "project": project,
        }
        _run_simple_mutation(
            session_provider,
            request_type=TaskRemoveDependencyRequest,
            request_values=values,
            expected_version=expected_version,
            action=f"remove prerequisite {prerequisite}",
            invoke=lambda session, request: session.remove_task_dependency(request),
            json_mode=json_mode,
            non_interactive=non_interactive,
        )


def require_explicit_version_for_automation(
    expected_version: int | None,
    *,
    json_mode: bool,
    non_interactive: bool,
) -> None:
    """Reject omitted versions outside an interactive Human terminal.

    Args:
        expected_version: Explicit caller version, when supplied.
        json_mode: Whether machine-readable output was selected.
        non_interactive: Whether interaction was explicitly disabled.

    Raises:
        ExpectedVersionRequiredError: If safe convenience is unavailable.

    """
    if expected_version is None and (
        json_mode or non_interactive or not _is_interactive_terminal()
    ):
        raise ExpectedVersionRequiredError


def prepare_task_mutation(
    provider: SessionProvider,
    *,
    task: str,
    project: str | None,
    expected_version: int | None,
    action: str,
) -> PreparedMutation | None:
    """Acquire one Session and optionally confirm its current exact version.

    Args:
        provider: Command-scoped Session factory.
        task: Caller-selected Task key or UID.
        project: Optional explicit Project selection.
        expected_version: Explicit version or ``None`` for Human convenience.
        action: Stable command-owned intended-action description.

    Returns:
        Prepared Session and exact version, or ``None`` after Human decline.

    """
    session = acquire_session(provider)
    if expected_version is not None:
        return PreparedMutation(session=session, expected_version=expected_version)
    details = session.get_task_details(TaskDetailsRequest(task=task, project=project))
    typer.echo(
        f"{details.task.key}\t{details.task.state.value}"
        f"\tversion={details.task.version}\taction={action}"
    )
    if not typer.confirm("Proceed?", default=False):
        typer.echo("No changes made.")
        return None
    return PreparedMutation(session=session, expected_version=details.task.version)


def _run_simple_mutation[  # noqa: PLR0913 - explicit shared mutation contract
    RequestT: BaseModel,
](
    provider: SessionProvider,
    *,
    request_type: type[RequestT],
    request_values: Mapping[str, object],
    expected_version: int | None,
    action: str,
    invoke: Callable[[WorkaholicSession, RequestT], TaskMutationResult],
    json_mode: bool,
    non_interactive: bool,
) -> None:
    """Validate, prepare, and invoke one non-structured Task mutation.

    Args:
        provider: Command-scoped Session factory.
        request_type: Exact strict Session request model.
        request_values: Caller values with a provisional positive version.
        expected_version: Explicit version when supplied.
        action: Stable Human confirmation description.
        invoke: Explicit typed WorkaholicSession operation.
        json_mode: Whether to emit the automation envelope.
        non_interactive: Whether terminal convenience is disabled.

    """
    _require_version_mode_or_exit(
        expected_version,
        json_mode=json_mode,
        non_interactive=non_interactive,
    )
    try:
        provisional = request_type.model_validate(request_values)
    except ValidationError:
        write_invalid_input("Task-mutation input is invalid.", json_mode=json_mode)
    task_value = request_values.get("task")
    project_value = request_values.get("project")
    if not isinstance(task_value, str) or not (
        project_value is None or isinstance(project_value, str)
    ):
        write_invalid_input("Task-mutation input is invalid.", json_mode=json_mode)
    prepared = _prepare_or_exit(
        provider,
        task=task_value,
        project=project_value,
        expected_version=expected_version,
        action=action,
        json_mode=json_mode,
    )
    if prepared is None:
        return
    request = _replace_expected_version(
        provisional,
        request_type,
        prepared.expected_version,
    )
    _invoke_mutation(
        lambda: invoke(prepared.session, request),
        json_mode=json_mode,
    )


def _require_version_mode_or_exit(
    expected_version: int | None,
    *,
    json_mode: bool,
    non_interactive: bool,
) -> None:
    """Render one stable invalid-input failure for unsafe version omission."""
    try:
        require_explicit_version_for_automation(
            expected_version,
            json_mode=json_mode,
            non_interactive=non_interactive,
        )
    except ExpectedVersionRequiredError:
        write_expected_task_version_required(json_mode=json_mode)


def _prepare_or_exit(  # noqa: PLR0913 - mirrors preparation inputs
    provider: SessionProvider,
    *,
    task: str,
    project: str | None,
    expected_version: int | None,
    action: str,
    json_mode: bool,
) -> PreparedMutation | None:
    """Prepare one mutation while redacting every Session boundary failure."""
    try:
        return prepare_task_mutation(
            provider,
            task=task,
            project=project,
            expected_version=expected_version,
            action=action,
        )
    except Exception as error:  # noqa: BLE001 - redact every boundary failure
        write_failure(error, json_mode=json_mode)


def _replace_expected_version[
    RequestT: BaseModel,
](
    request: RequestT,
    request_type: type[RequestT],
    expected_version: int,
) -> RequestT:
    """Revalidate one provisional request with its confirmed exact version."""
    values = request.model_dump(exclude_unset=True)
    values["expected_version"] = expected_version
    return request_type.model_validate(values)


def _invoke_mutation(
    operation: Callable[[], TaskMutationResult],
    *,
    json_mode: bool,
) -> None:
    """Invoke and render one Task mutation through the safe CLI boundary."""
    try:
        result: object = operation()
        result = _require_task_mutation_result(result)
        write_success(
            task_mutation_data(result) if json_mode else task_summary(result.task),
            json_mode=json_mode,
        )
    except Exception as error:  # noqa: BLE001 - redact every boundary failure
        write_failure(error, json_mode=json_mode)


def _explicit_update_fields(  # noqa: PLR0913 - mirrors public options
    ctx: typer.Context,
    *,
    title: str | None,
    objective: str | None,
    priority: int | None,
    available_at: str | None,
    clear_available_at: bool,
    approval: str | None,
) -> dict[str, object]:
    """Collect only update options explicitly present on the command line."""
    values: dict[str, object] = {
        name: value
        for name, value in (
            ("title", title),
            ("objective", objective),
            ("priority", priority),
            ("available_at", available_at),
            ("approval", approval),
        )
        if option_was_supplied(ctx, name)
    }
    if clear_available_at:
        values["available_at"] = None
    return values


def _require_distinct_availability_options(
    *,
    available_at: str | None,
    clear_available_at: bool,
) -> None:
    """Reject simultaneous set and clear availability intent.

    Args:
        available_at: Optional replacement timestamp.
        clear_available_at: Whether explicit clearing was requested.

    Raises:
        StructuredInputError: If both mutually exclusive options are present.

    """
    if available_at is not None and clear_available_at:
        raise StructuredInputError


def _require_task_mutation_result(value: object) -> TaskMutationResult:
    """Require one exact validated mutation result at the CLI boundary.

    Args:
        value: Candidate Session return value.

    Returns:
        Exact TaskMutationResult.

    Raises:
        TypeError: If the Session violates its public result contract.

    """
    if not isinstance(value, TaskMutationResult):
        raise TypeError
    return value


def _is_interactive_terminal() -> bool:
    """Return whether input and confirmation output share a real terminal."""
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except AttributeError, OSError:
        return False
