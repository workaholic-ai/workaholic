"""Shared strict Session fakes for capabilities outside a focused test."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from workaholic.application import (
        AddTaskDependencyInput,
        ApproveResultInput,
        BlockTaskInput,
        CancelTaskInput,
        RejectResultInput,
        RemoveTaskDependencyInput,
        SubmitHumanResultInput,
        TaskMutationResult,
        TaskSubmissionResult,
        UnblockTaskInput,
        UpdateTaskInput,
    )


class UnavailablePhaseThreeServices:
    """Fail if a Phase 1-2 focused test invokes a Phase 3 mutation service."""

    def update(self, _command: UpdateTaskInput) -> TaskMutationResult:
        """Fail an unexpected Task update."""
        pytest.fail("This focused Session test must not update a Task")

    def block(self, _command: BlockTaskInput) -> TaskMutationResult:
        """Fail an unexpected Task block."""
        pytest.fail("This focused Session test must not block a Task")

    def unblock(self, _command: UnblockTaskInput) -> TaskMutationResult:
        """Fail an unexpected Task unblock."""
        pytest.fail("This focused Session test must not unblock a Task")

    def cancel(self, _command: CancelTaskInput) -> TaskMutationResult:
        """Fail an unexpected Task cancellation."""
        pytest.fail("This focused Session test must not cancel a Task")

    def add(self, _command: AddTaskDependencyInput) -> TaskMutationResult:
        """Fail an unexpected dependency addition."""
        pytest.fail("This focused Session test must not add a dependency")

    def remove(self, _command: RemoveTaskDependencyInput) -> TaskMutationResult:
        """Fail an unexpected dependency removal."""
        pytest.fail("This focused Session test must not remove a dependency")

    def submit(self, _command: SubmitHumanResultInput) -> TaskSubmissionResult:
        """Fail an unexpected Human submission."""
        pytest.fail("This focused Session test must not submit a Result")

    def approve(self, _command: ApproveResultInput) -> TaskSubmissionResult:
        """Fail an unexpected Result approval."""
        pytest.fail("This focused Session test must not approve a Result")

    def reject(self, _command: RejectResultInput) -> TaskSubmissionResult:
        """Fail an unexpected Result rejection."""
        pytest.fail("This focused Session test must not reject a Result")
