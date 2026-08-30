"""Unit tests for backend-neutral administrative audit application service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    AuditApplication,
    AuditEventPage,
    AuditEventResult,
    PermissionDeniedError,
    ReadAuditEvents,
)
from workaholic.domain import (
    AuditEventId,
    AuditEventType,
    AuthenticatedActor,
    InstanceId,
    RequestId,
    SubjectId,
    SubjectKind,
    TokenId,
)

if TYPE_CHECKING:
    from workaholic.application import AuditRepository

_NOW = datetime(2026, 8, 29, 12, tzinfo=UTC)
_ACTOR = AuthenticatedActor(
    instance_id=InstanceId("ins_local"),
    subject_id=SubjectId("sub_admin"),
    subject_kind=SubjectKind.HUMAN,
    token_id=TokenId("tok_admin"),
)


def _event(*, cursor: int = 4) -> AuditEventResult:
    """Build one valid administrative AuditEvent result.

    Args:
        cursor: Positive monotonic Instance cursor.

    Returns:
        Valid non-secret audit result.

    """
    return AuditEventResult(
        id=AuditEventId(f"aev_{cursor}"),
        cursor=cursor,
        instance_id=_ACTOR.instance_id,
        actor_subject_id=_ACTOR.subject_id,
        actor_kind=_ACTOR.subject_kind,
        actor_token_id=_ACTOR.token_id,
        request_id=RequestId("req_audit"),
        event_type=AuditEventType.SUBJECT_CREATED,
        occurred_at=_NOW,
        payload={
            "subject_id": "sub_created",
            "handle": "worker",
            "kind": "agent",
            "version": 1,
        },
    )


class _Repository:
    """Strict recording Audit repository fake."""

    def __init__(self) -> None:
        """Initialize one valid page and no calls."""
        self.result: object = AuditEventPage(events=(_event(),), next_cursor=4)
        self.commands: list[ReadAuditEvents] = []

    def read_audit_events(self, command: ReadAuditEvents) -> object:
        """Record one query and return configured output."""
        self.commands.append(command)
        return self.result


def _application(repository: _Repository) -> AuditApplication:
    """Construct AuditApplication with an explicitly cast strict fake.

    Args:
        repository: Recording audit repository.

    Returns:
        Configured audit application.

    """
    return AuditApplication(repository=cast("AuditRepository", repository))


def test_read_builds_exact_cursor_query_and_returns_scoped_page() -> None:
    """Audit service preserves cursor and page-size semantics exactly."""
    repository = _Repository()

    page = _application(repository).read(actor=_ACTOR, after=3, limit=25)

    assert page is repository.result
    assert repository.commands == [ReadAuditEvents(actor=_ACTOR, after=3, limit=25)]


def test_invalid_input_stops_before_repository_io() -> None:
    """Negative cursors are rejected by the application command boundary."""
    repository = _Repository()
    with pytest.raises(ApplicationError) as captured:
        _application(repository).read(actor=_ACTOR, after=-1)
    assert captured.value.code is ApplicationErrorCode.INVALID_INPUT
    assert repository.commands == []


@pytest.mark.parametrize("failure", ["wrong-type", "wrong-instance", "old-cursor"])
def test_invalid_repository_output_fails_closed(failure: str) -> None:
    """Audit output is checked for type, Instance scope, and cursor progress."""
    repository = _Repository()
    if failure == "wrong-type":
        repository.result = object()
    elif failure == "wrong-instance":
        repository.result = AuditEventPage(
            events=(
                _event().model_copy(
                    update={"instance_id": InstanceId("ins_other")},
                ),
            ),
            next_cursor=4,
        )
    else:
        repository.result = AuditEventPage(events=(), next_cursor=2)

    with pytest.raises(ApplicationError) as captured:
        _application(repository).read(actor=_ACTOR, after=3)
    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


def test_stable_authorization_failure_passes_through() -> None:
    """Repository permission denial is preserved for Session error mapping."""

    class _Denied(_Repository):
        """Audit repository denying the actor."""

        def read_audit_events(self, _command: ReadAuditEvents) -> object:
            """Raise the stable permission failure."""
            raise PermissionDeniedError

    with pytest.raises(PermissionDeniedError):
        _application(_Denied()).read(actor=_ACTOR)


def test_constructor_runtime_validates_repository_contract() -> None:
    """Composition rejects a repository without the audit query operation."""
    with pytest.raises(TypeError, match="Identity"):
        AuditApplication(repository=cast("AuditRepository", object()))
