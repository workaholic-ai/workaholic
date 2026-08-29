"""Unit tests for strict SQLite scalar and serialization helpers."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.domain import (
    AcceptanceCriterion,
    ApprovalRequirement,
    ArtifactReference,
    AttemptId,
    AttemptStatus,
    ContextReference,
    CriterionOutcome,
    CriterionStatus,
    InstanceId,
    Project,
    ProjectId,
    ProposedFollowUp,
    RequestId,
    ResultId,
    ResultReview,
    ResultReviewStatus,
    SubjectId,
    SubjectKind,
    Task,
    TaskAttempt,
    TaskClaim,
    TaskEvent,
    TaskEventId,
    TaskEventType,
    TaskId,
    TaskResult,
    TaskState,
)
from workaholic.persistence.sqlite._claim_records import (
    TASK_ATTEMPT_FIELDS,
    TASK_CLAIM_FIELDS,
    TaskAttemptRecord,
    TaskClaimRecord,
    task_attempt_record_from_mapping,
    task_attempt_record_from_row,
    task_attempt_record_mapping,
    task_attempt_row,
    task_claim_record_from_mapping,
    task_claim_record_from_row,
    task_claim_record_mapping,
    task_claim_row,
)
from workaholic.persistence.sqlite._event_records import (
    TASK_EVENT_FIELDS,
    TaskEventRecord,
    load_task_event_record,
    require_persisted_task_event_record,
    task_event_record_from_mapping,
    task_event_record_from_row,
    task_event_record_mapping,
    task_event_row,
)
from workaholic.persistence.sqlite._records import (
    EVENT_PAYLOAD_JSON_MAX_LENGTH,
    PROJECT_FIELDS,
    STRUCTURED_COLLECTION_JSON_MAX_LENGTH,
    canonical_json,
    canonical_json_value,
    parse_json_array,
    parse_json_object,
    parse_optional_timestamp,
    parse_timestamp,
    project_from_mapping,
    project_from_row,
    project_to_mapping,
    require_boolean,
    require_integer,
    require_optional_text,
    require_text,
    serialize_timestamp,
)
from workaholic.persistence.sqlite._result_records import (
    TASK_RESULT_FIELDS,
    task_result_from_mapping,
    task_result_from_row,
    task_result_mapping,
    task_result_row,
)
from workaholic.persistence.sqlite._task_records import (
    TASK_FIELDS,
    task_from_mapping,
    task_from_row,
    task_mapping,
    task_row,
)
from workaholic.persistence.sqlite.errors import StorageUnavailableError

if TYPE_CHECKING:
    import sqlite3

_NOW = datetime(2026, 8, 1, 12, 15, 30, 654321, tzinfo=UTC)


class _EmptyCursor:
    """Return no row for one controlled event lookup."""

    def fetchone(self) -> None:
        """Return an absent event record.

        Returns:
            Always ``None``.

        """
        return


class _EmptyConnection:
    """Expose the minimal empty event lookup connection contract."""

    def execute(
        self,
        statement: str,
        parameters: tuple[object, ...],
    ) -> _EmptyCursor:
        """Validate query inputs and return an empty cursor.

        Args:
            statement: Closed lookup SQL.
            parameters: Exact event identity parameter.

        Returns:
            Cursor containing no row.

        """
        assert statement
        assert parameters
        return _EmptyCursor()


def _project() -> Project:
    """Build one canonical named Project record fixture.

    Returns:
        Valid immutable Project.

    """
    return Project(
        id=ProjectId("prj_acme"),
        instance_id=InstanceId("ins_local"),
        key="ACME",
        name="Acme Platform",
        created_at=datetime(2026, 7, 30, 12, 15, 30, 654321, tzinfo=UTC),
    )


def _task() -> Task:
    """Build one complete Phase 3 Task record fixture."""
    return Task(
        uid=TaskId("tsk_primary"),
        project_id=ProjectId("prj_acme"),
        number=7,
        key="ACME-7",
        title="Complete persistence",
        objective="Verify every durable field.",
        state=TaskState.BLOCKED,
        priority=80,
        version=4,
        created_by=SubjectId("sub_human"),
        created_at=_NOW,
        updated_at=_NOW,
        available_at=_NOW,
        approval=ApprovalRequirement.HUMAN,
        acceptance=(
            AcceptanceCriterion("ac_done", "Persistence is verified", required=True),
        ),
        context=(ContextReference("workspace://repo/spec.md", "git:abc"),),
        depends_on=(TaskId("tsk_prerequisite"),),
        blocking_reason="Waiting for review",
    )


def _result() -> TaskResult:
    """Build one complete approved Human Result record fixture."""
    return TaskResult(
        id=ResultId("res_primary"),
        task_uid=TaskId("tsk_primary"),
        submitted_by=SubjectId("sub_human"),
        attempt_id=None,
        submitted_at=_NOW,
        comment="Completed manually",
        summary="All checks passed.",
        criteria=(CriterionOutcome("ac_done", CriterionStatus.PASSED, "Verified"),),
        artifacts=(
            ArtifactReference(
                "workspace://repo/report.md",
                "text/markdown",
                "a" * 64,
            ),
        ),
        proposed_follow_ups=(ProposedFollowUp("Add a migration test"),),
        review=ResultReview(
            status=ResultReviewStatus.APPROVED,
            reviewed_by=SubjectId("sub_reviewer"),
            reviewed_at=_NOW,
            comment="Approved",
        ),
    )


def _event_record() -> TaskEventRecord:
    """Build one complete Human-attributed TaskEvent record fixture."""
    return TaskEventRecord(
        event=TaskEvent(
            id=TaskEventId("evt_primary"),
            cursor=17,
            task_uid=TaskId("tsk_primary"),
            project_id=ProjectId("prj_acme"),
            actor_subject_id=SubjectId("sub_human"),
            request_id=RequestId("req_primary"),
            event_type=TaskEventType.TASK_UPDATED,
            occurred_at=_NOW,
            payload={"fields": ("title",), "version": 4},
        ),
        actor_kind=SubjectKind.HUMAN,
        attempt_id=None,
    )


def test_event_record_lookup_helpers_fail_closed_on_invalid_or_missing_data() -> None:
    """Replay lookup helpers reject bad inputs and absent immutable events."""
    connection = cast("sqlite3.Connection", _EmptyConnection())
    with pytest.raises(StorageUnavailableError):
        load_task_event_record(
            connection,
            event_id="evt_invalid",  # type: ignore[arg-type]
        )
    assert (
        load_task_event_record(connection, event_id=TaskEventId("evt_missing")) is None
    )
    with pytest.raises(StorageUnavailableError):
        require_persisted_task_event_record(
            connection,
            expected=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(StorageUnavailableError):
        require_persisted_task_event_record(
            connection,
            expected=_event_record(),
        )


def _attempt_record() -> TaskAttemptRecord:
    """Build one canonical active Agent Attempt persistence fixture."""
    return TaskAttemptRecord(
        project_id=ProjectId("prj_acme"),
        attempt=TaskAttempt(
            id=AttemptId("atm_primary"),
            task_uid=TaskId("tsk_primary"),
            subject_id=SubjectId("sub_human"),
            status=AttemptStatus.ACTIVE,
            started_at=_NOW,
            ended_at=None,
            lease_expires_at=_NOW + timedelta(minutes=15),
        ),
    )


def _claim_record(*, attempt_id: AttemptId | None) -> TaskClaimRecord:
    """Build one canonical current Human or Agent Claim persistence fixture.

    Args:
        attempt_id: Null for Human ownership or one Agent Attempt identity.

    Returns:
        Valid current Claim record.

    """
    return TaskClaimRecord(
        project_id=ProjectId("prj_acme"),
        claim=TaskClaim(
            task_uid=TaskId("tsk_primary"),
            task_key="ACME-7",
            subject_id=SubjectId("sub_human"),
            attempt_id=attempt_id,
            claimed_at=_NOW,
            lease_expires_at=_NOW + timedelta(minutes=15),
        ),
    )


def test_project_record_round_trips_mapping_and_sqlite_row() -> None:
    """Named Projects use one exact canonical durable field order."""
    project = _project()
    expected = {
        "id": "prj_acme",
        "instance_id": "ins_local",
        "key": "ACME",
        "name": "Acme Platform",
        "created_at": "2026-07-30T12:15:30.654321Z",
    }

    mapping = project_to_mapping(project)

    assert PROJECT_FIELDS == (
        "id",
        "instance_id",
        "key",
        "name",
        "created_at",
    )
    assert mapping == expected
    assert project_from_mapping(mapping) == project
    assert project_from_row(tuple(mapping[field] for field in PROJECT_FIELDS)) == (
        project
    )


@pytest.mark.parametrize(
    "value",
    [
        {},
        {
            "id": "prj_acme",
            "instance_id": "ins_local",
            "key": "ACME",
            "name": "Acme",
            "created_at": "2026-07-30T12:15:30.654321Z",
            "unknown": "value",
        },
        cast("dict[str, object]", object()),
    ],
)
def test_project_mapping_rejects_noncanonical_shapes(
    value: dict[str, object],
) -> None:
    """Missing, open, and non-mapping Project records fail safely."""
    with pytest.raises(StorageUnavailableError):
        project_from_mapping(value)


@pytest.mark.parametrize(
    "value",
    [
        (),
        ("prj_acme", "ins_local", "ACME", "Acme"),
        ("prj_acme", "ins_local", "ACME", " Acme ", "2026-07-30T12:15:30.654321Z"),
        ("prj_acme", "ins_local", "ACME", "Cafe\u0301", "2026-07-30T12:15:30.654321Z"),
        ("prj_acme", "ins_local", "lower", "Acme", "2026-07-30T12:15:30.654321Z"),
        ("prj_acme", "ins_local", "ACME", "Acme", "not-a-timestamp"),
        "not-a-row",
        cast("tuple[object, ...]", object()),
    ],
)
def test_project_row_rejects_noncanonical_shapes_and_values(
    value: tuple[object, ...],
) -> None:
    """Wrong shapes, normalized aliases, and invalid scalars fail safely."""
    with pytest.raises(StorageUnavailableError):
        project_from_row(value)


def test_project_serializer_runtime_validates_input() -> None:
    """The record writer does not trust its Project type hint."""
    with pytest.raises(StorageUnavailableError):
        project_to_mapping(cast("Project", object()))


def test_complete_task_record_round_trips_mapping_and_sqlite_row() -> None:
    """Every Task definition field has one explicit lossless codec."""
    task = _task()
    mapping = task_mapping(task)
    row = task_row(task)

    assert TASK_FIELDS == (
        "uid",
        "project_id",
        "number",
        "key",
        "title",
        "objective",
        "state",
        "priority",
        "available_at",
        "approval",
        "acceptance_json",
        "context_json",
        "blocking_reason",
        "current_result_id",
        "version",
        "created_by",
        "created_at",
        "updated_at",
    )
    assert mapping["depends_on"] == ["tsk_prerequisite"]
    assert row[10] == (
        '[{"id":"ac_done","required":true,"text":"Persistence is verified"}]'
    )
    assert row[11] == ('[{"uri":"workspace://repo/spec.md","version":"git:abc"}]')
    assert task_from_mapping(mapping) == task
    assert task_from_row(row, depends_on=task.depends_on) == task


@pytest.mark.parametrize(
    "value",
    [
        (),
        ("short",),
        cast("tuple[object, ...]", object()),
    ],
)
def test_task_row_rejects_wrong_shapes(value: tuple[object, ...]) -> None:
    """Task rows require one exact non-text field sequence."""
    with pytest.raises(StorageUnavailableError):
        task_from_row(value)


def test_task_codecs_reject_open_shapes_corrupt_json_and_invalid_relationships() -> (
    None
):
    """Task persistence cannot bypass closed structures or lifecycle coupling."""
    task = _task()
    mapping = task_mapping(task)
    row = list(task_row(task))

    with pytest.raises(StorageUnavailableError):
        task_from_mapping({**mapping, "unknown": True})
    with pytest.raises(StorageUnavailableError):
        task_from_mapping(
            {key: value for key, value in mapping.items() if key != "uid"}
        )
    with pytest.raises(StorageUnavailableError):
        task_mapping(cast("Task", object()))
    with pytest.raises(StorageUnavailableError):
        task_row(cast("Task", object()))

    row[10] = '[ {"id":"ac_done","required":true,"text":"Done"}]'
    with pytest.raises(StorageUnavailableError):
        task_from_row(tuple(row), depends_on=task.depends_on)
    row = list(task_row(task))
    row[11] = '[{"uri":"workspace://repo/spec.md"}]'
    with pytest.raises(StorageUnavailableError):
        task_from_row(tuple(row), depends_on=task.depends_on)
    row = list(task_row(task))
    row[6] = "open"
    with pytest.raises(StorageUnavailableError):
        task_from_row(tuple(row), depends_on=task.depends_on)


def test_complete_result_record_round_trips_mapping_and_sqlite_row() -> None:
    """Structured Human Results and normalized review fields round trip exactly."""
    result = _result()
    mapping = task_result_mapping(result)
    row = task_result_row(result)

    assert TASK_RESULT_FIELDS == (
        "id",
        "task_uid",
        "submitted_by",
        "attempt_id",
        "submitted_at",
        "comment",
        "summary",
        "criteria_json",
        "artifacts_json",
        "proposed_follow_ups_json",
        "review_status",
        "reviewed_by",
        "reviewed_at",
        "review_comment",
        "rejection_reason",
    )
    assert row[7] == (
        '[{"criterion_id":"ac_done","evidence":"Verified","status":"passed"}]'
    )
    assert row[8] == (
        '[{"media_type":"text/markdown","sha256":"'
        + "a" * 64
        + '","uri":"workspace://repo/report.md"}]'
    )
    assert task_result_from_mapping(mapping) == result
    assert task_result_from_row(row) == result


def test_agent_result_record_round_trips_attempt_attribution() -> None:
    """Agent Result codecs retain their opaque Attempt identity exactly."""
    result = replace(_result(), attempt_id=AttemptId("atm_primary"))

    mapping = task_result_mapping(result)
    row = task_result_row(result)

    assert mapping["attempt_id"] == "atm_primary"
    assert task_result_from_mapping(mapping) == result
    assert task_result_from_row(row) == result


def test_result_codecs_reject_open_shapes_and_corrupt_collections() -> None:
    """Result codecs reject malformed attribution and structured content."""
    result = _result()
    mapping = task_result_mapping(result)
    row = list(task_result_row(result))

    with pytest.raises(StorageUnavailableError):
        task_result_from_mapping({**mapping, "unknown": True})
    with pytest.raises(StorageUnavailableError):
        task_result_mapping(cast("TaskResult", object()))
    row[3] = "invalid-attempt"
    with pytest.raises(StorageUnavailableError):
        task_result_from_row(tuple(row))
    row = list(task_result_row(result))
    row[7] = "{}"
    with pytest.raises(StorageUnavailableError):
        task_result_from_row(tuple(row))
    row = list(task_result_row(result))
    row[8] = '[{"uri":"workspace://repo/report.md"}]'
    with pytest.raises(StorageUnavailableError):
        task_result_from_row(tuple(row))
    row = list(task_result_row(result))
    row[10] = "pending"
    with pytest.raises(StorageUnavailableError):
        task_result_from_row(tuple(row))


def test_complete_event_record_round_trips_mapping_and_sqlite_row() -> None:
    """Event identity, attribution snapshot, payload, and cursor are lossless."""
    record = _event_record()
    mapping = task_event_record_mapping(record)
    row = task_event_row(record)

    assert TASK_EVENT_FIELDS == (
        "cursor",
        "id",
        "task_uid",
        "project_id",
        "actor_subject_id",
        "actor_kind",
        "attempt_id",
        "request_id",
        "event_type",
        "occurred_at",
        "payload_json",
    )
    assert row[10] == '{"fields":["title"],"version":4}'
    assert task_event_record_from_mapping(mapping) == record
    assert task_event_record_from_row(row) == record


def test_agent_event_record_round_trips_attempt_attribution() -> None:
    """Agent event codecs retain matching domain and snapshot attribution."""
    attempt_id = AttemptId("atm_primary")
    human = _event_record()
    event = replace(human.event, attempt_id=attempt_id)
    record = TaskEventRecord(
        event=event,
        actor_kind=SubjectKind.AGENT,
        attempt_id=attempt_id,
    )

    mapping = task_event_record_mapping(record)
    row = task_event_row(record)

    assert mapping["attempt_id"] == "atm_primary"
    assert task_event_record_from_mapping(mapping) == record
    assert task_event_record_from_row(row) == record


def test_event_codecs_reject_mismatched_attribution_and_noncanonical_payloads() -> None:
    """Event hydration rejects malformed attribution and corrupt JSON."""
    record = _event_record()
    mapping = task_event_record_mapping(record)
    row = list(task_event_row(record))

    with pytest.raises(StorageUnavailableError):
        task_event_record_from_mapping({**mapping, "unknown": True})
    with pytest.raises(StorageUnavailableError):
        task_event_record_mapping(cast("TaskEventRecord", object()))
    row[6] = "invalid-attempt"
    with pytest.raises(StorageUnavailableError):
        task_event_record_from_row(tuple(row))
    row = list(task_event_row(record))
    row[10] = '{"version":4,"fields":["title"]}'
    with pytest.raises(StorageUnavailableError):
        task_event_record_from_row(tuple(row))
    row = list(task_event_row(record))
    row[5] = "invalid"
    with pytest.raises(StorageUnavailableError):
        task_event_record_from_row(tuple(row))


def test_attempt_record_round_trips_mapping_and_sqlite_row() -> None:
    """Attempt status, ownership, timestamps, and Lease round trip exactly."""
    record = _attempt_record()
    mapping = task_attempt_record_mapping(record)
    row = task_attempt_row(record)

    assert TASK_ATTEMPT_FIELDS == (
        "id",
        "task_uid",
        "project_id",
        "subject_id",
        "status",
        "started_at",
        "ended_at",
        "lease_expires_at",
    )
    assert mapping["status"] == "active"
    assert mapping["ended_at"] is None
    assert task_attempt_record_from_mapping(mapping) == record
    assert task_attempt_record_from_row(row) == record


@pytest.mark.parametrize("attempt_id", [None, AttemptId("atm_primary")])
def test_claim_record_round_trips_human_and_agent_ownership(
    attempt_id: AttemptId | None,
) -> None:
    """Claim codecs retain null-Human and non-null-Agent owner tokens."""
    record = _claim_record(attempt_id=attempt_id)
    mapping = task_claim_record_mapping(record)
    row = task_claim_row(record)

    assert TASK_CLAIM_FIELDS == (
        "task_uid",
        "project_id",
        "subject_id",
        "attempt_id",
        "claimed_at",
        "lease_expires_at",
    )
    assert mapping["task_key"] == "ACME-7"
    assert task_claim_record_from_mapping(mapping) == record
    assert task_claim_record_from_row(row, task_key="ACME-7") == record


def test_claim_and_attempt_codecs_fail_closed_on_malformed_records() -> None:
    """Execution record codecs reject open shapes, invalid states, and aliases."""
    attempt = _attempt_record()
    attempt_mapping = task_attempt_record_mapping(attempt)
    claim = _claim_record(attempt_id=AttemptId("atm_primary"))
    claim_mapping = task_claim_record_mapping(claim)

    invalid_attempt_row = list(task_attempt_row(attempt))
    invalid_attempt_row[4] = "unknown"
    invalid_claim_row = list(task_claim_row(claim))
    invalid_claim_row[5] = invalid_claim_row[4]

    with pytest.raises(StorageUnavailableError):
        task_attempt_record_from_mapping({**attempt_mapping, "unknown": True})
    with pytest.raises(StorageUnavailableError):
        task_attempt_record_from_row(tuple(invalid_attempt_row))
    with pytest.raises(StorageUnavailableError):
        task_attempt_record_mapping(cast("TaskAttemptRecord", object()))
    with pytest.raises(StorageUnavailableError):
        task_claim_record_from_mapping({**claim_mapping, "unknown": True})
    with pytest.raises(StorageUnavailableError):
        task_claim_record_from_row(tuple(invalid_claim_row), task_key="ACME-7")
    with pytest.raises(StorageUnavailableError):
        task_claim_record_mapping(cast("TaskClaimRecord", object()))


def test_canonical_serialization_round_trips_supported_values() -> None:
    """Shared serialization is deterministic and preserves UTC microseconds."""
    timestamp = datetime(2026, 7, 30, 12, 15, 30, 654321, tzinfo=UTC)

    assert canonical_json({"text": "value", "number": 1}) == (
        '{"number":1,"text":"value"}'
    )
    assert canonical_json_value(({"ok": True}, None)) == '[{"ok":true},null]'
    assert parse_json_object(
        '{"number":1,"text":"value"}',
        maximum=EVENT_PAYLOAD_JSON_MAX_LENGTH,
    ) == {"number": 1, "text": "value"}
    assert parse_json_array(
        '[{"ok":true},null]',
        maximum=STRUCTURED_COLLECTION_JSON_MAX_LENGTH,
    ) == ({"ok": True}, None)
    assert serialize_timestamp(timestamp) == "2026-07-30T12:15:30.654321Z"
    assert parse_timestamp("2026-07-30T12:15:30.654321Z") == timestamp
    assert parse_optional_timestamp(None) is None
    assert parse_optional_timestamp("2026-07-30T12:15:30.654321Z") == timestamp
    assert require_text("value") == "value"
    assert require_optional_text(None) is None
    assert require_optional_text("value") == "value"
    assert require_integer(0, minimum=0) == 0
    assert require_boolean(0) is False
    assert require_boolean(1) is True


@pytest.mark.parametrize(
    ("helper", "value"),
    [
        (require_text, ""),
        (require_text, 1),
        (require_integer, True),
        (require_integer, 0),
        (require_boolean, 2),
        (require_boolean, False),
        (parse_timestamp, "2026-07-30T12:15:30Z"),
        (parse_timestamp, 1),
    ],
)
def test_scalar_helpers_reject_ambiguous_or_noncanonical_values(
    helper: object,
    value: object,
) -> None:
    """Wrong SQLite storage classes and timestamp shapes fail safely."""
    assert callable(helper)
    with pytest.raises(StorageUnavailableError):
        helper(value)


@pytest.mark.parametrize(
    ("helper", "value", "maximum"),
    [
        (parse_json_object, "[]", 100),
        (parse_json_array, "{}", 100),
        (parse_json_object, '{"a":1,"a":2}', 100),
        (parse_json_object, '{ "a":1}', 100),
        (parse_json_object, '{"b":1,"a":2}', 100),
        (parse_json_array, "[NaN]", 100),
        (parse_json_array, "[]", 1),
        (parse_json_array, "[]", True),
        (parse_json_array, 1, 100),
    ],
)
def test_json_codecs_reject_wrong_shapes_duplicates_and_noncanonical_text(
    helper: object,
    value: object,
    maximum: object,
) -> None:
    """Persisted JSON must be bounded, canonical, finite, and shape-specific."""
    assert callable(helper)
    with pytest.raises(StorageUnavailableError):
        helper(value, maximum=maximum)


@pytest.mark.parametrize("value", [{"bad": {1}}, float("inf"), object()])
def test_json_serializer_rejects_non_json_values(value: object) -> None:
    """Canonical serialization rejects sets, non-finite floats, and objects."""
    with pytest.raises(StorageUnavailableError):
        canonical_json_value(value)
