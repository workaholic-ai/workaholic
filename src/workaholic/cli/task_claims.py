"""CLI commands for Human and Agent Task Claim ownership."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer
from pydantic import ValidationError

from workaholic.cli.durations import (
    LeaseDurationError,
    LeaseOwner,
    parse_lease_duration,
)
from workaholic.cli.errors import write_failure, write_invalid_input
from workaholic.cli.options import (  # noqa: TC001 - Typer resolves aliases
    AttemptOption,
    IdempotencyKeyOption,
    JsonOption,
    LeaseOption,
    NonInteractiveOption,
    ProjectOption,
    TaskSelectorArgument,
)
from workaholic.cli.rendering import write_success
from workaholic.cli.runtime import SessionProvider, acquire_session
from workaholic.cli.serialization import task_claim_result_data, task_claim_summary
from workaholic.domain import AttemptId, DomainValidationError
from workaholic.session import (
    AgentHeartbeatRequest,
    AgentReleaseRequest,
    AgentTaskClaimRequest,
    HumanClaimReleaseRequest,
    HumanClaimRenewRequest,
    HumanTaskClaimRequest,
    TaskClaimResult,
)

if TYPE_CHECKING:
    from datetime import timedelta

OptionalTaskSelectorArgument = Annotated[
    str | None,
    typer.Argument(
        ...,
        help="Task to claim as a Human; omit it to pull the next Task as an Agent.",
        metavar="[TASK]",
    ),
]


def register_task_claim_commands(  # noqa: PLR0915 - explicit CLI command set
    application: typer.Typer,
    *,
    session_provider: SessionProvider,
) -> None:
    """Register ownership commands against an explicit Session provider.

    Args:
        application: Task Typer command group.
        session_provider: Command-scoped Session factory.

    """

    @application.command("claim")
    def claim_task(  # noqa: PLR0913 - explicit public CLI contract
        task: OptionalTaskSelectorArgument = None,
        lease: LeaseOption = None,
        project: ProjectOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002 - Typer option
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Claim one Task as a Human or pull the next ready Task as an Agent."""
        del non_interactive
        try:
            if task is None:
                agent_request = AgentTaskClaimRequest(
                    lease=_optional_lease(lease, owner=LeaseOwner.AGENT),
                    idempotency_key=idempotency_key,
                    project=project,
                )
                result = acquire_session(session_provider).claim_next_task(
                    agent_request
                )
            else:
                human_request = HumanTaskClaimRequest(
                    task=task,
                    lease=_optional_lease(lease, owner=LeaseOwner.HUMAN),
                    idempotency_key=idempotency_key,
                    project=project,
                )
                result = acquire_session(session_provider).claim_task(human_request)
        except LeaseDurationError, DomainValidationError, ValidationError:
            write_invalid_input("Task-claim input is invalid.", json_mode=json_mode)
        except Exception as error:  # noqa: BLE001 - redact every boundary failure
            write_failure(error, json_mode=json_mode)
        _write_claim_result(result, json_mode=json_mode)

    @application.command("renew")
    def renew_claim(  # noqa: PLR0913 - explicit public CLI contract
        task: TaskSelectorArgument,
        lease: LeaseOption = None,
        project: ProjectOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002 - Typer option
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Renew the current Human Claim on one Task."""
        del non_interactive
        try:
            request = HumanClaimRenewRequest(
                task=task,
                lease=_optional_lease(lease, owner=LeaseOwner.HUMAN),
                idempotency_key=idempotency_key,
                project=project,
            )
            result = acquire_session(session_provider).renew_claim(request)
        except LeaseDurationError, DomainValidationError, ValidationError:
            write_invalid_input("Task-renew input is invalid.", json_mode=json_mode)
        except Exception as error:  # noqa: BLE001 - redact every boundary failure
            write_failure(error, json_mode=json_mode)
        _write_claim_result(result, json_mode=json_mode)

    @application.command("heartbeat")
    def heartbeat_attempt(  # noqa: PLR0913 - explicit public CLI contract
        task: TaskSelectorArgument,
        attempt: AttemptOption = None,
        lease: LeaseOption = None,
        project: ProjectOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002 - Typer option
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Renew the Claim held by one exact active Agent Attempt."""
        del non_interactive
        try:
            request = AgentHeartbeatRequest(
                task=task,
                attempt=AttemptId(attempt or ""),
                lease=_optional_lease(lease, owner=LeaseOwner.AGENT),
                idempotency_key=idempotency_key,
                project=project,
            )
            result = acquire_session(session_provider).heartbeat_attempt(request)
        except LeaseDurationError, DomainValidationError, ValidationError:
            write_invalid_input("Task-heartbeat input is invalid.", json_mode=json_mode)
        except Exception as error:  # noqa: BLE001 - redact every boundary failure
            write_failure(error, json_mode=json_mode)
        _write_claim_result(result, json_mode=json_mode)

    @application.command("release")
    def release_claim(  # noqa: PLR0913 - explicit public CLI contract
        task: TaskSelectorArgument,
        attempt: AttemptOption = None,
        project: ProjectOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002 - Typer option
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Release one Human Claim or exact Agent Attempt."""
        del non_interactive
        try:
            if attempt is None:
                human_request = HumanClaimReleaseRequest(
                    task=task,
                    idempotency_key=idempotency_key,
                    project=project,
                )
                result = acquire_session(session_provider).release_claim(human_request)
            else:
                agent_request = AgentReleaseRequest(
                    task=task,
                    attempt=AttemptId(attempt),
                    idempotency_key=idempotency_key,
                    project=project,
                )
                result = acquire_session(session_provider).release_attempt(
                    agent_request
                )
        except DomainValidationError, ValidationError:
            write_invalid_input("Task-release input is invalid.", json_mode=json_mode)
        except Exception as error:  # noqa: BLE001 - redact every boundary failure
            write_failure(error, json_mode=json_mode)
        _write_claim_result(result, json_mode=json_mode)


def _optional_lease(
    value: str | None,
    *,
    owner: LeaseOwner,
) -> timedelta | None:
    """Parse an optional CLI Lease while preserving domain-owned defaults.

    Args:
        value: Optional explicit duration text.
        owner: Ownership path selecting the applicable bounds.

    Returns:
        Parsed duration, or ``None`` for the domain default.

    """
    return None if value is None else parse_lease_duration(value, owner=owner)


def _write_claim_result(result: TaskClaimResult, *, json_mode: bool) -> None:
    """Render one validated Session Claim result.

    Args:
        result: Session-returned Claim operation result.
        json_mode: Whether to emit the public JSON envelope.

    """
    try:
        data = task_claim_result_data(result)
        human_result = task_claim_summary(result)
        write_success(data if json_mode else human_result, json_mode=json_mode)
    except Exception as error:  # noqa: BLE001 - redact invalid Session results
        write_failure(error, json_mode=json_mode)
