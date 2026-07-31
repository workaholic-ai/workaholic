"""Tests for public documentation and the Phase 2 quick start."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
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
_CLI_CONTRACT = _PROJECT_ROOT / "docs" / "cli-contract.md"
_PERSISTENCE_CONTRACT = _PROJECT_ROOT / "docs" / "persistence-contract.md"
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
_EXPECTED_QUICK_START = (
    "uv sync --frozen\n"
    'export WORKAHOLIC_CONFIG_DIR="$(mktemp -d "'
    '${TMPDIR:-/tmp}/workaholic-quickstart-config.XXXXXX")"\n'
    'export WORKAHOLIC_DATA_DIR="$(mktemp -d "'
    '${TMPDIR:-/tmp}/workaholic-quickstart-data.XXXXXX")"\n'
    "workaholic_source_directory=$PWD\n"
    'workaholic_workspace_directory="$(mktemp -d "'
    '${TMPDIR:-/tmp}/workaholic-quickstart-workspaces.XXXXXX")"\n'
    'mkdir -p "$workaholic_workspace_directory/acme/src/app"\n'
    'mkdir -p "$workaholic_workspace_directory/docs/guides/draft"\n'
    "(\n"
    '  cd "$workaholic_workspace_directory/acme"\n'
    '  uv run --project "$workaholic_source_directory" workaholic up '
    '--project-key ACME --project-name "Acme delivery"\n'
    '  uv run --project "$workaholic_source_directory" workaholic project '
    'create --key DOCS --name "Documentation"\n'
    '  uv run --project "$workaholic_source_directory" workaholic project '
    "bind DOCS ../docs\n"
    "  cd src/app\n"
    '  uv run --project "$workaholic_source_directory" workaholic task add '
    '"First ACME task"\n'
    ")\n"
    "(\n"
    '  cd "$workaholic_workspace_directory/docs/guides/draft"\n'
    '  uv run --project "$workaholic_source_directory" workaholic task add '
    '"First DOCS task"\n'
    '  uv run --project "$workaholic_source_directory" workaholic task list\n'
    ")\n"
    "uv run workaholic task list --all-projects"
)
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


def test_readme_quick_start_contains_the_exact_phase_two_journey() -> None:
    """The quick start is the exact owner-approved multi-Project sequence."""
    readme = _README.read_text(encoding="utf-8")
    quick_start_match = _QUICK_START_PATTERN.search(readme)

    assert quick_start_match is not None
    bash_blocks = _BASH_BLOCK_PATTERN.findall(quick_start_match.group("section"))
    assert bash_blocks == [_EXPECTED_QUICK_START]


@pytest.mark.requires_uv
def test_readme_quick_start_executes_in_an_isolated_source_checkout(
    tmp_path: Path,
) -> None:
    """The literal quick-start shell block selects its own isolated state."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    for filename in ("LICENSE", "README.md", "pyproject.toml", "uv.lock"):
        shutil.copy2(_PROJECT_ROOT / filename, checkout / filename)
    shutil.copytree(_PROJECT_ROOT / "src", checkout / "src")
    inherited_config_directory = tmp_path / "inherited-config"
    inherited_config_directory.mkdir()
    (inherited_config_directory / "profiles.toml").write_text(
        "not valid TOML = [",
        encoding="utf-8",
    )
    inherited_data_directory = tmp_path / "inherited-data"
    inherited_data_directory.mkdir()
    (inherited_data_directory / "local.db").write_bytes(b"not a SQLite store")
    environment = os.environ.copy()
    environment.update(
        {
            "NO_COLOR": "1",
            "TMPDIR": str(tmp_path),
            "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
            "UV_LINK_MODE": "copy",
            "UV_NO_PROGRESS": "1",
            "WORKAHOLIC_CONFIG_DIR": str(inherited_config_directory),
            "WORKAHOLIC_DATA_DIR": str(inherited_data_directory),
        }
    )

    result = subprocess.run(
        ["/bin/sh", "-eu", "-c", _EXPECTED_QUICK_START],
        check=False,
        cwd=checkout,
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
    )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "Traceback" not in result.stderr
    assert "ACME-1" in result.stdout
    assert "DOCS-1" in result.stdout
    assert "First ACME task" in result.stdout
    assert "First DOCS task" in result.stdout
    config_directories = tuple(tmp_path.glob("workaholic-quickstart-config.*"))
    data_directories = tuple(tmp_path.glob("workaholic-quickstart-data.*"))
    workspace_directories = tuple(tmp_path.glob("workaholic-quickstart-workspaces.*"))
    assert len(config_directories) == 1
    assert len(data_directories) == 1
    assert len(workspace_directories) == 1
    assert (data_directories[0] / "local.db").is_file()
    assert (workspace_directories[0] / "acme" / ".workaholic.env").is_file()
    assert (workspace_directories[0] / "docs" / ".workaholic.env").is_file()
    assert tuple(config_directories[0].iterdir()) == ()
    assert (inherited_config_directory / "profiles.toml").read_text(
        encoding="utf-8"
    ) == "not valid TOML = ["
    assert (inherited_data_directory / "local.db").read_bytes() == (
        b"not a SQLite store"
    )


def test_readme_version_output_matches_installed_distribution() -> None:
    """The documented version output matches installed package metadata."""
    readme = _README.read_text(encoding="utf-8")
    output_match = _VERSION_OUTPUT_PATTERN.search(readme)

    assert output_match is not None
    assert output_match.group("output") == (
        f"workaholic {metadata.version(_DISTRIBUTION_NAME)}"
    )


def test_readme_publishes_the_phase_two_clean_state_gate() -> None:
    """Public development guidance exposes the isolated aggregate command."""
    readme = " ".join(_README.read_text(encoding="utf-8").split())

    assert "## Phase 2 acceptance gate" in _README.read_text(encoding="utf-8")
    assert "scripts/verify-phase-2.sh" in readme
    for guarantee in (
        "clean checkout",
        "no active virtual environment",
        "no pre-existing `.venv` or `dist`",
        "temporary virtual environment",
        "`WORKAHOLIC_CONFIG_DIR`",
        "`WORKAHOLIC_DATA_DIR`",
        "never uses the operator's default profile or database",
    ):
        assert guarantee in readme


def test_phase_two_status_and_limitations_are_explicit() -> None:
    """Public implementation notices distinguish Phase 2 from planned v1."""
    readme = " ".join(_README.read_text(encoding="utf-8").split())
    architecture = " ".join(_ARCHITECTURE.read_text(encoding="utf-8").split())
    cli_contract = " ".join(_CLI_CONTRACT.read_text(encoding="utf-8").split())
    persistence = " ".join(_PERSISTENCE_CONTRACT.read_text(encoding="utf-8").split())

    for document in (readme, architecture, cli_contract, persistence):
        assert "`0.2.0a1`" in document
    for command in (
        "workaholic up",
        "workaholic status",
        "workaholic context",
        "workaholic project create",
        "workaholic project bind",
        "workaholic project list",
        "workaholic task add",
        "workaholic task list",
        "workaholic task show",
    ):
        assert command in readme
    for implemented in (
        "upward Workspace discovery",
        "multiple named Projects",
        "trusted embedded profiles",
        "schema version `2`",
        "all-Project Task",
    ):
        assert implemented in readme
    for unavailable in (
        "Agents",
        "Tokens",
        "`RemoteSession`",
        "JSON or PostgreSQL persistence adapters",
        "schema migration",
        "Project archival",
        "Task updates",
    ):
        assert unavailable in readme
    assert "canonical upward `.workaholic.env` discovery" in architecture
    assert "canonical upward Workspace discovery" in cli_contract
    assert "JSON and PostgreSQL adapters and schema migration remain unavailable" in (
        persistence
    )
    assert "including Phase 1 schema version `1`, is rejected unchanged" in persistence


def test_foundation_scope_decisions_are_consistent_across_public_documents() -> None:
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
