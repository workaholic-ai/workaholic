"""Explicit Session-provider boundary for command registration."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from workaholic.session import WorkaholicSession

type SessionProvider = Callable[[], WorkaholicSession]

_SESSION_OPERATIONS = (
    "up",
    "status",
    "context",
    "whoami",
    "login",
    "logout",
    "recover_local",
    "create_subject",
    "list_subjects",
    "update_subject",
    "set_subject_enabled",
    "set_instance_admin",
    "assign_grant",
    "list_grants",
    "revoke_grant",
    "create_token",
    "list_tokens",
    "revoke_token",
    "read_audit_events",
    "list_projects",
    "create_project",
    "bind_project",
    "create_task",
    "list_tasks",
    "get_task",
    "update_task",
    "block_task",
    "unblock_task",
    "cancel_task",
    "add_task_dependency",
    "remove_task_dependency",
    "submit_human_result",
    "approve_result",
    "reject_result",
    "get_task_details",
    "list_tasks_by_view",
    "read_task_events",
)


def acquire_session(provider: SessionProvider) -> WorkaholicSession:
    """Acquire and runtime-check one command-scoped Session.

    Args:
        provider: Explicit Session factory supplied by the composition root.

    Returns:
        Session implementing the complete cumulative presentation boundary.

    Raises:
        TypeError: If the provider or returned Session violates its contract.

    """
    candidate_provider: object = provider
    if not callable(candidate_provider):
        message = "CLI Session provider must be callable."
        raise TypeError(message)
    candidate: object = provider()
    if any(
        not callable(getattr(candidate, operation, None))
        for operation in _SESSION_OPERATIONS
    ):
        message = "CLI Session provider returned an invalid Session."
        raise TypeError(message)
    return cast("WorkaholicSession", candidate)
