#!/bin/sh

set -eu

readonly_exit_usage=64
readonly_exit_data=65
readonly_exit_missing=66
readonly_python_version=3.14

phase_two_script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
phase_two_project_root=$(CDPATH='' cd -- "$phase_two_script_directory/.." && pwd)
phase_two_directory=

cleanup() {
  if [ -n "$phase_two_directory" ] && [ -d "$phase_two_directory" ]; then
    rm -rf -- "$phase_two_directory"
  fi
}

fail_data() {
  printf '%s\n' "smoke-phase-2-wheel: $1" >&2
  exit "$readonly_exit_data"
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "$#" -ne 1 ]; then
  printf '%s\n' "usage: scripts/smoke-phase-2-wheel.sh <wheel-path>" >&2
  exit "$readonly_exit_usage"
fi

phase_two_wheel_argument=$1
if [ ! -f "$phase_two_wheel_argument" ]; then
  printf '%s\n' \
    "smoke-phase-2-wheel: wheel file does not exist: $phase_two_wheel_argument" \
    >&2
  exit "$readonly_exit_missing"
fi

phase_two_wheel_name=$(basename -- "$phase_two_wheel_argument")
case "$phase_two_wheel_name" in
  *.whl) ;;
  *) fail_data "expected a .whl file: $phase_two_wheel_argument" ;;
esac

phase_two_wheel_directory=$(
  CDPATH='' cd -- "$(dirname -- "$phase_two_wheel_argument")" && pwd -P
)
phase_two_wheel_path=$phase_two_wheel_directory/$phase_two_wheel_name
phase_two_expected_version=$(
  CDPATH='' cd -- "$phase_two_project_root" && uv version --short
)

phase_two_directory=$(
  mktemp -d "${TMPDIR:-/tmp}/workaholic-phase-two-wheel.XXXXXX"
)
phase_two_directory=$(CDPATH='' cd -- "$phase_two_directory" && pwd -P)
phase_two_environment=$phase_two_directory/venv
phase_two_config_directory=$phase_two_directory/config
phase_two_fallback_data_directory=$phase_two_directory/fallback-data
phase_two_local_data_directory=$phase_two_directory/local-data
phase_two_isolated_data_directory=$phase_two_directory/isolated-data
phase_two_acme_workspace=$phase_two_directory/workspaces/acme
phase_two_docs_workspace=$phase_two_directory/workspaces/docs
phase_two_docs_mirror_workspace=$phase_two_directory/workspaces/docs-mirror
phase_two_unbound_workspace=$phase_two_directory/workspaces/unbound
phase_two_acme_deep=$phase_two_acme_workspace/src/service
phase_two_docs_deep=$phase_two_docs_workspace/guides/draft
phase_two_docs_mirror_deep=$phase_two_docs_mirror_workspace/notes/review

mkdir -p \
  "$phase_two_config_directory" \
  "$phase_two_acme_deep" \
  "$phase_two_docs_deep" \
  "$phase_two_docs_mirror_deep" \
  "$phase_two_unbound_workspace"

for phase_two_data_path in \
  "$phase_two_fallback_data_directory" \
  "$phase_two_local_data_directory" \
  "$phase_two_isolated_data_directory"
do
  if [ -e "$phase_two_data_path" ]; then
    fail_data "temporary data directories must start absent"
  fi
done

uv venv \
  --no-project \
  --python "$readonly_python_version" \
  "$phase_two_environment"

phase_two_python=$phase_two_environment/bin/python
phase_two_command=$phase_two_environment/bin/workaholic

uv pip install \
  --python "$phase_two_python" \
  --strict \
  "$phase_two_wheel_path"

unset PYTHONHOME PYTHONPATH VIRTUAL_ENV WORKAHOLIC_PROFILE
export NO_COLOR=1
export PYTHONNOUSERSITE=1
export WORKAHOLIC_CONFIG_DIR="$phase_two_config_directory"
export WORKAHOLIC_DATA_DIR="$phase_two_fallback_data_directory"

"$phase_two_python" -c '
import json
import pathlib
import sys

config_file = pathlib.Path(sys.argv[1])
local_data = sys.argv[2]
isolated_data = sys.argv[3]
config_file.write_text(
    "\n".join(
        (
            "version = 1",
            "default_profile = \"local\"",
            "",
            "[profiles.local]",
            "mode = \"embedded\"",
            f"data_directory = {json.dumps(local_data)}",
            "",
            "[profiles.isolated]",
            "mode = \"embedded\"",
            f"data_directory = {json.dumps(isolated_data)}",
            "",
        )
    ),
    encoding="utf-8",
)
config_file.chmod(0o600)
' \
  "$phase_two_config_directory/profiles.toml" \
  "$phase_two_local_data_directory" \
  "$phase_two_isolated_data_directory"

phase_two_installed_version=$(
  "$phase_two_python" -c \
    'from importlib.metadata import version; print(version("workaholic-ai"))'
)
if [ "$phase_two_installed_version" != "$phase_two_expected_version" ]; then
  fail_data \
    "expected version $phase_two_expected_version, installed $phase_two_installed_version"
fi

phase_two_up_output=$(
  CDPATH='' cd -- "$phase_two_acme_workspace"
  "$phase_two_command" up \
    --project-key ACME \
    --project-name "Acme delivery" \
    --idempotency-key phase-two-up-acme \
    --json \
    --non-interactive
)
phase_two_docs_output=$(
  CDPATH='' cd -- "$phase_two_acme_deep"
  "$phase_two_command" project create \
    --key DOCS \
    --name "Documentation" \
    --idempotency-key phase-two-create-docs \
    --json \
    --non-interactive
)
phase_two_docs_binding_output=$(
  CDPATH='' cd -- "$phase_two_acme_deep"
  "$phase_two_command" project bind \
    DOCS \
    "$phase_two_docs_workspace" \
    --json \
    --non-interactive
)
phase_two_mirror_binding_output=$(
  CDPATH='' cd -- "$phase_two_acme_deep"
  "$phase_two_command" project bind \
    DOCS \
    "$phase_two_docs_mirror_workspace" \
    --json \
    --non-interactive
)
phase_two_acme_task_output=$(
  CDPATH='' cd -- "$phase_two_acme_deep"
  "$phase_two_command" task add \
    "Acme implementation" \
    --idempotency-key phase-two-task-acme \
    --json \
    --non-interactive
)
phase_two_docs_task_output=$(
  CDPATH='' cd -- "$phase_two_docs_deep"
  "$phase_two_command" task add \
    "Documentation draft" \
    --idempotency-key phase-two-task-docs \
    --json \
    --non-interactive
)
phase_two_acme_list_output=$(
  CDPATH='' cd -- "$phase_two_acme_deep"
  "$phase_two_command" task list --json --non-interactive
)
phase_two_docs_list_output=$(
  CDPATH='' cd -- "$phase_two_docs_mirror_deep"
  "$phase_two_command" task list --json --non-interactive
)
phase_two_all_list_output=$(
  CDPATH='' cd -- "$phase_two_unbound_workspace"
  "$phase_two_command" task list --all-projects --json --non-interactive
)
phase_two_restarted_context_output=$(
  CDPATH='' cd -- "$phase_two_docs_deep"
  "$phase_two_command" context --json --non-interactive
)

"$phase_two_python" -c '
import json
import sys


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def data(argument: str) -> dict[str, object]:
    envelope = json.loads(argument)
    require(set(envelope) == {"schema", "ok", "data"}, "invalid success envelope")
    require(
        envelope["schema"] == "workaholic.cli/v1" and envelope["ok"] is True,
        "failed command",
    )
    payload = envelope["data"]
    require(isinstance(payload, dict), "success data is not an object")
    return payload


up = data(sys.argv[1])
created = data(sys.argv[2])
bound = data(sys.argv[3])
mirror = data(sys.argv[4])
acme_task = data(sys.argv[5])["task"]
docs_task = data(sys.argv[6])["task"]
acme_list = data(sys.argv[7])
docs_list = data(sys.argv[8])
all_list = data(sys.argv[9])
context = data(sys.argv[10])

acme_project = up["project"]
docs_project = created["project"]
require(acme_project["key"] == "ACME", "initial Project key changed")
require(docs_project["key"] == "DOCS", "created Project key changed")
require(acme_project["id"] != docs_project["id"], "Project identities overlap")
require(acme_task["project_id"] == acme_project["id"], "ACME Task changed Project")
require(docs_task["project_id"] == docs_project["id"], "DOCS Task changed Project")
require(
    (acme_task["number"], acme_task["key"]) == (1, "ACME-1"),
    "ACME allocation changed",
)
require(
    (docs_task["number"], docs_task["key"]) == (1, "DOCS-1"),
    "DOCS allocation changed",
)
require(bound["project"] == docs_project, "primary DOCS binding changed")
require(mirror["project"] == docs_project, "mirror DOCS binding changed")
require(
    [task["key"] for task in acme_list["tasks"]] == ["ACME-1"],
    "ACME discovery changed",
)
require(
    [task["key"] for task in docs_list["tasks"]] == ["DOCS-1"],
    "mirror DOCS discovery changed",
)
require(
    [task["key"] for task in all_list["tasks"]] == ["ACME-1", "DOCS-1"],
    "all-Project ordering changed",
)
require(all_list["next_cursor"] is None, "unexpected all-Project cursor")
require(context["project"] == docs_project, "restarted context changed")
require(context["workspace_root"] == sys.argv[11], "nearest Workspace changed")
require(context["schema_version"] == 4, "schema version changed")
' \
  "$phase_two_up_output" \
  "$phase_two_docs_output" \
  "$phase_two_docs_binding_output" \
  "$phase_two_mirror_binding_output" \
  "$phase_two_acme_task_output" \
  "$phase_two_docs_task_output" \
  "$phase_two_acme_list_output" \
  "$phase_two_docs_list_output" \
  "$phase_two_all_list_output" \
  "$phase_two_restarted_context_output" \
  "$phase_two_docs_workspace"

if [ -e "$phase_two_fallback_data_directory" ]; then
  fail_data "configured profile unexpectedly used the fallback data directory"
fi

printf '%s\n' \
  "Verified Phase 2 multi-Project journey from workaholic $phase_two_installed_version."
