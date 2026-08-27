#!/bin/sh

set -eu

readonly_exit_usage=64
readonly_exit_data=65
readonly_exit_missing=66
readonly_python_version=3.14

phase_three_script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
phase_three_project_root=$(CDPATH='' cd -- "$phase_three_script_directory/.." && pwd)
phase_three_directory=

# Remove only the unique directory created by mktemp in this process.
cleanup() {
  if [ -n "$phase_three_directory" ] && [ -d "$phase_three_directory" ]; then
    rm -rf -- "$phase_three_directory"
  fi
}

fail_data() {
  printf '%s\n' "smoke-phase-3-wheel: $1" >&2
  exit "$readonly_exit_data"
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "$#" -ne 1 ]; then
  printf '%s\n' "usage: scripts/smoke-phase-3-wheel.sh <wheel-path>" >&2
  exit "$readonly_exit_usage"
fi

phase_three_wheel_argument=$1
if [ ! -f "$phase_three_wheel_argument" ]; then
  printf '%s\n' \
    "smoke-phase-3-wheel: wheel file does not exist: $phase_three_wheel_argument" \
    >&2
  exit "$readonly_exit_missing"
fi

phase_three_wheel_name=$(basename -- "$phase_three_wheel_argument")
case "$phase_three_wheel_name" in
  *.whl) ;;
  *) fail_data "expected a .whl file: $phase_three_wheel_argument" ;;
esac

phase_three_wheel_directory=$(
  CDPATH='' cd -- "$(dirname -- "$phase_three_wheel_argument")" && pwd -P
)
phase_three_wheel_path=$phase_three_wheel_directory/$phase_three_wheel_name
phase_three_expected_version=$(
  CDPATH='' cd -- "$phase_three_project_root" && uv version --short
)

phase_three_directory=$(
  mktemp -d "${TMPDIR:-/tmp}/workaholic-phase-three-wheel.XXXXXX"
)
phase_three_directory=$(CDPATH='' cd -- "$phase_three_directory" && pwd -P)
phase_three_environment=$phase_three_directory/venv
phase_three_config_directory=$phase_three_directory/config
phase_three_data_directory=$phase_three_directory/data
phase_three_schema_two_directory=$phase_three_directory/schema-two-data
phase_three_workspace=$phase_three_directory/workspace
phase_three_schema_two_workspace=$phase_three_directory/schema-two-workspace

mkdir -p \
  "$phase_three_config_directory" \
  "$phase_three_workspace" \
  "$phase_three_schema_two_workspace"

for phase_three_data_path in \
  "$phase_three_data_directory" \
  "$phase_three_schema_two_directory"
do
  if [ -e "$phase_three_data_path" ]; then
    fail_data "temporary data directories must start absent"
  fi
done

uv venv \
  --no-project \
  --python "$readonly_python_version" \
  "$phase_three_environment"

phase_three_python=$phase_three_environment/bin/python
phase_three_command=$phase_three_environment/bin/workaholic

uv pip install \
  --python "$phase_three_python" \
  --strict \
  "$phase_three_wheel_path"

unset PYTHONHOME PYTHONPATH VIRTUAL_ENV WORKAHOLIC_PROFILE
export NO_COLOR=1
export PYTHONNOUSERSITE=1
export WORKAHOLIC_CONFIG_DIR="$phase_three_config_directory"
export WORKAHOLIC_DATA_DIR="$phase_three_data_directory"

phase_three_installed_version=$(
  "$phase_three_python" -c \
    'from importlib.metadata import version; print(version("workaholic-ai"))'
)
if [ "$phase_three_installed_version" != "$phase_three_expected_version" ]; then
  fail_data \
    "expected version $phase_three_expected_version, installed $phase_three_installed_version"
fi

phase_three_summary=$(
  "$phase_three_python" - \
    "$phase_three_command" \
    "$phase_three_workspace" \
    "$phase_three_config_directory" \
    "$phase_three_data_directory" \
    "$phase_three_schema_two_workspace" \
    "$phase_three_schema_two_directory" <<'PY'
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any


command = Path(sys.argv[1])
workspace = Path(sys.argv[2])
config_directory = Path(sys.argv[3])
data_directory = Path(sys.argv[4])
schema_two_workspace = Path(sys.argv[5])
schema_two_directory = Path(sys.argv[6])

safe_environment = {
    key: value
    for key, value in os.environ.items()
    if key
    in {
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "WINDIR",
    }
}
safe_environment.update(
    {
        "NO_COLOR": "1",
        "PYTHONNOUSERSITE": "1",
        "WORKAHOLIC_CONFIG_DIR": str(config_directory),
        "WORKAHOLIC_DATA_DIR": str(data_directory),
    }
)


def invoke(
    arguments: list[str],
    *,
    cwd: Path = workspace,
    input_text: str | None = None,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one installed CLI process through the public JSON boundary.

    Args:
        arguments: CLI arguments before the forced automation options.
        cwd: Working directory for Workspace discovery.
        input_text: Optional standard-input payload.
        environment: Optional complete environment for a negative boundary.

    Returns:
        Completed installed-CLI process.

    """
    return subprocess.run(
        [str(command), *arguments, "--json", "--non-interactive"],
        check=False,
        cwd=cwd,
        env=safe_environment if environment is None else environment,
        input=input_text,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )


def envelope(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    """Decode one exactly framed JSON object without tolerating diagnostics.

    Args:
        result: Completed installed-CLI process.

    Returns:
        Decoded top-level object.

    Raises:
        SystemExit: If standard output or standard error violates the contract.

    """
    if result.stderr:
        raise SystemExit(f"unexpected stderr: {result.stderr}")
    if not result.stdout.endswith("\n") or result.stdout.endswith("\n\n"):
        raise SystemExit("invalid JSON stdout framing")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SystemExit("invalid JSON stdout") from error
    if not isinstance(value, dict):
        raise SystemExit("CLI envelope is not an object")
    return value


def success(
    arguments: list[str],
    *,
    input_value: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Require one successful installed CLI invocation.

    Args:
        arguments: CLI arguments before the forced automation options.
        input_value: Optional JSON object sent to standard input.

    Returns:
        Validated success-envelope data object.

    Raises:
        SystemExit: If the process or response violates the success contract.

    """
    result = invoke(
        arguments,
        input_text=(
            None
            if input_value is None
            else json.dumps(input_value, separators=(",", ":"))
        ),
    )
    if result.returncode != 0:
        raise SystemExit(f"command failed ({result.returncode}): {result.stdout}")
    value = envelope(result)
    if set(value) != {"schema", "ok", "data"}:
        raise SystemExit("invalid success envelope shape")
    if value["schema"] != "workaholic.cli/v1" or value["ok"] is not True:
        raise SystemExit("invalid success envelope identity")
    data = value["data"]
    if not isinstance(data, dict):
        raise SystemExit("success data is not an object")
    return data


def failure(
    arguments: list[str],
    *,
    status: int,
    code: str,
    message: str,
    input_value: dict[str, Any] | None = None,
    cwd: Path = workspace,
    environment: dict[str, str] | None = None,
) -> None:
    """Require one exact safe installed CLI failure.

    Args:
        arguments: CLI arguments before the forced automation options.
        status: Required process exit status.
        code: Required stable error code.
        message: Required safe error message.
        input_value: Optional JSON object sent to standard input.
        cwd: Working directory for Workspace discovery.
        environment: Optional complete environment for boundary testing.

    Raises:
        SystemExit: If the process or response differs from the error contract.

    """
    result = invoke(
        arguments,
        cwd=cwd,
        environment=environment,
        input_text=(
            None
            if input_value is None
            else json.dumps(input_value, separators=(",", ":"))
        ),
    )
    if result.returncode != status:
        raise SystemExit(
            f"unexpected status for {code}: {result.returncode}: {result.stdout}"
        )
    value = envelope(result)
    if set(value) != {"schema", "ok", "error"}:
        raise SystemExit(f"invalid {code} envelope shape")
    detail = value["error"]
    if not isinstance(detail, dict):
        raise SystemExit(f"invalid {code} detail")
    expected = {"code": code, "message": message, "retryable": False}
    if value["schema"] != "workaholic.cli/v1" or value["ok"] is not False:
        raise SystemExit(f"invalid {code} envelope identity")
    if detail != expected:
        raise SystemExit(f"unexpected {code} detail: {detail!r}")


def task(data: dict[str, Any]) -> dict[str, Any]:
    """Extract one Task from a mutation or creation response.

    Args:
        data: Validated success-envelope data.

    Returns:
        Task object.

    Raises:
        SystemExit: If no Task object is present.

    """
    value = data.get("task")
    if not isinstance(value, dict):
        raise SystemExit("response does not contain a Task object")
    return value


def task_page(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract a validated Task list page.

    Args:
        data: Validated success-envelope data.

    Returns:
        Ordered Task objects.

    Raises:
        SystemExit: If the response does not contain a Task list.

    """
    values = data.get("tasks")
    if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
        raise SystemExit("response does not contain a Task page")
    return values


bootstrap = success(
    [
        "up",
        "--project-key",
        "ACME",
        "--project-name",
        "Acme delivery",
        "--idempotency-key",
        "phase-three-up",
    ]
)
if bootstrap["project"]["key"] != "ACME" or bootstrap["subject"]["kind"] != "human":
    raise SystemExit("bootstrap identity changed")

prerequisite = task(
    success(
        [
            "task",
            "add",
            "Prepare foundation",
            "--priority",
            "80",
            "--idempotency-key",
            "phase-three-prerequisite",
        ]
    )
)
definition = {
    "acceptance": [
        {
            "id": "ac_verified",
            "text": "The reviewed implementation is verified.",
            "required": True,
        }
    ],
    "context": [{"uri": "https://example.test/specification", "version": "v1"}],
}
reviewed = task(
    success(
        [
            "task",
            "add",
            "Deliver reviewed change",
            "--priority",
            "90",
            "--approval",
            "human",
            "--input-file",
            "-",
            "--idempotency-key",
            "phase-three-reviewed",
        ],
        input_value=definition,
    )
)
if (prerequisite["key"], prerequisite["version"]) != ("ACME-1", 1):
    raise SystemExit("prerequisite identity or version changed")
if (reviewed["key"], reviewed["version"]) != ("ACME-2", 1):
    raise SystemExit("reviewed Task identity or version changed")
if reviewed["acceptance"] != definition["acceptance"]:
    raise SystemExit("Task acceptance definition changed")
if reviewed["context"] != definition["context"]:
    raise SystemExit("Task context definition changed")

dependency = task(
    success(
        [
            "task",
            "add-dependency",
            "ACME-2",
            "ACME-1",
            "--expected-version",
            "1",
            "--idempotency-key",
            "phase-three-dependency",
        ]
    )
)
if dependency["version"] != 2 or dependency["depends_on"] != [prerequisite["uid"]]:
    raise SystemExit("dependency mutation changed")

failure(
    [
        "task",
        "update",
        "ACME-2",
        "--objective",
        "Stale overwrite",
        "--expected-version",
        "1",
    ],
    status=4,
    code="VERSION_CONFLICT",
    message="The Task changed after the expected version.",
)
updated = task(
    success(
        [
            "task",
            "update",
            "ACME-2",
            "--objective",
            "Deliver and verify the reviewed change.",
            "--expected-version",
            "2",
            "--idempotency-key",
            "phase-three-update",
        ]
    )
)
if updated["version"] != 3:
    raise SystemExit("optimistic update version changed")

failure(
    [
        "task",
        "add-dependency",
        "ACME-1",
        "ACME-2",
        "--expected-version",
        "1",
    ],
    status=4,
    code="DEPENDENCY_CYCLE",
    message="The dependency change would create a cycle.",
)
ready_before = task_page(success(["task", "list", "--view", "ready"]))
if [value["key"] for value in ready_before] != ["ACME-1"]:
    raise SystemExit("initial readiness changed")

block_arguments = [
    "task",
    "block",
    "ACME-1",
    "--reason",
    "Verify manually.",
    "--expected-version",
    "1",
    "--idempotency-key",
    "phase-three-block",
]
blocked = success(block_arguments)
if task(blocked)["version"] != 2:
    raise SystemExit("block version changed")
if success(block_arguments) != blocked:
    raise SystemExit("idempotent replay changed the committed outcome")
failure(
    [
        "task",
        "block",
        "ACME-1",
        "--reason",
        "Different reason.",
        "--expected-version",
        "1",
        "--idempotency-key",
        "phase-three-block",
    ],
    status=4,
    code="IDEMPOTENCY_CONFLICT",
    message="The idempotency key was already used for a different request.",
)
if task_page(success(["task", "list", "--view", "ready"])):
    raise SystemExit("blocked Task remained ready")

unblocked = task(
    success(
        [
            "task",
            "unblock",
            "ACME-1",
            "--expected-version",
            "2",
            "--idempotency-key",
            "phase-three-unblock",
        ]
    )
)
if unblocked["version"] != 3 or unblocked["state"] != "open":
    raise SystemExit("unblock transition changed")
submitted_prerequisite = success(
    [
        "task",
        "submit",
        "ACME-1",
        "--comment",
        "Foundation prepared manually.",
        "--expected-version",
        "3",
        "--idempotency-key",
        "phase-three-prerequisite-submit",
    ]
)
completed_prerequisite = task(submitted_prerequisite)
prerequisite_result = submitted_prerequisite["result"]
if completed_prerequisite["state"] != "done" or completed_prerequisite["version"] != 4:
    raise SystemExit("prerequisite completion changed")
if prerequisite_result["attempt_id"] is not None:
    raise SystemExit("Human Result acquired an Attempt")
ready_after = task_page(success(["task", "list", "--view", "ready"]))
if [value["key"] for value in ready_after] != ["ACME-2"]:
    raise SystemExit("dependency completion did not update readiness")

result_content = {
    "summary": "Implemented and verified the reviewed change.",
    "criteria": [
        {
            "criterion_id": "ac_verified",
            "status": "passed",
            "evidence": "The golden and regression suites pass.",
        }
    ],
    "artifacts": [
        {
            "uri": "workspace://repo/reports/result.md",
            "media_type": "text/markdown",
            "sha256": None,
        }
    ],
    "proposed_follow_ups": [{"title": "Document the reviewed workflow"}],
}
submitted_review = success(
    [
        "task",
        "submit",
        "ACME-2",
        "--comment",
        "Ready for Human review.",
        "--result-file",
        "-",
        "--expected-version",
        "3",
        "--idempotency-key",
        "phase-three-reviewed-submit",
    ],
    input_value=result_content,
)
pending_task = task(submitted_review)
pending_result = submitted_review["result"]
if pending_task["state"] != "review" or pending_task["version"] != 4:
    raise SystemExit("review submission transition changed")
if pending_result["attempt_id"] is not None:
    raise SystemExit("reviewed Human Result acquired an Attempt")
for field, expected_value in result_content.items():
    if pending_result[field] != expected_value:
        raise SystemExit(f"Result field changed: {field}")

failure(
    [
        "task",
        "block",
        "ACME-2",
        "--reason",
        "Invalid review mutation.",
        "--expected-version",
        "4",
    ],
    status=4,
    code="INVALID_TRANSITION",
    message="The Task cannot perform the requested lifecycle transition.",
)
review_page = task_page(success(["task", "list", "--view", "review"]))
if [value["key"] for value in review_page] != ["ACME-2"]:
    raise SystemExit("review view changed")

approval = success(
    [
        "task",
        "approve",
        "ACME-2",
        "--comment",
        "Evidence accepted.",
        "--expected-version",
        "4",
        "--idempotency-key",
        "phase-three-approve",
    ]
)
approved_task = task(approval)
approved_result = approval["result"]
if approved_task["state"] != "done" or approved_task["version"] != 5:
    raise SystemExit("approval transition changed")
if approved_result["id"] != pending_result["id"]:
    raise SystemExit("approval did not retain the submitted Result")
if approved_result["review"]["status"] != "approved":
    raise SystemExit("review disposition changed")

restarted = success(["task", "show", str(approved_task["uid"])])
if (
    restarted["task"]["uid"] != approved_task["uid"]
    or restarted["task"]["state"] != approved_task["state"]
    or restarted["task"]["version"] != approved_task["version"]
    or restarted["task"]["current_result_id"] != approved_task["current_result_id"]
    or restarted["current_result"] != approved_result
):
    raise SystemExit("process restart changed approved state")
history = success(["task", "events", "ACME-2", "--after", "0", "--limit", "100"])
event_types = [event["type"] for event in history["events"]]
expected_event_types = [
    "task_created",
    "task_updated",
    "task_updated",
    "result_submitted",
    "review_approved",
    "task_completed",
]
if event_types != expected_event_types:
    raise SystemExit(f"reviewed event order changed: {event_types!r}")
if any(event["actor_kind"] != "human" for event in history["events"]):
    raise SystemExit("TaskEvent actor attribution changed")
if any(event["attempt_id"] is not None for event in history["events"]):
    raise SystemExit("Human TaskEvent acquired an Attempt")

cancelled_prerequisite = task(
    success(
        [
            "task",
            "add",
            "Cancelled prerequisite",
            "--idempotency-key",
            "phase-three-cancelled-prerequisite",
        ]
    )
)
affected = task(
    success(
        [
            "task",
            "add",
            "Affected work",
            "--idempotency-key",
            "phase-three-affected",
        ]
    )
)
affected = task(
    success(
        [
            "task",
            "add-dependency",
            str(affected["key"]),
            str(cancelled_prerequisite["key"]),
            "--expected-version",
            "1",
            "--idempotency-key",
            "phase-three-cancelled-dependency",
        ]
    )
)
success(
    [
        "task",
        "cancel",
        str(cancelled_prerequisite["key"]),
        "--reason",
        "No longer needed.",
        "--expected-version",
        "1",
        "--idempotency-key",
        "phase-three-cancel",
    ]
)
failure(
    [
        "task",
        "submit",
        str(affected["key"]),
        "--expected-version",
        "2",
    ],
    status=4,
    code="UNSATISFIABLE_DEPENDENCY",
    message="The Task has a cancelled prerequisite and cannot be completed.",
)
affected = task(
    success(
        [
            "task",
            "remove-dependency",
            str(affected["key"]),
            str(cancelled_prerequisite["key"]),
            "--expected-version",
            "2",
            "--idempotency-key",
            "phase-three-remove-cancelled-dependency",
        ]
    )
)
failure(
    [
        "task",
        "submit",
        str(affected["key"]),
        "--result-file",
        "-",
        "--expected-version",
        "3",
    ],
    status=2,
    code="RESULT_INVALID",
    message="The submitted Result is invalid.",
    input_value={
        "criteria": [
            {
                "criterion_id": "ac_missing",
                "status": "passed",
                "evidence": "Forged criterion.",
            }
        ]
    },
)
unchanged = success(["task", "show", str(affected["key"])])
if unchanged["task"]["version"] != 3 or unchanged["current_result"] is not None:
    raise SystemExit("invalid Result partially mutated the Task")

schema_two_directory.mkdir()
schema_two_database = schema_two_directory / "local.db"
connection = sqlite3.connect(schema_two_database)
try:
    connection.execute(
        "CREATE TABLE store_metadata (singleton INTEGER, schema_version INTEGER)"
    )
    connection.execute("INSERT INTO store_metadata VALUES (1, 2)")
    connection.commit()
finally:
    connection.close()
schema_two_bytes = schema_two_database.read_bytes()
schema_two_environment = dict(safe_environment)
schema_two_environment["WORKAHOLIC_DATA_DIR"] = str(schema_two_directory)
failure(
    ["up", "--project-key", "LEGACY"],
    status=10,
    code="SCHEMA_UNSUPPORTED",
    message="Store schema is missing or unsupported.",
    cwd=schema_two_workspace,
    environment=schema_two_environment,
)
if schema_two_database.read_bytes() != schema_two_bytes:
    raise SystemExit("schema version 2 store changed after rejection")

summary = {
    "approved_version": approved_task["version"],
    "errors": [
        "VERSION_CONFLICT",
        "DEPENDENCY_CYCLE",
        "IDEMPOTENCY_CONFLICT",
        "INVALID_TRANSITION",
        "UNSATISFIABLE_DEPENDENCY",
        "RESULT_INVALID",
        "SCHEMA_UNSUPPORTED",
    ],
    "human_attempt_id": approved_result["attempt_id"],
    "prerequisite_version": completed_prerequisite["version"],
    "ready_after_prerequisite": [value["key"] for value in ready_after],
    "reviewed_events": event_types,
    "schema_version": 4,
}
print(json.dumps(summary, separators=(",", ":"), sort_keys=True))
PY
)

if [ ! -f "$phase_three_data_directory/local.db" ]; then
  fail_data "installed journey did not create the owned SQLite store"
fi
if [ ! -f "$phase_three_workspace/.workaholic.env" ]; then
  fail_data "installed journey did not create the owned Workspace context"
fi
if [ -n "$(find "$phase_three_config_directory" -mindepth 1 -maxdepth 1 -print)" ]; then
  fail_data "installed journey unexpectedly wrote trusted configuration"
fi

printf '%s\n' "$phase_three_summary"
printf '%s\n' \
  "Verified Phase 3 Human lifecycle from workaholic $phase_three_installed_version."
