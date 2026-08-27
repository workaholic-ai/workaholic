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
        TaskClaimResult,
        TaskMutationResult,
        TaskProgressResult,
        TaskSubmissionResult,
        UnblockTaskInput,
        UpdateTaskInput,
    )
    from workaholic.session import (
        AgentHeartbeatRequest,
        AgentProgressRequest,
        AgentReleaseRequest,
        AgentSubmitRequest,
        AgentTaskClaimRequest,
        HumanClaimReleaseRequest,
        HumanClaimRenewRequest,
        HumanTaskClaimRequest,
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


class UnavailablePhaseFourServices:
    """Fail if a pre-Phase-4 focused test invokes execution services."""

    def claim_task(self, **_kwargs: object) -> TaskClaimResult:
        """Fail an unexpected targeted Claim."""
        pytest.fail("This focused Session test must not claim a Task")

    def claim_next_task(self, **_kwargs: object) -> TaskClaimResult:
        """Fail an unexpected Agent pull."""
        pytest.fail("This focused Session test must not pull a Task")

    def renew_claim(self, **_kwargs: object) -> TaskClaimResult:
        """Fail an unexpected Claim renewal."""
        pytest.fail("This focused Session test must not renew a Claim")

    def release_claim(self, **_kwargs: object) -> TaskClaimResult:
        """Fail an unexpected Claim release."""
        pytest.fail("This focused Session test must not release a Claim")

    def report_progress(self, **_kwargs: object) -> TaskProgressResult:
        """Fail an unexpected Agent progress report."""
        pytest.fail("This focused Session test must not report progress")

    def submit_result(self, **_kwargs: object) -> TaskSubmissionResult:
        """Fail an unexpected Agent Result submission."""
        pytest.fail("This focused Session test must not submit an Agent Result")


class UnavailablePhaseFourSession:
    """Expose the exact Phase 4 Session surface while failing every call."""

    def claim_task(
        self,
        request: HumanTaskClaimRequest,
    ) -> TaskClaimResult:
        """Fail an unexpected Human Claim request."""
        del request
        pytest.fail("This focused test must not claim a Task")

    def claim_next_task(
        self,
        request: AgentTaskClaimRequest,
    ) -> TaskClaimResult:
        """Fail an unexpected Agent Claim request."""
        del request
        pytest.fail("This focused test must not pull a Task")

    def renew_claim(
        self,
        request: HumanClaimRenewRequest,
    ) -> TaskClaimResult:
        """Fail an unexpected Human Claim renewal request."""
        del request
        pytest.fail("This focused test must not renew a Claim")

    def heartbeat_attempt(
        self,
        request: AgentHeartbeatRequest,
    ) -> TaskClaimResult:
        """Fail an unexpected Agent heartbeat request."""
        del request
        pytest.fail("This focused test must not heartbeat an Attempt")

    def release_claim(
        self,
        request: HumanClaimReleaseRequest,
    ) -> TaskClaimResult:
        """Fail an unexpected Human Claim release request."""
        del request
        pytest.fail("This focused test must not release a Human Claim")

    def release_attempt(
        self,
        request: AgentReleaseRequest,
    ) -> TaskClaimResult:
        """Fail an unexpected Agent release request."""
        del request
        pytest.fail("This focused test must not release an Agent Attempt")

    def report_progress(
        self,
        request: AgentProgressRequest,
    ) -> TaskProgressResult:
        """Fail an unexpected Agent progress request."""
        del request
        pytest.fail("This focused test must not report progress")

    def submit_agent_result(
        self,
        request: AgentSubmitRequest,
    ) -> TaskSubmissionResult:
        """Fail an unexpected Agent Result request."""
        del request
        pytest.fail("This focused test must not submit an Agent Result")
