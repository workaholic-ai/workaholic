"""Protect the accepted Phase 2 multi-project and trust-boundary contracts."""

from __future__ import annotations

from pathlib import Path

_PROJECT_ROOT = Path(__file__).parents[3]
_DOCS = _PROJECT_ROOT / "docs"
_README = _PROJECT_ROOT / "README.md"
_ROADMAP = _DOCS / "roadmap.md"
_ARCHITECTURE = _DOCS / "architecture.md"
_CLI_CONTRACT = _DOCS / "cli-contract.md"
_PERSISTENCE_CONTRACT = _DOCS / "persistence-contract.md"
_THREAT_MODEL = _DOCS / "threat-model.md"
_CONTEXT_ADR = _DOCS / "adr" / "0006-project-context-trust-model.md"
_NORMATIVE_DOCUMENTS = (
    _ROADMAP,
    _ARCHITECTURE,
    _CLI_CONTRACT,
    _PERSISTENCE_CONTRACT,
    _THREAT_MODEL,
    _CONTEXT_ADR,
)


def _read_normalized(path: Path) -> str:
    """Read Markdown with whitespace normalized for contract assertions.

    Args:
        path: Documentation file to read.

    Returns:
        Document text with each whitespace run replaced by one space.

    """
    return " ".join(path.read_text(encoding="utf-8").split())


def _section(path: Path, start: str, end: str) -> str:
    """Extract a normalized Markdown section between unique headings.

    Args:
        path: Documentation file to read.
        start: Heading that starts the required section.
        end: Heading that follows the required section.

    Returns:
        Normalized text from ``start`` up to, but not including, ``end``.

    """
    document = path.read_text(encoding="utf-8")
    start_index = document.index(start)
    end_index = document.index(end, start_index + len(start))
    return " ".join(document[start_index:end_index].split())


def test_phase_two_public_commands_have_one_exact_contract() -> None:
    """Keep every Phase 2 command signature aligned across owner documents."""
    commands = (
        "workaholic up --project-key KEY [--project-name NAME] [--profile PROFILE]",
        "workaholic status [--profile PROFILE] [--project KEY]",
        "workaholic context [--profile PROFILE] [--project KEY]",
        "workaholic project create --key KEY --name NAME",
        "workaholic project bind KEY [PATH] [--profile PROFILE] [--replace]",
        "workaholic project list [--profile PROFILE]",
        "workaholic task add TITLE [--project KEY]",
        "workaholic task list [--project KEY | --all-projects]",
        "workaholic task show TASK [--project KEY]",
    )
    roadmap = _read_normalized(_ROADMAP)
    architecture = _read_normalized(_ARCHITECTURE)
    cli_contract = _read_normalized(_CLI_CONTRACT)

    for command in commands:
        assert command in roadmap
        assert command in architecture
        assert command in cli_contract

    for document in (roadmap, architecture, cli_contract):
        assert "Every command" in document
        assert "`--json`" in document
        assert "`--non-interactive`" in document


def test_phase_two_success_objects_and_failures_are_closed() -> None:
    """Lock the Project, context, selection, and exact failure contracts."""
    cli_contract = _read_normalized(_CLI_CONTRACT)
    roadmap = _read_normalized(_ROADMAP)
    architecture = _read_normalized(_ARCHITECTURE)
    exact_failures = {
        "PROFILE_NOT_FOUND": (
            "3",
            "false",
            "The selected profile was not found.",
        ),
        "PROFILE_INVALID": (
            "3",
            "false",
            "The trusted profile configuration is invalid.",
        ),
        "PROFILE_UNSUPPORTED": (
            "3",
            "false",
            "The selected profile mode or configuration version is not supported.",
        ),
        "PROJECT_NOT_FOUND": (
            "3",
            "false",
            "The selected Project was not found.",
        ),
        "WORKSPACE_BINDING_CONFLICT": (
            "4",
            "false",
            "The Workspace is already bound to a different Project, Instance, "
            "or profile.",
        ),
    }

    for field in (
        '"mode"',
        '"profile"',
        '"schema_version"',
        '"instance"',
        '"project"',
        '"workspace_root"',
        '"subject"',
        '"context_source"',
    ):
        assert field in cli_contract
    assert '{"id": "prj_01...", "key": "ACME", "name": "Acme"}' in cli_contract
    assert "`--project` and `--all-projects` are mutually exclusive" in roadmap

    for code, (exit_code, retryable, message) in exact_failures.items():
        expected_row = f"| `{code}` | {exit_code} | {retryable} | `{message}` |"
        assert expected_row in cli_contract
        assert f"`{code}`" in roadmap
        assert f"`{code}`" in architecture


def test_phase_two_embedded_profile_grammar_and_precedence_are_consistent() -> None:
    """Prevent profile ownership or selection behavior from drifting."""
    documents = tuple(_read_normalized(path) for path in _NORMATIVE_DOCUMENTS)
    trusted_profile_documents = (
        _read_normalized(_ROADMAP),
        _read_normalized(_ARCHITECTURE),
        _read_normalized(_CLI_CONTRACT),
        _read_normalized(_THREAT_MODEL),
        _read_normalized(_CONTEXT_ADR),
    )
    precedence = (
        "explicit `--profile`; 2. trusted `WORKAHOLIC_PROFILE`; 3. the discovered"
    )

    for document in trusted_profile_documents:
        assert "`profiles.toml`" in document
        assert 'mode = "embedded"' in document
        assert "`WORKAHOLIC_CONFIG_DIR`" in document
        assert "absolute" in document
        assert "non-symlink" in document

    for document in (
        _read_normalized(_ROADMAP),
        _read_normalized(_ARCHITECTURE),
        _read_normalized(_CLI_CONTRACT),
        _read_normalized(_CONTEXT_ADR),
    ):
        assert "`[a-z][a-z0-9_-]{0,31}`" in document
        assert "one-to-one" in document
        assert precedence in document
        assert "configured `default_profile`" in document
        assert "built-in `local`" in document

    for document in documents:
        assert "Phase 2" in document


def test_phase_two_context_discovery_and_binding_are_security_aligned() -> None:
    """Require the same physical traversal and replacement boundary everywhere."""
    context_documents = (
        _read_normalized(_ROADMAP),
        _read_normalized(_ARCHITECTURE),
        _read_normalized(_CLI_CONTRACT),
        _read_normalized(_THREAT_MODEL),
        _read_normalized(_CONTEXT_ADR),
    )

    for document in context_documents:
        assert "canonical physical" in document
        assert "filesystem root" in document
        assert "nearest" in document
        assert "non-symlink" in document
        assert "existing directory" in document
        assert "contained" in document
        assert "malformed file" in document
        assert "concurrently changed file" in document

    for path in _NORMATIVE_DOCUMENTS:
        document = _read_normalized(path)
        assert "may change shared `.gitignore`" not in document
        if "`.gitignore`" in document:
            assert (
                "never changes" in document
                or "must not modify" in document
                or "does not modify" in document
            )


def test_phase_two_schema_ordering_and_cursor_binding_are_consistent() -> None:
    """Lock clean-store schema, ordering, and cursor isolation semantics."""
    roadmap = _read_normalized(_ROADMAP)
    architecture = _read_normalized(_ARCHITECTURE)
    cli_contract = _read_normalized(_CLI_CONTRACT)
    persistence = _read_normalized(_PERSISTENCE_CONTRACT)

    for document in (roadmap, architecture, cli_contract, persistence):
        assert "schema version `2`" in document
        assert "schema version `1`" in document
        assert "no migration" in document

    for document in (roadmap, architecture, cli_contract, persistence):
        assert "(project key, task number)" in document
        assert "`v2.`" in document
        for binding in (
            "profile",
            "Instance",
            "Subject",
            "Project",
            "selection",
        ):
            assert binding in document

    assert "without changing any byte, schema object, allocation value" in persistence
    assert "Project key ascending" in persistence


def test_phase_two_explicitly_defers_remote_and_credential_capabilities() -> None:
    """Prevent normative documents from implying network or secret handling."""
    roadmap_phase_two = _section(
        _ROADMAP,
        "# Phase 2 — Multi-project context and working-directory discovery",
        "# Phase 3 — Complete task lifecycle and audit model",
    )
    cli_phase_two = _section(
        _CLI_CONTRACT,
        "## Phase 2 command contract",
        "## Conformance requirements",
    )
    persistence_phase_two = _section(
        _PERSISTENCE_CONTRACT,
        "## Phase 2 SQLite contract",
        "## Store opening and schema version",
    )
    architecture_boundary = _section(
        _ARCHITECTURE,
        "### Delivery boundary for local context",
        "## 6. Core domain model",
    )
    threat_model = _read_normalized(_THREAT_MODEL)
    context_adr = _read_normalized(_CONTEXT_ADR)

    assert "Remote profiles, URLs, credentials" in roadmap_phase_two
    assert "remain deferred to Phases 5 and 6" in roadmap_phase_two
    assert "Phase 2 remains embedded-only" in cli_phase_two
    assert "does not accept a remote profile, URL, credential, Token" in cli_phase_two
    assert "Profile resolution is outside persistence" in persistence_phase_two
    assert "Repository-controlled context can never supply or redirect that path" in (
        persistence_phase_two
    )
    assert "Remote profiles, endpoints, credentials, Tokens" in architecture_boundary
    assert "remain deferred to Phases 5 and 6" in architecture_boundary
    assert "Phase 2 has no remote profiles, endpoints, credentials, Tokens" in (
        threat_model
    )
    assert "Phase 2 does not accept a URL, credential, Token" in context_adr


def test_phase_two_behavior_remains_in_the_verified_phase_three_surface() -> None:
    """Retain the Phase 2 profile and Project foundation in the current alpha."""
    readme = _read_normalized(_README)
    current_cli = _section(
        _README,
        "## Current CLI",
        "## Phase 3 boundaries",
    )
    boundaries = _section(
        _README,
        "## Phase 3 boundaries",
        "## Planned for v1 (not implemented)",
    )

    assert "It exposes 19 Project, context, and Task operations" in readme
    for command in (
        "workaholic context",
        "workaholic project create",
        "workaholic project bind",
        "workaholic task list --all-projects",
    ):
        assert command in current_cli
    for selector in (
        "`profiles.toml`",
        "`WORKAHOLIC_CONFIG_DIR`",
        "`WORKAHOLIC_DATA_DIR`",
        "`WORKAHOLIC_PROFILE`",
        'mode = "embedded"',
    ):
        assert selector in readme
    assert "schema version `3`" in readme
    assert "schema version `2`" in readme
    assert (
        "There is no migration, conversion, import, export, or automatic reset"
        in readme
    )

    assert "It does not implement:" in boundaries
    for unavailable in (
        "Agents",
        "Tokens, credentials, remote profiles",
        "`RemoteSession`, a server",
        "JSON or PostgreSQL persistence adapters",
        "schema migration",
        "Project archival",
    ):
        assert unavailable in boundaries
    for unsupported_command in (
        "workaholic login",
        "workaholic server",
        "workaholic project archive",
    ):
        assert unsupported_command not in current_cli
    assert "workaholic task update" in current_cli
