"""Stable SQLite repository façade over focused semantic operations."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from workaholic.persistence.sqlite import _queries as sqlite_queries
from workaholic.persistence.sqlite import _task_views as sqlite_task_views
from workaholic.persistence.sqlite._audit_events import (
    read_audit_events as _read_audit_events,
)
from workaholic.persistence.sqlite._authentication import (
    authenticate_token as _authenticate_token,
)
from workaholic.persistence.sqlite._authentication import (
    get_current_identity as _get_current_identity,
)
from workaholic.persistence.sqlite._authorization import (
    authorize_actor as _authorize_actor,
)
from workaholic.persistence.sqlite._bootstrap import (
    bootstrap_local_project as _bootstrap_local_project,
)
from workaholic.persistence.sqlite._grants import (
    assign_project_grant as _assign_project_grant,
)
from workaholic.persistence.sqlite._grants import (
    list_project_grants as _list_project_grants,
)
from workaholic.persistence.sqlite._grants import (
    revoke_project_grant as _revoke_project_grant,
)
from workaholic.persistence.sqlite._projects import create_project as _create_project
from workaholic.persistence.sqlite._subjects import create_subject as _create_subject
from workaholic.persistence.sqlite._subjects import list_subjects as _list_subjects
from workaholic.persistence.sqlite._subjects import (
    set_instance_admin as _set_instance_admin,
)
from workaholic.persistence.sqlite._subjects import (
    set_subject_enabled as _set_subject_enabled,
)
from workaholic.persistence.sqlite._subjects import update_subject as _update_subject
from workaholic.persistence.sqlite._task_claims import (
    claim_next_task as _claim_next_task,
)
from workaholic.persistence.sqlite._task_claims import claim_task as _claim_task
from workaholic.persistence.sqlite._task_claims import release_claim as _release_claim
from workaholic.persistence.sqlite._task_claims import renew_claim as _renew_claim
from workaholic.persistence.sqlite._task_dependencies import (
    add_task_dependency as _add_task_dependency,
)
from workaholic.persistence.sqlite._task_dependencies import (
    remove_task_dependency as _remove_task_dependency,
)
from workaholic.persistence.sqlite._task_execution import (
    report_task_progress as _report_task_progress,
)
from workaholic.persistence.sqlite._task_lifecycle import (
    block_task as _block_task,
)
from workaholic.persistence.sqlite._task_lifecycle import (
    cancel_task as _cancel_task,
)
from workaholic.persistence.sqlite._task_lifecycle import (
    unblock_task as _unblock_task,
)
from workaholic.persistence.sqlite._task_lifecycle import (
    update_task_if_version as _update_task_if_version,
)
from workaholic.persistence.sqlite._task_results import (
    approve_result as _approve_result,
)
from workaholic.persistence.sqlite._task_results import reject_result as _reject_result
from workaholic.persistence.sqlite._task_results import (
    submit_agent_result as _submit_agent_result,
)
from workaholic.persistence.sqlite._task_results import (
    submit_human_result as _submit_human_result,
)
from workaholic.persistence.sqlite._tasks import create_task as _create_task
from workaholic.persistence.sqlite._tokens import activate_token as _activate_token
from workaholic.persistence.sqlite._tokens import (
    issue_pending_token as _issue_pending_token,
)
from workaholic.persistence.sqlite._tokens import list_tokens as _list_tokens
from workaholic.persistence.sqlite._tokens import recover_local as _recover_local
from workaholic.persistence.sqlite._tokens import revoke_token as _revoke_token
from workaholic.persistence.sqlite.schema import initialize_empty_store

if TYPE_CHECKING:
    from workaholic.application import (
        ActivateTokenMutation,
        AddTaskDependencyMutation,
        ApproveResultMutation,
        AssignProjectGrantMutation,
        AuditEventPage,
        AuthenticateToken,
        AuthorizeActor,
        BootstrapMutation,
        BootstrapResult,
        ClaimNextTaskMutation,
        ClaimTaskMutation,
        Clock,
        CreateSubjectMutation,
        CurrentIdentityResult,
        GetCurrentIdentity,
        GetLocalStatus,
        GetProjectByKey,
        GetTask,
        GetTaskDetails,
        IssueTokenMutation,
        ListInstanceTasks,
        ListProjectGrants,
        ListProjects,
        ListSubjects,
        ListTasks,
        ListTasksByView,
        ListTokens,
        ProjectCreationMutation,
        ProjectCreationResult,
        ProjectGrantPage,
        ProjectGrantResult,
        ReadAuditEvents,
        ReadTaskEvents,
        RecoverLocalMutation,
        RejectResultMutation,
        ReleaseClaimMutation,
        RemoveTaskDependencyMutation,
        RenewClaimMutation,
        ReportTaskProgressMutation,
        RevokeProjectGrantMutation,
        RevokeTokenMutation,
        SetInstanceAdminMutation,
        SetSubjectEnabledMutation,
        StatusResult,
        SubjectPage,
        SubjectResult,
        SubmitAgentResultMutation,
        SubmitHumanResultMutation,
        TaskBlockMutation,
        TaskCancelMutation,
        TaskClaimResult,
        TaskCreationMutation,
        TaskDetails,
        TaskEventPage,
        TaskMutationResult,
        TaskPage,
        TaskProgressResult,
        TaskSubmissionResult,
        TaskUnblockMutation,
        TaskUpdateMutation,
        TokenPage,
        TokenResult,
        UpdateSubjectMutation,
    )
    from workaholic.domain import AuthenticatedActor, Project, Subject, Task


class _UtcSystemClock:
    """Supply authoritative UTC time for direct repository query use."""

    def now(self) -> datetime:
        """Return the current timezone-aware UTC timestamp."""
        return datetime.now(UTC)


class SQLiteRepository:
    """Cumulative SQLite implementation of atomic semantic operations."""

    def __init__(self, database_path: Path, *, clock: Clock | None = None) -> None:
        """Bind the adapter to one absolute local database path.

        Args:
            database_path: Absolute path to a schema-version-5 SQLite store.
            clock: Optional authoritative clock for time-derived read views.

        Raises:
            TypeError: If the value is not an absolute Path.

        """
        candidate_path: object = database_path
        if not isinstance(candidate_path, Path) or not candidate_path.is_absolute():
            message = "SQLite repository database_path must be an absolute Path."
            raise TypeError(message)
        selected_clock = _UtcSystemClock() if clock is None else clock
        if not callable(getattr(selected_clock, "now", None)):
            message = "SQLite repository clock must provide now()."
            raise TypeError(message)
        self._database_path = candidate_path
        self._clock = selected_clock

    @property
    def database_path(self) -> Path:
        """Return the immutable configured database path.

        Returns:
            Absolute SQLite database path.

        """
        return self._database_path

    def bootstrap_local_project(
        self,
        mutation: BootstrapMutation,
    ) -> BootstrapResult:
        """Atomically bootstrap or locate the single local Project.

        Args:
            mutation: Validated candidate identities and semantic input.

        Returns:
            The committed local identity and Owner authorization graph.

        """
        initialize_empty_store(self._database_path)
        return _bootstrap_local_project(self._database_path, mutation)

    def create_task(self, mutation: TaskCreationMutation) -> Task:
        """Atomically allocate, create, and attribute one initial Task.

        Args:
            mutation: Validated Task creation mutation.

        Returns:
            The new or idempotently replayed Task.

        """
        return _create_task(self._database_path, mutation)

    def update_task_if_version(
        self,
        mutation: TaskUpdateMutation,
    ) -> TaskMutationResult:
        """Atomically update Task definition fields at an expected version.

        Args:
            mutation: Validated optimistic Task update mutation.

        Returns:
            The committed Task and its attributable update event.

        """
        return _update_task_if_version(self._database_path, mutation)

    def block_task(self, mutation: TaskBlockMutation) -> TaskMutationResult:
        """Atomically block an open Task at an expected version.

        Args:
            mutation: Validated optimistic blocking mutation.

        Returns:
            The committed blocked Task and its attributable event.

        """
        return _block_task(self._database_path, mutation)

    def unblock_task(self, mutation: TaskUnblockMutation) -> TaskMutationResult:
        """Atomically return a blocked Task to open.

        Args:
            mutation: Validated optimistic unblocking mutation.

        Returns:
            The committed open Task and its attributable event.

        """
        return _unblock_task(self._database_path, mutation)

    def cancel_task(self, mutation: TaskCancelMutation) -> TaskMutationResult:
        """Atomically cancel a mutable Task at an expected version.

        Args:
            mutation: Validated optimistic cancellation mutation.

        Returns:
            The committed cancelled Task and its attributable event.

        """
        return _cancel_task(self._database_path, mutation)

    def claim_task(self, mutation: ClaimTaskMutation) -> TaskClaimResult:
        """Atomically acquire one explicit ready Task for a Human.

        Args:
            mutation: Validated targeted Human Claim mutation.

        Returns:
            Current Human Claim with ordered acquisition events.

        """
        return _claim_task(self._database_path, mutation)

    def claim_next_task(
        self,
        mutation: ClaimNextTaskMutation,
    ) -> TaskClaimResult:
        """Atomically pull the highest-ranked ready Task for an Agent.

        Args:
            mutation: Validated Project-scoped Agent Claim mutation.

        Returns:
            Selected Task, active Claim/Attempt, and ordered events.

        """
        return _claim_next_task(self._database_path, mutation)

    def renew_claim(self, mutation: RenewClaimMutation) -> TaskClaimResult:
        """Atomically renew a Human Claim or heartbeat an Agent Attempt.

        Args:
            mutation: Validated exact owner token and replacement duration.

        Returns:
            Task and atomically renewed Claim ownership.

        """
        return _renew_claim(self._database_path, mutation)

    def release_claim(self, mutation: ReleaseClaimMutation) -> TaskClaimResult:
        """Atomically release one exact current owner token.

        Args:
            mutation: Validated exact Human or Agent owner token.

        Returns:
            Task with no Claim and a nullable released Agent Attempt.

        """
        return _release_claim(self._database_path, mutation)

    def report_task_progress(
        self,
        mutation: ReportTaskProgressMutation,
    ) -> TaskProgressResult:
        """Atomically append structured progress for a current Agent Attempt.

        Args:
            mutation: Validated progress and exact current owner token.

        Returns:
            Unchanged Task and ownership with ordered progress events.

        """
        return _report_task_progress(self._database_path, mutation)

    def add_task_dependency(
        self,
        mutation: AddTaskDependencyMutation,
    ) -> TaskMutationResult:
        """Atomically add one same-Project prerequisite edge.

        Args:
            mutation: Validated optimistic dependency addition.

        Returns:
            The committed dependant Task and attributable update event.

        """
        return _add_task_dependency(self._database_path, mutation)

    def remove_task_dependency(
        self,
        mutation: RemoveTaskDependencyMutation,
    ) -> TaskMutationResult:
        """Atomically remove one existing prerequisite edge.

        Args:
            mutation: Validated optimistic dependency removal.

        Returns:
            The committed dependant Task and attributable update event.

        """
        return _remove_task_dependency(self._database_path, mutation)

    def submit_human_result(
        self,
        mutation: SubmitHumanResultMutation,
    ) -> TaskSubmissionResult:
        """Atomically submit one Human Result without an Agent Attempt.

        Args:
            mutation: Validated optimistic Human submission mutation.

        Returns:
            Committed Task, Result, and ordered semantic events.

        """
        return _submit_human_result(self._database_path, mutation)

    def submit_agent_result(
        self,
        mutation: SubmitAgentResultMutation,
    ) -> TaskSubmissionResult:
        """Atomically submit one Result through an exact current Agent Attempt.

        Args:
            mutation: Validated optimistic Agent submission mutation.

        Returns:
            Committed Task, Result, terminal Attempt, and ordered events.

        """
        return _submit_agent_result(self._database_path, mutation)

    def approve_result(
        self,
        mutation: ApproveResultMutation,
    ) -> TaskSubmissionResult:
        """Atomically approve the current pending Result.

        Args:
            mutation: Validated optimistic approval mutation.

        Returns:
            Committed Task, approved Result, and ordered events.

        """
        return _approve_result(self._database_path, mutation)

    def reject_result(
        self,
        mutation: RejectResultMutation,
    ) -> TaskSubmissionResult:
        """Atomically reject and deselect the current pending Result.

        Args:
            mutation: Validated optimistic rejection mutation.

        Returns:
            Reopened Task, retained Result, and rejection event.

        """
        return _reject_result(self._database_path, mutation)

    def create_project(
        self,
        mutation: ProjectCreationMutation,
    ) -> ProjectCreationResult:
        """Atomically create one Project and grant its creator Owner access.

        Args:
            mutation: Validated Project creation mutation.

        Returns:
            The new or idempotently replayed Project and Owner grant.

        """
        return _create_project(self._database_path, mutation)

    def create_subject(self, mutation: CreateSubjectMutation) -> SubjectResult:
        """Create one enabled, non-administrative Subject atomically.

        Args:
            mutation: Authenticated Subject creation mutation.

        Returns:
            Committed or idempotently replayed Subject.

        """
        return _create_subject(self._database_path, mutation)

    def list_subjects(self, command: ListSubjects) -> SubjectPage:
        """List one stable handle-ordered page of Instance Subjects.

        Args:
            command: Authenticated actor-bound pagination query.

        Returns:
            Stable Subject page with an opaque continuation cursor.

        """
        return _list_subjects(
            self._database_path,
            command,
            now=self._clock.now(),
        )

    def update_subject(self, mutation: UpdateSubjectMutation) -> SubjectResult:
        """Update one Subject display name at its exact version.

        Args:
            mutation: Authenticated optimistic Subject update.

        Returns:
            Committed or idempotently replayed Subject.

        """
        return _update_subject(self._database_path, mutation)

    def set_subject_enabled(
        self,
        mutation: SetSubjectEnabledMutation,
    ) -> SubjectResult:
        """Enable or disable one Subject at its exact version.

        Args:
            mutation: Authenticated optimistic state mutation.

        Returns:
            Committed or idempotently replayed Subject.

        """
        return _set_subject_enabled(self._database_path, mutation)

    def set_instance_admin(
        self,
        mutation: SetInstanceAdminMutation,
    ) -> SubjectResult:
        """Grant or revoke Instance administration at an exact version.

        Args:
            mutation: Authenticated optimistic administrator mutation.

        Returns:
            Committed or idempotently replayed Subject.

        """
        return _set_instance_admin(self._database_path, mutation)

    def authenticate_token(
        self,
        command: AuthenticateToken,
    ) -> AuthenticatedActor:
        """Authenticate one canonical Token digest at an explicit time.

        Args:
            command: Parsed Token identity, digest, expected Instance, and time.

        Returns:
            Secret-free authenticated actor context.

        """
        return _authenticate_token(self._database_path, command)

    def get_current_identity(
        self,
        command: GetCurrentIdentity,
    ) -> CurrentIdentityResult:
        """Revalidate and return current non-secret identity metadata.

        Args:
            command: Previously authenticated actor query.

        Returns:
            Current enabled Subject and active Token metadata.

        """
        return _get_current_identity(
            self._database_path,
            command,
            now=self._clock.now(),
        )

    def issue_pending_token(self, mutation: IssueTokenMutation) -> TokenResult:
        """Persist one pending non-authenticating Token digest.

        Args:
            mutation: Authenticated pending-Token metadata mutation.

        Returns:
            Non-secret pending Token metadata.

        """
        return _issue_pending_token(self._database_path, mutation)

    def activate_token(self, mutation: ActivateTokenMutation) -> TokenResult:
        """Activate one pending Token after its credential sink succeeds.

        Args:
            mutation: Authenticated activation mutation.

        Returns:
            Non-secret active Token metadata.

        """
        return _activate_token(self._database_path, mutation)

    def list_tokens(self, command: ListTokens) -> TokenPage:
        """List one stable page of visible non-secret Token metadata.

        Args:
            command: Authenticated self or administrator query.

        Returns:
            Creation-ordered Token metadata page.

        """
        return _list_tokens(
            self._database_path,
            command,
            now=self._clock.now(),
        )

    def revoke_token(self, mutation: RevokeTokenMutation) -> TokenResult:
        """Monotonically revoke one visible Token.

        Args:
            mutation: Authenticated Token revocation mutation.

        Returns:
            Non-secret revoked Token metadata.

        """
        return _revoke_token(self._database_path, mutation)

    def recover_local(
        self,
        mutation: RecoverLocalMutation,
    ) -> CurrentIdentityResult:
        """Replace every bootstrap-Human Token through confirmed recovery.

        Args:
            mutation: Exact tokenless local recovery mutation.

        Returns:
            Bootstrap Human and active replacement Token metadata.

        """
        return _recover_local(self._database_path, mutation)

    def read_audit_events(self, command: ReadAuditEvents) -> AuditEventPage:
        """Read one bounded ascending administrator-authorized audit page.

        Args:
            command: Authenticated Instance audit cursor query.

        Returns:
            Strictly ascending administrative events and next cursor.

        """
        return _read_audit_events(
            self._database_path,
            command,
            now=self._clock.now(),
        )

    def authorize_actor(self, command: AuthorizeActor) -> Subject:
        """Resolve one fresh authorization projection transactionally.

        Args:
            command: Actor, permission, scope, kind, and authoritative time.

        Returns:
            Current authorized Subject projection.

        """
        return _authorize_actor(self._database_path, command)

    def assign_project_grant(
        self,
        mutation: AssignProjectGrantMutation,
    ) -> ProjectGrantResult:
        """Create or replace one cumulative ProjectGrant atomically.

        Args:
            mutation: Authenticated create-or-replace grant mutation.

        Returns:
            Committed or idempotently replayed grant snapshot.

        """
        return _assign_project_grant(self._database_path, mutation)

    def list_project_grants(
        self,
        command: ListProjectGrants,
    ) -> ProjectGrantPage:
        """List one stable page of grants for an exact Project.

        Args:
            command: Authenticated Project-scoped pagination query.

        Returns:
            Current grant page with an opaque continuation cursor.

        """
        return _list_project_grants(
            self._database_path,
            command,
            now=self._clock.now(),
        )

    def revoke_project_grant(
        self,
        mutation: RevokeProjectGrantMutation,
    ) -> ProjectGrantResult:
        """Revoke one exact current ProjectGrant atomically.

        Args:
            mutation: Authenticated optimistic grant revocation.

        Returns:
            Revoked grant snapshot or exact idempotent replay.

        """
        return _revoke_project_grant(self._database_path, mutation)

    def get_local_status(self, command: GetLocalStatus) -> StatusResult:
        """Read authorized local status without mutating storage.

        Args:
            command: Validated exact identity selection.

        Returns:
            Current local status.

        """
        return sqlite_queries.get_local_status(
            self._database_path,
            command,
            now=self._clock.now(),
        )

    def list_projects(self, command: ListProjects) -> tuple[Project, ...]:
        """List authorized Projects by immutable key.

        Args:
            command: Validated Instance and Subject selection.

        Returns:
            Authorized Projects ordered by key ascending.

        """
        return sqlite_queries.list_projects(
            self._database_path,
            command,
            now=self._clock.now(),
        )

    def get_project_by_key(self, command: GetProjectByKey) -> Project:
        """Read one authorized Project by immutable key.

        Args:
            command: Validated Instance-, Subject-, and key-bound query.

        Returns:
            Matching authorized Project.

        """
        return sqlite_queries.get_project_by_key(
            self._database_path,
            command,
            now=self._clock.now(),
        )

    def list_tasks(self, command: ListTasks) -> TaskPage:
        """Read one deterministic Project-bound Task page.

        Args:
            command: Validated pagination query.

        Returns:
            Tasks ordered by Project-local number.

        """
        return sqlite_queries.list_tasks(
            self._database_path,
            command,
            now=self._clock.now(),
        )

    def list_tasks_for_instance(self, command: ListInstanceTasks) -> TaskPage:
        """Read one Task page across authorized Projects in an Instance.

        Args:
            command: Validated Instance-scoped pagination query.

        Returns:
            Tasks ordered by Project key and Project-local number.

        """
        return sqlite_queries.list_tasks_for_instance(
            self._database_path,
            command,
            now=self._clock.now(),
        )

    def get_task(self, command: GetTask) -> Task:
        """Read one Task by exact UID or stable Human key.

        Args:
            command: Validated Project-scoped selector.

        Returns:
            Matching immutable Task.

        """
        return sqlite_queries.get_task(
            self._database_path,
            command,
            now=self._clock.now(),
        )

    def get_task_details(self, command: GetTaskDetails) -> TaskDetails:
        """Read complete Task details with authoritative derived readiness.

        Args:
            command: Validated Project-scoped detail query.

        Returns:
            Complete Task details at one clock snapshot.

        """
        return sqlite_task_views.get_task_details(
            self._database_path,
            command,
            now=self._clock.now(),
        )

    def list_tasks_by_view(self, command: ListTasksByView) -> TaskPage:
        """Read one view-bound deterministic Phase 4 Task page.

        Args:
            command: Validated view, scope, and pagination query.

        Returns:
            Tasks and aligned readiness using a version-3 cursor.

        """
        return sqlite_task_views.list_tasks_by_view(
            self._database_path,
            command,
            now=self._clock.now(),
        )

    def read_task_events_after(self, command: ReadTaskEvents) -> TaskEventPage:
        """Read one authorized bounded TaskEvent snapshot.

        Args:
            command: Validated Task, Project, actor, cursor, and limit query.

        Returns:
            Polling-safe attributable events in cursor order.

        """
        return sqlite_queries.read_task_events_after(
            self._database_path,
            command,
            now=self._clock.now(),
        )
