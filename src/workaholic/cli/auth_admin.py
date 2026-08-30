"""Subject, administrator, and ProjectGrant CLI administration."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Never

import typer
from pydantic import ValidationError

from workaholic.cli.errors import (
    write_failure,
    write_identity_expected_version_required,
    write_invalid_input,
)
from workaholic.cli.options import (  # noqa: TC001 - Typer resolves aliases.
    CursorOption,
    ExpectedVersionOption,
    IdempotencyKeyOption,
    JsonOption,
    LimitOption,
    NonInteractiveOption,
    ProfileOption,
    ProjectOption,
)
from workaholic.cli.rendering import write_success
from workaholic.cli.runtime import SessionProvider, acquire_session
from workaholic.cli.serialization import (
    project_grant_page_data,
    project_grant_result_data,
    subject_page_data,
    subject_result_data,
)
from workaholic.domain import ProjectId, ProjectRole, SubjectId, SubjectKind
from workaholic.session import (
    ApplicationError,
    ApplicationErrorCode,
    GrantAssignRequest,
    GrantListRequest,
    GrantRevokeRequest,
    ProjectGrantPage,
    ProjectGrantResult,
    SubjectAdminRequest,
    SubjectCreateRequest,
    SubjectEnabledRequest,
    SubjectListRequest,
    SubjectPage,
    SubjectResult,
    SubjectUpdateRequest,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from workaholic.domain import ProjectGrant, Subject
    from workaholic.session import WorkaholicSession

SubjectArgument = Annotated[
    str,
    typer.Argument(
        ...,
        help="Exact immutable Subject handle or public Subject ID.",
        metavar="SUBJECT",
    ),
]
RoleArgument = Annotated[
    str,
    typer.Argument(
        ...,
        help="Cumulative Project role: viewer, agent, operator, or owner.",
        metavar="ROLE",
    ),
]
DisplayNameOption = Annotated[
    str | None,
    typer.Option(
        ...,
        "--display-name",
        help="Set the Human-readable Subject display name.",
        metavar="NAME",
        prompt=False,
    ),
]

_IDENTITY_INPUT_INVALID_MESSAGE = "Identity administration input is invalid."
_SUBJECT_NOT_FOUND_MESSAGE = "The Subject was not found."
_GRANT_NOT_FOUND_MESSAGE = "The ProjectGrant was not found."
_IDENTITY_PAGE_LIMIT = 500
_MAX_INTERACTIVE_PAGES = 10_000


@dataclass(frozen=True, slots=True)
class _PreparedVersion:
    """One command-scoped Session and exact optimistic version."""

    session: WorkaholicSession
    expected_version: int


def register_identity_admin_commands(  # noqa: PLR0915
    application: typer.Typer,
    *,
    session_provider: SessionProvider,
) -> None:
    """Register Subject, administrator, and ProjectGrant commands.

    Args:
        application: Shared ``auth`` Typer command group.
        session_provider: Command-scoped Session factory.

    """

    @application.command("create-human")
    def create_human(  # noqa: PLR0913 - explicit public CLI contract.
        handle: SubjectArgument,
        display_name: DisplayNameOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        profile: ProfileOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Create one enabled non-administrative Human Subject."""
        del non_interactive
        _create_subject(
            session_provider,
            kind=SubjectKind.HUMAN,
            handle=handle,
            display_name=display_name,
            idempotency_key=idempotency_key,
            profile=profile,
            json_mode=json_mode,
        )

    @application.command("create-agent")
    def create_agent(  # noqa: PLR0913 - explicit public CLI contract.
        handle: SubjectArgument,
        display_name: DisplayNameOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        profile: ProfileOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Create one enabled non-administrative Agent Subject."""
        del non_interactive
        _create_subject(
            session_provider,
            kind=SubjectKind.AGENT,
            handle=handle,
            display_name=display_name,
            idempotency_key=idempotency_key,
            profile=profile,
            json_mode=json_mode,
        )

    @application.command("list-subjects")
    def list_subjects(
        cursor: CursorOption = None,
        limit: LimitOption = 100,
        profile: ProfileOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """List one stable administrator-visible page of Subjects."""
        del non_interactive
        try:
            request = SubjectListRequest(
                cursor=cursor,
                limit=limit,
                profile=profile,
            )
            result = acquire_session(session_provider).list_subjects(request)
            _write_subject_page(result, json_mode=json_mode)
        except ValidationError:
            write_invalid_input(_IDENTITY_INPUT_INVALID_MESSAGE, json_mode=json_mode)
        except Exception as error:  # noqa: BLE001 - redact Session failures.
            write_failure(error, json_mode=json_mode)

    @application.command("update-subject")
    def update_subject(  # noqa: PLR0913
        subject: SubjectArgument,
        display_name: Annotated[
            str,
            typer.Option(
                ...,
                "--display-name",
                help="Replace the Human-readable display name.",
                metavar="NAME",
                prompt=False,
            ),
        ],
        expected_version: ExpectedVersionOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        profile: ProfileOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Replace one Subject display name at its exact version."""
        _run_subject_change(
            session_provider,
            subject=subject,
            expected_version=expected_version,
            action="update display name",
            provisional=lambda version: SubjectUpdateRequest(
                subject=_subject_selector(subject),
                expected_version=version,
                display_name=display_name,
                profile=profile,
                idempotency_key=idempotency_key,
            ),
            invoke=lambda session, request: session.update_subject(request),
            json_mode=json_mode,
            non_interactive=non_interactive,
        )

    def set_enabled(  # noqa: PLR0913
        subject: str,
        *,
        enabled: bool,
        expected_version: int | None,
        idempotency_key: str | None,
        profile: str | None,
        json_mode: bool,
        non_interactive: bool,
    ) -> None:
        """Run one explicit Subject enabled-state command."""
        action = "enable Subject" if enabled else "disable Subject"
        _run_subject_change(
            session_provider,
            subject=subject,
            expected_version=expected_version,
            action=action,
            provisional=lambda version: SubjectEnabledRequest(
                subject=_subject_selector(subject),
                expected_version=version,
                enabled=enabled,
                profile=profile,
                idempotency_key=idempotency_key,
            ),
            invoke=lambda session, request: session.set_subject_enabled(request),
            json_mode=json_mode,
            non_interactive=non_interactive,
        )

    @application.command("enable-subject")
    def enable_subject(  # noqa: PLR0913
        subject: SubjectArgument,
        expected_version: ExpectedVersionOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        profile: ProfileOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Enable one Subject at its exact version."""
        set_enabled(
            subject,
            enabled=True,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            profile=profile,
            json_mode=json_mode,
            non_interactive=non_interactive,
        )

    @application.command("disable-subject")
    def disable_subject(  # noqa: PLR0913
        subject: SubjectArgument,
        expected_version: ExpectedVersionOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        profile: ProfileOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Disable one Subject while preserving administrator and Owner guards."""
        set_enabled(
            subject,
            enabled=False,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            profile=profile,
            json_mode=json_mode,
            non_interactive=non_interactive,
        )

    def set_admin(  # noqa: PLR0913
        subject: str,
        *,
        is_admin: bool,
        expected_version: int | None,
        idempotency_key: str | None,
        profile: str | None,
        json_mode: bool,
        non_interactive: bool,
    ) -> None:
        """Run one explicit Instance-administrator state command."""
        action = "grant Instance administrator" if is_admin else "revoke administrator"
        _run_subject_change(
            session_provider,
            subject=subject,
            expected_version=expected_version,
            action=action,
            provisional=lambda version: SubjectAdminRequest(
                subject=_subject_selector(subject),
                expected_version=version,
                is_instance_admin=is_admin,
                profile=profile,
                idempotency_key=idempotency_key,
            ),
            invoke=lambda session, request: session.set_instance_admin(request),
            json_mode=json_mode,
            non_interactive=non_interactive,
        )

    @application.command("grant-admin")
    def grant_admin(  # noqa: PLR0913
        subject: SubjectArgument,
        expected_version: ExpectedVersionOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        profile: ProfileOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Grant Instance-administrator status at an exact Subject version."""
        set_admin(
            subject,
            is_admin=True,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            profile=profile,
            json_mode=json_mode,
            non_interactive=non_interactive,
        )

    @application.command("revoke-admin")
    def revoke_admin(  # noqa: PLR0913
        subject: SubjectArgument,
        expected_version: ExpectedVersionOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        profile: ProfileOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Revoke Instance-administrator status while retaining one administrator."""
        set_admin(
            subject,
            is_admin=False,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            profile=profile,
            json_mode=json_mode,
            non_interactive=non_interactive,
        )

    @application.command("grant")
    def assign_grant(  # noqa: PLR0913
        subject: SubjectArgument,
        role: RoleArgument,
        project: ProjectOption,
        expected_version: ExpectedVersionOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        profile: ProfileOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Create or replace one cumulative ProjectGrant."""
        if project is None:
            write_invalid_input(_IDENTITY_INPUT_INVALID_MESSAGE, json_mode=json_mode)
        try:
            parsed_role = ProjectRole(role)
            project_selector = _project_selector(project)
            session = acquire_session(session_provider)
            version = expected_version
            if expected_version is None and _is_interactive_mode(
                json_mode=json_mode,
                non_interactive=non_interactive,
            ):
                target = _find_subject(session, subject=subject, profile=profile)
                current = _find_grant(
                    session,
                    subject_id=target.id,
                    project=project_selector,
                    profile=profile,
                )
                version = None if current is None else current.version
                current_text = "absent" if current is None else current.role.value
                typer.echo(
                    f"{target.handle}\tgrant={current_text}"
                    f"\tversion={version or 0}\taction=assign {parsed_role.value}"
                )
                if not typer.confirm("Proceed?", default=False):
                    typer.echo("No changes made.")
                    return
            request = GrantAssignRequest(
                subject=_subject_selector(subject),
                project=project_selector,
                role=parsed_role,
                expected_version=version,
                profile=profile,
                idempotency_key=idempotency_key,
            )
            result = session.assign_grant(request)
            _write_grant_result(result, json_mode=json_mode)
        except ValidationError, ValueError:
            write_invalid_input(_IDENTITY_INPUT_INVALID_MESSAGE, json_mode=json_mode)
        except Exception as error:  # noqa: BLE001 - redact Session failures.
            write_failure(error, json_mode=json_mode)

    @application.command("list-grants")
    def list_grants(  # noqa: PLR0913 - explicit public CLI contract.
        project: ProjectOption,
        cursor: CursorOption = None,
        limit: LimitOption = 100,
        profile: ProfileOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """List one stable Project-scoped ProjectGrant page."""
        del non_interactive
        if project is None:
            write_invalid_input(_IDENTITY_INPUT_INVALID_MESSAGE, json_mode=json_mode)
        try:
            request = GrantListRequest(
                project=_project_selector(project),
                cursor=cursor,
                limit=limit,
                profile=profile,
            )
            result = acquire_session(session_provider).list_grants(request)
            _write_grant_page(result, json_mode=json_mode)
        except ValidationError, ValueError:
            write_invalid_input(_IDENTITY_INPUT_INVALID_MESSAGE, json_mode=json_mode)
        except Exception as error:  # noqa: BLE001 - redact Session failures.
            write_failure(error, json_mode=json_mode)

    @application.command("revoke-grant")
    def revoke_grant(  # noqa: PLR0913
        subject: SubjectArgument,
        project: ProjectOption,
        expected_version: ExpectedVersionOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        profile: ProfileOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Revoke one exact ProjectGrant while retaining an enabled Owner."""
        if project is None:
            write_invalid_input(_IDENTITY_INPUT_INVALID_MESSAGE, json_mode=json_mode)
        _require_existing_version(
            expected_version,
            json_mode=json_mode,
            non_interactive=non_interactive,
        )
        try:
            project_selector = _project_selector(project)
            session = acquire_session(session_provider)
            version = expected_version
            if version is None:
                target = _find_subject(session, subject=subject, profile=profile)
                current = _find_grant(
                    session,
                    subject_id=target.id,
                    project=project_selector,
                    profile=profile,
                )
                if current is None:
                    _raise_grant_not_found()
                version = current.version
                typer.echo(
                    f"{target.handle}\tgrant={current.role.value}"
                    f"\tversion={version}\taction=revoke grant"
                )
                if not typer.confirm("Proceed?", default=False):
                    typer.echo("No changes made.")
                    return
            request = GrantRevokeRequest(
                subject=_subject_selector(subject),
                project=project_selector,
                expected_version=version,
                profile=profile,
                idempotency_key=idempotency_key,
            )
            result = session.revoke_grant(request)
            _write_grant_result(result, json_mode=json_mode)
        except ValidationError, ValueError:
            write_invalid_input(_IDENTITY_INPUT_INVALID_MESSAGE, json_mode=json_mode)
        except Exception as error:  # noqa: BLE001 - redact Session failures.
            write_failure(error, json_mode=json_mode)


def _create_subject(  # noqa: PLR0913
    provider: SessionProvider,
    *,
    kind: SubjectKind,
    handle: str,
    display_name: str | None,
    idempotency_key: str | None,
    profile: str | None,
    json_mode: bool,
) -> None:
    """Validate, invoke, and render one Subject creation."""
    try:
        request = SubjectCreateRequest(
            kind=kind,
            handle=handle,
            display_name=display_name,
            profile=profile,
            idempotency_key=idempotency_key,
        )
        result = acquire_session(provider).create_subject(request)
        _write_subject_result(result, json_mode=json_mode)
    except ValidationError, ValueError:
        write_invalid_input(_IDENTITY_INPUT_INVALID_MESSAGE, json_mode=json_mode)
    except Exception as error:  # noqa: BLE001 - redact Session failures.
        write_failure(error, json_mode=json_mode)


def _run_subject_change[  # noqa: PLR0913 - explicit mutation boundary.
    RequestT: SubjectUpdateRequest | SubjectEnabledRequest | SubjectAdminRequest
](
    provider: SessionProvider,
    *,
    subject: str,
    expected_version: int | None,
    action: str,
    provisional: Callable[[int], RequestT],
    invoke: Callable[[WorkaholicSession, RequestT], SubjectResult],
    json_mode: bool,
    non_interactive: bool,
) -> None:
    """Run one optimistic existing-Subject mutation without automatic retry."""
    _require_existing_version(
        expected_version,
        json_mode=json_mode,
        non_interactive=non_interactive,
    )
    try:
        candidate = provisional(1 if expected_version is None else expected_version)
        prepared = _prepare_subject_version(
            provider,
            subject=subject,
            expected_version=expected_version,
            action=action,
            profile=candidate.profile,
        )
        if prepared is None:
            return
        request = provisional(prepared.expected_version)
        result = invoke(prepared.session, request)
        _write_subject_result(result, json_mode=json_mode)
    except ValidationError, ValueError:
        write_invalid_input(_IDENTITY_INPUT_INVALID_MESSAGE, json_mode=json_mode)
    except Exception as error:  # noqa: BLE001 - redact Session failures.
        write_failure(error, json_mode=json_mode)


def _prepare_subject_version(
    provider: SessionProvider,
    *,
    subject: str,
    expected_version: int | None,
    action: str,
    profile: str | None,
) -> _PreparedVersion | None:
    """Acquire one Session and optionally confirm a current Subject version."""
    session = acquire_session(provider)
    if expected_version is not None:
        return _PreparedVersion(session, expected_version)
    current = _find_subject(session, subject=subject, profile=profile)
    typer.echo(
        f"{current.handle}\t{current.kind.value}\tversion={current.version}"
        f"\taction={action}"
    )
    if not typer.confirm("Proceed?", default=False):
        typer.echo("No changes made.")
        return None
    return _PreparedVersion(session, current.version)


def _require_existing_version(
    expected_version: int | None,
    *,
    json_mode: bool,
    non_interactive: bool,
) -> None:
    """Require an explicit version outside an interactive Human terminal."""
    if expected_version is None and not _is_interactive_mode(
        json_mode=json_mode,
        non_interactive=non_interactive,
    ):
        write_identity_expected_version_required(json_mode=json_mode)


def _is_interactive_mode(*, json_mode: bool, non_interactive: bool) -> bool:
    """Return whether safe one-read Human convenience may be used."""
    if json_mode or non_interactive:
        return False
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except AttributeError, OSError:
        return False


def _find_subject(
    session: WorkaholicSession,
    *,
    subject: str,
    profile: str | None,
) -> Subject:
    """Resolve one exact Subject through bounded stable pages."""
    selector = _subject_selector(subject)
    cursor: str | None = None
    visited: set[str] = set()
    for _ in range(_MAX_INTERACTIVE_PAGES):
        page = session.list_subjects(
            SubjectListRequest(
                cursor=cursor,
                limit=_IDENTITY_PAGE_LIMIT,
                profile=profile,
            )
        )
        _require_subject_page(page)
        for item in page.subjects:
            if (isinstance(selector, SubjectId) and item.id == selector) or (
                isinstance(selector, str) and item.handle == selector
            ):
                return item
        if page.next_cursor is None:
            break
        if page.next_cursor in visited:
            raise TypeError
        visited.add(page.next_cursor)
        cursor = page.next_cursor
    raise ApplicationError(
        ApplicationErrorCode.SUBJECT_NOT_FOUND,
        _SUBJECT_NOT_FOUND_MESSAGE,
    )


def _find_grant(
    session: WorkaholicSession,
    *,
    subject_id: SubjectId,
    project: ProjectId | str,
    profile: str | None,
) -> ProjectGrant | None:
    """Resolve one exact current ProjectGrant through bounded stable pages."""
    cursor: str | None = None
    visited: set[str] = set()
    for _ in range(_MAX_INTERACTIVE_PAGES):
        page = session.list_grants(
            GrantListRequest(
                project=project,
                cursor=cursor,
                limit=_IDENTITY_PAGE_LIMIT,
                profile=profile,
            )
        )
        _require_grant_page(page)
        for item in page.grants:
            if item.subject_id == subject_id:
                return item
        if page.next_cursor is None:
            return None
        if page.next_cursor in visited:
            raise TypeError
        visited.add(page.next_cursor)
        cursor = page.next_cursor
    raise TypeError


def _raise_grant_not_found() -> Never:
    """Raise the fixed non-disclosing missing-ProjectGrant failure."""
    raise ApplicationError(
        ApplicationErrorCode.GRANT_NOT_FOUND,
        _GRANT_NOT_FOUND_MESSAGE,
    )


def _subject_selector(value: str) -> SubjectId | str:
    """Parse an opaque Subject ID while preserving handles as exact text."""
    return SubjectId(value) if value.startswith("sub_") else value


def _project_selector(value: str) -> ProjectId | str:
    """Parse an opaque Project ID while preserving keys as exact text."""
    return ProjectId(value) if value.startswith("prj_") else value


def _require_subject_page(value: object) -> SubjectPage:
    """Require an exact validated Subject page from a Session."""
    if not isinstance(value, SubjectPage):
        raise TypeError
    return value


def _require_grant_page(value: object) -> ProjectGrantPage:
    """Require an exact validated ProjectGrant page from a Session."""
    if not isinstance(value, ProjectGrantPage):
        raise TypeError
    return value


def _write_subject_result(value: object, *, json_mode: bool) -> None:
    """Validate and render one Subject mutation result."""
    if not isinstance(value, SubjectResult):
        raise TypeError
    write_success(subject_result_data(value), json_mode=json_mode)


def _write_subject_page(value: object, *, json_mode: bool) -> None:
    """Validate and render one Subject page."""
    if not isinstance(value, SubjectPage):
        raise TypeError
    write_success(subject_page_data(value), json_mode=json_mode)


def _write_grant_result(value: object, *, json_mode: bool) -> None:
    """Validate and render one ProjectGrant mutation result."""
    if not isinstance(value, ProjectGrantResult):
        raise TypeError
    write_success(project_grant_result_data(value), json_mode=json_mode)


def _write_grant_page(value: object, *, json_mode: bool) -> None:
    """Validate and render one ProjectGrant page."""
    if not isinstance(value, ProjectGrantPage):
        raise TypeError
    write_success(project_grant_page_data(value), json_mode=json_mode)
