"""Tests for public documentation and the authenticated Human quick start."""

from __future__ import annotations

import os
import re
import shutil
import stat
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
_RAW_TOKEN_PATTERN = re.compile(r"tok_[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}")
_REQUIRED_GLOSSARY_TERMS = frozenset(
    {
        "Agent",
        "Attempt",
        "Claim",
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
        "Unauthorized Claim or Attempt mutation",
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


def test_v1_task_model_excludes_parent_child_hierarchy() -> None:
    """V1 decomposition uses dependencies and provenance, not a hierarchy."""
    architecture = _ARCHITECTURE.read_text(encoding="utf-8")
    roadmap = _ROADMAP.read_text(encoding="utf-8")
    product_scope = _PRODUCT_SCOPE.read_text(encoding="utf-8")

    assert '"parent_uid"' not in architecture
    assert "create permitted child tasks" not in architecture
    assert "parent/child relationship" not in roadmap
    assert "explicit same-Project dependencies" in architecture
    assert "explicit same-Project dependencies" in roadmap
    assert "parent/child Task hierarchies" in product_scope


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


def _read_quick_start_script() -> str:
    """Return the one literal public quick-start shell block.

    Returns:
        Shell source copied directly from the README quick-start section.

    """
    readme = _README.read_text(encoding="utf-8")
    quick_start_match = _QUICK_START_PATTERN.search(readme)

    assert quick_start_match is not None
    bash_blocks = _BASH_BLOCK_PATTERN.findall(quick_start_match.group("section"))
    assert len(bash_blocks) == 1
    script = bash_blocks[0]
    assert isinstance(script, str)
    return script


def test_readme_quick_start_is_the_authenticated_human_two_agent_journey() -> None:
    """The literal quick start keeps its required identity and safety steps."""
    quick_start = _read_quick_start_script()

    for fragment in (
        "(\n  set -eu",
        "uv sync --frozen",
        "export WORKAHOLIC_CREDENTIAL_BACKEND=file",
        "workaholic up --project-key ACME",
        "workaholic auth create-agent agent-one",
        "workaholic auth create-agent agent-two",
        "workaholic auth grant agent-one agent --project ACME",
        "workaholic auth grant agent-two agent --project ACME",
        'WORKAHOLIC_TOKEN_FILE="$agent_one_token_file"',
        'WORKAHOLIC_TOKEN_FILE="$agent_two_token_file"',
        "workaholic task claim --json --non-interactive",
        "workaholic task submit ACME-1 --attempt",
        "workaholic task submit ACME-2 --attempt",
        "workaholic auth events",
    ):
        assert fragment in quick_start
    assert "WORKAHOLIC_TOKEN=" not in quick_start
    assert _RAW_TOKEN_PATTERN.search(quick_start) is None


@pytest.mark.requires_uv
def test_readme_quick_start_executes_in_an_isolated_source_checkout(
    tmp_path: Path,
) -> None:
    """Execute the literal Human/two-Agent quick start in isolated state."""
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
        ["/bin/sh", "-eu", "-c", _read_quick_start_script()],
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
    assert "ACME-2" in result.stdout
    assert "Agent one delivery" in result.stdout
    assert "Agent two delivery" in result.stdout
    assert "task_completed" in result.stdout
    assert "token_issued" in result.stdout
    assert _RAW_TOKEN_PATTERN.search(result.stdout) is None
    assert _RAW_TOKEN_PATTERN.search(result.stderr) is None
    config_directories = tuple(tmp_path.glob("workaholic-quickstart-config.*"))
    data_directories = tuple(tmp_path.glob("workaholic-quickstart-data.*"))
    token_directories = tuple(tmp_path.glob("workaholic-quickstart-tokens.*"))
    workspace_directories = tuple(tmp_path.glob("workaholic-quickstart-workspace.*"))
    assert len(config_directories) == 1
    assert len(data_directories) == 1
    assert len(token_directories) == 1
    assert len(workspace_directories) == 1
    assert (data_directories[0] / "local.db").is_file()
    assert (workspace_directories[0] / ".workaholic.env").is_file()
    credential_file = config_directories[0] / "credentials" / "credentials.toml"
    assert credential_file.is_file()
    assert stat.S_IMODE(credential_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(token_directories[0].stat().st_mode) == 0o700
    token_files = tuple(sorted(token_directories[0].glob("*.token")))
    assert tuple(path.name for path in token_files) == (
        "agent-one.token",
        "agent-two.token",
    )
    for token_file in token_files:
        assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
        assert _RAW_TOKEN_PATTERN.fullmatch(token_file.read_text().strip()) is not None
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


def test_readme_publishes_current_checks_and_clean_state_gate() -> None:
    """Public guidance exposes current checks and the latest complete gate."""
    readme = " ".join(_README.read_text(encoding="utf-8").split())

    assert "## Development checks" in _README.read_text(encoding="utf-8")
    for command in (
        "uv run pre-commit run --all-files",
        "uv run pytest",
        "uv build --no-progress",
        "scripts/smoke-install.sh",
        "scripts/verify-phase-0.sh",
        "scripts/verify-phase-1.sh",
        "scripts/verify-phase-2.sh",
        "scripts/verify-phase-3.sh",
        "scripts/verify-phase-4.sh",
        "scripts/verify-phase-5.sh",
        "scripts/smoke-phase-5-wheel.sh",
    ):
        assert command in readme
    for guarantee in (
        "## Current clean-state acceptance gate",
        "active virtual environment",
        "refuses a dirty checkout",
        "temporary config, credential, data, Token-file, and Workspace roots",
        "Phase 5 identity journey",
    ):
        assert guarantee in readme


def test_phase_five_status_and_limitations_are_explicit() -> None:
    """Public implementation notices distinguish Phase 5 from planned v1."""
    readme = " ".join(_README.read_text(encoding="utf-8").split())
    architecture = " ".join(_ARCHITECTURE.read_text(encoding="utf-8").split())
    cli_contract = " ".join(_CLI_CONTRACT.read_text(encoding="utf-8").split())
    persistence = " ".join(_PERSISTENCE_CONTRACT.read_text(encoding="utf-8").split())

    for document in (readme, architecture, cli_contract, persistence):
        assert "`0.5.0a1`" in document
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
        "workaholic task update",
        "workaholic task block",
        "workaholic task unblock",
        "workaholic task cancel",
        "workaholic task add-dependency",
        "workaholic task remove-dependency",
        "workaholic task claim",
        "workaholic task renew",
        "workaholic task heartbeat",
        "workaholic task progress",
        "workaholic task release",
        "workaholic task submit",
        "workaholic task approve",
        "workaholic task reject",
        "workaholic task events",
    ):
        assert command in readme
    for implemented in (
        "upward Workspace discovery",
        "multiple Projects",
        "trusted embedded profiles",
        "schema version `5`",
        "structured progress and Results",
        "exclusive Claims",
        "Agent Attempts",
        "authenticating as distinct Subjects",
        "least-privilege Project roles",
        "administrative audit",
    ):
        assert implemented in readme
    for unavailable in (
        "`RemoteSession`",
        "JSON or PostgreSQL persistence adapters",
        "schema migration",
        "capability-based scheduling",
        "Project archival",
        "force interruption",
        "parent/child Task hierarchies",
    ):
        assert unavailable in readme
    assert "canonical upward `.workaholic.env` discovery" in architecture
    assert "canonical upward Workspace discovery" in cli_contract
    assert "JSON and PostgreSQL adapters and schema migration remain unavailable" in (
        persistence
    )
    assert "including Phase 4 schema version `4`, is rejected unchanged" in persistence


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
