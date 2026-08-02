"""Protect the accepted Phase 4 Claim and Agent execution contracts."""

from __future__ import annotations

from pathlib import Path

_PROJECT_ROOT = Path(__file__).parents[3]
_DOCS = _PROJECT_ROOT / "docs"
_ADR = _DOCS / "adr" / "0012-phase-four-local-claim-and-execution-model.md"
_ARCHITECTURE = _DOCS / "architecture.md"
_CLI_CONTRACT = _DOCS / "cli-contract.md"
_GLOSSARY = _DOCS / "glossary.md"
_PERSISTENCE_CONTRACT = _DOCS / "persistence-contract.md"
_PRODUCT_SCOPE = _DOCS / "product-scope.md"
_ROADMAP = _DOCS / "roadmap.md"
_THREAT_MODEL = _DOCS / "threat-model.md"


def _read_normalized(path: Path) -> str:
    """Read a Markdown document with whitespace normalized.

    Args:
        path: Documentation file to read.

    Returns:
        Document text with every whitespace run replaced by one space.

    """
    return " ".join(path.read_text(encoding="utf-8").split())


def _section(path: Path, start: str, end: str) -> str:
    """Read and normalize a section between two unique headings.

    Args:
        path: Documentation file to read.
        start: Heading that starts the section.
        end: Heading immediately following the section.

    Returns:
        Normalized section text, including the starting heading.

    """
    document = path.read_text(encoding="utf-8")
    start_index = document.index(start)
    end_index = document.index(end, start_index + len(start))
    return " ".join(document[start_index:end_index].split())


def test_phase_four_decision_is_accepted_and_linked() -> None:
    """Keep the owner-approved model durable and discoverable."""
    adr = _read_normalized(_ADR)

    assert "- Status: Accepted" in adr
    assert "- Decision date: 2026-08-02" in adr
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
        _ARCHITECTURE,
        _CLI_CONTRACT,
        _PERSISTENCE_CONTRACT,
        _PRODUCT_SCOPE,
        _ROADMAP,
        _THREAT_MODEL,
    ):
        assert "ADR 0012" in _read_normalized(path), path


def test_phase_four_reuses_bootstrap_identity_until_phase_five() -> None:
    """Prevent Agent identity management from leaking into Phase 4."""
    for path in (_ADR, _ARCHITECTURE, _CLI_CONTRACT, _ROADMAP, _THREAT_MODEL):
        document = _read_normalized(path)
        assert "bootstrap Subject" in document, path
        assert "Attempt identity distinguishes" in document, path
        assert "Phase 5" in document, path

    assert "distinct Agent Subjects, Tokens, grants" in _read_normalized(_ADR)
    assert "does not distinguish different Human operators" in _read_normalized(_ADR)


def test_claim_model_distinguishes_human_and_agent_execution() -> None:
    """Require one exclusive Claim model without synthetic Human Attempts."""
    for path in (
        _ADR,
        _ARCHITECTURE,
        _CLI_CONTRACT,
        _GLOSSARY,
        _PERSISTENCE_CONTRACT,
        _ROADMAP,
    ):
        document = _read_normalized(path)
        for phrase in ("Human Claim", "Agent Claim", "null Attempt"):
            assert phrase in document, path

    for path in (_ADR, _ARCHITECTURE, _ROADMAP):
        document = _read_normalized(path)
        assert "longer Lease" in document, path
        assert "shorter Lease" in document, path

    assert "longer than Agent Lease windows" in _read_normalized(_CLI_CONTRACT)
    assert "longer Lease window than an Agent Claim" in _read_normalized(
        _PERSISTENCE_CONTRACT
    )
    glossary = _read_normalized(_GLOSSARY)
    assert "Attempt remains an Agent-only execution record" in _read_normalized(_ADR)
    assert "Humans do not receive synthetic Attempts" in glossary
    assert "No current Claim means the Task is unclaimed" in _read_normalized(_ADR)


def test_capability_scheduling_is_post_v1() -> None:
    """Keep capability matching out of the Phase 4 command contract."""
    phase_four = _section(
        _ROADMAP,
        "# Phase 4 — Local Claims, Agent execution, and extended JSON CLI contract",
        "# Phase 5 — Identity, authentication, and authorization",
    )

    assert "--capability" not in phase_four
    assert "Capability filtering is not part of v1" in phase_four
    for path in (_ADR, _ARCHITECTURE, _PRODUCT_SCOPE, _ROADMAP):
        assert (
            "capability-based task scheduling" in _read_normalized(path).casefold()
        ), path


def test_human_and_agent_commands_have_distinct_claim_ux() -> None:
    """Protect the no-Attempt Human UX and explicit Agent execution path."""
    cli_contract = _read_normalized(_CLI_CONTRACT)

    for command in (
        "workaholic task claim TASK [--lease DURATION]",
        "workaholic task renew TASK [--lease DURATION]",
        "workaholic task release TASK",
        "workaholic task claim [--lease DURATION]",
        "workaholic task heartbeat TASK --attempt ATTEMPT [--lease DURATION]",
        "workaholic task progress TASK --attempt ATTEMPT --input-file PATH|-",
        "workaholic task release TASK --attempt ATTEMPT",
        "workaholic task submit TASK --attempt ATTEMPT --expected-version INTEGER",
    ):
        assert command in cli_contract

    assert "Human commands never require an Attempt ID" in _read_normalized(
        _ARCHITECTURE
    )
    assert "Human `task renew` and Agent heartbeat share one semantic renewal" in (
        cli_contract
    )
    assert "returns it without extending the Lease" in cli_contract
    assert "do not renew implicitly" in cli_contract


def test_current_claim_is_an_exclusive_mutation_lock() -> None:
    """Require non-owner rejection and the accepted owner permission split."""
    for path in (_ADR, _ARCHITECTURE, _CLI_CONTRACT, _PERSISTENCE_CONTRACT, _ROADMAP):
        document = _read_normalized(path)
        assert "non-owner" in document, path

    for path in (_ARCHITECTURE, _CLI_CONTRACT, _PERSISTENCE_CONTRACT, _ROADMAP):
        assert "exclusive mutation lock" in _read_normalized(path).casefold(), path

    adr = _read_normalized(_ADR)
    assert "rejected mutation changes no Task, Claim, Attempt, Result" in adr
    assert "Definition updates, block/unblock, and dependency changes retain" in adr
    assert "Agent owner may only heartbeat, report progress, release, or submit" in adr
    assert "provides no force-interrupt command in v1" in adr
    threat_model = _read_normalized(_THREAT_MODEL)
    assert "| Unauthorized Claim or Attempt mutation |" in threat_model


def test_claim_versions_expiry_and_attempt_terminal_states_are_fixed() -> None:
    """Lock the concurrency boundary needed before implementation planning."""
    for path in (_ADR, _CLI_CONTRACT, _PERSISTENCE_CONTRACT):
        document = _read_normalized(path)
        for phrase in (
            "now < lease_expires_at",
            "current Task version",
            "expected Task version",
            "last three",
            "terminal",
            "never revive",
        ):
            assert phrase in document, path

    for path in (_ADR, _CLI_CONTRACT):
        assert "do not change the Task version" in _read_normalized(path), path
    assert "do not increment the Task version" in _read_normalized(
        _PERSISTENCE_CONTRACT
    )

    for path in (_CLI_CONTRACT, _PERSISTENCE_CONTRACT):
        assert "exactly `active`, `released`, `expired`, and `submitted`" in (
            _read_normalized(path)
        )
    adr = _read_normalized(_ADR)
    for state in ("active", "released", "expired", "submitted"):
        assert state in adr
    assert "including when the Task enters review" in _read_normalized(_CLI_CONTRACT)

    roadmap = _read_normalized(_ROADMAP)
    for event_type in (
        "task_claimed",
        "claim_renewed",
        "claim_released",
        "claim_expired",
        "progress_reported",
        "observation_added",
    ):
        assert event_type in _read_normalized(_PERSISTENCE_CONTRACT)
    assert "does not depend on a background scheduler" in roadmap
