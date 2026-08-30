"""Protect the accepted Phase 3 Human lifecycle and automation contracts."""

from __future__ import annotations

from pathlib import Path

_PROJECT_ROOT = Path(__file__).parents[3]
_DOCS = _PROJECT_ROOT / "docs"
_README = _PROJECT_ROOT / "README.md"
_ROADMAP = _DOCS / "roadmap.md"
_ARCHITECTURE = _DOCS / "architecture.md"
_CLI_CONTRACT = _DOCS / "cli-contract.md"
_PERSISTENCE_CONTRACT = _DOCS / "persistence-contract.md"
_GLOSSARY = _DOCS / "glossary.md"
_THREAT_MODEL = _DOCS / "threat-model.md"
_LIFECYCLE_ADR = (
    _DOCS / "adr" / "0011-phase-three-task-mutation-and-human-submission.md"
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


def test_phase_three_commands_have_one_exact_contract() -> None:
    """Keep every Phase 3 command signature aligned across owner documents."""
    commands = (
        "workaholic task add TITLE [--objective TEXT] [--priority INTEGER]",
        "workaholic task update TASK",
        "workaholic task block TASK --reason TEXT",
        "workaholic task unblock TASK",
        "workaholic task add-dependency TASK PREREQUISITE",
        "workaholic task remove-dependency TASK PREREQUISITE",
        "workaholic task submit TASK [--comment TEXT] [--result-file PATH|-]",
        "workaholic task approve TASK [--comment TEXT]",
        "workaholic task reject TASK --reason TEXT",
        "workaholic task cancel TASK [--reason TEXT]",
        "workaholic task events TASK [--after INTEGER] [--limit INTEGER] [--follow]",
    )
    roadmap = _read_normalized(_ROADMAP)
    architecture = _read_normalized(_ARCHITECTURE)
    cli_contract = _read_normalized(_CLI_CONTRACT)

    for command in commands:
        assert command in roadmap
        assert command in architecture
        assert command in cli_contract

    for document in (roadmap, architecture, cli_contract):
        assert "`--json`" in document
        assert "`--non-interactive`" in document
        assert "`--expected-version`" in document


def test_phase_three_optimistic_version_policy_is_consistent() -> None:
    """Require explicit automation versions and safe Human convenience."""
    documents = tuple(
        _read_normalized(path)
        for path in (
            _ROADMAP,
            _ARCHITECTURE,
            _CLI_CONTRACT,
            _PERSISTENCE_CONTRACT,
            _THREAT_MODEL,
            _LIFECYCLE_ADR,
        )
    )

    for document in documents:
        assert "expected version" in document.lower()
        assert "VERSION_CONFLICT" in document
        assert "silently retr" in document

    cli_contract = _read_normalized(_CLI_CONTRACT)
    adr = _read_normalized(_LIFECYCLE_ADR)
    persistence = _read_normalized(_PERSISTENCE_CONTRACT)
    for phrase in (
        "JSON mode, `--non-interactive`",
        "stdin is not a terminal",
        "displays the selected Task key",
        "current stored state",
        "current version",
        "intended semantic action",
        "Declining exits zero",
        "Supplying the option skips",
    ):
        assert phrase in cli_contract
    assert "Every mutation of an existing Task requires" in adr
    assert "increments the Task version exactly once" in adr
    assert "equivalent replay returns the recorded outcome before comparing" in (
        persistence
    )


def test_phase_three_human_results_never_create_attempts() -> None:
    """Keep Human completion distinct from Agent Attempt execution."""
    roadmap = _read_normalized(_ROADMAP)
    architecture = _read_normalized(_ARCHITECTURE)
    cli_contract = _read_normalized(_CLI_CONTRACT)
    persistence = _read_normalized(_PERSISTENCE_CONTRACT)
    glossary = _read_normalized(_GLOSSARY)
    adr = _read_normalized(_LIFECYCLE_ADR)

    for document in (roadmap, architecture, adr):
        assert "Agent-only" in document
        assert "`attempt_id = null`" in document
    assert "Human Result records the authenticated Human and a null Attempt" in (
        persistence
    )
    assert "Humans do not receive synthetic Attempts" in glossary
    assert "Human Phase 3 Results always have null `attempt_id`" in cli_contract

    cli_phase_three = _section(
        _CLI_CONTRACT,
        "## Phase 3 command contract",
        "## Conformance requirements",
    )
    assert "does not accept `--attempt`" in cli_phase_three
    assert "submitting neither is a valid manual completion" in cli_phase_three
    assert "do not create Tasks, dependencies, or hierarchy" in cli_phase_three


def test_phase_three_transitions_events_and_versions_are_closed() -> None:
    """Lock lifecycle transitions and multi-event single-version behavior."""
    cli_contract = _read_normalized(_CLI_CONTRACT)
    roadmap = _read_normalized(_ROADMAP)
    persistence = _read_normalized(_PERSISTENCE_CONTRACT)
    event_types = (
        "task_created",
        "task_updated",
        "task_blocked",
        "task_unblocked",
        "result_submitted",
        "review_approved",
        "review_rejected",
        "task_completed",
        "task_cancelled",
    )

    for event_type in event_types:
        for document in (cli_contract, roadmap, persistence):
            assert event_type in document

    for transition in (
        "`open -> blocked`",
        "`blocked -> open`",
        "`open|blocked|review -> cancelled`",
    ):
        assert transition in cli_contract
    assert "`done` and `cancelled` are terminal" in cli_contract
    assert "`result_submitted` then `task_completed`" in cli_contract
    assert "`review_approved` then `task_completed`" in cli_contract
    assert "increments the Task version once regardless of event count" in cli_contract
    assert "Generic update cannot change state" in persistence


def test_phase_three_dependencies_replace_task_hierarchy() -> None:
    """Require one acyclic same-Project decomposition graph."""
    roadmap = _read_normalized(_ROADMAP)
    architecture = _read_normalized(_ARCHITECTURE)
    cli_contract = _read_normalized(_CLI_CONTRACT)
    persistence = _read_normalized(_PERSISTENCE_CONTRACT)
    glossary = _read_normalized(_GLOSSARY)
    adr = _read_normalized(_LIFECYCLE_ADR)

    for document in (roadmap, architecture, cli_contract, persistence, glossary, adr):
        assert "same-Project" in document
    for document in (roadmap, architecture, glossary, adr):
        assert "parent/child" in document

    for phrase in (
        "Self edges",
        "duplicate additions",
        "absent removals",
        "cycles",
        "cancelled prerequisite",
        "UNSATISFIABLE_DEPENDENCY",
    ):
        assert phrase in cli_contract
    assert "versions only the dependant" in cli_contract
    assert "does not mutate dependant Tasks" in cli_contract
    assert "does not mutate dependant Tasks" in persistence


def test_phase_three_json_input_objects_cursors_and_errors_are_exact() -> None:
    """Lock automation shapes, limits, cursor binding, and safe failures."""
    cli_contract = _read_normalized(_CLI_CONTRACT)
    exact_failures = {
        "VERSION_CONFLICT": (
            "4",
            "false",
            "The Task changed after the expected version.",
        ),
        "INVALID_TRANSITION": (
            "4",
            "false",
            "The Task cannot perform the requested lifecycle transition.",
        ),
        "DEPENDENCY_CONFLICT": (
            "4",
            "false",
            "The dependency change conflicts with the current Task graph.",
        ),
        "DEPENDENCY_CYCLE": (
            "4",
            "false",
            "The dependency change would create a cycle.",
        ),
        "UNSATISFIABLE_DEPENDENCY": (
            "4",
            "false",
            "The Task has a cancelled prerequisite and cannot be completed.",
        ),
        "RESULT_INVALID": (
            "2",
            "false",
            "The submitted Result is invalid.",
        ),
    }

    for field in (
        '"available_at"',
        '"approval"',
        '"acceptance"',
        '"context"',
        '"depends_on"',
        '"blocking_reason"',
        '"current_result_id"',
        '"views"',
        '"readiness_reasons"',
        '"attempt_id"',
        '"review"',
        '"actor_kind"',
        '"payload"',
        '"next_cursor"',
    ):
        assert field in cli_contract

    for code, (exit_code, retryable, message) in exact_failures.items():
        expected_row = f"| `{code}` | {exit_code} | {retryable} | `{message}` |"
        assert expected_row in cli_contract

    for bound in (
        "1,048,576 bytes",
        "at most 16 containers deep",
        "at most 128 members",
        "500 items",
        "at most 100 entries",
        "1 through 500",
    ):
        assert bound in cli_contract
    assert "Phase 3 Task cursors begin with `v3.`" in cli_contract
    assert "bind the Phase 2 profile, Instance, Subject" in cli_contract
    assert "Cross-view reuse returns `INVALID_INPUT`" in cli_contract


def test_phase_three_schema_security_contract_remains_documented() -> None:
    """Keep the Phase 3 contract while publishing Phase 5 as current."""
    architecture = _read_normalized(_ARCHITECTURE)
    cli_contract = _read_normalized(_CLI_CONTRACT)
    persistence = _read_normalized(_PERSISTENCE_CONTRACT)
    threat_model = _read_normalized(_THREAT_MODEL)
    readme = _read_normalized(_README)

    for document in (
        _read_normalized(_ROADMAP),
        architecture,
        cli_contract,
        persistence,
        threat_model,
    ):
        assert "schema version `3`" in document
        assert "version `2`" in document
    assert "no migration, conversion, import, export, or automatic reset" in (
        persistence
    )
    for document in (architecture, cli_contract, persistence, threat_model, readme):
        assert "`0.5.0a1`" in document
    assert "implemented normative contract for `0.3.0a1`" in cli_contract
    assert "Phase 5 Identity and Authorization Alpha implements" in architecture
    assert "Task updates" in architecture
    assert "`workaholic task update`" in readme
    assert "Human Claims and Results had a null Attempt" in persistence
    assert "automatic Task creation from proposed follow-ups" in readme
    assert "schema version `5`" in readme
    assert "rejected unchanged" in readme

    for threat in (
        "Concurrent mutation overwrite",
        "Structured Task or Result abuse",
        "expected version",
        "never refresh and silently retry",
        "null Attempt",
        "bounded structured input",
    ):
        assert threat in threat_model


def test_readme_defers_every_post_phase_five_capability() -> None:
    """Prevent the current public surface from claiming later roadmap slices."""
    current_cli = _section(
        _README,
        "## Current CLI",
        "## Phase 5 boundaries",
    )
    boundaries = _section(
        _README,
        "## Phase 5 boundaries",
        "## Development checks",
    )

    for unavailable in (
        "`RemoteSession`, a server, remote profiles",
        "distributed team coordination",
        "JSON or PostgreSQL persistence adapters",
        "schema migration",
        "capability-based scheduling",
        "parent/child Task hierarchies",
        "automatic Task creation from proposed follow-ups",
    ):
        assert unavailable in boundaries
    for future_command in (
        "workaholic login",
        "workaholic server",
    ):
        assert future_command not in current_cli


def test_phase_three_adr_is_accepted_and_linked() -> None:
    """Require the owner-approved decisions to remain durable and discoverable."""
    adr = _read_normalized(_LIFECYCLE_ADR)

    assert "- Status: Accepted" in adr
    assert "- Decision date: 2026-08-01" in adr
    assert "- Deciders: Pavels Gurskis" in adr
    for heading in (
        "## Context",
        "## Decision",
        "## Alternatives considered",
        "## Consequences",
        "## References",
    ):
        assert heading in adr
    for path in (_CLI_CONTRACT, _PERSISTENCE_CONTRACT, _THREAT_MODEL):
        assert "ADR 0011" in _read_normalized(path)
