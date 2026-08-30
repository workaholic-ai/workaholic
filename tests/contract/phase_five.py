"""Reusable typed fixtures for cumulative Phase 5 conformance."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from tests.contract.phase_four import (
    PhaseFourIdentifierFactory,
    PhaseFourRepositoryFactory,
    PhaseFourSessionFactory,
)
from tests.contract.phase_two import DeterministicIdentifierFactory

from workaholic.application import (
    AssignProjectGrantMutation,
    BootstrapResult,
    CreateSubjectMutation,
    IdentityRepository,
    IssueTokenMutation,
    WorkaholicRepository,
)
from workaholic.auth import HumanCredential
from workaholic.domain import (
    AuditEventId,
    AuthenticatedActor,
    ProjectId,
    ProjectRole,
    RequestId,
    SubjectId,
    SubjectKind,
    TokenId,
)

if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from pathlib import Path

    from workaholic.auth import RawToken
    from workaholic.session import WorkaholicSession

PHASE_FIVE_NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


class PhaseFiveTransactionFailurePoint(StrEnum):
    """Semantic identity-write boundaries exposed for rollback conformance."""

    SUBJECT_AUDIT = "subject_audit"
    SUBJECT_IDEMPOTENCY = "subject_idempotency"
    TOKEN_AUDIT = "token_audit"  # noqa: S105 - semantic failure label.
    TOKEN_IDEMPOTENCY = "token_idempotency"  # noqa: S105 - semantic label.
    GRANT_AUDIT = "grant_audit"
    GRANT_IDEMPOTENCY = "grant_idempotency"


class PhaseFiveIdentifierFactory(PhaseFourIdentifierFactory, Protocol):
    """Generate deterministic cumulative IDs, including Token and audit IDs."""

    def new_token_id(self) -> TokenId:
        """Return one opaque Token identifier."""
        ...

    def new_audit_event_id(self) -> AuditEventId:
        """Return one opaque administrative AuditEvent identifier."""
        ...


class PhaseFiveRepository(WorkaholicRepository, IdentityRepository, Protocol):
    """Expose cumulative task plus identity persistence operations."""


class DeterministicPhaseFiveIdentifierFactory(DeterministicIdentifierFactory):
    """Thread-safe deterministic identifier source including Phase 5 IDs."""

    def new_token_id(self) -> TokenId:
        """Return the next deterministic Token identifier."""
        return TokenId(self._next("tok"))

    def new_audit_event_id(self) -> AuditEventId:
        """Return the next deterministic AuditEvent identifier."""
        return AuditEventId(self._next("aev"))


class PhaseFiveRepositoryFactory(PhaseFourRepositoryFactory, Protocol):
    """Construct authenticated repositories with semantic failure hooks."""

    def identifiers(self, namespace: str) -> PhaseFiveIdentifierFactory:
        """Construct one deterministic cumulative identity source."""
        ...

    def bootstrap_authenticated(
        self,
        root: Path,
        namespace: str,
    ) -> tuple[PhaseFiveRepository, BootstrapResult, AuthenticatedActor]:
        """Create one bootstrap graph with an active Human Token.

        Args:
            root: Test-owned backend persistence root.
            namespace: Stable scenario-specific identity namespace.

        Returns:
            Repository, bootstrap graph, and authenticated administrator actor.

        """
        ...

    def inject_phase_five_failure(
        self,
        point: PhaseFiveTransactionFailurePoint,
    ) -> AbstractContextManager[None]:
        """Fail one semantic identity write inside its active transaction.

        Args:
            point: Stable adapter-neutral failure boundary.

        Returns:
            Context manager scoping the injected failure.

        """
        ...


class PhaseFiveSessionFactory(PhaseFourSessionFactory, Protocol):
    """Construct isolated Sessions for distinct explicit credentials."""

    def create_with_token(
        self,
        root: Path,
        workspace: Path,
        token_file: Path,
    ) -> WorkaholicSession:
        """Reopen one local Session using an explicit mounted Token file.

        Args:
            root: Test-owned trusted data root.
            workspace: Existing exact Workspace directory.
            token_file: Protected absolute Token source.

        Returns:
            Session authenticated only by ``token_file``.

        """
        ...


@dataclass(slots=True)
class DeterministicCredentialStore:
    """In-memory Human credential store with explicit deterministic state."""

    values: dict[str, HumanCredential] = field(default_factory=dict)

    def load(self, profile: str) -> HumanCredential | None:
        """Return the exact stored credential for one profile, if present."""
        return self.values.get(profile)

    def replace(self, credential: HumanCredential) -> None:
        """Atomically replace one profile's in-memory credential."""
        self.values[credential.profile] = credential

    def delete(self, profile: str) -> None:
        """Idempotently remove one profile's in-memory credential."""
        self.values.pop(profile, None)


def phase_five_time(offset: int = 0) -> datetime:
    """Return a deterministic Phase 5 UTC timestamp.

    Args:
        offset: Nonnegative whole-second offset from the fixture epoch.

    Returns:
        UTC fixture timestamp advanced by ``offset`` seconds.

    Raises:
        ValueError: If ``offset`` is not a nonnegative integer.

    """
    if type(offset) is not int or offset < 0:
        raise ValueError
    return PHASE_FIVE_NOW + timedelta(seconds=offset)


def actor_for(
    bootstrap: BootstrapResult,
    *,
    token_id: TokenId,
) -> AuthenticatedActor:
    """Build the bootstrap Human's authenticated actor context.

    Args:
        bootstrap: Validated bootstrap graph.
        token_id: Active Token attributed to the bootstrap Human.

    Returns:
        Secret-free authenticated actor.

    """
    return AuthenticatedActor(
        instance_id=bootstrap.instance.id,
        subject_id=bootstrap.subject.id,
        subject_kind=SubjectKind.HUMAN,
        token_id=token_id,
    )


def subject_mutation(
    actor: AuthenticatedActor,
    label: str,
    *,
    kind: SubjectKind = SubjectKind.AGENT,
    idempotency_key: str | None = None,
) -> CreateSubjectMutation:
    """Build one deterministic Subject creation mutation.

    Args:
        actor: Authenticated Instance administrator.
        label: Stable Subject, handle, request, and event suffix.
        kind: Immutable Subject kind.
        idempotency_key: Optional caller replay key.

    Returns:
        Validated attributable Subject mutation.

    """
    handle_label = label.replace("_", "-")
    return CreateSubjectMutation(
        actor=actor,
        request_id=RequestId(f"req_{label}"),
        occurred_at=phase_five_time(1),
        idempotency_key=idempotency_key,
        subject_id=SubjectId(f"sub_{label}"),
        kind=kind,
        handle=f"{handle_label}-subject",
        display_name=f"{label.title()} subject",
    )


def token_mutation(
    actor: AuthenticatedActor,
    subject: SubjectId,
    label: str,
    *,
    digest: str,
    idempotency_key: str | None = None,
) -> IssueTokenMutation:
    """Build one deterministic pending Token mutation without a raw secret.

    Args:
        actor: Authenticated Instance administrator.
        subject: Target Subject identity.
        label: Stable Token and request suffix.
        digest: Canonical complete-Token SHA-256 digest.
        idempotency_key: Optional activation replay key.

    Returns:
        Validated non-secret pending Token mutation.

    """
    return IssueTokenMutation(
        actor=actor,
        request_id=RequestId(f"req_{label}"),
        occurred_at=phase_five_time(2),
        idempotency_key=idempotency_key,
        token_id=TokenId(f"tok_{label}"),
        subject=subject,
        token_digest=digest,
        expires_at=phase_five_time(2) + timedelta(days=1),
    )


def grant_mutation(  # noqa: PLR0913 - complete grant boundary fixture.
    actor: AuthenticatedActor,
    subject: SubjectId,
    project: ProjectId,
    label: str,
    *,
    role: ProjectRole = ProjectRole.AGENT,
    expected_version: int | None = None,
    idempotency_key: str | None = None,
) -> AssignProjectGrantMutation:
    """Build one deterministic cumulative ProjectGrant assignment.

    Args:
        actor: Authenticated Owner or Instance administrator.
        subject: Target enabled Subject.
        project: Exact target Project.
        label: Stable request suffix.
        role: Cumulative role to assign.
        expected_version: Existing grant version or null for creation.
        idempotency_key: Optional caller replay key.

    Returns:
        Validated ProjectGrant mutation.

    """
    return AssignProjectGrantMutation(
        actor=actor,
        request_id=RequestId(f"req_{label}"),
        occurred_at=phase_five_time(3),
        idempotency_key=idempotency_key,
        subject=subject,
        project=project,
        role=role,
        expected_version=expected_version,
    )


def credential(
    *,
    profile: str,
    bootstrap: BootstrapResult,
    raw_token: RawToken,
) -> HumanCredential:
    """Build one deterministic profile-bound Human credential.

    Args:
        profile: Trusted local profile name.
        bootstrap: Bootstrap graph owning the Human Subject.
        raw_token: Canonical opaque bearer Token.

    Returns:
        Validated in-memory Human credential.

    """
    return HumanCredential(
        profile=profile,
        instance_id=bootstrap.instance.id,
        subject_id=bootstrap.subject.id,
        raw_token=raw_token,
    )
