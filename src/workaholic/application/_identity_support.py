"""Shared explicit guards for Phase 5 identity application services."""

from __future__ import annotations

from typing import TYPE_CHECKING

from workaholic.application.errors import ApplicationError, ApplicationErrorCode
from workaholic.domain import RequestId, validate_utc_timestamp

if TYPE_CHECKING:
    from datetime import datetime

    from workaholic.application.ports import Clock, IdentityIdentifierFactory


def require_callable(value: object, member_name: str, label: str) -> None:
    """Require one explicit dependency method.

    Args:
        value: Candidate dependency.
        member_name: Required callable attribute.
        label: Safe dependency label.

    Raises:
        TypeError: If the dependency lacks the required operation.

    """
    if not callable(getattr(value, member_name, None)):
        message = f"Identity {label} must provide {member_name}()."
        raise TypeError(message)


def dependency_time(clock: Clock, *, operation: str) -> datetime:
    """Read and validate one authoritative dependency time.

    Args:
        clock: Injected application clock.
        operation: Safe operation label.

    Returns:
        Valid timezone-aware UTC time.

    Raises:
        ApplicationError: If the clock raises or violates its contract.

    """
    try:
        return validate_utc_timestamp(
            clock.now(),
            label=f"{operation} dependency time",
        )
    except Exception as error:
        raise invalid_dependencies(operation) from error


def dependency_request_id(
    identifiers: IdentityIdentifierFactory,
    *,
    operation: str,
) -> RequestId:
    """Generate and runtime-validate one request identity.

    Args:
        identifiers: Injected identity identifier factory.
        operation: Safe operation label.

    Returns:
        Valid candidate request identity.

    Raises:
        ApplicationError: If generation raises or returns a wrong type.

    """
    try:
        request_id = identifiers.new_request_id()
    except Exception as error:
        raise invalid_dependencies(operation) from error
    if not isinstance(request_id, RequestId):
        raise invalid_dependencies(operation)
    return request_id


def invalid_input(operation: str) -> ApplicationError:
    """Build one stable input failure.

    Args:
        operation: Safe operation label.

    Returns:
        Stable invalid-input application error.

    """
    return ApplicationError(
        ApplicationErrorCode.INVALID_INPUT,
        f"{operation} input is invalid.",
    )


def invalid_dependencies(operation: str) -> ApplicationError:
    """Build one stable dependency failure.

    Args:
        operation: Safe operation label.

    Returns:
        Stable internal application error.

    """
    return ApplicationError(
        ApplicationErrorCode.INTERNAL_ERROR,
        f"{operation} dependencies returned invalid values.",
    )


def invalid_result(operation: str) -> ApplicationError:
    """Build one stable repository-result failure.

    Args:
        operation: Safe operation label.

    Returns:
        Stable internal application error.

    """
    return ApplicationError(
        ApplicationErrorCode.INTERNAL_ERROR,
        f"{operation} persistence returned an invalid result.",
    )
