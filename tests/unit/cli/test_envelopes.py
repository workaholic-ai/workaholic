"""Unit tests for Phase 1 CLI envelopes, rendering, and option aliases."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from subprocess import CompletedProcess
from typing import get_args

import pytest
import typer
from pydantic import ValidationError
from tests.golden import require_object, require_success
from typer.models import OptionInfo
from typer.testing import CliRunner

from workaholic.cli.envelopes import (
    CLI_SCHEMA,
    JsonError,
    JsonErrorDetail,
    JsonSuccess,
    is_json_value,
    normalize_json_value,
)
from workaholic.cli.options import (
    AttemptOption,
    CursorOption,
    IdempotencyKeyOption,
    JsonOption,
    LeaseOption,
    LimitOption,
    NonInteractiveOption,
)
from workaholic.cli.rendering import write_success
from workaholic.domain import (
    ProjectId,
    SubjectId,
    Task,
    TaskId,
    TaskState,
)

_NOW = datetime(2026, 7, 30, 16, 30, 45, 123456, tzinfo=UTC)
_RUNNER = CliRunner()


def _task() -> Task:
    """Build one real domain Task for serialization coverage.

    Returns:
        Deterministic initial Task.

    """
    return Task(
        uid=TaskId("tsk_first"),
        project_id=ProjectId("prj_acme"),
        number=1,
        key="ACME-1",
        title="Ž first task",
        objective="Ž first task",
        state=TaskState.OPEN,
        priority=50,
        version=1,
        created_by=SubjectId("sub_local"),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _completed(stdout: str) -> CompletedProcess[str]:
    """Build one successful completed process for golden assertions.

    Args:
        stdout: Captured CLI JSON output.

    Returns:
        Deterministic successful process value.

    """
    return CompletedProcess(
        args=("workaholic", "--json", "--non-interactive"),
        returncode=0,
        stdout=stdout,
        stderr="",
    )


def _option_info(alias: object) -> OptionInfo:
    """Extract one Typer option metadata object from an Annotated alias.

    Args:
        alias: Public CLI option type alias.

    Returns:
        Typer OptionInfo metadata.

    """
    metadata = get_args(alias)
    assert len(metadata) == 2
    option = metadata[1]
    assert isinstance(option, OptionInfo)
    return option


def test_success_and_error_models_have_exact_closed_shapes() -> None:
    """Envelope models emit only the documented schema fields."""
    success = JsonSuccess(data={"task": {"key": "ACME-1"}})
    error = JsonError(
        error=JsonErrorDetail(
            code="TASK_NOT_FOUND",
            message="The Task was not found.",
            retryable=False,
        )
    )

    assert success.model_dump(by_alias=True) == {
        "schema": CLI_SCHEMA,
        "ok": True,
        "data": {"task": {"key": "ACME-1"}},
    }
    assert error.model_dump(by_alias=True) == {
        "schema": CLI_SCHEMA,
        "ok": False,
        "error": {
            "code": "TASK_NOT_FOUND",
            "message": "The Task was not found.",
            "retryable": False,
        },
    }
    with pytest.raises(ValidationError):
        JsonSuccess.model_validate({"data": {}, "error": {}})
    with pytest.raises(ValidationError):
        JsonError.model_validate(
            {
                "error": {
                    "code": "lowercase",
                    "message": "Invalid code.",
                    "retryable": False,
                }
            }
        )
    with pytest.raises(ValidationError):
        JsonErrorDetail(
            code="INTERNAL_ERROR",
            message=" line\nbreak ",
            retryable=False,
        )


def test_json_success_is_one_utf8_envelope_accepted_by_golden_helpers(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """JSON rendering preserves Unicode and satisfies the golden contract."""
    write_success(
        {"task": {"key": "ACME-1", "title": "Ž first task"}},
        json_mode=True,
    )

    captured = capsys.readouterr()

    assert captured.err == ""
    assert captured.out.endswith("\n")
    assert not captured.out.endswith("\n\n")
    assert "Ž first task" in captured.out
    assert "\\u017d" not in captured.out
    data = require_object(
        require_success(_completed(captured.out)),
        context="success data",
    )
    assert data == {"task": {"key": "ACME-1", "title": "Ž first task"}}


def test_domain_dataclass_identifiers_enums_and_timestamps_serialize_exactly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Boundary normalization emits stable primitives for a real domain Task."""
    write_success({"task": _task()}, json_mode=True)

    payload = require_object(
        require_success(_completed(capsys.readouterr().out)),
        context="success data",
    )
    task = require_object(payload["task"], context="serialized Task")

    assert task == {
        "uid": "tsk_first",
        "project_id": "prj_acme",
        "number": 1,
        "key": "ACME-1",
        "title": "Ž first task",
        "objective": "Ž first task",
        "state": "open",
        "priority": 50,
        "version": 1,
        "created_by": "sub_local",
        "created_at": "2026-07-30T16:30:45.123456Z",
        "updated_at": "2026-07-30T16:30:45.123456Z",
        "available_at": None,
        "approval": "none",
        "acceptance": [],
        "context": [],
        "depends_on": [],
        "blocking_reason": None,
        "current_result_id": None,
    }


def test_supported_nested_values_are_normalized_without_omission() -> None:
    """Tuples, paths, nulls, and UTC timestamps retain explicit semantics."""
    value = normalize_json_value(
        {
            "items": (1, None, True),
            "model": JsonErrorDetail(
                code="INTERNAL_ERROR",
                message="Safe.",
                retryable=False,
            ),
            "path": Path("/workspace/example"),
            "timestamp": datetime(2026, 7, 30, 16, 30, tzinfo=UTC),
        }
    )

    assert value == {
        "items": [1, None, True],
        "model": {
            "code": "INTERNAL_ERROR",
            "message": "Safe.",
            "retryable": False,
        },
        "path": "/workspace/example",
        "timestamp": "2026-07-30T16:30:00Z",
    }
    assert is_json_value(value)


@pytest.mark.parametrize("number", [math.nan, math.inf, -math.inf])
def test_non_finite_numbers_are_rejected_before_output(
    number: float,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No nonstandard JSON numeric token or silent null reaches stdout."""
    with pytest.raises(ValidationError):
        write_success({"number": number}, json_mode=True)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.parametrize(
    "timestamp",
    [
        datetime(2026, 7, 30, 16, 30),  # noqa: DTZ001 - rejection fixture
        datetime(
            2026,
            7,
            30,
            16,
            30,
            tzinfo=timezone(timedelta(hours=2)),
        ),
    ],
)
def test_non_utc_timestamps_are_rejected(
    timestamp: datetime,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The public boundary never emits naive or offset timestamps."""
    with pytest.raises(ValidationError):
        write_success({"timestamp": timestamp}, json_mode=True)

    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "value",
    [
        object(),
        {1: "non-string key"},
    ],
)
def test_unsupported_json_values_are_rejected(
    value: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unsupported objects cannot fall back to unsafe string representations."""
    with pytest.raises(TypeError):
        write_success(value, json_mode=True)

    assert capsys.readouterr().out == ""


def test_human_success_rendering_is_deterministic_and_readable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Human mode uses stable sorted indentation without stderr diagnostics."""
    write_success({"z": "Ž", "a": 1}, json_mode=False)

    captured = capsys.readouterr()

    assert captured.out == '{\n  "a": 1,\n  "z": "Ž"\n}\n'
    assert captured.err == ""


def test_human_string_success_is_rendered_without_json_quoting(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Human mode renders normalized text directly."""
    write_success("Ready.", json_mode=False)

    captured = capsys.readouterr()

    assert captured.out == "Ready.\n"
    assert captured.err == ""


@pytest.mark.parametrize(
    "value",
    [
        math.nan,
        {"number": math.inf},
        {"invalid": object()},
        ("tuple",),
    ],
)
def test_is_json_value_rejects_values_outside_the_strict_model(
    value: object,
) -> None:
    """The type guard rejects non-finite and unsupported nested values."""
    assert not is_json_value(value)


def test_rendering_runtime_validates_json_mode() -> None:
    """Direct callers cannot pass integer lookalikes as mode flags."""
    with pytest.raises(TypeError, match="json_mode"):
        write_success({}, json_mode=1)  # type: ignore[arg-type]


def test_option_aliases_are_explicit_non_prompting_boundaries() -> None:
    """Every shared option has one stable flag and no prompt callback."""
    expectations = (
        (JsonOption, "--json"),
        (NonInteractiveOption, "--non-interactive"),
        (IdempotencyKeyOption, "--idempotency-key"),
        (LeaseOption, "--lease"),
        (AttemptOption, "--attempt"),
        (CursorOption, "--cursor"),
        (LimitOption, "--limit"),
    )

    for alias, flag in expectations:
        option = _option_info(alias)
        assert option.param_decls == (flag,)
        assert option.prompt is False
        assert option.callback is None

    limit = _option_info(LimitOption)
    assert limit.min == 1
    assert limit.max == 500


def test_option_aliases_parse_without_stdin_or_terminal_interaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-interactive parsing never calls a prompt helper."""
    application = typer.Typer(add_completion=False)
    observed: list[tuple[bool, bool, str | None, str | None, int]] = []

    @application.command()
    def probe(
        json_mode: JsonOption = False,  # noqa: FBT002 - Typer option
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
        idempotency_key: IdempotencyKeyOption = None,
        cursor: CursorOption = None,
        limit: LimitOption = 100,
    ) -> None:
        """Record parsed shared option values."""
        observed.append(
            (
                json_mode,
                non_interactive,
                idempotency_key,
                cursor,
                limit,
            )
        )

    def fail_prompt(*_arguments: object, **_keywords: object) -> str:
        """Fail if Click attempts any prompt interaction."""
        pytest.fail("non-interactive options must not prompt")

    monkeypatch.setattr("click.termui.visible_prompt_func", fail_prompt)

    result = _RUNNER.invoke(
        application,
        [
            "--json",
            "--non-interactive",
            "--idempotency-key",
            "request-1",
            "--cursor",
            "opaque",
            "--limit",
            "25",
        ],
        input=None,
    )

    assert result.exit_code == 0
    assert observed == [(True, True, "request-1", "opaque", 25)]


@pytest.mark.parametrize(
    ("arguments", "expected_fragment"),
    [
        (("--limit", "0"), "Invalid value"),
        (("--limit", "501"), "Invalid value"),
    ],
)
def test_limit_option_enforces_public_bounds(
    arguments: tuple[str, ...],
    expected_fragment: str,
) -> None:
    """Typer rejects page limits outside the shared 1-through-500 contract."""
    application = typer.Typer(add_completion=False)

    @application.command()
    def probe(limit: LimitOption = 100) -> None:
        """Accept a bounded Task page limit."""
        del limit

    result = _RUNNER.invoke(application, list(arguments))

    assert result.exit_code == 2
    assert expected_fragment in result.output
