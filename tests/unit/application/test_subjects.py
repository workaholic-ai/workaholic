"""Unit tests for backend-neutral Subject application services."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    CreateSubjectMutation,
    IdentityVersionConflictError,
    ListSubjects,
    SetInstanceAdminMutation,
    SetSubjectEnabledMutation,
    SubjectApplication,
    SubjectPage,
    SubjectResult,
    UpdateSubjectMutation,
)
from workaholic.domain import (
    AuditEventId,
    AuthenticatedActor,
    InstanceId,
    RequestId,
    Subject,
    SubjectId,
    SubjectKind,
    TokenId,
)

if TYPE_CHECKING:
    from workaholic.application import (
        Clock,
        IdentityIdentifierFactory,
        SubjectRepository,
    )

_NOW = datetime(2026, 8, 29, 12, tzinfo=UTC)
_ACTOR = AuthenticatedActor(
    instance_id=InstanceId("ins_local"),
    subject_id=SubjectId("sub_admin"),
    subject_kind=SubjectKind.HUMAN,
    token_id=TokenId("tok_admin"),
)
_CREATED_ID = SubjectId("sub_created")


def _subject(  # noqa: PLR0913 - explicit fixture variants aid readability.
    *,
    subject_id: SubjectId = _CREATED_ID,
    handle: str = "worker",
    display_name: str = "Worker",
    enabled: bool = True,
    is_admin: bool = False,
    version: int = 1,
) -> Subject:
    """Build one valid Subject projection.

    Args:
        subject_id: Opaque Subject identity.
        handle: Immutable lookup handle.
        display_name: Mutable presentation name.
        enabled: Current enabled state.
        is_admin: Current Instance-administrator state.
        version: Positive optimistic version.

    Returns:
        Valid Subject fixture.

    """
    return Subject(
        id=subject_id,
        instance_id=_ACTOR.instance_id,
        kind=SubjectKind.AGENT,
        handle=handle,
        display_name=display_name,
        enabled=enabled,
        is_instance_admin=is_admin,
        version=version,
        created_by=_ACTOR.subject_id,
        created_at=_NOW,
        updated_at=_NOW,
    )


class _Clock:
    """Deterministic Subject-service test clock."""

    def now(self) -> datetime:
        """Return the fixed authoritative timestamp."""
        return _NOW


class _Identifiers:
    """Deterministic complete identity identifier factory."""

    def new_subject_id(self) -> SubjectId:
        """Return the candidate Subject identity."""
        return SubjectId("sub_created")

    def new_token_id(self) -> TokenId:
        """Return an unused candidate Token identity."""
        return TokenId("tok_candidate")

    def new_audit_event_id(self) -> AuditEventId:
        """Return an unused candidate AuditEvent identity."""
        return AuditEventId("aev_candidate")

    def new_request_id(self) -> RequestId:
        """Return the candidate request identity."""
        return RequestId("req_subject")


class _Repository:
    """Strict recording Subject repository fake."""

    def __init__(self) -> None:
        """Initialize valid outputs and empty recordings."""
        self.result: object = SubjectResult(subject=_subject())
        self.page: object = SubjectPage(subjects=(_subject(),), next_cursor=None)
        self.calls: list[object] = []

    def create_subject(self, mutation: CreateSubjectMutation) -> object:
        """Record one create mutation."""
        self.calls.append(mutation)
        return self.result

    def list_subjects(self, command: ListSubjects) -> object:
        """Record one list command."""
        self.calls.append(command)
        return self.page

    def update_subject(self, mutation: UpdateSubjectMutation) -> object:
        """Record one display-name mutation."""
        self.calls.append(mutation)
        return self.result

    def set_subject_enabled(self, mutation: SetSubjectEnabledMutation) -> object:
        """Record one enabled-state mutation."""
        self.calls.append(mutation)
        return self.result

    def set_instance_admin(self, mutation: SetInstanceAdminMutation) -> object:
        """Record one administrator-state mutation."""
        self.calls.append(mutation)
        return self.result


def _application(
    repository: _Repository,
    *,
    clock: object | None = None,
    identifiers: object | None = None,
) -> SubjectApplication:
    """Construct SubjectApplication with explicitly cast fakes.

    Args:
        repository: Recording Subject repository.
        clock: Optional clock override.
        identifiers: Optional identifier-factory override.

    Returns:
        Configured Subject application.

    """
    return SubjectApplication(
        repository=cast("SubjectRepository", repository),
        clock=cast("Clock", _Clock() if clock is None else clock),
        identifiers=cast(
            "IdentityIdentifierFactory",
            _Identifiers() if identifiers is None else identifiers,
        ),
    )


def test_create_owns_subject_request_and_time_and_preserves_idempotency() -> None:
    """Subject creation constructs one complete validated mutation."""
    repository = _Repository()
    application = _application(repository)

    result = application.create(
        actor=_ACTOR,
        kind=SubjectKind.AGENT,
        handle="worker",
        display_name="  Worker  ",
        idempotency_key="subject-create-1",
    )

    assert result is repository.result
    assert repository.calls == [
        CreateSubjectMutation(
            actor=_ACTOR,
            request_id=RequestId("req_subject"),
            occurred_at=_NOW,
            idempotency_key="subject-create-1",
            subject_id=SubjectId("sub_created"),
            kind=SubjectKind.AGENT,
            handle="worker",
            display_name="Worker",
        )
    ]


def test_list_builds_exact_page_query_and_rejects_cross_instance_output() -> None:
    """Subject listing forwards pagination and validates Instance scope."""
    repository = _Repository()
    application = _application(repository)

    page = application.list(actor=_ACTOR, cursor="cursor-1", limit=25)

    assert page is repository.page
    assert repository.calls == [ListSubjects(actor=_ACTOR, cursor="cursor-1", limit=25)]
    repository.page = SubjectPage(
        subjects=(replace(_subject(), instance_id=InstanceId("ins_other")),),
        next_cursor=None,
    )
    with pytest.raises(ApplicationError) as captured:
        application.list(actor=_ACTOR)
    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


def test_update_enable_and_admin_methods_construct_exact_optimistic_mutations() -> None:
    """Every existing-Subject mutation owns request/time and preserves version."""
    repository = _Repository()
    application = _application(repository)

    repository.result = SubjectResult(
        subject=_subject(display_name="Renamed", version=2)
    )
    application.update(
        actor=_ACTOR,
        subject=SubjectId("sub_created"),
        expected_version=1,
        display_name="Renamed",
        idempotency_key="subject-update",
    )
    repository.result = SubjectResult(subject=_subject(enabled=False, version=3))
    application.set_enabled(
        actor=_ACTOR,
        subject="worker",
        expected_version=2,
        enabled=False,
    )
    repository.result = SubjectResult(subject=_subject(is_admin=True, version=4))
    application.set_instance_admin(
        actor=_ACTOR,
        subject=SubjectId("sub_created"),
        expected_version=3,
        is_instance_admin=True,
    )

    assert repository.calls == [
        UpdateSubjectMutation(
            actor=_ACTOR,
            request_id=RequestId("req_subject"),
            occurred_at=_NOW,
            idempotency_key="subject-update",
            subject=SubjectId("sub_created"),
            expected_version=1,
            display_name="Renamed",
        ),
        SetSubjectEnabledMutation(
            actor=_ACTOR,
            request_id=RequestId("req_subject"),
            occurred_at=_NOW,
            subject="worker",
            expected_version=2,
            enabled=False,
        ),
        SetInstanceAdminMutation(
            actor=_ACTOR,
            request_id=RequestId("req_subject"),
            occurred_at=_NOW,
            subject=SubjectId("sub_created"),
            expected_version=3,
            is_instance_admin=True,
        ),
    ]


def test_invalid_input_dependency_and_output_fail_before_unsafe_progress() -> None:
    """Runtime guards distinguish caller, dependency, and persistence failures."""
    repository = _Repository()
    with pytest.raises(ApplicationError) as invalid_input:
        _application(repository).update(
            actor=_ACTOR,
            subject="worker",
            expected_version=0,
            display_name="Worker",
        )

    class _InvalidIdentifiers(_Identifiers):
        """Identifier factory returning a wrong Subject ID type."""

        def new_subject_id(self) -> SubjectId:
            """Return a deliberately invalid runtime value."""
            return cast("SubjectId", "sub_wrong")

    with pytest.raises(ApplicationError) as invalid_dependency:
        _application(repository, identifiers=_InvalidIdentifiers()).create(
            actor=_ACTOR,
            kind=SubjectKind.AGENT,
            handle="worker",
        )
    repository.result = object()
    with pytest.raises(ApplicationError) as invalid_output:
        _application(repository).create(
            actor=_ACTOR,
            kind=SubjectKind.AGENT,
            handle="worker",
        )

    assert invalid_input.value.code is ApplicationErrorCode.INVALID_INPUT
    assert invalid_dependency.value.code is ApplicationErrorCode.INTERNAL_ERROR
    assert invalid_output.value.code is ApplicationErrorCode.INTERNAL_ERROR


def test_repository_errors_pass_through_and_constructor_checks_dependencies() -> None:
    """Stable persistence failures propagate and malformed fakes fail at wiring."""

    class _Conflicting(_Repository):
        """Repository exposing an optimistic conflict."""

        def update_subject(self, _mutation: UpdateSubjectMutation) -> object:
            """Raise the stable identity conflict."""
            raise IdentityVersionConflictError

    with pytest.raises(IdentityVersionConflictError):
        _application(_Conflicting()).update(
            actor=_ACTOR,
            subject="worker",
            expected_version=1,
            display_name="Renamed",
        )
    with pytest.raises(TypeError, match="Identity"):
        SubjectApplication(
            repository=cast("SubjectRepository", object()),
            clock=cast("Clock", _Clock()),
            identifiers=cast("IdentityIdentifierFactory", _Identifiers()),
        )


def test_create_rejects_identifier_failure_and_invalid_subject_input() -> None:
    """Subject creation maps dependency and caller failures to stable errors."""

    class _FailingIdentifiers(_Identifiers):
        """Identifier factory that cannot allocate a Subject identity."""

        def new_subject_id(self) -> SubjectId:
            """Raise the simulated allocation failure."""
            message = "identifier source unavailable"
            raise RuntimeError(message)

    repository = _Repository()
    with pytest.raises(ApplicationError) as dependency_error:
        _application(repository, identifiers=_FailingIdentifiers()).create(
            actor=_ACTOR,
            kind=SubjectKind.AGENT,
            handle="worker",
        )
    with pytest.raises(ApplicationError) as input_error:
        _application(repository).create(
            actor=_ACTOR,
            kind=cast("SubjectKind", "robot"),
            handle="worker",
        )

    assert dependency_error.value.code is ApplicationErrorCode.INTERNAL_ERROR
    assert input_error.value.code is ApplicationErrorCode.INVALID_INPUT
    assert repository.calls == []


def test_subject_queries_and_mutations_reject_invalid_commands() -> None:
    """Every public Subject operation validates its runtime command contract."""
    application = _application(_Repository())

    invalid_calls = (
        lambda: application.list(actor=_ACTOR, limit=0),
        lambda: application.set_enabled(
            actor=_ACTOR,
            subject="worker",
            expected_version=0,
            enabled=False,
        ),
        lambda: application.set_instance_admin(
            actor=_ACTOR,
            subject="worker",
            expected_version=0,
            is_instance_admin=True,
        ),
    )
    for invalid_call in invalid_calls:
        with pytest.raises(ApplicationError) as captured:
            invalid_call()
        assert captured.value.code is ApplicationErrorCode.INVALID_INPUT


@pytest.mark.parametrize("operation", ["update", "enabled", "admin"])
def test_existing_subject_mutations_reject_malformed_repository_output(
    operation: str,
) -> None:
    """Optimistic Subject mutations fail closed on a malformed result type."""
    repository = _Repository()
    repository.result = object()
    application = _application(repository)

    operation_calls = {
        "update": lambda: application.update(
            actor=_ACTOR,
            subject="worker",
            expected_version=1,
            display_name="Renamed",
        ),
        "enabled": lambda: application.set_enabled(
            actor=_ACTOR,
            subject="worker",
            expected_version=1,
            enabled=False,
        ),
        "admin": lambda: application.set_instance_admin(
            actor=_ACTOR,
            subject="worker",
            expected_version=1,
            is_instance_admin=True,
        ),
    }
    with pytest.raises(ApplicationError) as captured:
        operation_calls[operation]()

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


def test_subject_list_rejects_malformed_repository_output() -> None:
    """Subject listing rejects a repository value outside its result contract."""
    repository = _Repository()
    repository.page = object()

    with pytest.raises(ApplicationError) as captured:
        _application(repository).list(actor=_ACTOR)

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
