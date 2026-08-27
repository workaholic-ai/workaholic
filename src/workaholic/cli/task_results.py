"""Human and Agent Task submission plus Human review CLI commands."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer
from pydantic import ValidationError

from workaholic.cli.errors import (
    write_failure,
    write_invalid_input,
)
from workaholic.cli.options import (  # noqa: TC001 - Typer resolves aliases
    AttemptOption,
    ExpectedVersionOption,
    IdempotencyKeyOption,
    JsonOption,
    NonInteractiveOption,
    ProjectOption,
    TaskSelectorArgument,
)
from workaholic.cli.rendering import write_success
from workaholic.cli.runtime import (
    SessionProvider,
    acquire_session,
)
from workaholic.cli.serialization import (
    agent_submission_data,
    agent_submission_summary,
    task_submission_data,
    task_submission_summary,
)
from workaholic.cli.structured_input import (
    StructuredInputError,
    load_required_structured_object,
    load_structured_object,
    merge_structured_fields,
)
from workaholic.cli.task_mutations import (
    prepare_task_mutation_or_exit,
    replace_task_expected_version,
    require_task_mutation_version_or_exit,
)
from workaholic.domain import AttemptId, DomainValidationError
from workaholic.session import (
    AgentSubmitRequest,
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
        attempt: AttemptOption = None,
        comment: CommentOption = None,
        result_file: ResultFileOption = None,
        expected_version: ExpectedVersionOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        project: ProjectOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Submit Human work or one exact Agent Attempt's structured Result."""
        if attempt is not None:
            _submit_agent_task(
                session_provider,
                task=task,
                attempt=attempt,
                comment=comment,
                result_file=result_file,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
                project=project,
                json_mode=json_mode,
            )
            return
        _submit_human_task(
            session_provider,
            task=task,
            comment=comment,
            result_file=result_file,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            project=project,
            json_mode=json_mode,
            non_interactive=non_interactive,
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


def _submit_human_task(  # noqa: PLR0913 - explicit dispatch boundary
    session_provider: SessionProvider,
    *,
    task: str,
    comment: str | None,
    result_file: str | None,
    expected_version: int | None,
    idempotency_key: str | None,
    project: str | None,
    json_mode: bool,
    non_interactive: bool,
) -> None:
    """Validate and submit direct Human work with existing convenience.

    Args:
        session_provider: Command-scoped Session factory.
        task: Selected Task key or UID.
        comment: Optional Human comment.
        result_file: Optional structured Result source.
        expected_version: Explicit version or Human convenience omission.
        idempotency_key: Optional replay key.
        project: Optional explicit Project key.
        json_mode: Whether machine-readable output was selected.
        non_interactive: Whether Human interaction is disabled.

    """
    require_task_mutation_version_or_exit(
        expected_version,
        json_mode=json_mode,
        non_interactive=non_interactive,
    )
    try:
        file_values = {} if result_file is None else load_structured_object(result_file)
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
                "expected_version": 1 if expected_version is None else expected_version,
                "idempotency_key": idempotency_key,
                "project": project,
            }
        )
    except StructuredInputError, ValidationError:
        write_invalid_input("Task-submission input is invalid.", json_mode=json_mode)
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


def _submit_agent_task(  # noqa: PLR0913 - explicit dispatch boundary
    session_provider: SessionProvider,
    *,
    task: str,
    attempt: str,
    comment: str | None,
    result_file: str | None,
    expected_version: int | None,
    idempotency_key: str | None,
    project: str | None,
    json_mode: bool,
) -> None:
    """Validate and submit one exact Agent Attempt without interaction.

    Args:
        session_provider: Command-scoped Session factory.
        task: Selected Task key or UID.
        attempt: Exact Agent Attempt owner token.
        comment: Human-only option, which must be absent.
        result_file: Required structured Result source.
        expected_version: Required positive claimed Task version.
        idempotency_key: Optional replay key.
        project: Optional explicit Project key.
        json_mode: Whether machine-readable output was selected.

    """
    if expected_version is None:
        write_invalid_input(
            "Agent submission requires --expected-version.",
            json_mode=json_mode,
        )
    try:
        _require_absent_agent_comment(comment)
        result_values = merge_structured_fields(
            file_values=load_required_structured_object(result_file),
            inline_values={},
            allowed_fields=_RESULT_FILE_FIELDS,
        )
        request = AgentSubmitRequest.model_validate(
            {
                "task": task,
                "attempt": AttemptId(attempt),
                "result": result_values,
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
                "project": project,
            }
        )
    except StructuredInputError, DomainValidationError, ValidationError:
        write_invalid_input("Task-submission input is invalid.", json_mode=json_mode)
    _invoke_agent_submission(
        lambda: acquire_session(session_provider).submit_agent_result(request),
        json_mode=json_mode,
    )


def _require_absent_agent_comment(comment: str | None) -> None:
    """Reject the Human-only comment option on Agent submission.

    Args:
        comment: Optional CLI comment value.

    Raises:
        StructuredInputError: If a Human comment was supplied.

    """
    if comment is not None:
        raise StructuredInputError


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


def _invoke_agent_submission(
    operation: Callable[[], TaskSubmissionResult],
    *,
    json_mode: bool,
) -> None:
    """Invoke and safely render one Attempt-backed Agent submission.

    Args:
        operation: Deferred Session call returning one exact Agent result.
        json_mode: Whether to emit the automation envelope.

    """
    try:
        result = _validated_submission_result(operation())
        write_success(
            (
                agent_submission_data(result)
                if json_mode
                else agent_submission_summary(result)
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
