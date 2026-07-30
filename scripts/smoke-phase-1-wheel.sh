#!/bin/sh

set -eu

readonly_exit_usage=64
readonly_exit_data=65
readonly_exit_missing=66
readonly_python_version=3.14

phase_one_script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
phase_one_project_root=$(CDPATH='' cd -- "$phase_one_script_directory/.." && pwd)
phase_one_directory=

cleanup() {
  if [ -n "$phase_one_directory" ] && [ -d "$phase_one_directory" ]; then
    rm -rf -- "$phase_one_directory"
  fi
}

fail_data() {
  printf '%s\n' "smoke-phase-1-wheel: $1" >&2
  exit "$readonly_exit_data"
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "$#" -ne 1 ]; then
  printf '%s\n' "usage: scripts/smoke-phase-1-wheel.sh <wheel-path>" >&2
  exit "$readonly_exit_usage"
fi

phase_one_wheel_argument=$1
if [ ! -f "$phase_one_wheel_argument" ]; then
  printf '%s\n' \
    "smoke-phase-1-wheel: wheel file does not exist: $phase_one_wheel_argument" \
    >&2
  exit "$readonly_exit_missing"
fi

phase_one_wheel_name=$(basename -- "$phase_one_wheel_argument")
case "$phase_one_wheel_name" in
  *.whl) ;;
  *) fail_data "expected a .whl file: $phase_one_wheel_argument" ;;
esac

phase_one_wheel_directory=$(
  CDPATH='' cd -- "$(dirname -- "$phase_one_wheel_argument")" && pwd -P
)
phase_one_wheel_path=$phase_one_wheel_directory/$phase_one_wheel_name
phase_one_expected_version=$(
  CDPATH='' cd -- "$phase_one_project_root" && uv version --short
)

phase_one_directory=$(
  mktemp -d "${TMPDIR:-/tmp}/workaholic-phase-one-wheel.XXXXXX"
)
phase_one_environment=$phase_one_directory/venv
phase_one_data_directory=$phase_one_directory/data
phase_one_workspace=$phase_one_directory/workspace
mkdir -p "$phase_one_workspace"

if [ -e "$phase_one_data_directory" ]; then
  fail_data "temporary data directory must start absent"
fi

uv venv \
  --no-project \
  --python "$readonly_python_version" \
  "$phase_one_environment"

phase_one_python=$phase_one_environment/bin/python
phase_one_command=$phase_one_environment/bin/workaholic

uv pip install \
  --python "$phase_one_python" \
  --strict \
  "$phase_one_wheel_path"

unset PYTHONHOME PYTHONPATH VIRTUAL_ENV
export NO_COLOR=1
export PYTHONNOUSERSITE=1
export WORKAHOLIC_DATA_DIR="$phase_one_data_directory"

phase_one_installed_version=$(
  "$phase_one_python" -c \
    'from importlib.metadata import version; print(version("workaholic-ai"))'
)
if [ "$phase_one_installed_version" != "$phase_one_expected_version" ]; then
  fail_data \
    "expected version $phase_one_expected_version, installed $phase_one_installed_version"
fi

phase_one_up_output=$(
  CDPATH='' cd -- "$phase_one_workspace"
  "$phase_one_command" up \
    --project-key ACME \
    --idempotency-key phase-one-up \
    --json \
    --non-interactive
)
phase_one_created_output=$(
  CDPATH='' cd -- "$phase_one_workspace"
  "$phase_one_command" task add \
    "First persistent task" \
    --idempotency-key phase-one-task \
    --json \
    --non-interactive
)
phase_one_replayed_output=$(
  CDPATH='' cd -- "$phase_one_workspace"
  "$phase_one_command" task add \
    "First persistent task" \
    --idempotency-key phase-one-task \
    --json \
    --non-interactive
)

phase_one_task_uid=$(
  "$phase_one_python" -c '
import json
import sys


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


up = json.loads(sys.argv[1])
created = json.loads(sys.argv[2])
replayed = json.loads(sys.argv[3])
require(set(up) == {"schema", "ok", "data"}, "invalid up envelope")
require(up["schema"] == "workaholic.cli/v1" and up["ok"] is True, "failed up")
require(set(up["data"]) == {"instance", "project", "subject", "workspace"}, "invalid up data")
require(set(created) == {"schema", "ok", "data"}, "invalid create envelope")
require(created["schema"] == "workaholic.cli/v1" and created["ok"] is True, "failed create")
require(replayed == created, "idempotent replay changed its outcome")
task = created["data"]["task"]
required_fields = {
    "uid", "project_id", "number", "key", "title", "objective", "state",
    "priority", "version", "created_by", "created_at", "updated_at",
}
require(set(task) == required_fields, "invalid Task shape")
require(task["project_id"] == up["data"]["project"]["id"], "Task Project changed")
require(task["created_by"] == up["data"]["subject"]["id"], "Task attribution changed")
require(task["number"] == 1 and task["key"] == "ACME-1", "Task allocation changed")
require(task["title"] == "First persistent task", "Task title changed")
require(task["objective"] == task["title"], "Task objective default changed")
require(task["state"] == "open" and task["priority"] == 50, "Task defaults changed")
require(task["version"] == 1, "Task initial version changed")
print(task["uid"])
' "$phase_one_up_output" "$phase_one_created_output" "$phase_one_replayed_output"
)

phase_one_listed_output=$(
  CDPATH='' cd -- "$phase_one_workspace"
  "$phase_one_command" task list --json --non-interactive
)
phase_one_shown_key_output=$(
  CDPATH='' cd -- "$phase_one_workspace"
  "$phase_one_command" task show ACME-1 --json --non-interactive
)
phase_one_shown_uid_output=$(
  CDPATH='' cd -- "$phase_one_workspace"
  "$phase_one_command" task show \
    "$phase_one_task_uid" \
    --json \
    --non-interactive
)

"$phase_one_python" -c '
import json
import sys


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


created = json.loads(sys.argv[1])["data"]["task"]
listed = json.loads(sys.argv[2])
shown_key = json.loads(sys.argv[3])
shown_uid = json.loads(sys.argv[4])
require(set(listed) == {"schema", "ok", "data"}, "invalid list envelope")
require(listed["ok"] is True, "failed list")
require(listed["data"] == {"tasks": [created], "next_cursor": None}, "Task did not persist")
require(shown_key["data"] == {"task": created}, "key lookup changed the Task")
require(shown_uid["data"] == {"task": created}, "UID lookup changed the Task")
' \
  "$phase_one_created_output" \
  "$phase_one_listed_output" \
  "$phase_one_shown_key_output" \
  "$phase_one_shown_uid_output"

set +e
phase_one_conflict_output=$(
  CDPATH='' cd -- "$phase_one_workspace"
  "$phase_one_command" task add \
    "Conflicting task" \
    --idempotency-key phase-one-task \
    --json \
    --non-interactive
) 2>"$phase_one_directory/conflict.stderr"
phase_one_conflict_status=$?
set -e

if [ "$phase_one_conflict_status" -ne 4 ]; then
  fail_data "idempotency conflict returned status $phase_one_conflict_status"
fi
if [ -s "$phase_one_directory/conflict.stderr" ]; then
  fail_data "idempotency conflict wrote unexpected diagnostics"
fi
"$phase_one_python" -c '
import json
import sys

payload = json.loads(sys.argv[1])
if set(payload) != {"schema", "ok", "error"}:
    raise SystemExit("invalid conflict envelope")
if payload["schema"] != "workaholic.cli/v1" or payload["ok"] is not False:
    raise SystemExit("invalid conflict status")
if payload["error"]["code"] != "IDEMPOTENCY_CONFLICT":
    raise SystemExit("unexpected conflict code")
' "$phase_one_conflict_output"

printf '%s\n' \
  "Verified Phase 1 persistent Task journey from workaholic $phase_one_installed_version."
