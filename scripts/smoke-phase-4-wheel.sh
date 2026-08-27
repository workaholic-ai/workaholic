#!/bin/sh

set -eu

readonly_exit_usage=64
readonly_exit_data=65
readonly_exit_missing=66
readonly_python_version=3.14

phase_four_script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
phase_four_project_root=$(CDPATH='' cd -- "$phase_four_script_directory/.." && pwd)
phase_four_directory=

# Remove only the unique directory created by mktemp in this process.
cleanup() {
  if [ -n "$phase_four_directory" ] && [ -d "$phase_four_directory" ]; then
    rm -rf -- "$phase_four_directory"
  fi
}

fail_data() {
  printf '%s\n' "smoke-phase-4-wheel: $1" >&2
  exit "$readonly_exit_data"
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "$#" -ne 1 ]; then
  printf '%s\n' "usage: scripts/smoke-phase-4-wheel.sh <wheel-path>" >&2
  exit "$readonly_exit_usage"
fi

phase_four_wheel_argument=$1
if [ ! -f "$phase_four_wheel_argument" ]; then
  printf '%s\n' \
    "smoke-phase-4-wheel: wheel file does not exist: $phase_four_wheel_argument" \
    >&2
  exit "$readonly_exit_missing"
fi

phase_four_wheel_name=$(basename -- "$phase_four_wheel_argument")
case "$phase_four_wheel_name" in
  *.whl) ;;
  *) fail_data "expected a .whl file: $phase_four_wheel_argument" ;;
esac

phase_four_wheel_directory=$(
  CDPATH='' cd -- "$(dirname -- "$phase_four_wheel_argument")" && pwd -P
)
phase_four_wheel_path=$phase_four_wheel_directory/$phase_four_wheel_name
phase_four_expected_version=$(
  CDPATH='' cd -- "$phase_four_project_root" && uv version --short
)

phase_four_directory=$(
  mktemp -d "${TMPDIR:-/tmp}/workaholic-phase-four-wheel.XXXXXX"
)
phase_four_directory=$(CDPATH='' cd -- "$phase_four_directory" && pwd -P)
phase_four_environment=$phase_four_directory/venv
phase_four_config_directory=$phase_four_directory/config
phase_four_data_directory=$phase_four_directory/data
phase_four_schema_three_directory=$phase_four_directory/schema-three-data
phase_four_workspace=$phase_four_directory/workspace
phase_four_schema_three_workspace=$phase_four_directory/schema-three-workspace

mkdir -p \
  "$phase_four_config_directory" \
  "$phase_four_workspace" \
  "$phase_four_schema_three_workspace"

for phase_four_data_path in \
  "$phase_four_data_directory" \
  "$phase_four_schema_three_directory"
do
  if [ -e "$phase_four_data_path" ]; then
    fail_data "temporary data directories must start absent"
  fi
done

uv venv \
  --no-project \
  --python "$readonly_python_version" \
  "$phase_four_environment"

phase_four_python=$phase_four_environment/bin/python
phase_four_command=$phase_four_environment/bin/workaholic

uv pip install \
  --python "$phase_four_python" \
  --strict \
  "$phase_four_wheel_path"

unset PYTHONHOME PYTHONPATH VIRTUAL_ENV WORKAHOLIC_PROFILE
export NO_COLOR=1
export PYTHONNOUSERSITE=1
export WORKAHOLIC_CONFIG_DIR="$phase_four_config_directory"
export WORKAHOLIC_DATA_DIR="$phase_four_data_directory"

phase_four_installed_version=$(
  "$phase_four_python" -c \
    'from importlib.metadata import version; print(version("workaholic-ai"))'
)
if [ "$phase_four_installed_version" != "$phase_four_expected_version" ]; then
  fail_data \
    "expected version $phase_four_expected_version, installed $phase_four_installed_version"
fi

phase_four_summary=$(
  "$phase_four_python" - \
    "$phase_four_command" \
    "$phase_four_workspace" \
    "$phase_four_config_directory" \
    "$phase_four_data_directory" \
    "$phase_four_schema_three_workspace" \
    "$phase_four_schema_three_directory" <<'PY'
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


command = Path(sys.argv[1])
workspace = Path(sys.argv[2])
config_directory = Path(sys.argv[3])
data_directory = Path(sys.argv[4])
schema_three_workspace = Path(sys.argv[5])
schema_three_directory = Path(sys.argv[6])

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
    retryable: bool = False,
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
        retryable: Required retry guidance.
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
    expected = {"code": code, "message": message, "retryable": retryable}
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
        "phase-four-up",
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
            "phase-four-prerequisite",
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
            "phase-four-reviewed",
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
            "phase-four-dependency",
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
            "phase-four-update",
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
    "phase-four-block",
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
        "phase-four-block",
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
            "phase-four-unblock",
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
        "phase-four-prerequisite-submit",
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
        "phase-four-reviewed-submit",
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
        "phase-four-approve",
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
            "phase-four-cancelled-prerequisite",
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
            "phase-four-affected",
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
            "phase-four-cancelled-dependency",
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
        "phase-four-cancel",
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
            "phase-four-remove-cancelled-dependency",
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

# Remove the intentionally incomplete Phase 3 boundary Task from readiness so
# every untargeted Agent Claim below has one deterministic candidate.
success(
    [
        "task",
        "cancel",
        str(affected["key"]),
        "--reason",
        "Boundary test complete.",
        "--expected-version",
        "3",
        "--idempotency-key",
        "phase-four-affected-cancel",
    ]
)

human_owned = task(
    success(
        [
            "task",
            "add",
            "Human Claim lifecycle",
            "--idempotency-key",
            "phase-four-human-task",
        ]
    )
)
failure(
    ["task", "claim", str(human_owned["key"]), "--lease", "0s"],
    status=2,
    code="INVALID_INPUT",
    message="Task-claim input is invalid.",
)
human_claim = success(
    [
        "task",
        "claim",
        str(human_owned["key"]),
        "--lease",
        "8h",
        "--idempotency-key",
        "phase-four-human-claim",
    ]
)
if human_claim["attempt"] is not None:
    raise SystemExit("Human Claim acquired an Attempt")
if human_claim["claim"]["attempt_id"] is not None:
    raise SystemExit("Human Claim stored an Attempt identity")
renewed_human = success(
    [
        "task",
        "renew",
        str(human_owned["key"]),
        "--lease",
        "12h",
        "--idempotency-key",
        "phase-four-human-renew",
    ]
)
if renewed_human["task"]["version"] != 1:
    raise SystemExit("Human renewal changed the Task version")
if renewed_human["claim"]["lease_expires_at"] <= human_claim["claim"][
    "lease_expires_at"
]:
    raise SystemExit("Human renewal did not extend from authoritative now")
released_human = success(
    [
        "task",
        "release",
        str(human_owned["key"]),
        "--idempotency-key",
        "phase-four-human-release",
    ]
)
if released_human["claim"] is not None or released_human["attempt"] is not None:
    raise SystemExit("Human release retained ownership")
success(
    [
        "task",
        "claim",
        str(human_owned["key"]),
        "--idempotency-key",
        "phase-four-human-reclaim",
    ]
)
human_submission = success(
    [
        "task",
        "submit",
        str(human_owned["key"]),
        "--comment",
        "Completed manually.",
        "--expected-version",
        "1",
        "--idempotency-key",
        "phase-four-human-submit",
    ]
)
if human_submission["result"]["attempt_id"] is not None:
    raise SystemExit("Human Claim submission acquired an Attempt")

agent_released_task = task(
    success(
        [
            "task",
            "add",
            "Released Agent execution",
            "--idempotency-key",
            "phase-four-agent-release-task",
        ]
    )
)
agent_claim = success(
    [
        "task",
        "claim",
        "--lease",
        "15m",
        "--idempotency-key",
        "phase-four-agent-claim",
    ]
)
if agent_claim["task"]["uid"] != agent_released_task["uid"]:
    raise SystemExit("Agent Claim did not atomically select the ready Task")
agent_attempt_id = agent_claim["attempt"]["id"]
if agent_claim["claim"]["attempt_id"] != agent_attempt_id:
    raise SystemExit("Agent Claim and Attempt identities differ")
failure(
    [
        "task",
        "update",
        str(agent_released_task["key"]),
        "--priority",
        "90",
        "--expected-version",
        "1",
    ],
    status=4,
    code="TASK_LOCKED",
    message="The Task has a current Claim owned by another execution.",
    retryable=True,
)
foreign_attempt_id = agent_attempt_id[:-1] + (
    "0" if agent_attempt_id[-1] != "0" else "1"
)
failure(
    [
        "task",
        "heartbeat",
        str(agent_released_task["key"]),
        "--attempt",
        foreign_attempt_id,
    ],
    status=4,
    code="LEASE_LOST",
    message="The Claim is no longer current.",
)
heartbeat_arguments = [
    "task",
    "heartbeat",
    str(agent_released_task["key"]),
    "--attempt",
    agent_attempt_id,
    "--lease",
    "30m",
    "--idempotency-key",
    "phase-four-agent-heartbeat",
]
heartbeat = success(heartbeat_arguments)
if heartbeat["task"]["version"] != 1:
    raise SystemExit("Agent heartbeat changed the Task version")
if success(heartbeat_arguments) != heartbeat:
    raise SystemExit("Agent heartbeat replay changed its committed outcome")
failure(
    [
        "task",
        "heartbeat",
        str(agent_released_task["key"]),
        "--attempt",
        agent_attempt_id,
        "--lease",
        "1h",
        "--idempotency-key",
        "phase-four-agent-heartbeat",
    ],
    status=4,
    code="IDEMPOTENCY_CONFLICT",
    message="The idempotency key was already used for a different request.",
)
failure(
    [
        "task",
        "progress",
        str(agent_released_task["key"]),
        "--attempt",
        agent_attempt_id,
        "--input-file",
        "-",
    ],
    status=2,
    code="INVALID_INPUT",
    message="Task-progress input is invalid.",
    input_value={"message": "Forged progress.", "actor_subject_id": "sub_forged"},
)
progress = success(
    [
        "task",
        "progress",
        str(agent_released_task["key"]),
        "--attempt",
        agent_attempt_id,
        "--input-file",
        "-",
        "--idempotency-key",
        "phase-four-agent-progress",
    ],
    input_value={
        "message": "Implementing and verifying the installed wheel.",
        "percent_complete": 70,
        "observations": [
            {"kind": "risk", "text": "Release behavior still needs proof."}
        ],
    },
)
if [event["type"] for event in progress["events"]] != [
    "progress_reported",
    "observation_added",
]:
    raise SystemExit("Agent progress event order changed")
released_agent = success(
    [
        "task",
        "release",
        str(agent_released_task["key"]),
        "--attempt",
        agent_attempt_id,
        "--idempotency-key",
        "phase-four-agent-release",
    ]
)
if released_agent["claim"] is not None:
    raise SystemExit("Agent release retained the Claim")
if released_agent["attempt"]["status"] != "released":
    raise SystemExit("Agent release did not terminalize its Attempt")
failure(
    [
        "task",
        "progress",
        str(agent_released_task["key"]),
        "--attempt",
        agent_attempt_id,
        "--input-file",
        "-",
    ],
    status=4,
    code="LEASE_LOST",
    message="The Claim is no longer current.",
    input_value={"message": "Stale released writer."},
)
success(
    [
        "task",
        "cancel",
        str(agent_released_task["key"]),
        "--reason",
        "Release path verified.",
        "--expected-version",
        "1",
        "--idempotency-key",
        "phase-four-agent-release-cancel",
    ]
)

review_task = task(
    success(
        [
            "task",
            "add",
            "Reviewed Agent execution",
            "--approval",
            "human",
            "--idempotency-key",
            "phase-four-agent-review-task",
        ]
    )
)
review_claim = success(
    [
        "task",
        "claim",
        "--idempotency-key",
        "phase-four-agent-review-claim",
    ]
)
review_attempt_id = review_claim["attempt"]["id"]
failure(
    ["task", "claim", "--idempotency-key", "phase-four-double-claim"],
    status=3,
    code="NO_TASK_AVAILABLE",
    message="No ready Task is available to claim.",
    retryable=True,
)
review_progress = success(
    [
        "task",
        "progress",
        str(review_task["key"]),
        "--attempt",
        review_attempt_id,
        "--input-file",
        "-",
        "--idempotency-key",
        "phase-four-review-progress",
    ],
    input_value={"message": "Review evidence prepared.", "percent_complete": 100},
)
failure(
    [
        "task",
        "submit",
        str(review_task["key"]),
        "--attempt",
        review_attempt_id,
        "--expected-version",
        "2",
        "--result-file",
        "-",
    ],
    status=4,
    code="VERSION_CONFLICT",
    message="The Task changed after the expected version.",
    input_value={
        "summary": "Wrong-version submission.",
        "criteria": [],
        "artifacts": [],
        "proposed_follow_ups": [],
    },
)
review_result_input = {
    "summary": "Installed Agent execution verified.",
    "criteria": [],
    "artifacts": [],
    "proposed_follow_ups": [],
}
review_submission = success(
    [
        "task",
        "submit",
        str(review_task["key"]),
        "--attempt",
        review_attempt_id,
        "--expected-version",
        "1",
        "--result-file",
        "-",
        "--idempotency-key",
        "phase-four-agent-review-submit",
    ],
    input_value=review_result_input,
)
if review_submission["task"]["state"] != "review":
    raise SystemExit("Agent review submission did not enter review")
if review_submission["claim"] is not None:
    raise SystemExit("Agent review submission retained its Claim")
if review_submission["attempt"]["status"] != "submitted":
    raise SystemExit("Agent review submission did not terminalize its Attempt")
if review_submission["result"]["attempt_id"] != review_attempt_id:
    raise SystemExit("Agent Result lost Attempt attribution")
restarted_review = success(["task", "show", str(review_task["key"])])
if restarted_review["current_result"] != review_submission["result"]:
    raise SystemExit("process restart changed the Agent Result")
review_history = success(
    ["task", "events", str(review_task["key"]), "--after", "0", "--limit", "100"]
)
review_event_types = [event["type"] for event in review_history["events"]]
expected_review_event_types = [
    "task_created",
    "task_claimed",
    "progress_reported",
    "result_submitted",
]
if review_event_types != expected_review_event_types:
    raise SystemExit(f"Agent review event order changed: {review_event_types!r}")
if review_history["events"][0]["attempt_id"] is not None:
    raise SystemExit("Task creation unexpectedly acquired an Attempt")
if any(
    event["attempt_id"] != review_attempt_id
    for event in review_history["events"][1:]
):
    raise SystemExit("Agent event history lost Attempt attribution")
review_approval = success(
    [
        "task",
        "approve",
        str(review_task["key"]),
        "--comment",
        "Installed evidence accepted.",
        "--expected-version",
        "2",
        "--idempotency-key",
        "phase-four-agent-review-approve",
    ]
)
if review_approval["task"]["version"] != 3:
    raise SystemExit("Agent review approval version changed")

expiry_task = task(
    success(
        [
            "task",
            "add",
            "Expiring Agent execution",
            "--idempotency-key",
            "phase-four-expiry-task",
        ]
    )
)
expiring_claim = success(
    [
        "task",
        "claim",
        "--lease",
        "1s",
        "--idempotency-key",
        "phase-four-expiring-claim",
    ]
)
expired_attempt_id = expiring_claim["attempt"]["id"]
time.sleep(1.1)
failure(
    [
        "task",
        "heartbeat",
        str(expiry_task["key"]),
        "--attempt",
        expired_attempt_id,
    ],
    status=4,
    code="LEASE_LOST",
    message="The Claim is no longer current.",
)
reclaimed = success(
    [
        "task",
        "claim",
        "--idempotency-key",
        "phase-four-reclaimed-claim",
    ]
)
reclaimed_attempt_id = reclaimed["attempt"]["id"]
if reclaimed_attempt_id == expired_attempt_id:
    raise SystemExit("reclaim revived the expired Attempt")
if [event["type"] for event in reclaimed["events"]] != [
    "claim_expired",
    "task_claimed",
]:
    raise SystemExit("reclaim event order changed")
expiry_submission = success(
    [
        "task",
        "submit",
        str(expiry_task["key"]),
        "--attempt",
        reclaimed_attempt_id,
        "--expected-version",
        "1",
        "--result-file",
        "-",
        "--idempotency-key",
        "phase-four-reclaimed-submit",
    ],
    input_value={
        "summary": "Expiry and reclaim verified.",
        "criteria": [],
        "artifacts": [],
        "proposed_follow_ups": [],
    },
)
if expiry_submission["attempt"]["status"] != "submitted":
    raise SystemExit("reclaimed Attempt was not submitted")

schema_three_directory.mkdir()
schema_three_database = schema_three_directory / "local.db"
connection = sqlite3.connect(schema_three_database)
try:
    connection.execute(
        "CREATE TABLE store_metadata (singleton INTEGER, schema_version INTEGER)"
    )
    connection.execute("INSERT INTO store_metadata VALUES (1, 3)")
    connection.commit()
finally:
    connection.close()
schema_three_bytes = schema_three_database.read_bytes()
schema_three_environment = dict(safe_environment)
schema_three_environment["WORKAHOLIC_DATA_DIR"] = str(schema_three_directory)
failure(
    ["up", "--project-key", "LEGACY"],
    status=10,
    code="SCHEMA_UNSUPPORTED",
    message="Store schema is missing or unsupported.",
    cwd=schema_three_workspace,
    environment=schema_three_environment,
)
if schema_three_database.read_bytes() != schema_three_bytes:
    raise SystemExit("schema version 3 store changed after rejection")

summary = {
    "agent_review_version": review_approval["task"]["version"],
    "errors": [
        "VERSION_CONFLICT",
        "DEPENDENCY_CYCLE",
        "IDEMPOTENCY_CONFLICT",
        "INVALID_TRANSITION",
        "UNSATISFIABLE_DEPENDENCY",
        "RESULT_INVALID",
        "NO_TASK_AVAILABLE",
        "TASK_LOCKED",
        "LEASE_LOST",
        "SCHEMA_UNSUPPORTED",
    ],
    "expired_attempt_changed": expired_attempt_id != reclaimed_attempt_id,
    "human_attempt_id": human_submission["result"]["attempt_id"],
    "progress_events": [event["type"] for event in progress["events"]],
    "review_attempt_attributed": (
        review_submission["result"]["attempt_id"] == review_attempt_id
    ),
    "reviewed_events": review_event_types,
    "schema_version": 4,
}
print(json.dumps(summary, separators=(",", ":"), sort_keys=True))
PY
)

if [ ! -f "$phase_four_data_directory/local.db" ]; then
  fail_data "installed journey did not create the owned SQLite store"
fi
if [ ! -f "$phase_four_workspace/.workaholic.env" ]; then
  fail_data "installed journey did not create the owned Workspace context"
fi
if [ -n "$(find "$phase_four_config_directory" -mindepth 1 -maxdepth 1 -print)" ]; then
  fail_data "installed journey unexpectedly wrote trusted configuration"
fi

printf '%s\n' "$phase_four_summary"
printf '%s\n' \
  "Verified Phase 4 Human and Agent execution from workaholic $phase_four_installed_version."
