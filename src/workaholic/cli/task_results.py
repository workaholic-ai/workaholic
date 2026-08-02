"""Human Task submission and review CLI commands."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer
from pydantic import ValidationError

from workaholic.cli.errors import write_failure, write_invalid_input
from workaholic.cli.options import (  # noqa: TC001 - Typer resolves aliases
    ExpectedVersionOption,
    IdempotencyKeyOption,
    JsonOption,
    NonInteractiveOption,
    ProjectOption,
    TaskSelectorArgument,
)
from workaholic.cli.rendering import write_success
from workaholic.cli.runtime import SessionProvider  # noqa: TC001 - public callback
from workaholic.cli.serialization import (
    task_submission_data,
    task_submission_summary,
)
from workaholic.cli.structured_input import (
    StructuredInputError,
    load_structured_object,
    merge_structured_fields,
)
from workaholic.cli.task_mutations import (
    prepare_task_mutation_or_exit,
    replace_task_expected_version,
    require_task_mutation_version_or_exit,
)
from workaholic.session import (
    TaskApproveRequest,
    TaskRejectRequest,
    TaskSubmissionResult,
    TaskSubmitRequest,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from workaholic.session import WorkaholicSession

_RESULT_FILE_FIELDS = frozenset(
    (
        "summary",
        "criteria",
        "artifacts",
        "proposed_follow_ups",
    )
)

ResultFileOption = Annotated[
    str | None,
    typer.Option(
        ...,
        "--result-file",
        help="Read bounded structured Result input from PATH, or stdin with '-'.",
        metavar="PATH|-",
        prompt=False,
    ),
]
CommentOption = Annotated[
    str | None,
    typer.Option(
        ...,
        "--comment",
        help="Optionally record a Human submission or approval comment.",
        metavar="TEXT",
        prompt=False,
    ),
]
RejectReasonOption = Annotated[
    str,
    typer.Option(
        ...,
        "--reason",
        help="Explain why the pending Result is rejected.",
        metavar="TEXT",
        prompt=False,
    ),
]


def register_task_result_commands(
    application: typer.Typer,
    *,
    session_provider: SessionProvider,
) -> None:
    """Register direct Human submission and review commands.

    Args:
        application: Task Typer command group.
        session_provider: Command-scoped Session factory.

    """

    @application.command("submit")
    def submit_task(  # noqa: PLR0913 - explicit public CLI contract
        task: TaskSelectorArgument,
        comment: CommentOption = None,
        result_file: ResultFileOption = None,
        expected_version: ExpectedVersionOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        project: ProjectOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Submit Human work directly without an Agent Attempt."""
        require_task_mutation_version_or_exit(
            expected_version,
            json_mode=json_mode,
            non_interactive=non_interactive,
        )
        try:
            file_values = (
                {} if result_file is None else load_structured_object(result_file)
            )
            result_values = merge_structured_fields(
                file_values=file_values,
                inline_values={},
                allowed_fields=_RESULT_FILE_FIELDS,
            )
            provisional = TaskSubmitRequest.model_validate(
                {
                    "task": task,
                    "comment": comment,
                    "result": result_values,
                    "expected_version": (
                        1 if expected_version is None else expected_version
                    ),
                    "idempotency_key": idempotency_key,
                    "project": project,
                }
            )
        except StructuredInputError, ValidationError:
            write_invalid_input(
                "Task-submission input is invalid.", json_mode=json_mode
            )
        prepared = prepare_task_mutation_or_exit(
            session_provider,
            task=task,
            project=project,
            expected_version=expected_version,
            action="submit Human work",
            json_mode=json_mode,
        )
        if prepared is None:
            return
        request = replace_task_expected_version(
            provisional,
            TaskSubmitRequest,
            prepared.expected_version,
        )
        _invoke_submission(
            lambda: prepared.session.submit_human_result(request),
            json_mode=json_mode,
        )

    @application.command("approve")
    def approve_task(  # noqa: PLR0913 - explicit public CLI contract
        task: TaskSelectorArgument,
        comment: CommentOption = None,
        expected_version: ExpectedVersionOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        project: ProjectOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Approve the current pending Result and complete its Task."""
        _run_review_mutation(
            session_provider,
            task=task,
            project=project,
            request_type=TaskApproveRequest,
            request_values={
                "task": task,
                "comment": comment,
                "expected_version": (
                    1 if expected_version is None else expected_version
                ),
                "idempotency_key": idempotency_key,
                "project": project,
            },
            expected_version=expected_version,
            action="approve its current Result",
            invoke=lambda session, request: session.approve_result(request),
            json_mode=json_mode,
            non_interactive=non_interactive,
        )

    @application.command("reject")
    def reject_task(  # noqa: PLR0913 - explicit public CLI contract
        task: TaskSelectorArgument,
        reason: RejectReasonOption,
        expected_version: ExpectedVersionOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        project: ProjectOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Reject the current pending Result and reopen its Task."""
        _run_review_mutation(
            session_provider,
            task=task,
            project=project,
            request_type=TaskRejectRequest,
            request_values={
                "task": task,
                "reason": reason,
                "expected_version": (
                    1 if expected_version is None else expected_version
                ),
                "idempotency_key": idempotency_key,
                "project": project,
            },
            expected_version=expected_version,
            action="reject its current Result",
            invoke=lambda session, request: session.reject_result(request),
            json_mode=json_mode,
            non_interactive=non_interactive,
        )


def _run_review_mutation[  # noqa: PLR0913 - shared explicit review contract
    RequestT: (TaskApproveRequest, TaskRejectRequest),
](
    provider: SessionProvider,
    *,
    task: str,
    project: str | None,
    request_type: type[RequestT],
    request_values: Mapping[str, object],
    expected_version: int | None,
    action: str,
    invoke: Callable[[WorkaholicSession, RequestT], TaskSubmissionResult],
    json_mode: bool,
    non_interactive: bool,
) -> None:
    """Validate, confirm, and invoke one optimistic Result review mutation.

    Args:
        provider: Command-scoped Session factory.
        task: Caller-selected Task key or UID.
        project: Optional explicit Project selection.
        request_type: Exact strict Session request model.
        request_values: Caller values with a provisional positive version.
        expected_version: Explicit version when supplied.
        action: Stable Human confirmation description.
        invoke: Explicit typed WorkaholicSession operation.
        json_mode: Whether to emit the automation envelope.
        non_interactive: Whether terminal convenience is disabled.

    """
    require_task_mutation_version_or_exit(
        expected_version,
        json_mode=json_mode,
        non_interactive=non_interactive,
    )
    try:
        provisional = request_type.model_validate(request_values)
    except ValidationError:
        write_invalid_input("Task-review input is invalid.", json_mode=json_mode)
    prepared = prepare_task_mutation_or_exit(
        provider,
        task=task,
        project=project,
        expected_version=expected_version,
        action=action,
        json_mode=json_mode,
    )
    if prepared is None:
        return
    request = replace_task_expected_version(
        provisional,
        request_type,
        prepared.expected_version,
    )
    _invoke_submission(
        lambda: invoke(prepared.session, request),
        json_mode=json_mode,
    )


def _invoke_submission(
    operation: Callable[[], TaskSubmissionResult],
    *,
    json_mode: bool,
) -> None:
    """Invoke and safely render one Human submission or review transition.

    Args:
        operation: Deferred Session call returning one exact result.
        json_mode: Whether to emit the automation envelope.

    """
    try:
        result = _validated_submission_result(operation())
        write_success(
            (
                task_submission_data(result)
                if json_mode
                else task_submission_summary(result)
            ),
            json_mode=json_mode,
        )
    except Exception as error:  # noqa: BLE001 - redact every boundary failure
        write_failure(error, json_mode=json_mode)


def _validated_submission_result(value: object) -> TaskSubmissionResult:
    """Require the exact validated Session submission result contract.

    Args:
        value: Candidate Session return value.

    Returns:
        Validated Task submission or review result.

    Raises:
        TypeError: If the Session violates its declared result contract.

    """
    if not isinstance(value, TaskSubmissionResult):
        raise TypeError
    return value
