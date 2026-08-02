"""Protect the accepted Phase 1 delivery and public command contracts."""

from __future__ import annotations

from pathlib import Path

_PROJECT_ROOT = Path(__file__).parents[3]
_DOCS = _PROJECT_ROOT / "docs"
_ROADMAP = _DOCS / "roadmap.md"
_ARCHITECTURE = _DOCS / "architecture.md"
_CLI_CONTRACT = _DOCS / "cli-contract.md"
_PERSISTENCE_CONTRACT = _DOCS / "persistence-contract.md"
_CONTEXT_ADR = _DOCS / "adr" / "0006-project-context-trust-model.md"
_IDENTITY_ADR = _DOCS / "adr" / "0007-human-and-agent-identity-model.md"


def _read_normalized(path: Path) -> str:
    """Read Markdown with whitespace normalized for contract assertions.

    Args:
        path: Documentation file to read.

    Returns:
        Document text with each whitespace run replaced by one space.

    """
    return " ".join(path.read_text(encoding="utf-8").split())


def _section(document: str, start: str, end: str) -> str:
    """Extract a normalized Markdown section between two unique headings.

    Args:
        document: Complete Markdown source.
        start: Heading that starts the required section.
        end: Heading that follows the required section.

    Returns:
        Normalized text from ``start`` up to, but not including, ``end``.

    """
    start_index = document.index(start)
    end_index = document.index(end, start_index + len(start))
    return " ".join(document[start_index:end_index].split())


def test_roadmap_assigns_phase_one_foundations_only_once() -> None:
    """Keep context, automation, events, identity, and version timing aligned."""
    roadmap = _ROADMAP.read_text(encoding="utf-8")
    phase_one = _section(
        roadmap,
        "# Phase 1 — Local SQLite vertical slice",
        "# Phase 2 — Multi-project context and working-directory discovery",
    )
    phase_two = _section(
        roadmap,
        "# Phase 2 — Multi-project context and working-directory discovery",
        "# Phase 3 — Complete task lifecycle and audit model",
    )
    phase_three = _section(
        roadmap,
        "# Phase 3 — Complete task lifecycle and audit model",
        "# Phase 4 — Local Claims, Agent execution, and extended JSON CLI contract",
    )
    phase_four = _section(
        roadmap,
        "# Phase 4 — Local Claims, Agent execution, and extended JSON CLI contract",
        "# Phase 5 — Identity, authentication, and authorization",
    )
    phase_five = _section(
        roadmap,
        "# Phase 5 — Identity, authentication, and authorization",
        "# Phase 6 — Shared server and remote CLI",
    )

    for required in (
        "Subject",
        "ProjectGrant",
        ".workaholic.env in the exact current directory",
        "Every accepted Task mutation",
        "All six commands accept `--json` and `--non-interactive`",
        "initial task version `1`",
    ):
        assert required in phase_one

    assert "extends the strict context file written by Phase 1" in phase_two
    assert "version increments and stale-update rejection" in phase_three
    assert "extends the JSON, non-interactive, idempotency" in phase_four
    assert "Phase 1 already creates one real Human Subject" in phase_five


def test_phase_one_context_is_exact_directory_and_local_only() -> None:
    """Prevent upward discovery or configurable trust from moving into Phase 1."""
    architecture = _read_normalized(_ARCHITECTURE)
    context_adr = _read_normalized(_CONTEXT_ADR)

    for document in (architecture, context_adr):
        assert "<current-working-directory>/.workaholic.env" in document
        assert "does not search a parent directory" in document
        assert "Phase 2" in document

    local_profile_contract = (
        "`WORKAHOLIC_PROFILE=local` selects the built-in embedded SQLite profile"
    )
    assert local_profile_contract in architecture
    assert "Phase 1 does not read a user profile" in context_adr
    assert "select RemoteSession" in context_adr


def test_phase_one_bootstrap_uses_a_real_attributed_human_without_tokens() -> None:
    """Keep bootstrap attribution real while deferring credentials to Phase 5."""
    architecture = _read_normalized(_ARCHITECTURE)
    identity_adr = _read_normalized(_IDENTITY_ADR)
    persistence = _read_normalized(_PERSISTENCE_CONTRACT)

    for document in (architecture, identity_adr, persistence):
        assert "Local operator" in document
        assert "Instance administrator" in document
        assert "Owner" in document
        assert "Phase 5" in document

    assert "Phase 2 does not create a Token" in architecture
    assert "Phase 1 is an embedded single-user slice" in identity_adr
    assert "it creates no bearer Token" in identity_adr
    assert "Bootstrap does not persist a Token" in persistence
    assert "does not append a TaskEvent" in persistence


def test_phase_one_cli_contract_specifies_every_command_and_success_shape() -> None:
    """Ensure implementation tasks never need to invent Phase 1 CLI behavior."""
    contract = _read_normalized(_CLI_CONTRACT)
    commands = (
        "workaholic up --project-key KEY",
        "workaholic status",
        "workaholic project list",
        "workaholic task add TITLE",
        "workaholic task list",
        "workaholic task show TASK",
    )
    success_fields = (
        '"instance"',
        '"project"',
        '"subject"',
        '"workspace"',
        '"mode"',
        '"schema_version"',
        '"projects"',
        '"task"',
        '"tasks"',
        '"next_cursor"',
    )
    task_fields = (
        "`uid`",
        "`project_id`",
        "`number`",
        "`key`",
        "`title`",
        "`objective`",
        "`state`",
        "`priority`",
        "`version`",
        "`created_by`",
        "`created_at`",
        "`updated_at`",
    )

    for command in commands:
        assert command in contract
    for field in success_fields:
        assert field in contract
    for field in task_fields:
        assert field in contract

    assert "Every command below accepts `--json` and `--non-interactive`" in contract
    assert "[--idempotency-key KEY]" in contract
    assert "[--objective TEXT] [--priority INTEGER]" in contract
    assert "[--cursor CURSOR] [--limit INTEGER]" in contract


def test_phase_one_cli_contract_fixes_defaults_paging_and_errors() -> None:
    """Lock the accepted defaults, idempotency semantics, and exit categories."""
    contract = _read_normalized(_CLI_CONTRACT)
    error_codes = (
        "INVALID_INPUT",
        "CONTEXT_NOT_FOUND",
        "CONTEXT_INVALID",
        "NOT_INITIALIZED",
        "TASK_NOT_FOUND",
        "PROJECT_KEY_CONFLICT",
        "IDEMPOTENCY_CONFLICT",
        "PERMISSION_DENIED",
        "SCHEMA_UNSUPPORTED",
        "STORAGE_BUSY",
        "STORAGE_UNAVAILABLE",
        "INTERNAL_ERROR",
    )

    for error_code in error_codes:
        assert f"`{error_code}`" in contract
    for exit_code in ("`2`", "`3`", "`4`", "`5`", "`10`"):
        assert exit_code in contract

    for requirement in (
        "defaults to the normalized title",
        "defaults to `50`",
        "state `open` and version `1`",
        "ordered by task number ascending",
        "defaults to `100`",
        "must not exceed `500`",
        "same key and input returns that outcome",
        "`IDEMPOTENCY_CONFLICT` and changes neither state nor events",
    ):
        assert requirement in contract


def test_phase_one_persistence_contract_is_atomic_and_non_mutating_on_reads() -> None:
    """Protect the executable SQLite invariants required by later tasks."""
    persistence = _read_normalized(_PERSISTENCE_CONTRACT)

    for requirement in (
        "schema version `1`",
        "Task at version `1`",
        "one attributable `task_created` event",
        "generated request identity",
        "default limit is 100",
        "maximum is 500",
        "These reads do not change persisted state",
        "fail explicitly",
        "leave an unsupported store unchanged",
    ):
        assert requirement in persistence
