"""Tests for golden-journey CLI contract assertions."""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from tests.golden import (
    GoldenCliInvocation,
    SubprocessGoldenJourneyRunner,
    _isolated_cli_environment,
    require_error,
    require_integer,
    require_object,
    require_success,
)


def _completed(
    *,
    returncode: int,
    stdout: str,
    stderr: str = "",
) -> CompletedProcess[str]:
    """Build a completed command result without invoking a process.

    Args:
        returncode: Process exit status.
        stdout: Captured standard output.
        stderr: Captured standard error.

    Returns:
        A deterministic completed-process value.

    """
    return CompletedProcess(
        args=("workaholic", "--json", "--non-interactive"),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_require_success_returns_validated_data() -> None:
    """A valid successful envelope exposes its JSON data."""
    result = _completed(
        returncode=0,
        stdout=(
            '{"schema":"workaholic.cli/v1","ok":true,'
            '"data":{"task":{"key":"ACME-1"}}}\n'
        ),
    )

    data = require_object(require_success(result), context="success data")

    assert data == {"task": {"key": "ACME-1"}}


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [
        pytest.param(
            1,
            '{"schema":"workaholic.cli/v1","ok":true,"data":{}}\n',
            id="nonzero-success",
        ),
        pytest.param(
            0,
            '{"schema":"workaholic.cli/v2","ok":true,"data":{}}\n',
            id="wrong-schema",
        ),
        pytest.param(
            0,
            '{"schema":"workaholic.cli/v1","ok":true,"data":{}}\nnoise',
            id="mixed-stdout",
        ),
        pytest.param(
            0,
            '{"schema":"workaholic.cli/v1","ok":true,"data":NaN}\n',
            id="nonstandard-number",
        ),
        pytest.param(
            0,
            '{"schema":"workaholic.cli/v1","ok":true,"data":{}}\n\n',
            id="extra-newline",
        ),
        pytest.param(
            0,
            ('{"schema":"workaholic.cli/v1","ok":true,"data":{},"unexpected":true}\n'),
            id="extra-envelope-field",
        ),
    ],
)
def test_require_success_rejects_contract_violations(
    returncode: int,
    stdout: str,
) -> None:
    """Malformed success output cannot satisfy a golden journey."""
    with pytest.raises(AssertionError):
        require_success(_completed(returncode=returncode, stdout=stdout))


def test_json_helpers_reject_any_stderr_output() -> None:
    """Machine-readable success and failure never mix in diagnostics."""
    success = _completed(
        returncode=0,
        stdout='{"schema":"workaholic.cli/v1","ok":true,"data":{}}\n',
        stderr="warning\n",
    )
    failure = _completed(
        returncode=3,
        stdout=(
            '{"schema":"workaholic.cli/v1","ok":false,'
            '"error":{"code":"TASK_NOT_FOUND","message":"Missing.",'
            '"retryable":false}}\n'
        ),
        stderr="warning\n",
    )

    with pytest.raises(AssertionError, match="stderr"):
        require_success(success)
    with pytest.raises(AssertionError, match="stderr"):
        require_error(failure, expected_code="TASK_NOT_FOUND")


@pytest.mark.parametrize("value", [True, 1.5, "1", None, -1])
def test_require_integer_rejects_non_integer_or_below_bound(value: object) -> None:
    """Cursor assertions cannot accept booleans, coercions, or negatives."""
    with pytest.raises(AssertionError):
        require_integer(value, context="cursor", minimum=0)  # type: ignore[arg-type]


def test_require_integer_returns_a_bounded_integer() -> None:
    """A valid event cursor is narrowed without coercion."""
    assert require_integer(7, context="cursor", minimum=0) == 7


def test_require_error_validates_machine_readable_failure() -> None:
    """A valid failure envelope exposes its structured error."""
    result = _completed(
        returncode=3,
        stdout=json.dumps(
            {
                "schema": "workaholic.cli/v1",
                "ok": False,
                "error": {
                    "code": "LEASE_LOST",
                    "message": "The Attempt is no longer active.",
                    "retryable": False,
                },
            },
            separators=(",", ":"),
        )
        + "\n",
    )

    error = require_error(result, expected_code="LEASE_LOST")

    assert error["retryable"] is False


@pytest.mark.parametrize(
    ("returncode", "error"),
    [
        pytest.param(0, {}, id="zero-exit"),
        pytest.param(
            3,
            {"code": "OTHER", "message": "Wrong code.", "retryable": False},
            id="wrong-code",
        ),
        pytest.param(
            3,
            {"code": "LEASE_LOST", "message": "", "retryable": False},
            id="empty-message",
        ),
        pytest.param(
            3,
            {"code": "LEASE_LOST", "message": "Lost.", "retryable": "false"},
            id="invalid-retryable",
        ),
    ],
)
def test_require_error_rejects_contract_violations(
    returncode: int,
    error: dict[str, object],
) -> None:
    """Malformed error output cannot satisfy a golden journey."""
    stdout = (
        json.dumps(
            {
                "schema": "workaholic.cli/v1",
                "ok": False,
                "error": error,
            },
            separators=(",", ":"),
        )
        + "\n"
    )

    with pytest.raises(AssertionError):
        require_error(
            _completed(returncode=returncode, stdout=stdout),
            expected_code="LEASE_LOST",
        )


def test_require_error_rejects_extra_envelope_fields() -> None:
    """An error envelope cannot silently extend its versioned top-level shape."""
    result = _completed(
        returncode=3,
        stdout=(
            '{"schema":"workaholic.cli/v1","ok":false,'
            '"error":{"code":"LEASE_LOST","message":"Lost.","retryable":false},'
            '"unexpected":true}\n'
        ),
    )

    with pytest.raises(AssertionError):
        require_error(result, expected_code="LEASE_LOST")


def test_subprocess_runner_validates_owned_paths_and_overrides(
    tmp_path: Path,
) -> None:
    """The runner cannot be redirected to developer state at runtime."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_directory = tmp_path / "data"
    config_directory = tmp_path / "config"
    runner = SubprocessGoldenJourneyRunner(
        data_directory=data_directory,
        config_directory=config_directory,
    )

    with pytest.raises(TypeError):
        SubprocessGoldenJourneyRunner(
            data_directory=Path("relative-data"),
            config_directory=config_directory,
        )
    with pytest.raises(TypeError):
        SubprocessGoldenJourneyRunner(
            data_directory=data_directory,
            config_directory=Path("relative-config"),
        )
    with pytest.raises(ValueError, match="must be distinct"):
        SubprocessGoldenJourneyRunner(
            data_directory=data_directory,
            config_directory=data_directory,
        )
    with pytest.raises(TypeError):
        runner.cli("status", cwd=workspace)
    with pytest.raises(TypeError):
        runner.cli((), cwd=Path("relative-workspace"))
    with pytest.raises(ValueError, match="existing directory"):
        runner.cli((), cwd=tmp_path / "missing")
    with pytest.raises(TypeError):
        runner.cli((), cwd=workspace, input_text=7)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="harness-owned"):
        runner.cli(
            (),
            cwd=workspace,
            environment={"WORKAHOLIC_DATA_DIR": str(tmp_path / "other")},
        )
    with pytest.raises(ValueError, match="config directory"):
        runner.cli(
            (),
            cwd=workspace,
            environment={"WORKAHOLIC_CONFIG_DIR": str(tmp_path / "other-config")},
        )
    with pytest.raises(ValueError, match="profile override"):
        runner.cli(
            (),
            cwd=workspace,
            environment={"WORKAHOLIC_PROFILE": "INVALID"},
        )
    with pytest.raises(ValueError, match="undocumented"):
        runner.cli((), cwd=workspace, environment={"UNSUPPORTED": "value"})


@pytest.mark.parametrize(
    "environment_key",
    [
        "WORKAHOLIC_SERVER_URL",
        "WORKAHOLIC_TOKEN",
        "WORKAHOLIC_CREDENTIAL",
        "AWS_ACCESS_KEY_ID",
        "PYTHONPATH",
        "UNSUPPORTED",
    ],
)
def test_subprocess_runner_rejects_sensitive_and_arbitrary_environment_injection(
    environment_key: str,
    tmp_path: Path,
) -> None:
    """Only documented local selectors may enter a golden CLI process."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runner = SubprocessGoldenJourneyRunner(
        data_directory=tmp_path / "data",
        config_directory=tmp_path / "config",
    )

    with pytest.raises(ValueError, match="undocumented"):
        runner.cli(
            (),
            cwd=workspace,
            environment={environment_key: "untrusted"},
        )


def test_subprocess_runner_accepts_only_exact_owned_paths_and_valid_profile(
    tmp_path: Path,
) -> None:
    """Documented Phase 2 selectors can repeat owned roots and select a profile."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_directory = tmp_path / "data"
    config_directory = tmp_path / "config"
    runner = SubprocessGoldenJourneyRunner(
        data_directory=data_directory,
        config_directory=config_directory,
    )

    result = runner.cli(
        ("--help",),
        cwd=workspace,
        environment={
            "WORKAHOLIC_CONFIG_DIR": str(config_directory),
            "WORKAHOLIC_DATA_DIR": str(data_directory),
            "WORKAHOLIC_PROFILE": "local",
        },
    )

    assert result.returncode == 0


def test_subprocess_runner_accepts_only_protected_owned_agent_tokens(
    tmp_path: Path,
) -> None:
    """An Agent override must select a mode-0600 Token below golden config."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_directory = tmp_path / "config"
    token_directory = config_directory / "tokens"
    token_directory.mkdir(parents=True)
    token_directory.chmod(0o700)
    token_file = token_directory / "agent.token"
    token_file.write_text("not-read-by-version-command\n", encoding="utf-8")
    token_file.chmod(0o600)
    runner = SubprocessGoldenJourneyRunner(
        data_directory=tmp_path / "data",
        config_directory=config_directory,
    )

    result = runner.cli(
        ("--version",),
        cwd=workspace,
        environment={"WORKAHOLIC_TOKEN_FILE": str(token_file)},
    )
    assert result.returncode == 0

    token_file.chmod(0o644)
    with pytest.raises(ValueError, match="protected harness-owned"):
        runner.cli(
            ("--version",),
            cwd=workspace,
            environment={"WORKAHOLIC_TOKEN_FILE": str(token_file)},
        )
    with pytest.raises(ValueError, match="file credential backend"):
        runner.cli(
            ("--version",),
            cwd=workspace,
            environment={"WORKAHOLIC_CREDENTIAL_BACKEND": "keyring"},
        )


def test_subprocess_runner_races_fresh_processes_in_stable_order(
    tmp_path: Path,
) -> None:
    """The race helper starts bounded real CLIs and preserves caller order."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runner = SubprocessGoldenJourneyRunner(
        data_directory=tmp_path / "data",
        config_directory=tmp_path / "config",
    )
    invocations = (
        GoldenCliInvocation(arguments=("--version",), cwd=workspace),
        GoldenCliInvocation(arguments=("--help",), cwd=workspace),
    )

    results = runner.cli_race(invocations)

    assert tuple(result.returncode for result in results) == (0, 0)
    assert "workaholic" in results[0].stdout.lower()
    assert "usage" in results[1].stdout.lower()


def test_subprocess_runner_validates_every_race_before_starting_processes(
    tmp_path: Path,
) -> None:
    """Malformed race collections and unsafe siblings fail before execution."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runner = SubprocessGoldenJourneyRunner(
        data_directory=tmp_path / "data",
        config_directory=tmp_path / "config",
    )
    valid = GoldenCliInvocation(arguments=("--version",), cwd=workspace)
    unsafe = GoldenCliInvocation(
        arguments=("--version",),
        cwd=workspace,
        environment={"WORKAHOLIC_TOKEN": "private"},
    )

    with pytest.raises(ValueError, match="between two and eight"):
        runner.cli_race((valid,))
    with pytest.raises(TypeError, match="exact invocation"):
        runner.cli_race((valid, object()))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="undocumented"):
        runner.cli_race((valid, unsafe))
    with pytest.raises(TypeError):
        GoldenCliInvocation(
            arguments=("--version",),
            cwd=Path("relative-workspace"),
        )


def test_subprocess_runner_does_not_inherit_credentials_or_arbitrary_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Only a small platform allowlist reaches fresh golden CLI processes."""
    monkeypatch.setenv("PATH", "/test-owned/path")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "private")
    monkeypatch.setenv("GITHUB_TOKEN", "private")
    monkeypatch.setenv("PYTHONPATH", "/untrusted/imports")
    monkeypatch.setenv("ARBITRARY_PARENT_STATE", "private")

    environment = _isolated_cli_environment(
        tmp_path / "data",
        tmp_path / "config",
        None,
    )

    assert environment["PATH"] == "/test-owned/path"
    assert environment["NO_COLOR"] == "1"
    assert environment["WORKAHOLIC_DATA_DIR"] == str(tmp_path / "data")
    assert environment["WORKAHOLIC_CONFIG_DIR"] == str(tmp_path / "config")
    assert environment["WORKAHOLIC_CREDENTIAL_BACKEND"] == "file"
    for forbidden in (
        "AWS_SECRET_ACCESS_KEY",
        "GITHUB_TOKEN",
        "PYTHONPATH",
        "ARBITRARY_PARENT_STATE",
    ):
        assert forbidden not in environment


def test_local_instance_is_available_while_future_golden_operations_are_not(
    tmp_path: Path,
) -> None:
    """Phase 5 exposes local orchestration but not future transports."""
    runner = SubprocessGoldenJourneyRunner(
        data_directory=tmp_path / "data",
        config_directory=tmp_path / "config",
    )

    local = runner.instance(
        backend="sqlite",
        project_key="ACME",
        remote=False,
        root=tmp_path / "instance",
        subjects={"operator": "human", "agent-one": "agent"},
    )
    assert local is not None
    with pytest.raises(NotImplementedError, match="non-SQLite"):
        runner.instance(
            backend="json",
            project_key="ACME",
            remote=False,
            root=tmp_path / "json-instance",
            subjects={"operator": "human"},
        )
    with pytest.raises(NotImplementedError, match="remote"):
        runner.instance(
            backend="sqlite",
            project_key="ACME",
            remote=True,
            root=tmp_path / "remote-instance",
            subjects={"operator": "human"},
        )
    with pytest.raises(NotImplementedError, match="Published-package"):
        runner.published_package_spec()
    with pytest.raises(NotImplementedError, match="uvx execution"):
        runner.uvx(
            "workaholic-ai==0.4.0a1",
            ("--version",),
            cwd=tmp_path,
        )
