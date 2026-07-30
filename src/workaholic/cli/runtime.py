"""Explicit Session-provider boundary for command registration."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from workaholic.session import WorkaholicSession

type SessionProvider = Callable[[], WorkaholicSession]

_SESSION_OPERATIONS = (
    "up",
    "status",
    "list_projects",
    "create_task",
    "list_tasks",
    "get_task",
)


def acquire_session(provider: SessionProvider) -> WorkaholicSession:
    """Acquire and runtime-check one command-scoped Session.

    Args:
        provider: Explicit Session factory supplied by the composition root.

    Returns:
        Session implementing the complete Phase 1 presentation boundary.

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
