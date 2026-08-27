"""Validate the least-privilege continuous-integration contract."""

from __future__ import annotations

import re
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

_PROJECT_ROOT = Path(__file__).parents[3]
_WORKFLOW_PATH = _PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
_DEPENDABOT_PATH = _PROJECT_ROOT / ".github" / "dependabot.yml"
_PRE_COMMIT_PATH = _PROJECT_ROOT / ".pre-commit-config.yaml"
_EXPECTED_ACTIONS = {
    "actions/checkout": (
        "de0fac2e4500dabe0009e67214ff5f5447ce83dd",
        "v6.0.2",
    ),
    "actions/download-artifact": (
        "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
        "v8.0.1",
    ),
    "actions/upload-artifact": (
        "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        "v7.0.1",
    ),
    "astral-sh/setup-uv": (
        "08807647e7069bb48b6ef5acd8ec9567f424441b",
        "v8.1.0",
    ),
}
_EXPECTED_ACTION_COUNTS = Counter(
    {
        "actions/checkout": 4,
        "actions/download-artifact": 1,
        "actions/upload-artifact": 1,
        "astral-sh/setup-uv": 4,
    }
)
_ACTION_LINE_PATTERN = re.compile(
    r"^\s*uses:\s*(?P<repository>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"
    r"@(?P<revision>[0-9a-f]{40})\s+#\s+(?P<version>v[0-9.]+)\s*$"
)


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a repository YAML file as a mapping.

    Args:
        path: YAML file to parse.

    Returns:
        Parsed top-level mapping.

    Raises:
        AssertionError: If the top-level value is not a mapping.

    """
    loaded: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{path} must contain a YAML mapping."
    return loaded


def _workflow() -> dict[str, Any]:
    """Return the parsed CI workflow."""
    return _load_yaml(_WORKFLOW_PATH)


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    """Return validated steps from one workflow job.

    Args:
        job: Parsed GitHub Actions job mapping.

    Returns:
        Ordered workflow step mappings.

    """
    steps = job.get("steps")
    assert isinstance(steps, list)
    assert all(isinstance(step, dict) for step in steps)
    return steps


def _named_step(job: dict[str, Any], name: str) -> dict[str, Any]:
    """Return exactly one job step by display name.

    Args:
        job: Parsed GitHub Actions job mapping.
        name: Exact step name.

    Returns:
        Matching workflow step.

    Raises:
        AssertionError: If the step is missing or duplicated.

    """
    matching = [step for step in _steps(job) if step.get("name") == name]
    assert len(matching) == 1, f"Expected one workflow step named {name}."
    return matching[0]


def _run_commands(job: dict[str, Any]) -> tuple[str, ...]:
    """Return shell commands declared by a workflow job.

    Args:
        job: Parsed GitHub Actions job mapping.

    Returns:
        Ordered run-command strings.

    """
    commands = [step["run"] for step in _steps(job) if "run" in step]
    assert all(isinstance(command, str) for command in commands)
    return tuple(commands)


def test_workflow_exposes_exact_branch_protection_checks() -> None:
    """CI publishes the four stable checks required by the roadmap."""
    workflow = _workflow()
    jobs = workflow["jobs"]

    assert workflow["name"] == "CI"
    assert workflow["on"] == {
        "pull_request": None,
        "push": {"branches": ["main"]},
    }
    assert workflow["env"] == {
        "PYTHON_VERSION": "3.14",
        "UV_VERSION": "0.11.16",
    }
    assert workflow["defaults"] == {"run": {"shell": "bash"}}
    assert set(jobs) == {"quality", "tests", "build", "wheel-smoke"}
    for job_id, job in jobs.items():
        assert job["name"] == job_id
        assert job["runs-on"] == "ubuntu-24.04"
        assert 1 <= job["timeout-minutes"] <= 15

    assert jobs["wheel-smoke"]["needs"] == ["build"]


def test_workflow_has_read_only_permissions_and_no_secret_or_publish_path() -> None:
    """CI cannot mutate repository state or publish with a long-lived secret."""
    workflow = _workflow()
    source = _WORKFLOW_PATH.read_text(encoding="utf-8")

    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] is True
    assert "pull_request_target" not in source
    assert "secrets." not in source
    assert "id-token:" not in source
    assert re.search(r"^\s+[A-Za-z-]+:\s+write\s*$", source, re.MULTILINE) is None
    assert "persist-credentials: true" not in source
    assert "uv publish" not in source
    assert all("permissions" not in job for job in workflow["jobs"].values())


def test_every_action_is_allowlisted_and_pinned_to_an_immutable_commit() -> None:
    """Third-party workflow code uses audited full-length release revisions."""
    action_references: list[str] = []
    action_counts: Counter[str] = Counter()

    for line in _WORKFLOW_PATH.read_text(encoding="utf-8").splitlines():
        if "uses:" not in line:
            continue
        match = _ACTION_LINE_PATTERN.fullmatch(line)
        assert match is not None, f"Action reference is not immutably pinned: {line}"
        repository = match.group("repository")
        assert repository in _EXPECTED_ACTIONS
        expected_revision, expected_version = _EXPECTED_ACTIONS[repository]
        assert match.group("revision") == expected_revision
        assert match.group("version") == expected_version
        action_references.append(repository)
        action_counts[repository] += 1

    assert action_references
    assert action_counts == _EXPECTED_ACTION_COUNTS


def test_checkout_and_uv_setup_are_reproducible_in_every_job() -> None:
    """Every job gets only source plus the pinned uv and Python runtimes."""
    for job in _workflow()["jobs"].values():
        checkout = _named_step(job, "Check out the repository")
        setup_uv = _named_step(job, "Install uv and Python")

        assert checkout["with"] == {"persist-credentials": False}
        assert setup_uv["with"] == {
            "cache-dependency-glob": "uv.lock",
            "enable-cache": True,
            "python-version": "${{ env.PYTHON_VERSION }}",
            "version": "${{ env.UV_VERSION }}",
        }


def test_quality_tests_and_build_use_the_frozen_dependency_graph() -> None:
    """Source-based CI jobs install without mutating the committed lockfile."""
    jobs = _workflow()["jobs"]
    expected_commands = {
        "quality": (
            "uv sync --frozen",
            "uv run --frozen pre-commit run --all-files",
        ),
        "tests": (
            "uv sync --frozen",
            'uv run --frozen pytest -m "not distribution"',
        ),
        "build": (
            "uv sync --frozen",
            "uv build --no-progress",
        ),
    }

    for job_id, commands in expected_commands.items():
        assert _run_commands(jobs[job_id]) == commands
    assert all(
        "uv lock" not in command
        for job in jobs.values()
        for command in _run_commands(job)
    )


def test_source_job_excludes_only_explicit_distribution_acceptance() -> None:
    """Recursive clean-checkout suites stay outside the bounded source job."""
    distribution_specs = sorted(
        (_PROJECT_ROOT / "tests" / "e2e").glob("test_phase_*_distribution.py")
    )

    assert len(distribution_specs) >= 5
    for specification in distribution_specs:
        source = specification.read_text(encoding="utf-8")
        assert "\n    pytest.mark.distribution,\n" in source, specification.name


def test_build_artifact_is_inspectable_but_never_published() -> None:
    """Build outputs are retained briefly and passed to the smoke job by name."""
    jobs = _workflow()["jobs"]
    upload = _named_step(jobs["build"], "Upload distributions for inspection")
    download = _named_step(
        jobs["wheel-smoke"],
        "Download the distributions built in this workflow",
    )

    assert upload["with"] == {
        "if-no-files-found": "error",
        "name": "python-distributions",
        "path": "dist/*.tar.gz\ndist/*.whl\n",
        "retention-days": 7,
    }
    assert download["with"] == {
        "digest-mismatch": "error",
        "name": "python-distributions",
        "path": "dist",
    }


def test_wheel_smoke_job_cannot_fall_back_to_an_editable_install() -> None:
    """The final job tests only the downloaded wheel outside the checkout."""
    commands = _run_commands(_workflow()["jobs"]["wheel-smoke"])

    assert commands == (
        "scripts/smoke-install.sh dist/workaholic_ai-*.whl",
        "scripts/smoke-phase-4-wheel.sh dist/workaholic_ai-*.whl",
    )
    assert all("uv sync" not in command for command in commands)
    assert all("uv run" not in command for command in commands)


def test_dependabot_covers_every_external_action_reference() -> None:
    """Dependabot monitors immutable action revisions for reviewed updates."""
    updates = _load_yaml(_DEPENDABOT_PATH)["updates"]
    github_actions = [
        update
        for update in updates
        if update.get("package-ecosystem") == "github-actions"
    ]

    assert len(github_actions) == 1
    assert github_actions[0]["directory"] == "/"
    assert github_actions[0]["schedule"]["interval"] == "weekly"


def test_pre_commit_runs_ci_contracts_when_ci_files_change() -> None:
    """Commit-stage checks validate workflow security and smoke behavior."""
    repositories = _load_yaml(_PRE_COMMIT_PATH)["repos"]
    local_repository = next(
        repository for repository in repositories if repository["repo"] == "local"
    )
    hooks = local_repository["hooks"]
    actionlint_hooks = [hook for hook in hooks if hook.get("id") == "actionlint"]
    ci_hooks = [hook for hook in hooks if hook.get("id") == "ci-contracts"]

    assert len(actionlint_hooks) == 1
    assert actionlint_hooks[0]["entry"] == "uv run --frozen actionlint"
    assert actionlint_hooks[0]["files"] == r"^\.github/workflows/.*\.ya?ml$"
    assert len(ci_hooks) == 1
    hook = ci_hooks[0]
    assert "test_ci_configuration.py" in hook["entry"]
    assert "test_smoke_install.py" in hook["entry"]
    assert hook["pass_filenames"] is False


def test_source_distribution_retains_ci_contract_inputs() -> None:
    """Source archives include workflows and scripts required by their tests."""
    pyproject = tomllib.loads(
        (_PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    includes = set(pyproject["tool"]["hatch"]["build"]["targets"]["sdist"]["include"])

    assert "/.github" in includes
    assert "/scripts" in includes
    assert "/tests" in includes
