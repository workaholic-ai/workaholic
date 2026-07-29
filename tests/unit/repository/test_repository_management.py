"""Validate community policy and GitHub repository-management contracts."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

_PROJECT_ROOT = Path(__file__).parents[3]
_GITHUB_DIRECTORY = _PROJECT_ROOT / ".github"
_ISSUE_TEMPLATE_DIRECTORY = _GITHUB_DIRECTORY / "ISSUE_TEMPLATE"
_ISSUE_FORM_PATHS = tuple(
    _ISSUE_TEMPLATE_DIRECTORY / filename
    for filename in ("architecture-decision.yml", "bug.yml", "feature.yml")
)
_DEPENDABOT_PATH = _GITHUB_DIRECTORY / "dependabot.yml"
_ISSUE_CONFIG_PATH = _ISSUE_TEMPLATE_DIRECTORY / "config.yml"
_CODEOWNERS_PATH = _GITHUB_DIRECTORY / "CODEOWNERS"
_OWNER = "@pavelsg"
_SECURITY_CONTACT = "pg@ithesion.com"
_AREA_LABELS = frozenset(
    {
        "area:auth",
        "area:cli",
        "area:context",
        "area:docs",
        "area:domain",
        "area:release",
        "area:server",
        "area:storage",
    }
)
_KIND_LABELS = frozenset(
    {
        "kind:bug",
        "kind:decision",
        "kind:feature",
        "kind:refactor",
        "kind:security",
        "kind:test",
    }
)
_PRIORITY_LABELS = frozenset(
    {"priority:p0", "priority:p1", "priority:p2", "priority:p3"}
)
_STATUS_LABELS = frozenset({"status:blocked", "status:needs-design", "status:ready"})
_LABEL_TAXONOMY = _AREA_LABELS | _KIND_LABELS | _PRIORITY_LABELS | _STATUS_LABELS
_ISSUE_FORM_TYPES = frozenset(
    {"checkboxes", "dropdown", "input", "markdown", "textarea"}
)
_SECRET_REQUEST_PATTERN = re.compile(
    r"\b(?:attach|enter|paste|provide|share|supply|upload)\b"
    r"[^.\n]{0,80}"
    r"\b(?:access tokens?|credentials?|passwords?|private keys?|secrets?)\b",
    flags=re.IGNORECASE,
)
_CODEOWNER_PATTERN = re.compile(
    r"^@[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
    r"(?:/[A-Za-z0-9][A-Za-z0-9_.-]*)?$"
)
_REQUIRED_CODEOWNER_PATTERNS = frozenset(
    {
        "*",
        "/.github/",
        "/.github/CODEOWNERS",
        "/.github/workflows/",
        "/SECURITY.md",
        "/docs/threat-model.md",
        "/src/workaholic/auth/",
        "/src/workaholic/protocol/",
        "/docs/persistence-contract.md",
        "/src/workaholic/persistence/",
    }
)
_PUBLIC_POLICY_LINKS = frozenset(
    {
        "(CHANGELOG.md)",
        "(CODE_OF_CONDUCT.md)",
        "(CONTRIBUTING.md)",
        "(LICENSE)",
        "(SECURITY.md)",
        "(docs/compatibility-policy.md)",
    }
)
_PLACEHOLDER_PATTERN = re.compile(
    r"(?:\[insert|@owner\b|@your|example\.com|security@example|"
    r"\bTBD\b|\bTODO:|your[- ]organi[sz]ation)",
    flags=re.IGNORECASE,
)


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load one YAML mapping with safe construction.

    Args:
        path: Repository YAML file to parse.

    Returns:
        Parsed top-level mapping.

    Raises:
        AssertionError: If the YAML document is not a mapping.

    """
    parsed: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), f"{path} must contain a YAML mapping."
    return parsed


def _issue_field(form: dict[str, Any], field_id: str) -> dict[str, Any]:
    """Return a named issue-form field.

    Args:
        form: Parsed issue-form mapping.
        field_id: Stable field identifier to locate.

    Returns:
        Matching issue-form field.

    Raises:
        AssertionError: If the field is missing or malformed.

    """
    body = form.get("body")
    assert isinstance(body, list)
    matching_fields = [
        field
        for field in body
        if isinstance(field, dict) and field.get("id") == field_id
    ]
    assert len(matching_fields) == 1, f"Expected one issue field named {field_id}."
    return matching_fields[0]


def _parse_codeowners() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Parse and validate the supported CODEOWNERS syntax subset.

    Returns:
        Ordered pattern and owner entries.

    Raises:
        AssertionError: If a non-comment line has unsupported syntax.

    """
    entries: list[tuple[str, tuple[str, ...]]] = []
    for line_number, line in enumerate(
        _CODEOWNERS_PATH.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        parts = stripped.split()
        assert len(parts) >= 2, f"CODEOWNERS:{line_number} has no owner."
        pattern, *owners = parts
        assert not pattern.startswith("!")
        assert not any(character in pattern for character in "[]#")
        assert all(_CODEOWNER_PATTERN.fullmatch(owner) for owner in owners), (
            f"CODEOWNERS:{line_number} has an invalid owner."
        )
        entries.append((pattern, tuple(owners)))

    assert entries, "CODEOWNERS must contain at least one ownership rule."
    return tuple(entries)


def test_task_nine_deliverables_exist() -> None:
    """Every community and repository-management deliverable is present."""
    expected_paths = (
        _PROJECT_ROOT / "CHANGELOG.md",
        _PROJECT_ROOT / "CODE_OF_CONDUCT.md",
        _PROJECT_ROOT / "README.md",
        _PROJECT_ROOT / "SECURITY.md",
        _CODEOWNERS_PATH,
        _DEPENDABOT_PATH,
        _GITHUB_DIRECTORY / "pull_request_template.md",
        _ISSUE_CONFIG_PATH,
        *_ISSUE_FORM_PATHS,
    )

    assert all(path.is_file() for path in expected_paths)


@pytest.mark.parametrize(
    "path",
    [*_ISSUE_FORM_PATHS, _ISSUE_CONFIG_PATH, _DEPENDABOT_PATH],
    ids=lambda path: path.name,
)
def test_repository_yaml_files_parse_as_mappings(path: Path) -> None:
    """Every issue and dependency-management YAML file parses safely."""
    assert _load_yaml(path)


@pytest.mark.parametrize("path", _ISSUE_FORM_PATHS, ids=lambda path: path.stem)
def test_issue_forms_follow_schema_and_label_taxonomy(path: Path) -> None:
    """Issue forms use stable fields and the accepted repository labels."""
    form = _load_yaml(path)

    assert isinstance(form.get("name"), str)
    assert form["name"].strip()
    assert isinstance(form.get("description"), str)
    assert form["description"].strip()
    assert isinstance(form.get("body"), list)
    assert form["body"]
    labels = form.get("labels")
    assert isinstance(labels, list)
    assert labels
    assert all(isinstance(label, str) and label in _LABEL_TAXONOMY for label in labels)
    assert any(label in _KIND_LABELS for label in labels)
    assert any(label in _PRIORITY_LABELS for label in labels)
    assert any(label in _STATUS_LABELS for label in labels)

    field_ids: list[str] = []
    for field in form["body"]:
        assert isinstance(field, dict)
        assert field.get("type") in _ISSUE_FORM_TYPES
        if field["type"] == "markdown":
            assert "id" not in field
            continue
        field_id = field.get("id")
        assert isinstance(field_id, str)
        assert re.fullmatch(r"[a-z][a-z0-9_]*", field_id)
        field_ids.append(field_id)
        assert isinstance(field.get("attributes"), dict)

    assert len(field_ids) == len(set(field_ids))
    area_field = _issue_field(form, "area")
    assert area_field["type"] == "dropdown"
    assert set(area_field["attributes"]["options"]) == _AREA_LABELS
    assert area_field["validations"]["required"] is True
    assert _issue_field(form, "confirmation")["type"] == "checkboxes"


@pytest.mark.parametrize("path", _ISSUE_FORM_PATHS, ids=lambda path: path.stem)
def test_issue_forms_divert_sensitive_reports_and_never_request_secrets(
    path: Path,
) -> None:
    """Public forms reject sensitive data and point to private reporting."""
    source = path.read_text(encoding="utf-8")
    normalized = " ".join(source.casefold().split())

    assert "private security-reporting process" in normalized
    assert "unpatched vulnerabilit" in normalized
    assert "contains no secrets, credentials" in normalized
    assert _SECRET_REQUEST_PATTERN.search(source) is None


def test_issue_chooser_disables_blank_security_bypasses() -> None:
    """The issue chooser directs vulnerability reports to the policy page."""
    configuration = _load_yaml(_ISSUE_CONFIG_PATH)

    assert configuration["blank_issues_enabled"] is False
    contact_links = configuration["contact_links"]
    assert isinstance(contact_links, list)
    assert len(contact_links) == 1
    security_link = contact_links[0]
    assert security_link["url"] == (
        "https://github.com/workaholic-ai/workaholic/security/policy"
    )
    assert "Do not disclose" in security_link["about"]


def test_dependabot_updates_are_bounded_grouped_and_staggered() -> None:
    """Dependency automation remains reviewable across every used ecosystem."""
    configuration = _load_yaml(_DEPENDABOT_PATH)

    assert configuration["version"] == 2
    updates = configuration["updates"]
    assert isinstance(updates, list)
    assert {update["package-ecosystem"] for update in updates} == {
        "github-actions",
        "pre-commit",
        "uv",
    }

    expected_days = {
        "uv": "monday",
        "pre-commit": "tuesday",
        "github-actions": "wednesday",
    }
    expected_labels = {
        "area:release",
        "kind:refactor",
        "priority:p2",
        "status:ready",
    }
    for update in updates:
        ecosystem = update["package-ecosystem"]
        assert update["directory"] == "/"
        assert 1 <= update["open-pull-requests-limit"] <= 5
        assert set(update["labels"]) == expected_labels
        assert update["commit-message"] == {"prefix": "deps"}
        assert update["schedule"] == {
            "interval": "weekly",
            "day": expected_days[ecosystem],
            "time": "06:00",
            "timezone": "Europe/Riga",
        }

        groups = update["groups"]
        assert isinstance(groups, dict)
        assert len(groups) == 1
        group = next(iter(groups.values()))
        assert group["patterns"] == ["*"]
        assert group["update-types"] == ["minor", "patch"]


def test_codeowners_has_valid_default_and_sensitive_boundary_rules() -> None:
    """CODEOWNERS protects governance and high-risk architecture boundaries."""
    entries = _parse_codeowners()
    patterns = {pattern for pattern, _owners in entries}

    assert _CODEOWNERS_PATH.stat().st_size < 3 * 1024 * 1024
    assert entries[0] == ("*", (_OWNER,))
    assert patterns >= _REQUIRED_CODEOWNER_PATTERNS
    assert all(owners == (_OWNER,) for _pattern, owners in entries)


def test_public_policies_use_real_contacts_and_have_no_placeholders() -> None:
    """Public policy files retain the accepted owner and security identity."""
    policy_paths = (
        _PROJECT_ROOT / "CODE_OF_CONDUCT.md",
        _PROJECT_ROOT / "SECURITY.md",
        _CODEOWNERS_PATH,
        *_ISSUE_FORM_PATHS,
        _ISSUE_CONFIG_PATH,
        _DEPENDABOT_PATH,
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in policy_paths)

    assert _SECURITY_CONTACT in combined
    assert _OWNER in combined
    assert _PLACEHOLDER_PATTERN.search(combined) is None


def test_readme_links_every_public_policy_file() -> None:
    """README exposes all public contribution, support, and legal policies."""
    readme = (_PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    for link in _PUBLIC_POLICY_LINKS:
        assert link in readme
    assert "https://github.com/workaholic-ai/workaholic/issues/new/choose" in readme


def test_source_distribution_includes_public_policy_documents() -> None:
    """The source archive retains policies referenced by its packaged README."""
    pyproject = tomllib.loads(
        (_PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    includes = set(pyproject["tool"]["hatch"]["build"]["targets"]["sdist"]["include"])

    assert includes >= {
        "/CHANGELOG.md",
        "/CODE_OF_CONDUCT.md",
        "/CONTRIBUTING.md",
        "/LICENSE",
        "/README.md",
        "/SECURITY.md",
    }


def test_security_policy_defines_support_and_private_response_process() -> None:
    """Security policy states support scope, reporting, and remediation flow."""
    security_policy = " ".join(
        (_PROJECT_ROOT / "SECURITY.md").read_text(encoding="utf-8").split()
    )

    assert _SECURITY_CONTACT in security_policy
    assert "| `main` | Yes |" in security_policy
    assert "Do not publicly disclose an unpatched vulnerability" in security_policy
    for stage in (
        "acknowledge the report",
        "validate the finding",
        "assess severity",
        "prepare and verify a remediation",
        "coordinate a disclosure date",
        "publish security guidance",
    ):
        assert stage in security_policy


def test_changelog_starts_with_an_unreleased_keep_a_changelog_section() -> None:
    """Changelog structure remains compatible with Keep a Changelog."""
    changelog = (_PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    second_level_headings = re.findall(r"^## (.+)$", changelog, flags=re.MULTILINE)

    assert "https://keepachangelog.com/en/1.1.0/" in changelog
    assert second_level_headings[0] == "Unreleased"
