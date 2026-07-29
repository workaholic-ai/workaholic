"""Tests for the public Phase 0 documentation contracts."""

from __future__ import annotations

import re
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlsplit

from scripts.check_doc_links import (
    discover_markdown_files,
    validate_document_links,
)

_PROJECT_ROOT = Path(__file__).parents[3]
_README = _PROJECT_ROOT / "README.md"
_CONTRIBUTING = _PROJECT_ROOT / "CONTRIBUTING.md"
_PULL_REQUEST_TEMPLATE = _PROJECT_ROOT / ".github" / "pull_request_template.md"
_GLOSSARY = _PROJECT_ROOT / "docs" / "glossary.md"
_ARCHITECTURE = _PROJECT_ROOT / "docs" / "architecture.md"
_ROADMAP = _PROJECT_ROOT / "docs" / "roadmap.md"
_THREAT_MODEL = _PROJECT_ROOT / "docs" / "threat-model.md"
_PRODUCT_SCOPE = _PROJECT_ROOT / "docs" / "product-scope.md"
_COMPATIBILITY_POLICY = _PROJECT_ROOT / "docs" / "compatibility-policy.md"
_IDENTITY_ADR = (
    _PROJECT_ROOT / "docs" / "adr" / "0007-human-and-agent-identity-model.md"
)
_SESSIONS_ADR = _PROJECT_ROOT / "docs" / "adr" / "0002-local-and-remote-sessions.md"
_DISTRIBUTION_NAME = "workaholic-ai"
_INLINE_LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]+\]\((?P<target>[^)\s]+)\)")
_QUICK_START_PATTERN = re.compile(
    r"## Quick start\n(?P<section>.*?)(?=\n## )",
    flags=re.DOTALL,
)
_BASH_BLOCK_PATTERN = re.compile(
    r"```bash\n(?P<body>.*?)\n```",
    flags=re.DOTALL,
)
_VERSION_OUTPUT_PATTERN = re.compile(
    r"The version command prints:\n\n```text\n(?P<output>[^\n]+)\n```"
)
_EXPECTED_QUICK_START = """uv sync --frozen
uv run pre-commit run --all-files
uv run workaholic --version
uv run pytest
uv build
scripts/smoke-install.sh dist/*.whl"""
_REQUIRED_GLOSSARY_TERMS = frozenset(
    {
        "Agent",
        "Attempt",
        "Human",
        "Instance",
        "Lease",
        "LocalSession",
        "Project",
        "ProjectGrant",
        "RemoteSession",
        "Result",
        "Session",
        "Subject",
        "Task",
        "TaskEvent",
        "Workspace",
    }
)
_REQUIRED_THREATS = frozenset(
    {
        "Command injection",
        "Compromised Agent",
        "Denial of service",
        "Event forgery",
        "Secret exposure",
        "Stolen Token",
        "Token redirection",
        "Unauthorized Attempt mutation",
    }
)


def test_readme_relative_links_resolve_within_the_repository() -> None:
    """Every relative README link resolves to an existing repository path."""
    readme = _README.read_text(encoding="utf-8")
    targets = [match.group("target") for match in _INLINE_LINK_PATTERN.finditer(readme)]
    relative_targets = [
        target
        for target in targets
        if not urlsplit(target).scheme and not target.startswith("#")
    ]

    assert relative_targets, "README.md must link to repository documentation."
    for target in relative_targets:
        parsed_target = urlsplit(target)
        relative_path = Path(unquote(parsed_target.path))
        resolved_path = (_PROJECT_ROOT / relative_path).resolve()

        assert resolved_path.is_relative_to(_PROJECT_ROOT)
        assert resolved_path.exists(), f"README link does not resolve: {target}"


def test_all_repository_documentation_links_resolve() -> None:
    """Every repository-local Markdown path and heading fragment resolves."""
    markdown_files = discover_markdown_files(_PROJECT_ROOT)
    issues = validate_document_links(_PROJECT_ROOT, markdown_files)

    assert not issues, "\n".join(issue.format(_PROJECT_ROOT) for issue in issues)


def test_canonical_documents_have_no_root_level_copies() -> None:
    """Architecture and roadmap exist only under their canonical docs paths."""
    assert _ARCHITECTURE.is_file()
    assert _ROADMAP.is_file()
    assert not (_PROJECT_ROOT / "ARCHITECTURE.md").exists()
    assert not (_PROJECT_ROOT / "ROADMAP.md").exists()


def test_glossary_defines_terms_used_by_architecture_and_roadmap() -> None:
    """Canonical planning documents use every required glossary term."""
    glossary = _GLOSSARY.read_text(encoding="utf-8")
    headings = frozenset(re.findall(r"^## (.+)$", glossary, flags=re.MULTILINE))
    planning_documents = _ARCHITECTURE.read_text(encoding="utf-8") + _ROADMAP.read_text(
        encoding="utf-8"
    )

    assert headings >= _REQUIRED_GLOSSARY_TERMS
    for term in _REQUIRED_GLOSSARY_TERMS:
        assert term in planning_documents


def test_threat_model_covers_required_boundaries_and_attack_scenarios() -> None:
    """The accepted threat baseline retains every owner-required scenario."""
    threat_model = " ".join(_THREAT_MODEL.read_text(encoding="utf-8").split())

    for threat in _REQUIRED_THREATS:
        assert f"| {threat} |" in threat_model
    for boundary in (
        "Instance administrator",
        "operating-system credential store",
        "ProjectGrant",
        ".workaholic.env",
        "one organization",
        "public multi-tenant",
    ):
        assert boundary in threat_model


def test_readme_quick_start_contains_only_executable_phase_zero_commands() -> None:
    """The quick start remains an exact, executable Phase 0 command sequence."""
    readme = _README.read_text(encoding="utf-8")
    quick_start_match = _QUICK_START_PATTERN.search(readme)

    assert quick_start_match is not None
    bash_blocks = _BASH_BLOCK_PATTERN.findall(quick_start_match.group("section"))
    assert bash_blocks == [_EXPECTED_QUICK_START]


def test_readme_version_output_matches_installed_distribution() -> None:
    """The documented version output matches installed package metadata."""
    readme = _README.read_text(encoding="utf-8")
    output_match = _VERSION_OUTPUT_PATTERN.search(readme)

    assert output_match is not None
    assert output_match.group("output") == (
        f"workaholic {metadata.version(_DISTRIBUTION_NAME)}"
    )


def test_phase_zero_scope_decisions_are_consistent_across_public_documents() -> None:
    """Tenancy, compatibility, and v1 sequencing retain one accepted answer."""
    readme = " ".join(_README.read_text(encoding="utf-8").split())
    scope = " ".join(_PRODUCT_SCOPE.read_text(encoding="utf-8").split())
    compatibility = " ".join(_COMPATIBILITY_POLICY.read_text(encoding="utf-8").split())
    identity_adr = " ".join(_IDENTITY_ADR.read_text(encoding="utf-8").split())
    sessions_adr = " ".join(_SESSIONS_ADR.read_text(encoding="utf-8").split())
    roadmap = " ".join(_ROADMAP.read_text(encoding="utf-8").split())

    assert "compatibility is not promised" in readme
    assert "before `1.0.0`" in readme
    assert (
        "formal public backward-compatibility promise begins with the final "
        "`1.0.0` release" in compatibility
    )
    assert "Cross-organization tenant isolation" in scope
    assert "outside v1" in scope
    assert "Single-organization scope" in identity_adr
    assert "embedded local operation before distributed team coordination" in (
        sessions_adr
    )
    assert "Local task workflows arrive before agent and distributed-team" in readme
    assert "Distributed teams can begin using it after **Phase 6**" in roadmap


def test_contribution_policy_defines_readme_update_triggers() -> None:
    """Contribution guidance names every required README maintenance trigger."""
    contribution_guide = " ".join(
        _CONTRIBUTING.read_text(encoding="utf-8").casefold().split()
    )

    required_triggers = (
        "installation",
        "commands",
        "documented output",
        "prerequisites",
        "compatibility or support status",
        "security guidance",
        "principal user journey",
    )
    for trigger in required_triggers:
        assert trigger in contribution_guide


def test_pull_request_template_requires_every_impact_review() -> None:
    """The pull request template preserves the five mandatory impact reviews."""
    pull_request_template = _PULL_REQUEST_TEMPLATE.read_text(
        encoding="utf-8"
    ).casefold()

    required_reviews = (
        "test impact has been assessed",
        "public interface impact has been assessed",
        "readme and quick-start impact has been assessed",
        "security impact has been assessed",
        "architecture-decision impact has been assessed",
    )
    for review in required_reviews:
        assert f"- [ ] {review}" in pull_request_template
