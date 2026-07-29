"""Tests for the repository documentation-link checker."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from scripts.check_doc_links import (
    collect_document_links,
    discover_markdown_files,
    validate_document_links,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_validate_document_links_accepts_paths_and_heading_fragments(
    tmp_path: Path,
) -> None:
    """Existing local paths and duplicate heading anchors resolve."""
    guide = tmp_path / "guide.md"
    guide.write_text(
        "# Guide\n\n## Install\n\n## Install\n",
        encoding="utf-8",
    )
    readme = tmp_path / "README.md"
    readme.write_text(
        "# Project\n\n[Guide](guide.md#install)\n"
        "[Second install](guide.md#install-1)\n",
        encoding="utf-8",
    )

    issues = validate_document_links(tmp_path, (readme, guide))

    assert issues == ()


def test_validate_document_links_reports_unsafe_and_missing_targets(
    tmp_path: Path,
) -> None:
    """Escapes, missing files, and stale fragments produce diagnostics."""
    guide = tmp_path / "guide.md"
    guide.write_text("# Guide\n", encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text(
        "# Project\n\n"
        "[Escape](../outside.md)\n"
        "[Missing](missing.md)\n"
        "[Stale](guide.md#missing)\n",
        encoding="utf-8",
    )

    issues = validate_document_links(tmp_path, (readme, guide))

    assert [issue.reason for issue in issues] == [
        "link escapes the repository",
        "local target does not exist",
        "Markdown heading does not exist",
    ]


def test_collect_document_links_ignores_code_and_external_url(
    tmp_path: Path,
) -> None:
    """Only parsed Markdown links are returned for later local validation."""
    readme = tmp_path / "README.md"
    readme.write_text(
        "# Project\n\n"
        "`[not a link](missing.md)`\n\n"
        "[External](https://example.com/docs)\n",
        encoding="utf-8",
    )

    links = collect_document_links(readme)

    assert [link.target for link in links] == ["https://example.com/docs"]


def test_discover_markdown_files_falls_back_outside_git_and_ignores_tools(
    tmp_path: Path,
) -> None:
    """Filesystem discovery excludes generated and environment directories."""
    readme = tmp_path / "README.md"
    readme.write_text("# Project\n", encoding="utf-8")
    ignored_directory = tmp_path / ".venv"
    ignored_directory.mkdir()
    (ignored_directory / "IGNORED.md").write_text("# Ignore\n", encoding="utf-8")

    markdown_files = discover_markdown_files(tmp_path)

    assert markdown_files == (readme,)


def test_validate_document_links_rejects_input_outside_project(
    tmp_path: Path,
) -> None:
    """The checker refuses files outside its declared repository boundary."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside project root"):
        validate_document_links(project_root, (outside,))
