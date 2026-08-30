"""Protect the accepted Phase 5 identity and authorization contracts."""

from __future__ import annotations

from pathlib import Path

_PROJECT_ROOT = Path(__file__).parents[3]
_DOCS = _PROJECT_ROOT / "docs"
_ADR = _DOCS / "adr" / "0013-phase-five-token-and-authorization-model.md"
_IDENTITY_ADR = _DOCS / "adr" / "0007-human-and-agent-identity-model.md"
_ARCHITECTURE = _DOCS / "architecture.md"
_CLI_CONTRACT = _DOCS / "cli-contract.md"
_GLOSSARY = _DOCS / "glossary.md"
_PERSISTENCE_CONTRACT = _DOCS / "persistence-contract.md"
_ROADMAP = _DOCS / "roadmap.md"
_THREAT_MODEL = _DOCS / "threat-model.md"
_README = _PROJECT_ROOT / "README.md"


def _read_normalized(path: Path) -> str:
    """Read a Markdown document with whitespace normalized.

    Args:
        path: Documentation file to read.

    Returns:
        Document text with every whitespace run replaced by one space.

    """
    return " ".join(path.read_text(encoding="utf-8").split())


def _section(path: Path, start: str, end: str) -> str:
    """Read and normalize one section between unique headings.

    Args:
        path: Documentation file to read.
        start: Heading that starts the section.
        end: Heading immediately following the section.

    Returns:
        Normalized text including the starting heading.

    """
    document = path.read_text(encoding="utf-8")
    start_index = document.index(start)
    end_index = document.index(end, start_index + len(start))
    return " ".join(document[start_index:end_index].split())


def test_phase_five_decision_is_accepted_and_linked() -> None:
    """Keep the owner-approved Phase 5 model durable and discoverable."""
    adr = _read_normalized(_ADR)

    assert "- Status: Accepted" in adr
    assert "- Decision date: 2026-08-28" in adr
    assert "- Deciders: Pavels Gurskis" in adr
    for heading in (
        "## Context",
        "## Decision",
        "## Alternatives considered",
        "## Consequences",
        "## References",
    ):
        assert heading in adr

    for path in (
        _IDENTITY_ADR,
        _ARCHITECTURE,
        _CLI_CONTRACT,
        _GLOSSARY,
        _PERSISTENCE_CONTRACT,
        _ROADMAP,
        _THREAT_MODEL,
    ):
        assert "ADR 0013" in _read_normalized(path), path

    assert "Accepted v1 contract through Phase 5" in _read_normalized(_CLI_CONTRACT)
    assert "Accepted v1 contract through Phase 5" in _read_normalized(
        _PERSISTENCE_CONTRACT
    )


def test_readme_publishes_verified_phase_five_behavior() -> None:
    """Publish implemented Phase 5 behavior without claiming Phase 6."""
    readme = _read_normalized(_README)

    assert "`0.5.0a1`" in readme
    assert "schema version `5`" in readme
    assert "distinct Subjects" in readme
    assert "Raw Tokens appear exactly once" in readme
    assert "viewer < agent < operator < owner" in readme.casefold()
    for command in (
        "workaholic auth login",
        "workaholic auth create-agent",
        "workaholic auth create-token",
        "workaholic auth recover-local",
    ):
        assert command in readme
    for deferred in (
        "`RemoteSession`, a server, remote profiles",
        "JSON or PostgreSQL persistence adapters",
        "capability-based scheduling",
        "SSO/OAuth",
    ):
        assert deferred in readme


def test_subject_handles_and_cumulative_roles_are_exact() -> None:
    """Fix stable identity and least-privilege role semantics before coding."""
    for path in (
        _ADR,
        _ARCHITECTURE,
        _CLI_CONTRACT,
        _PERSISTENCE_CONTRACT,
        _ROADMAP,
    ):
        document = _read_normalized(path)
        assert "`^[a-z][a-z0-9-]{1,62}$`" in document, path
        assert "`local-operator`" in document, path
        assert "viewer < agent < operator < owner" in document.casefold(), path
        assert "Instance administrator" in document, path
        assert "does not" in document, path
        assert "ProjectGrant" in document, path

    glossary = _read_normalized(_GLOSSARY)
    assert "immutable automation identifier" in glossary
    assert "display name is mutable presentation text" in glossary
    assert "Subjects are not deleted" in glossary


def test_token_format_lifetime_and_hash_contract_are_exact() -> None:
    """Prevent incompatible or recoverable bearer credential implementations."""
    for path in (
        _ADR,
        _ARCHITECTURE,
        _CLI_CONTRACT,
        _GLOSSARY,
        _PERSISTENCE_CONTRACT,
        _ROADMAP,
    ):
        document = _read_normalized(path)
        assert "`<token-id>.<secret>`" in document, path
        assert "32" in document, path
        assert "unpadded URL-safe base64" in document, path
        assert "SHA-256" in document, path

    for path in (_ADR, _CLI_CONTRACT, _PERSISTENCE_CONTRACT, _ROADMAP):
        document = _read_normalized(path)
        for phrase in (
            "`pending`",
            "`active`",
            "`expired`",
            "`revoked`",
            "now < expires_at",
            "`30d`",
            "`1h`",
            "`365d`",
            "`24h`",
            "`5m`",
        ):
            assert phrase in document, (path, phrase)

    adr = _read_normalized(_ADR)
    assert "constant-time digest comparison" in adr
    assert "Tokens are not deleted" in adr
    assert "does not introduce a database pepper" in adr


def test_credential_precedence_and_protected_storage_fail_closed() -> None:
    """Keep repository context and backend downgrade out of credential lookup."""
    adr = _read_normalized(_ADR)
    threat_model = _read_normalized(_THREAT_MODEL)
    for phrase in (
        "`WORKAHOLIC_TOKEN`",
        "`WORKAHOLIC_TOKEN_FILE`",
        "mutually exclusive",
        "never falls back",
        "`credentials/credentials.toml`",
        "`0600`",
        "`0700`",
    ):
        assert phrase in adr
        assert phrase in threat_model
    assert "operating-system keyring" in adr
    assert "operating-system credential store" in threat_model

    cli_contract = _read_normalized(_CLI_CONTRACT)
    roadmap = _read_normalized(_ROADMAP)
    for phrase in (
        "`WORKAHOLIC_TOKEN`",
        "`WORKAHOLIC_TOKEN_FILE`",
        "mutually exclusive",
        "operating-system keyring",
        "`credentials/credentials.toml`",
        "`0600`",
        "`0700`",
    ):
        assert phrase in cli_contract
        assert phrase.strip("`") in roadmap
    assert "must not fall back" in cli_contract
    assert "never falls through" in roadmap

    for path in (_ADR, _CLI_CONTRACT, _ROADMAP):
        document = _read_normalized(path)
        assert "`.workaholic.env`" in document, path
        assert "`profiles.toml`" in document, path

    assert "| Credential downgrade |" in threat_model
    assert "| Token provisioning failure |" in threat_model

    for path in (_ADR, _CLI_CONTRACT, _ROADMAP, _THREAT_MODEL):
        document = _read_normalized(path)
        assert "512 bytes" in document, path
        assert "1,048,576 bytes" in document, path
        assert "posix" in document.casefold(), path
        assert "current-user" in document, path


def test_token_provisioning_retry_is_secret_safe_and_resumable() -> None:
    """Define observable crash recovery without reconstructing raw Tokens."""
    for path in (_ADR, _CLI_CONTRACT, _PERSISTENCE_CONTRACT):
        document = _read_normalized(path)
        for phrase in (
            "pending",
            "activation",
            "idempotency key",
            "protected",
            "resume",
            "cannot be reconstructed",
        ):
            assert phrase in document, (path, phrase)

    for path in (_ADR, _CLI_CONTRACT, _THREAT_MODEL):
        document = _read_normalized(path)
        assert "Git worktree/repository" in document, path
        assert "outside the discovered Workspace" in document, path


def test_phase_five_command_inventory_and_secret_boundaries_are_fixed() -> None:
    """Require a complete discoverable CLI without exposing Token material."""
    cli_contract = _read_normalized(_CLI_CONTRACT)
    phase_five = _section(
        _ROADMAP,
        "# Phase 5 — Identity, authentication, and authorization",
        "# Phase 6 — Shared server and remote CLI",
    )
    commands = (
        "workaholic auth whoami",
        "workaholic auth login --token-file PATH|-",
        "workaholic auth logout",
        "workaholic auth recover-local --instance INSTANCE",
        "workaholic auth create-human HANDLE",
        "workaholic auth create-agent HANDLE",
        "workaholic auth list-subjects",
        "workaholic auth update-subject SUBJECT",
        "workaholic auth enable-subject SUBJECT",
        "workaholic auth disable-subject SUBJECT",
        "workaholic auth grant-admin SUBJECT",
        "workaholic auth revoke-admin SUBJECT",
        "workaholic auth grant SUBJECT viewer|agent|operator|owner",
        "workaholic auth list-grants --project PROJECT",
        "workaholic auth revoke-grant SUBJECT",
        "workaholic auth create-token SUBJECT --token-file ABSOLUTE_PATH",
        "workaholic auth list-tokens [SUBJECT]",
        "workaholic auth revoke-token TOKEN",
        "workaholic auth events",
    )
    for command in commands:
        assert command in cli_contract, command
        assert command in phase_five, command

    for path in (_ADR, _ARCHITECTURE, _CLI_CONTRACT, _ROADMAP):
        document = _read_normalized(path)
        assert "never" in document, path
        assert "raw Token" in document, path
        assert "normal" in document, path


def test_transaction_time_authorization_matrix_is_normative() -> None:
    """Prevent Session-only checks and Instance-admin Project bypasses."""
    for path in (
        _ADR,
        _ARCHITECTURE,
        _CLI_CONTRACT,
        _PERSISTENCE_CONTRACT,
        _ROADMAP,
        _THREAT_MODEL,
    ):
        document = _read_normalized(path)
        for phrase in (
            "active Token",
            "enabled Subject",
            "selected Instance",
            "required ProjectGrant",
        ):
            assert phrase in document, (path, phrase)
        assert "transaction" in document, path

    cli_contract = _read_normalized(_CLI_CONTRACT)
    for permission in (
        "Read Project task data",
        "Agent Claim/Attempt path",
        "Task/Human mutation path",
        "Manage ProjectGrants",
        "Create Projects/Subjects/Tokens",
    ):
        assert permission in cli_contract
    assert "Instance administrator status alone does not authorize" in cli_contract


def test_claim_ownership_survives_credential_state_changes() -> None:
    """Keep authentication revocation separate from unsafe force interruption."""
    for path in (
        _ADR,
        _ARCHITECTURE,
        _CLI_CONTRACT,
        _GLOSSARY,
        _PERSISTENCE_CONTRACT,
        _ROADMAP,
        _THREAT_MODEL,
    ):
        document = _read_normalized(path)
        assert "does not force-release" in document, path
        assert "same Subject" in document, path
        assert "exact" in document, path
        assert "Attempt" in document, path

    adr = _read_normalized(_ADR)
    assert "Claim ownership belongs to Subject identity" in adr
    assert "cannot override the lock" in adr


def test_last_administrator_and_owner_guards_are_atomic() -> None:
    """Prevent identity-management races from making state unmanageable."""
    for path in (
        _ADR,
        _ARCHITECTURE,
        _CLI_CONTRACT,
        _GLOSSARY,
        _PERSISTENCE_CONTRACT,
        _ROADMAP,
        _THREAT_MODEL,
    ):
        document = _read_normalized(path)
        assert "enabled administrator" in document, path
        assert "enabled Owner" in document, path

    threat_model = _read_normalized(_THREAT_MODEL)
    assert "| Administrative lockout |" in threat_model


def test_bootstrap_recovery_is_explicit_local_and_narrow() -> None:
    """Keep the necessary local recovery path from becoming an auth bypass."""
    for path in (_ADR, _ARCHITECTURE, _CLI_CONTRACT, _ROADMAP, _THREAT_MODEL):
        document = _read_normalized(path)
        assert "`auth recover-local`" in document, path
        assert "tokenless" in document, path
        assert "embedded" in document, path
        assert "bootstrap Subject" in document, path

    adr = _read_normalized(_ADR)
    for unchanged in ("ProjectGrant", "Project", "Task", "Claim", "Attempt"):
        assert unchanged in adr
    assert "unavailable through RemoteSession" in adr
    assert "| Recovery abuse |" in _read_normalized(_THREAT_MODEL)


def test_administrative_audit_is_attributable_and_secret_free() -> None:
    """Separate security history from TaskEvents without storing credentials."""
    event_types = (
        "instance_bootstrapped",
        "project_created",
        "subject_created",
        "subject_updated",
        "subject_enabled",
        "subject_disabled",
        "instance_admin_granted",
        "instance_admin_revoked",
        "project_grant_assigned",
        "project_grant_revoked",
        "token_issued",
        "token_revoked",
    )
    for path in (_ADR, _CLI_CONTRACT, _PERSISTENCE_CONTRACT):
        document = _read_normalized(path)
        assert "AuditEvent" in document, path
        assert "TaskEvents" in document, path
        assert "actor Token" in document, path
        for event_type in event_types:
            assert event_type in document, (path, event_type)

    for path in (_ADR, _ARCHITECTURE, _CLI_CONTRACT, _PERSISTENCE_CONTRACT):
        document = _read_normalized(path)
        for secret in ("raw Tokens", "Token hashes", "credential paths"):
            assert secret in document, (path, secret)

    for path in (_ADR, _CLI_CONTRACT, _PERSISTENCE_CONTRACT):
        document = _read_normalized(path)
        for field in (
            "`instance_id`",
            "`subject_id`",
            "`project_id`",
            "`project_key`",
            "`grant_role`",
            "`changed_fields`",
            "`previous_role`",
            "`previous_version`",
            "`token_id`",
            "`expires_at`",
        ):
            assert field in document, (path, field)
        assert "null actor Token" in document, path


def test_identity_and_audit_pagination_are_exact() -> None:
    """Use opaque scoped identity cursors and integer audit cursors."""
    for path in (_CLI_CONTRACT, _PERSISTENCE_CONTRACT):
        document = _read_normalized(path)
        for phrase in (
            "`v5.`",
            "unpadded URL-safe base64",
            "`actor_subject_id`",
            "`scope_id`",
            "`last`",
            "cross-Instance",
            "cross-Subject",
        ):
            assert phrase in document, (path, phrase)
        assert "`after` is" in document, path
        assert "`next_cursor`" in document, path
        assert "default" in document, path
        assert "100" in document, path
        assert "1 through 500" in document, path


def test_phase_five_errors_are_exact_and_non_disclosing() -> None:
    """Protect stable automation failures and credential error collapse."""
    cli_contract = _read_normalized(_CLI_CONTRACT)
    expected_errors = (
        ("AUTHENTICATION_REQUIRED", "5", "Authentication is required."),
        (
            "AUTHENTICATION_FAILED",
            "5",
            "The supplied credential is not valid.",
        ),
        ("SUBJECT_NOT_FOUND", "3", "The Subject was not found."),
        (
            "SUBJECT_HANDLE_CONFLICT",
            "4",
            "The Subject handle is already in use.",
        ),
        ("TOKEN_NOT_FOUND", "3", "The Token was not found."),
        ("GRANT_NOT_FOUND", "3", "The ProjectGrant was not found."),
        (
            "IDENTITY_VERSION_CONFLICT",
            "4",
            "The identity or grant changed after the expected version.",
        ),
        (
            "LAST_INSTANCE_ADMIN",
            "4",
            "The Instance must retain an enabled administrator.",
        ),
        (
            "LAST_PROJECT_OWNER",
            "4",
            "The Project must retain an enabled Owner.",
        ),
        (
            "CREDENTIAL_UNAVAILABLE",
            "10",
            "The credential store is unavailable.",
        ),
    )
    for code, exit_code, message in expected_errors:
        row = f"| `{code}` | {exit_code} | false | `{message}` |"
        assert row in cli_contract

    for phrase in (
        "missing Token row",
        "wrong digest",
        "pending/expired/revoked Token",
        "disabled Subject",
        "Instance mismatch",
        "`AUTHENTICATION_FAILED`",
    ):
        assert phrase in cli_contract


def test_phase_five_schema_and_deferred_scope_are_explicit() -> None:
    """Advance disposable storage without pulling Phase 6 or backlog work in."""
    for path in (_ADR, _CLI_CONTRACT, _PERSISTENCE_CONTRACT, _ROADMAP):
        document = _read_normalized(path)
        assert "schema version `5`" in document, path
        assert "Version `4`" in document, path
        assert "no migration" in document.casefold(), path

    phase_five = _section(
        _ROADMAP,
        "# Phase 5 — Identity, authentication, and authorization",
        "# Phase 6 — Shared server and remote CLI",
    )
    for phrase in (
        "does not add a server",
        "`RemoteSession`",
        "Capability filtering remains post-v1",
        "SSO/OAuth",
        "custom roles",
        "process interruption",
    ):
        assert phrase in phase_five
