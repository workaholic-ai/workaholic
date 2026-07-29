"""Validate architecture decision records and delivery contracts."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parents[3]
_ADR_DIRECTORY = _PROJECT_ROOT / "docs" / "adr"
_CLI_CONTRACT = _PROJECT_ROOT / "docs" / "cli-contract.md"
_PERSISTENCE_CONTRACT = _PROJECT_ROOT / "docs" / "persistence-contract.md"
_ADR_FILENAME_PATTERN = re.compile(r"^(?P<number>\d{4})-[a-z0-9-]+\.md$")
_ADR_TITLE_PATTERN = re.compile(r"^# ADR (?P<number>\d{4}): .+$", re.MULTILINE)
_METADATA_PATTERN = re.compile(
    r"^- (?P<key>Status|Decision date|Deciders|Supersedes|Superseded by): "
    r"(?P<value>.+)$",
    re.MULTILINE,
)
_JSON_BLOCK_PATTERN = re.compile(r"```json\n(?P<body>.+?)\n```", re.DOTALL)
_REQUIRED_ADR_HEADINGS = (
    "## Context",
    "## Decision",
    "## Alternatives considered",
    "## Consequences",
    "## References",
)
_VALID_ADR_STATUSES = {"Proposed", "Accepted", "Superseded", "Rejected"}


def _decision_paths() -> tuple[Path, ...]:
    """Return numbered ADR paths, excluding the ADR template."""
    return tuple(
        sorted(
            path
            for path in _ADR_DIRECTORY.glob("[0-9][0-9][0-9][0-9]-*.md")
            if not path.name.startswith("0000-")
        )
    )


def _read(path: Path) -> str:
    """Read a UTF-8 documentation file.

    Args:
        path: Documentation path to read.

    Returns:
        The complete file contents.

    """
    return path.read_text(encoding="utf-8")


def _metadata(document: str) -> dict[str, str]:
    """Extract ADR metadata fields from a Markdown document.

    Args:
        document: ADR Markdown source.

    Returns:
        A mapping from metadata labels to their values.

    """
    return {
        match.group("key"): match.group("value")
        for match in _METADATA_PATTERN.finditer(document)
    }


def test_adr_numbers_are_unique_and_contiguous() -> None:
    """Keep the accepted ADR sequence complete and collision-free."""
    paths = _decision_paths()
    numbers = [
        int(match.group("number"))
        for path in paths
        if (match := _ADR_FILENAME_PATTERN.fullmatch(path.name)) is not None
    ]

    assert len(numbers) == len(paths)
    assert numbers[:10] == list(range(1, 11))
    assert numbers == list(range(1, numbers[-1] + 1))


def test_adrs_follow_the_required_record_structure() -> None:
    """Require stable metadata and headings in every accepted ADR."""
    for path in _decision_paths():
        document = _read(path)
        filename_match = _ADR_FILENAME_PATTERN.fullmatch(path.name)
        title_match = _ADR_TITLE_PATTERN.search(document)
        metadata = _metadata(document)

        assert filename_match is not None, path
        assert title_match is not None, path
        assert title_match.group("number") == filename_match.group("number"), path
        assert metadata.keys() == {
            "Status",
            "Decision date",
            "Deciders",
            "Supersedes",
            "Superseded by",
        }, path
        assert metadata["Status"] in _VALID_ADR_STATUSES, path
        if int(filename_match.group("number")) <= 10:
            assert metadata["Status"] == "Accepted", path
        decision_date = date.fromisoformat(metadata["Decision date"])
        assert decision_date.isoformat() == metadata["Decision date"], path
        assert metadata["Deciders"].strip(), path

        heading_positions = [
            document.find(heading) for heading in _REQUIRED_ADR_HEADINGS
        ]
        assert all(position >= 0 for position in heading_positions), path
        assert heading_positions == sorted(heading_positions), path
        assert "TBD" not in document, path


def test_cli_contract_defines_machine_readable_envelopes() -> None:
    """Protect the exact top-level success and failure envelope fields."""
    documents = [
        json.loads(match.group("body"))
        for match in _JSON_BLOCK_PATTERN.finditer(_read(_CLI_CONTRACT))
    ]
    success_envelope = next(document for document in documents if document.get("ok"))
    error_envelope = next(document for document in documents if not document.get("ok"))

    assert set(success_envelope) == {"schema", "ok", "data"}
    assert success_envelope["schema"] == "workaholic.cli/v1"
    assert set(error_envelope) == {"schema", "ok", "error"}
    assert error_envelope["schema"] == "workaholic.cli/v1"
    assert set(error_envelope["error"]) == {"code", "message", "retryable"}


def test_cli_contract_distinguishes_public_and_private_protocols() -> None:
    """Prevent accidental coupling of CLI JSON to the private transport."""
    contract = _read(_CLI_CONTRACT)
    protocol_adr = _read(
        _ADR_DIRECTORY / "0004-private-versioned-client-server-protocol.md"
    )

    assert "`workaholic.cli/v1`" in contract
    assert "`workaholic/v1`" in contract
    assert "public automation contract" in contract
    assert "private official-client protocol" in contract
    assert "workaholic/v1" in protocol_adr
    assert "does not create a public HTTP API" in protocol_adr


def test_delivery_contracts_record_compatibility_timeline() -> None:
    """Keep compatibility promises aligned with the approved roadmap."""
    for path in (_CLI_CONTRACT, _PERSISTENCE_CONTRACT):
        contract = _read(path)

        assert "Phase 7" in contract, path
        assert "Phase 8" in contract, path
        assert "release candidate" in contract, path
        assert "`1.0.0`" in contract, path


def test_persistence_contract_covers_required_semantics() -> None:
    """Require the semantic invariants every persistence adapter must share."""
    contract = _read(_PERSISTENCE_CONTRACT).casefold()
    required_terms = (
        "semantic transaction",
        "schema version",
        "task-number allocation",
        "optimistic version",
        "append-only",
        "idempotency",
        "atomic claims",
        "conformance suite",
    )

    for term in required_terms:
        assert term in contract
