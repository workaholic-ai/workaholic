"""Transactional Phase 5 SQLite schema creation and exact validation."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Final

from workaholic.persistence.sqlite._driver import _initialize_connection
from workaholic.persistence.sqlite._records import (
    EVENT_PAYLOAD_JSON_MAX_LENGTH,
    IDEMPOTENCY_OUTCOME_JSON_MAX_LENGTH,
    STRUCTURED_COLLECTION_JSON_MAX_LENGTH,
)
from workaholic.persistence.sqlite.errors import SchemaUnsupportedError

if TYPE_CHECKING:
    from pathlib import Path

SCHEMA_VERSION: Final = 5
_CREATE_STATEMENT_MIN_WORDS: Final = 3

_SCHEMA_STATEMENTS: Final = (
    """
    CREATE TABLE store_metadata (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        schema_version INTEGER NOT NULL CHECK (schema_version >= 1)
    ) STRICT
    """,
    """
    CREATE TABLE instances (
        id TEXT PRIMARY KEY
            CHECK (length(id) BETWEEN 5 AND 132 AND id GLOB 'ins_*'),
        created_at TEXT NOT NULL
            CHECK (
                length(created_at) BETWEEN 20 AND 27
                AND substr(created_at, 11, 1) = 'T'
                AND substr(created_at, -1, 1) = 'Z'
            )
    ) STRICT
    """,
    """
    CREATE TABLE subjects (
        id TEXT PRIMARY KEY
            CHECK (length(id) BETWEEN 5 AND 132 AND id GLOB 'sub_*'),
        instance_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('human', 'agent')),
        handle TEXT NOT NULL
            CHECK (
                length(handle) BETWEEN 2 AND 63
                AND substr(handle, 1, 1) GLOB '[a-z]'
                AND handle NOT GLOB '*[^a-z0-9-]*'
            ),
        display_name TEXT NOT NULL
            CHECK (
                length(display_name) BETWEEN 1 AND 200
                AND display_name = trim(display_name)
        ),
        enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
        is_instance_admin INTEGER NOT NULL CHECK (is_instance_admin IN (0, 1)),
        version INTEGER NOT NULL CHECK (version >= 1),
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL
            CHECK (
                length(created_at) BETWEEN 20 AND 27
                AND substr(created_at, 11, 1) = 'T'
                AND substr(created_at, -1, 1) = 'Z'
            ),
        updated_at TEXT NOT NULL
            CHECK (
                length(updated_at) BETWEEN 20 AND 27
                AND substr(updated_at, 11, 1) = 'T'
                AND substr(updated_at, -1, 1) = 'Z'
                AND updated_at >= created_at
            ),
        UNIQUE (id, instance_id),
        UNIQUE (id, kind),
        UNIQUE (id, instance_id, kind),
        UNIQUE (instance_id, handle),
        FOREIGN KEY (instance_id)
            REFERENCES instances(id) ON DELETE RESTRICT,
        FOREIGN KEY (created_by, instance_id)
            REFERENCES subjects(id, instance_id) ON DELETE RESTRICT
    ) STRICT
    """,
    """
    CREATE TABLE projects (
        id TEXT PRIMARY KEY
            CHECK (length(id) BETWEEN 5 AND 132 AND id GLOB 'prj_*'),
        instance_id TEXT NOT NULL REFERENCES instances(id) ON DELETE RESTRICT,
        key TEXT NOT NULL
            CHECK (
                length(key) BETWEEN 2 AND 16
                AND key NOT GLOB '*[^A-Z0-9]*'
                AND substr(key, 1, 1) GLOB '[A-Z]'
            ),
        name TEXT NOT NULL
            CHECK (
                length(name) BETWEEN 1 AND 200
                AND name = trim(name)
            ),
        next_task_number INTEGER NOT NULL
            CHECK (next_task_number >= 1),
        created_at TEXT NOT NULL
            CHECK (
                length(created_at) BETWEEN 20 AND 27
                AND substr(created_at, 11, 1) = 'T'
                AND substr(created_at, -1, 1) = 'Z'
            ),
        UNIQUE (instance_id, key),
        UNIQUE (id, instance_id)
    ) STRICT
    """,
    """
    CREATE TABLE project_grants (
        instance_id TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        role TEXT NOT NULL
            CHECK (role IN ('viewer', 'agent', 'operator', 'owner')),
        version INTEGER NOT NULL CHECK (version >= 1),
        granted_by TEXT NOT NULL,
        created_at TEXT NOT NULL
            CHECK (
                length(created_at) BETWEEN 20 AND 27
                AND substr(created_at, 11, 1) = 'T'
                AND substr(created_at, -1, 1) = 'Z'
            ),
        updated_at TEXT NOT NULL
            CHECK (
                length(updated_at) BETWEEN 20 AND 27
                AND substr(updated_at, 11, 1) = 'T'
                AND substr(updated_at, -1, 1) = 'Z'
                AND updated_at >= created_at
            ),
        PRIMARY KEY (subject_id, project_id),
        UNIQUE (instance_id, subject_id, project_id),
        FOREIGN KEY (subject_id, instance_id)
            REFERENCES subjects(id, instance_id) ON DELETE RESTRICT,
        FOREIGN KEY (project_id, instance_id)
            REFERENCES projects(id, instance_id) ON DELETE RESTRICT,
        FOREIGN KEY (granted_by, instance_id)
            REFERENCES subjects(id, instance_id) ON DELETE RESTRICT
    ) STRICT
    """,
    """
    CREATE TABLE tokens (
        id TEXT PRIMARY KEY
            CHECK (length(id) BETWEEN 5 AND 132 AND id GLOB 'tok_*'),
        instance_id TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE
            CHECK (
                length(token_hash) = 64
                AND token_hash NOT GLOB '*[^0-9a-f]*'
            ),
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL
            CHECK (
                length(created_at) BETWEEN 20 AND 27
                AND substr(created_at, 11, 1) = 'T'
                AND substr(created_at, -1, 1) = 'Z'
            ),
        activated_at TEXT
            CHECK (
                activated_at IS NULL
                OR (
                    length(activated_at) BETWEEN 20 AND 27
                    AND substr(activated_at, 11, 1) = 'T'
                    AND substr(activated_at, -1, 1) = 'Z'
                    AND activated_at >= created_at
                )
            ),
        expires_at TEXT NOT NULL
            CHECK (
                length(expires_at) BETWEEN 20 AND 27
                AND substr(expires_at, 11, 1) = 'T'
                AND substr(expires_at, -1, 1) = 'Z'
                AND expires_at > created_at
                AND (activated_at IS NULL OR activated_at < expires_at)
            ),
        revoked_at TEXT
            CHECK (
                revoked_at IS NULL
                OR (
                    length(revoked_at) BETWEEN 20 AND 27
                    AND substr(revoked_at, 11, 1) = 'T'
                    AND substr(revoked_at, -1, 1) = 'Z'
                    AND revoked_at >= created_at
                    AND (activated_at IS NULL OR revoked_at >= activated_at)
                )
            ),
        revoked_by TEXT,
        UNIQUE (id, instance_id, subject_id),
        FOREIGN KEY (subject_id, instance_id)
            REFERENCES subjects(id, instance_id) ON DELETE RESTRICT,
        FOREIGN KEY (created_by, instance_id)
            REFERENCES subjects(id, instance_id) ON DELETE RESTRICT,
        FOREIGN KEY (revoked_by, instance_id)
            REFERENCES subjects(id, instance_id) ON DELETE RESTRICT,
        CHECK ((revoked_at IS NULL) = (revoked_by IS NULL))
    ) STRICT
    """,
    f"""
    CREATE TABLE tasks (
        uid TEXT PRIMARY KEY
            CHECK (length(uid) BETWEEN 5 AND 132 AND uid GLOB 'tsk_*'),
        project_id TEXT NOT NULL
            REFERENCES projects(id) ON DELETE RESTRICT,
        number INTEGER NOT NULL CHECK (number >= 1),
        key TEXT NOT NULL
            CHECK (
                length(key) BETWEEN 4 AND 37
                AND key NOT GLOB '*[^A-Z0-9-]*'
                AND key GLOB ('*-' || CAST(number AS TEXT))
            ),
        title TEXT NOT NULL
            CHECK (
                length(title) BETWEEN 1 AND 200
                AND title = trim(title)
            ),
        objective TEXT NOT NULL
            CHECK (
                length(objective) BETWEEN 1 AND 4000
                AND objective = trim(objective)
            ),
        state TEXT NOT NULL
            CHECK (state IN ('open', 'blocked', 'review', 'done', 'cancelled')),
        priority INTEGER NOT NULL CHECK (priority BETWEEN 0 AND 100),
        available_at TEXT
            CHECK (
                available_at IS NULL
                OR (
                    length(available_at) BETWEEN 20 AND 27
                    AND substr(available_at, 11, 1) = 'T'
                    AND substr(available_at, -1, 1) = 'Z'
                )
            ),
        approval TEXT NOT NULL DEFAULT 'none'
            CHECK (approval IN ('none', 'human')),
        acceptance_json TEXT NOT NULL DEFAULT '[]'
            CHECK (
                length(acceptance_json)
                    BETWEEN 2 AND {STRUCTURED_COLLECTION_JSON_MAX_LENGTH}
                AND substr(acceptance_json, 1, 1) = '['
                AND substr(acceptance_json, -1, 1) = ']'
                AND json_valid(acceptance_json)
                AND json_type(acceptance_json) = 'array'
            ),
        context_json TEXT NOT NULL DEFAULT '[]'
            CHECK (
                length(context_json)
                    BETWEEN 2 AND {STRUCTURED_COLLECTION_JSON_MAX_LENGTH}
                AND substr(context_json, 1, 1) = '['
                AND substr(context_json, -1, 1) = ']'
                AND json_valid(context_json)
                AND json_type(context_json) = 'array'
            ),
        blocking_reason TEXT
            CHECK (
                blocking_reason IS NULL
                OR (
                    length(blocking_reason) BETWEEN 1 AND 1000
                    AND blocking_reason = trim(blocking_reason)
                )
            ),
        current_result_id TEXT
            CHECK (
                current_result_id IS NULL
                OR (
                    length(current_result_id) BETWEEN 5 AND 132
                    AND current_result_id GLOB 'res_*'
                )
            ),
        version INTEGER NOT NULL CHECK (version >= 1),
        created_by TEXT NOT NULL
            REFERENCES subjects(id) ON DELETE RESTRICT,
        created_at TEXT NOT NULL
            CHECK (
                length(created_at) BETWEEN 20 AND 27
                AND substr(created_at, 11, 1) = 'T'
                AND substr(created_at, -1, 1) = 'Z'
            ),
        updated_at TEXT NOT NULL
            CHECK (
                length(updated_at) BETWEEN 20 AND 27
                AND substr(updated_at, 11, 1) = 'T'
                AND substr(updated_at, -1, 1) = 'Z'
                AND updated_at >= created_at
            ),
        UNIQUE (project_id, number),
        UNIQUE (project_id, key),
        UNIQUE (key),
        UNIQUE (uid, project_id),
        CHECK (
            (state = 'blocked' AND blocking_reason IS NOT NULL)
            OR (state != 'blocked' AND blocking_reason IS NULL)
        ),
        FOREIGN KEY (current_result_id, uid)
            REFERENCES task_results(id, task_uid) ON DELETE RESTRICT
    ) STRICT
    """,
    """
    CREATE TABLE task_dependencies (
        task_uid TEXT NOT NULL,
        prerequisite_uid TEXT NOT NULL,
        project_id TEXT NOT NULL,
        PRIMARY KEY (task_uid, prerequisite_uid),
        CHECK (task_uid != prerequisite_uid),
        FOREIGN KEY (task_uid, project_id)
            REFERENCES tasks(uid, project_id) ON DELETE RESTRICT,
        FOREIGN KEY (prerequisite_uid, project_id)
            REFERENCES tasks(uid, project_id) ON DELETE RESTRICT
    ) STRICT
    """,
    """
    CREATE TABLE task_attempts (
        id TEXT PRIMARY KEY
            CHECK (length(id) BETWEEN 5 AND 132 AND id GLOB 'atm_*'),
        task_uid TEXT NOT NULL,
        project_id TEXT NOT NULL,
        subject_id TEXT NOT NULL
            REFERENCES subjects(id) ON DELETE RESTRICT,
        status TEXT NOT NULL
            CHECK (status IN ('active', 'released', 'expired', 'submitted')),
        started_at TEXT NOT NULL
            CHECK (
                length(started_at) BETWEEN 20 AND 27
                AND substr(started_at, 11, 1) = 'T'
                AND substr(started_at, -1, 1) = 'Z'
            ),
        ended_at TEXT
            CHECK (
                ended_at IS NULL
                OR (
                    length(ended_at) BETWEEN 20 AND 27
                    AND substr(ended_at, 11, 1) = 'T'
                    AND substr(ended_at, -1, 1) = 'Z'
                    AND ended_at >= started_at
                )
            ),
        lease_expires_at TEXT NOT NULL
            CHECK (
                length(lease_expires_at) BETWEEN 20 AND 27
                AND substr(lease_expires_at, 11, 1) = 'T'
                AND substr(lease_expires_at, -1, 1) = 'Z'
                AND lease_expires_at > started_at
            ),
        UNIQUE (id, task_uid, subject_id),
        UNIQUE (id, task_uid, project_id),
        UNIQUE (id, task_uid, project_id, subject_id),
        FOREIGN KEY (task_uid, project_id)
            REFERENCES tasks(uid, project_id) ON DELETE RESTRICT,
        CHECK (
            (status = 'active' AND ended_at IS NULL)
            OR (status != 'active' AND ended_at IS NOT NULL)
        ),
        CHECK (status != 'expired' OR ended_at = lease_expires_at),
        CHECK (
            status NOT IN ('released', 'submitted')
            OR ended_at < lease_expires_at
        )
    ) STRICT
    """,
    """
    CREATE TABLE task_claims (
        task_uid TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        subject_id TEXT NOT NULL
            REFERENCES subjects(id) ON DELETE RESTRICT,
        attempt_id TEXT UNIQUE
            CHECK (
                attempt_id IS NULL
                OR (
                    length(attempt_id) BETWEEN 5 AND 132
                    AND attempt_id GLOB 'atm_*'
                )
            ),
        claimed_at TEXT NOT NULL
            CHECK (
                length(claimed_at) BETWEEN 20 AND 27
                AND substr(claimed_at, 11, 1) = 'T'
                AND substr(claimed_at, -1, 1) = 'Z'
            ),
        lease_expires_at TEXT NOT NULL
            CHECK (
                length(lease_expires_at) BETWEEN 20 AND 27
                AND substr(lease_expires_at, 11, 1) = 'T'
                AND substr(lease_expires_at, -1, 1) = 'Z'
                AND lease_expires_at > claimed_at
            ),
        FOREIGN KEY (task_uid, project_id)
            REFERENCES tasks(uid, project_id) ON DELETE RESTRICT,
        FOREIGN KEY (attempt_id, task_uid, project_id, subject_id)
            REFERENCES task_attempts(id, task_uid, project_id, subject_id)
            ON DELETE RESTRICT
    ) STRICT
    """,
    f"""
    CREATE TABLE task_results (
        id TEXT PRIMARY KEY
            CHECK (length(id) BETWEEN 5 AND 132 AND id GLOB 'res_*'),
        task_uid TEXT NOT NULL REFERENCES tasks(uid) ON DELETE RESTRICT,
        submitted_by TEXT NOT NULL REFERENCES subjects(id) ON DELETE RESTRICT,
        attempt_id TEXT
            CHECK (
                attempt_id IS NULL
                OR (
                    length(attempt_id) BETWEEN 5 AND 132
                    AND attempt_id GLOB 'atm_*'
                )
            ),
        submitted_at TEXT NOT NULL
            CHECK (
                length(submitted_at) BETWEEN 20 AND 27
                AND substr(submitted_at, 11, 1) = 'T'
                AND substr(submitted_at, -1, 1) = 'Z'
            ),
        comment TEXT
            CHECK (
                comment IS NULL
                OR (
                    length(comment) BETWEEN 1 AND 4000
                    AND comment = trim(comment)
                )
            ),
        summary TEXT
            CHECK (
                summary IS NULL
                OR (
                    length(summary) BETWEEN 1 AND 4000
                    AND summary = trim(summary)
                )
            ),
        criteria_json TEXT NOT NULL DEFAULT '[]'
            CHECK (
                length(criteria_json)
                    BETWEEN 2 AND {STRUCTURED_COLLECTION_JSON_MAX_LENGTH}
                AND substr(criteria_json, 1, 1) = '['
                AND substr(criteria_json, -1, 1) = ']'
                AND json_valid(criteria_json)
                AND json_type(criteria_json) = 'array'
            ),
        artifacts_json TEXT NOT NULL DEFAULT '[]'
            CHECK (
                length(artifacts_json)
                    BETWEEN 2 AND {STRUCTURED_COLLECTION_JSON_MAX_LENGTH}
                AND substr(artifacts_json, 1, 1) = '['
                AND substr(artifacts_json, -1, 1) = ']'
                AND json_valid(artifacts_json)
                AND json_type(artifacts_json) = 'array'
            ),
        proposed_follow_ups_json TEXT NOT NULL DEFAULT '[]'
            CHECK (
                length(proposed_follow_ups_json)
                    BETWEEN 2 AND {STRUCTURED_COLLECTION_JSON_MAX_LENGTH}
                AND substr(proposed_follow_ups_json, 1, 1) = '['
                AND substr(proposed_follow_ups_json, -1, 1) = ']'
                AND json_valid(proposed_follow_ups_json)
                AND json_type(proposed_follow_ups_json) = 'array'
            ),
        review_status TEXT NOT NULL
            CHECK (
                review_status IN (
                    'not_required', 'pending', 'approved', 'rejected'
                )
            ),
        reviewed_by TEXT REFERENCES subjects(id) ON DELETE RESTRICT,
        reviewed_at TEXT
            CHECK (
                reviewed_at IS NULL
                OR (
                    length(reviewed_at) BETWEEN 20 AND 27
                    AND substr(reviewed_at, 11, 1) = 'T'
                    AND substr(reviewed_at, -1, 1) = 'Z'
                )
            ),
        review_comment TEXT
            CHECK (
                review_comment IS NULL
                OR (
                    length(review_comment) BETWEEN 1 AND 4000
                    AND review_comment = trim(review_comment)
                )
            ),
        rejection_reason TEXT
            CHECK (
                rejection_reason IS NULL
                OR (
                    length(rejection_reason) BETWEEN 1 AND 1000
                    AND rejection_reason = trim(rejection_reason)
                )
            ),
        UNIQUE (id, task_uid),
        CHECK (
            (
                review_status IN ('not_required', 'pending')
                AND reviewed_by IS NULL
                AND reviewed_at IS NULL
                AND review_comment IS NULL
                AND rejection_reason IS NULL
            )
            OR (
                review_status = 'approved'
                AND reviewed_by IS NOT NULL
                AND reviewed_at IS NOT NULL
                AND rejection_reason IS NULL
            )
            OR (
                review_status = 'rejected'
                AND reviewed_by IS NOT NULL
                AND reviewed_at IS NOT NULL
                AND review_comment IS NULL
                AND rejection_reason IS NOT NULL
            )
        ),
        FOREIGN KEY (attempt_id, task_uid, submitted_by)
            REFERENCES task_attempts(id, task_uid, subject_id)
            ON DELETE RESTRICT
    ) STRICT
    """,
    f"""
    CREATE TABLE task_events (
        cursor INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE
            CHECK (length(id) BETWEEN 5 AND 132 AND id GLOB 'evt_*'),
        task_uid TEXT NOT NULL,
        project_id TEXT NOT NULL,
        actor_subject_id TEXT NOT NULL
            CHECK (
                length(actor_subject_id) BETWEEN 5 AND 132
                AND actor_subject_id GLOB 'sub_*'
            ),
        actor_kind TEXT NOT NULL DEFAULT 'human'
            CHECK (actor_kind IN ('human', 'agent')),
        attempt_id TEXT
            CHECK (
                attempt_id IS NULL
                OR (
                    length(attempt_id) BETWEEN 5 AND 132
                    AND attempt_id GLOB 'atm_*'
                )
            ),
        request_id TEXT NOT NULL
            CHECK (
                length(request_id) BETWEEN 5 AND 132
                AND request_id GLOB 'req_*'
            ),
        event_type TEXT NOT NULL
            CHECK (
                event_type IN (
                    'task_created',
                    'task_updated',
                    'task_blocked',
                    'task_unblocked',
                    'result_submitted',
                    'review_approved',
                    'review_rejected',
                    'task_completed',
                    'task_cancelled',
                    'task_claimed',
                    'claim_renewed',
                    'claim_released',
                    'claim_expired',
                    'progress_reported',
                    'observation_added'
                )
            ),
        occurred_at TEXT NOT NULL
            CHECK (
                length(occurred_at) BETWEEN 20 AND 27
                AND substr(occurred_at, 11, 1) = 'T'
                AND substr(occurred_at, -1, 1) = 'Z'
            ),
        payload_json TEXT NOT NULL
            CHECK (
                length(payload_json) BETWEEN 2 AND {EVENT_PAYLOAD_JSON_MAX_LENGTH}
                AND substr(payload_json, 1, 1) = '{{'
                AND substr(payload_json, -1, 1) = '}}'
                AND json_valid(payload_json)
                AND json_type(payload_json) = 'object'
            ),
        FOREIGN KEY (task_uid, project_id)
            REFERENCES tasks(uid, project_id) ON DELETE RESTRICT,
        FOREIGN KEY (actor_subject_id, actor_kind)
            REFERENCES subjects(id, kind) ON DELETE RESTRICT,
        FOREIGN KEY (attempt_id, task_uid, project_id)
            REFERENCES task_attempts(id, task_uid, project_id)
            ON DELETE RESTRICT
    ) STRICT
    """,
    f"""
    CREATE TABLE audit_events (
        cursor INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE
            CHECK (length(id) BETWEEN 5 AND 132 AND id GLOB 'aev_*'),
        instance_id TEXT NOT NULL,
        actor_subject_id TEXT NOT NULL,
        actor_kind TEXT NOT NULL CHECK (actor_kind IN ('human', 'agent')),
        actor_token_id TEXT
            CHECK (
                actor_token_id IS NULL
                OR (
                    length(actor_token_id) BETWEEN 5 AND 132
                    AND actor_token_id GLOB 'tok_*'
                )
            ),
        request_id TEXT NOT NULL
            CHECK (
                length(request_id) BETWEEN 5 AND 132
                AND request_id GLOB 'req_*'
            ),
        event_type TEXT NOT NULL
            CHECK (
                event_type IN (
                    'instance_bootstrapped',
                    'project_created',
                    'subject_created',
                    'subject_updated',
                    'subject_enabled',
                    'subject_disabled',
                    'instance_admin_granted',
                    'instance_admin_revoked',
                    'project_grant_assigned',
                    'project_grant_revoked',
                    'token_issued',
                    'token_revoked'
                )
            ),
        occurred_at TEXT NOT NULL
            CHECK (
                length(occurred_at) BETWEEN 20 AND 27
                AND substr(occurred_at, 11, 1) = 'T'
                AND substr(occurred_at, -1, 1) = 'Z'
            ),
        payload_json TEXT NOT NULL
            CHECK (
                length(payload_json) BETWEEN 2 AND {EVENT_PAYLOAD_JSON_MAX_LENGTH}
                AND substr(payload_json, 1, 1) = '{{'
                AND substr(payload_json, -1, 1) = '}}'
                AND json_valid(payload_json)
                AND json_type(payload_json) = 'object'
            ),
        FOREIGN KEY (actor_subject_id, instance_id, actor_kind)
            REFERENCES subjects(id, instance_id, kind) ON DELETE RESTRICT,
        FOREIGN KEY (actor_token_id, instance_id, actor_subject_id)
            REFERENCES tokens(id, instance_id, subject_id) ON DELETE RESTRICT
    ) STRICT
    """,
    f"""
    CREATE TABLE idempotency_records (
        subject_scope TEXT NOT NULL
            CHECK (
                length(subject_scope) BETWEEN 1 AND 200
                AND subject_scope = trim(subject_scope)
            ),
        operation TEXT NOT NULL
            CHECK (
                operation IN (
                    'bootstrap.local_project',
                    'project.create',
                    'task.create',
                    'task.update',
                    'task.block',
                    'task.unblock',
                    'task.cancel',
                    'task.dependency.add',
                    'task.dependency.remove',
                    'task.result.submit',
                    'task.result.approve',
                    'task.result.reject',
                    'task.claim',
                    'task.claim.next',
                    'task.claim.renew',
                    'task.claim.release',
                    'task.progress.report',
                    'task.result.submit.agent',
                    'subject.create',
                    'subject.update',
                    'subject.enable',
                    'subject.disable',
                    'subject.admin.grant',
                    'subject.admin.revoke',
                    'project.grant.assign',
                    'project.grant.revoke',
                    'token.activate',
                    'token.revoke',
                    'auth.recover.local'
                )
            ),
        caller_key TEXT NOT NULL
            CHECK (
                length(caller_key) BETWEEN 1 AND 200
                AND caller_key = trim(caller_key)
            ),
        request_fingerprint TEXT NOT NULL
            CHECK (
                length(request_fingerprint) BETWEEN 1 AND 256
                AND request_fingerprint = trim(request_fingerprint)
            ),
        outcome_json TEXT NOT NULL
            CHECK (
                length(outcome_json)
                    BETWEEN 2 AND {IDEMPOTENCY_OUTCOME_JSON_MAX_LENGTH}
                AND substr(outcome_json, 1, 1) = '{{'
                AND substr(outcome_json, -1, 1) = '}}'
                AND json_valid(outcome_json)
                AND json_type(outcome_json) = 'object'
            ),
        created_at TEXT NOT NULL
            CHECK (
                length(created_at) BETWEEN 20 AND 27
                AND substr(created_at, 11, 1) = 'T'
                AND substr(created_at, -1, 1) = 'Z'
            ),
        PRIMARY KEY (subject_scope, operation, caller_key)
    ) STRICT
    """,
    """
    CREATE INDEX idx_tasks_readiness
    ON tasks (project_id, state, available_at, priority DESC, number)
    """,
    """
    CREATE INDEX idx_subjects_instance_handle
    ON subjects (instance_id, handle, id)
    """,
    """
    CREATE INDEX idx_subjects_instance_admin
    ON subjects (instance_id, enabled, is_instance_admin, id)
    """,
    """
    CREATE INDEX idx_project_grants_project_subject
    ON project_grants (instance_id, project_id, subject_id)
    """,
    """
    CREATE INDEX idx_project_grants_subject_project
    ON project_grants (instance_id, subject_id, project_id)
    """,
    """
    CREATE INDEX idx_tokens_subject_created
    ON tokens (instance_id, subject_id, created_at, id)
    """,
    """
    CREATE INDEX idx_tokens_active_expiry
    ON tokens (instance_id, activated_at, revoked_at, expires_at, id)
    """,
    """
    CREATE INDEX idx_task_dependencies_prerequisite
    ON task_dependencies (prerequisite_uid, project_id, task_uid)
    """,
    """
    CREATE INDEX idx_task_results_task
    ON task_results (task_uid, submitted_at, id)
    """,
    """
    CREATE INDEX idx_task_attempts_task_history
    ON task_attempts (task_uid, started_at DESC, id)
    """,
    """
    CREATE INDEX idx_task_attempts_active_lease
    ON task_attempts (status, lease_expires_at, task_uid)
    """,
    """
    CREATE INDEX idx_task_claims_project_task
    ON task_claims (project_id, task_uid)
    """,
    """
    CREATE INDEX idx_task_claims_owner
    ON task_claims (subject_id, attempt_id, task_uid)
    """,
    """
    CREATE INDEX idx_task_claims_lease_expiry
    ON task_claims (lease_expires_at, task_uid)
    """,
    """
    CREATE INDEX idx_task_events_task_cursor
    ON task_events (task_uid, cursor)
    """,
    """
    CREATE INDEX idx_task_events_project_cursor
    ON task_events (project_id, cursor)
    """,
    """
    CREATE INDEX idx_audit_events_instance_cursor
    ON audit_events (instance_id, cursor)
    """,
    """
    INSERT INTO store_metadata (singleton, schema_version)
    VALUES (1, 5)
    """,
)


def _schema_signature_from_statements(
    statements: tuple[str, ...],
) -> tuple[tuple[str, str, str], ...]:
    """Build the expected explicit SQLite schema-object signature.

    Args:
        statements: Closed schema statement sequence.

    Returns:
        Sorted object type, name, and canonical SQL triples.

    """
    signature: list[tuple[str, str, str]] = []
    for statement in statements:
        sql = statement.strip()
        words = sql.split(maxsplit=3)
        if len(words) < _CREATE_STATEMENT_MIN_WORDS or words[0] != "CREATE":
            continue
        object_type = words[1].lower()
        if object_type not in {"index", "table", "trigger", "view"}:
            continue
        signature.append((object_type, words[2], sql))
    return tuple(sorted(signature))


_EXPECTED_SCHEMA_SIGNATURE: Final = _schema_signature_from_statements(
    _SCHEMA_STATEMENTS
)


def initialize_empty_store(database_path: Path) -> None:
    """Atomically create or accept one empty Phase 5 SQLite store.

    Concurrent callers serialize through a bounded immediate transaction. An
    existing nonempty store is validated and never repaired or migrated.

    Args:
        database_path: Absolute target path for the SQLite database.

    Raises:
        SchemaUnsupportedError: If an existing store is not exact version 5.
        StorageBusyError: If another writer outlives the bounded lock wait.
        StorageUnavailableError: If storage cannot be initialized safely.

    """
    with _initialize_connection(database_path) as connection:
        if _contains_application_schema(connection):
            validate_store_schema(connection)
            return
        _create_schema(connection)
        validate_store_schema(connection)


def validate_store_schema(connection: sqlite3.Connection) -> None:
    """Require exactly one supported schema-version metadata row.

    Validation is strictly read-only. It does not create, repair, migrate, or
    otherwise interpret a missing or unsupported store.

    Args:
        connection: Open SQLite connection to inspect.

    Raises:
        SchemaUnsupportedError: If metadata is absent, malformed, or not version 5.

    """
    candidate: object = connection
    if not isinstance(candidate, sqlite3.Connection):
        raise SchemaUnsupportedError
    try:
        rows = connection.execute(
            """
            SELECT singleton, schema_version
            FROM store_metadata
            ORDER BY singleton
            LIMIT 2
            """
        ).fetchall()
    except sqlite3.DatabaseError as error:
        raise SchemaUnsupportedError from error
    if len(rows) != 1:
        raise SchemaUnsupportedError
    singleton, schema_version = rows[0]
    if (
        type(singleton) is not int
        or singleton != 1
        or type(schema_version) is not int
        or schema_version != SCHEMA_VERSION
    ):
        raise SchemaUnsupportedError
    try:
        rows = connection.execute(
            """
            SELECT type, name, sql
            FROM sqlite_schema
            WHERE name NOT LIKE 'sqlite_%'
              AND type IN ('table', 'index', 'view', 'trigger')
            ORDER BY type, name
            """
        ).fetchall()
        signature = tuple(
            (object_type, name, sql.strip())
            for object_type, name, sql in rows
            if isinstance(sql, str)
        )
    except sqlite3.DatabaseError as error:
        raise SchemaUnsupportedError from error
    if signature != _EXPECTED_SCHEMA_SIGNATURE:
        raise SchemaUnsupportedError


def _contains_application_schema(connection: sqlite3.Connection) -> bool:
    """Return whether a transaction sees any non-SQLite schema object.

    Args:
        connection: Initialization transaction connection.

    Returns:
        Whether application-owned schema content already exists.

    """
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_schema
        WHERE name NOT LIKE 'sqlite_%'
          AND type IN ('table', 'index', 'view', 'trigger')
        LIMIT 1
        """
    ).fetchone()
    return row is not None


def _create_schema(connection: sqlite3.Connection) -> None:
    """Execute the fixed schema inside the caller-owned transaction.

    Args:
        connection: Initialization transaction connection.

    """
    for statement in _SCHEMA_STATEMENTS:
        connection.execute(statement)
