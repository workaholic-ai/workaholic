"""Tests for the public Phase 0 documentation contracts."""

from __future__ import annotations

import re
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlsplit

_PROJECT_ROOT = Path(__file__).parents[3]
_README = _PROJECT_ROOT / "README.md"
_CONTRIBUTING = _PROJECT_ROOT / "CONTRIBUTING.md"
_PULL_REQUEST_TEMPLATE = _PROJECT_ROOT / ".github" / "pull_request_template.md"
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
_EXPECTED_QUICK_START = """uv sync
uv run workaholic --version
uv run pytest"""


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
