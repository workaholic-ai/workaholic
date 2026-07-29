"""Validate repository-local links in Markdown documentation."""

from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from markdown_it.token import Token

_IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".gitnexus",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "build",
        "dist",
        "node_modules",
    }
)
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_MARKDOWN_PARSER = MarkdownIt("commonmark")


@dataclass(frozen=True, slots=True)
class DocumentLink:
    """One link extracted from a Markdown document.

    Attributes:
        source: Markdown file containing the link.
        line: One-based source line for the surrounding inline block.
        target: Link destination exactly as parsed from Markdown.

    """

    source: Path
    line: int
    target: str


@dataclass(frozen=True, slots=True)
class LinkIssue:
    """One invalid repository-local documentation link.

    Attributes:
        link: Link that failed validation.
        reason: Human-readable validation failure.

    """

    link: DocumentLink
    reason: str

    def format(self, project_root: Path) -> str:
        """Format the issue for command-line diagnostics.

        Args:
            project_root: Repository root used to shorten the source path.

        Returns:
            A stable ``path:line`` diagnostic.

        """
        source = self.link.source.relative_to(project_root)
        return f"{source}:{self.link.line}: {self.reason}: {self.link.target}"


def discover_markdown_files(project_root: Path) -> tuple[Path, ...]:
    """Discover tracked and untracked, non-ignored Markdown files.

    Git discovery includes new documentation before it is staged and respects
    repository ignore rules. A filesystem fallback keeps the checker usable
    from an extracted source distribution.

    Args:
        project_root: Repository or extracted source root to scan.

    Returns:
        Sorted absolute Markdown file paths.

    Raises:
        ValueError: If ``project_root`` is not an existing directory.

    """
    resolved_root = project_root.resolve()
    if not resolved_root.is_dir():
        message = f"Documentation root is not a directory: {resolved_root}"
        raise ValueError(message)

    git_executable = shutil.which("git")
    if git_executable is None:
        candidates = tuple(resolved_root.rglob("*.md"))
    else:
        # The resolved executable and fixed argument vector avoid shell input.
        git_result = subprocess.run(  # noqa: S603
            [
                git_executable,
                "-C",
                str(resolved_root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                "*.md",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if git_result.returncode == 0:
            candidates = tuple(
                resolved_root / relative_path
                for relative_path in git_result.stdout.splitlines()
                if relative_path
            )
        else:
            candidates = tuple(resolved_root.rglob("*.md"))

    markdown_files = {
        candidate.resolve()
        for candidate in candidates
        if candidate.is_file() and not _has_ignored_directory(candidate, resolved_root)
    }
    return tuple(sorted(markdown_files))


def collect_document_links(markdown_file: Path) -> tuple[DocumentLink, ...]:
    """Extract inline links and image sources from one Markdown document.

    Args:
        markdown_file: Existing Markdown file to parse.

    Returns:
        Links in document order.

    Raises:
        ValueError: If the input is not an existing Markdown file.

    """
    resolved_file = markdown_file.resolve()
    if not resolved_file.is_file() or resolved_file.suffix.casefold() != ".md":
        message = f"Not a Markdown file: {resolved_file}"
        raise ValueError(message)

    tokens = _MARKDOWN_PARSER.parse(resolved_file.read_text(encoding="utf-8"))
    links: list[DocumentLink] = []
    for token in tokens:
        if token.type != "inline" or token.children is None:
            continue
        line = token.map[0] + 1 if token.map is not None else 1
        for child in token.children:
            attribute = (
                "href"
                if child.type == "link_open"
                else "src"
                if child.type == "image"
                else None
            )
            if attribute is None:
                continue
            target = child.attrGet(attribute)
            if isinstance(target, str) and target:
                links.append(
                    DocumentLink(
                        source=resolved_file,
                        line=line,
                        target=target,
                    )
                )
    return tuple(links)


def validate_document_links(
    project_root: Path,
    markdown_files: Iterable[Path],
) -> tuple[LinkIssue, ...]:
    """Validate repository-local paths and Markdown heading fragments.

    External URLs and email links are outside this offline check. Local targets
    must stay inside the repository, exist, and reference a real Markdown
    heading when a fragment is present.

    Args:
        project_root: Boundary within which local links must resolve.
        markdown_files: Markdown files whose links should be checked.

    Returns:
        Validation issues in deterministic source order.

    Raises:
        ValueError: If the project root or an input path is invalid.

    """
    resolved_root = project_root.resolve()
    if not resolved_root.is_dir():
        message = f"Documentation root is not a directory: {resolved_root}"
        raise ValueError(message)

    anchor_cache: dict[Path, frozenset[str]] = {}
    issues: list[LinkIssue] = []
    for markdown_file in sorted(path.resolve() for path in markdown_files):
        if not markdown_file.is_relative_to(resolved_root):
            message = f"Markdown file is outside project root: {markdown_file}"
            raise ValueError(message)
        for link in collect_document_links(markdown_file):
            issue = _validate_link(resolved_root, link, anchor_cache)
            if issue is not None:
                issues.append(issue)
    return tuple(issues)


# Keeping each invalid state beside its diagnostic makes this boundary auditable.
def _validate_link(  # noqa: PLR0911
    project_root: Path,
    link: DocumentLink,
    anchor_cache: dict[Path, frozenset[str]],
) -> LinkIssue | None:
    """Validate one parsed link against the repository boundary."""
    parsed = urlsplit(link.target)
    if parsed.scheme or parsed.netloc:
        return None

    decoded_path = unquote(parsed.path)
    if decoded_path.startswith("/"):
        return LinkIssue(link=link, reason="root-relative link is not portable")

    target_path = (
        link.source
        if not decoded_path
        else (link.source.parent / decoded_path).resolve()
    )
    if not target_path.is_relative_to(project_root):
        return LinkIssue(link=link, reason="link escapes the repository")
    if not target_path.exists():
        return LinkIssue(link=link, reason="local target does not exist")

    fragment = unquote(parsed.fragment).casefold()
    if not fragment:
        return None
    if not target_path.is_file() or target_path.suffix.casefold() != ".md":
        return LinkIssue(
            link=link,
            reason="fragment target is not a Markdown file",
        )

    anchors = anchor_cache.setdefault(
        target_path,
        _document_anchors(target_path),
    )
    if fragment not in anchors:
        return LinkIssue(link=link, reason="Markdown heading does not exist")
    return None


def _document_anchors(markdown_file: Path) -> frozenset[str]:
    """Return GitHub-style heading anchors for one Markdown document."""
    tokens = _MARKDOWN_PARSER.parse(markdown_file.read_text(encoding="utf-8"))
    slug_counts: defaultdict[str, int] = defaultdict(int)
    anchors: set[str] = set()
    for index, token in enumerate(tokens):
        if token.type != "heading_open" or index + 1 >= len(tokens):
            continue
        inline = tokens[index + 1]
        if inline.type != "inline":
            continue
        base_slug = _slugify_heading(_inline_text(inline))
        duplicate_index = slug_counts[base_slug]
        slug_counts[base_slug] += 1
        slug = base_slug if duplicate_index == 0 else f"{base_slug}-{duplicate_index}"
        anchors.add(slug)
    return frozenset(anchors)


def _inline_text(token: Token) -> str:
    """Extract reader-visible text from a Markdown inline token."""
    if token.children is None:
        return token.content

    content: list[str] = []
    for child in token.children:
        if child.type in {"text", "code_inline", "image"}:
            content.append(child.content)
        elif child.type in {"softbreak", "hardbreak"}:
            content.append(" ")
        elif child.type == "html_inline":
            content.append(_HTML_TAG_PATTERN.sub("", child.content))
    return "".join(content)


def _slugify_heading(heading: str) -> str:
    """Convert heading text into the subset of GitHub anchor rules we use."""
    decoded_heading = html.unescape(heading).strip().casefold()
    characters = (
        character
        for character in decoded_heading
        if not unicodedata.category(character).startswith("P")
        or character in {"-", "_"}
    )
    return re.sub(r"\s+", "-", "".join(characters))


def _has_ignored_directory(path: Path, project_root: Path) -> bool:
    """Return whether a path traverses a generated or private tool directory."""
    relative_path = path.relative_to(project_root)
    return any(part in _IGNORED_DIRECTORY_NAMES for part in relative_path.parts[:-1])


def _resolve_requested_files(
    project_root: Path,
    requested_paths: Sequence[str],
) -> tuple[Path, ...]:
    """Resolve explicitly requested Markdown files within the project root."""
    if not requested_paths:
        return discover_markdown_files(project_root)

    resolved_files: list[Path] = []
    for requested_path in requested_paths:
        candidate = (project_root / requested_path).resolve()
        if not candidate.is_relative_to(project_root):
            message = f"Requested path is outside project root: {requested_path}"
            raise ValueError(message)
        if not candidate.is_file() or candidate.suffix.casefold() != ".md":
            message = f"Requested path is not a Markdown file: {requested_path}"
            raise ValueError(message)
        resolved_files.append(candidate)
    return tuple(sorted(set(resolved_files)))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the repository documentation-link checker.

    Args:
        argv: Optional command-line arguments excluding the executable name.

    Returns:
        Zero when every local link resolves, otherwise one.

    """
    parser = argparse.ArgumentParser(
        description="Validate repository-local Markdown links and anchors."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Optional Markdown paths relative to the repository root.",
    )
    arguments = parser.parse_args(argv)
    project_root = Path(__file__).resolve().parents[1]

    try:
        markdown_files = _resolve_requested_files(project_root, arguments.paths)
        issues = validate_document_links(project_root, markdown_files)
    except ValueError as error:
        sys.stderr.write(f"{error}\n")
        return 1

    if issues:
        for issue in issues:
            sys.stderr.write(f"{issue.format(project_root)}\n")
        return 1

    sys.stdout.write(f"Validated {len(markdown_files)} Markdown files.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
