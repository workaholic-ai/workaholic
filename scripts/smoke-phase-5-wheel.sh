#!/bin/sh

set -eu

readonly_exit_usage=64
readonly_exit_data=65
readonly_exit_missing=66
readonly_python_version=3.14

phase_five_script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
phase_five_project_root=$(CDPATH='' cd -- "$phase_five_script_directory/.." && pwd)
phase_five_directory=

# The smoke boundary removes only the unique directory it allocated.
cleanup() {
  if [ -n "$phase_five_directory" ] && [ -d "$phase_five_directory" ]; then
    rm -rf -- "$phase_five_directory"
  fi
}

fail_data() {
  printf '%s\n' "smoke-phase-5-wheel: $1" >&2
  exit "$readonly_exit_data"
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "$#" -ne 1 ]; then
  printf '%s\n' "usage: scripts/smoke-phase-5-wheel.sh <wheel-path>" >&2
  exit "$readonly_exit_usage"
fi

phase_five_wheel_argument=$1
if [ ! -f "$phase_five_wheel_argument" ]; then
  printf '%s\n' \
    "smoke-phase-5-wheel: wheel file does not exist: $phase_five_wheel_argument" \
    >&2
  exit "$readonly_exit_missing"
fi

phase_five_wheel_name=$(basename -- "$phase_five_wheel_argument")
case "$phase_five_wheel_name" in
  *.whl) ;;
  *) fail_data "expected a .whl file: $phase_five_wheel_argument" ;;
esac

phase_five_wheel_directory=$(
  CDPATH='' cd -- "$(dirname -- "$phase_five_wheel_argument")" && pwd -P
)
phase_five_wheel_path=$phase_five_wheel_directory/$phase_five_wheel_name
phase_five_expected_version=$(
  CDPATH='' cd -- "$phase_five_project_root" && uv version --short
)

phase_five_directory=$(
  mktemp -d "${TMPDIR:-/tmp}/workaholic-phase-five-wheel.XXXXXX"
)
phase_five_directory=$(CDPATH='' cd -- "$phase_five_directory" && pwd -P)
phase_five_environment=$phase_five_directory/venv
phase_five_config_directory=$phase_five_directory/config
phase_five_data_directory=$phase_five_directory/data
phase_five_token_directory=$phase_five_directory/tokens
phase_five_workspace=$phase_five_directory/workspace
phase_five_legacy_workspace=$phase_five_directory/schema-four-workspace
phase_five_legacy_data=$phase_five_directory/schema-four-data

mkdir -p \
  "$phase_five_config_directory" \
  "$phase_five_token_directory" \
  "$phase_five_workspace" \
  "$phase_five_legacy_workspace"
chmod 700 "$phase_five_config_directory" "$phase_five_token_directory"

for phase_five_absent in "$phase_five_data_directory" "$phase_five_legacy_data"; do
  if [ -e "$phase_five_absent" ]; then
    fail_data "temporary data directories must start absent"
  fi
done

uv venv \
  --no-project \
  --python "$readonly_python_version" \
  "$phase_five_environment"

phase_five_python=$phase_five_environment/bin/python
phase_five_command=$phase_five_environment/bin/workaholic

uv pip install \
  --python "$phase_five_python" \
  --strict \
  "$phase_five_wheel_path"

unset \
  PYTHONHOME \
  PYTHONPATH \
  VIRTUAL_ENV \
  WORKAHOLIC_PROFILE \
  WORKAHOLIC_TOKEN \
  WORKAHOLIC_TOKEN_FILE
export NO_COLOR=1
export PYTHONNOUSERSITE=1
export WORKAHOLIC_CONFIG_DIR="$phase_five_config_directory"
export WORKAHOLIC_CREDENTIAL_BACKEND=file
export WORKAHOLIC_DATA_DIR="$phase_five_data_directory"

phase_five_installed_version=$(
  "$phase_five_python" -c \
    'from importlib.metadata import version; print(version("workaholic-ai"))'
)
if [ "$phase_five_installed_version" != "$phase_five_expected_version" ]; then
  fail_data \
    "expected version $phase_five_expected_version, installed $phase_five_installed_version"
fi

phase_five_summary=$(
  "$phase_five_python" - \
    "$phase_five_command" \
    "$phase_five_workspace" \
    "$phase_five_config_directory" \
    "$phase_five_data_directory" \
    "$phase_five_token_directory" \
    "$phase_five_legacy_workspace" \
    "$phase_five_legacy_data" <<'PY'
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
token_directory = Path(sys.argv[5])
legacy_workspace = Path(sys.argv[6])
legacy_data = Path(sys.argv[7])

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
        "WORKAHOLIC_CREDENTIAL_BACKEND": "file",
        "WORKAHOLIC_DATA_DIR": str(data_directory),
    }
)


def invoke(
    arguments: list[str],
    *,
    environment: dict[str, str] | None = None,
    input_text: str | None = None,
    cwd: Path = workspace,
) -> subprocess.CompletedProcess[str]:
    """Run one fresh installed CLI process through its JSON boundary.

    Args:
        arguments: CLI arguments before automation options.
        environment: Optional complete trusted environment.
        input_text: Optional standard input.
        cwd: Workspace used for context discovery.

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
    """Decode one exactly framed CLI envelope.

    Args:
        result: Completed CLI process.

    Returns:
        Decoded top-level object.

    """
    if result.stderr or not result.stdout.endswith("\n"):
        raise SystemExit("CLI output framing changed")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SystemExit("CLI emitted invalid JSON") from error
    if not isinstance(value, dict) or value.get("schema") != "workaholic.cli/v1":
        raise SystemExit("CLI envelope schema changed")
    return value


def success(
    arguments: list[str],
    *,
    environment: dict[str, str] | None = None,
    input_value: dict[str, Any] | None = None,
    cwd: Path = workspace,
) -> dict[str, Any]:
    """Require a successful command with object data.

    Args:
        arguments: CLI arguments before automation options.
        environment: Optional complete trusted environment.
        input_value: Optional JSON object serialized to standard input.
        cwd: Workspace used for context discovery.

    Returns:
        Successful data object.

    """
    result = invoke(
        arguments,
        environment=environment,
        input_text=(
            None
            if input_value is None
            else json.dumps(input_value, separators=(",", ":"))
        ),
        cwd=cwd,
    )
    value = envelope(result)
    if result.returncode != 0 or set(value) != {"schema", "ok", "data"}:
        raise SystemExit(f"command failed safely but unexpectedly: {value!r}")
    if value["ok"] is not True or not isinstance(value["data"], dict):
        raise SystemExit("success envelope changed")
    return value["data"]


def failure(
    arguments: list[str],
    *,
    code: str,
    environment: dict[str, str] | None = None,
    cwd: Path = workspace,
) -> None:
    """Require one safe documented CLI failure.

    Args:
        arguments: CLI arguments before automation options.
        code: Expected public error code.
        environment: Optional complete trusted environment.
        cwd: Workspace used for context discovery.

    """
    result = invoke(arguments, environment=environment, cwd=cwd)
    value = envelope(result)
    if result.returncode == 0 or set(value) != {"schema", "ok", "error"}:
        raise SystemExit("failure envelope changed")
    error = value["error"]
    if value["ok"] is not False or not isinstance(error, dict):
        raise SystemExit("failure envelope identity changed")
    if error.get("code") != code:
        raise SystemExit(f"expected {code}, received {error!r}")
    serialized = json.dumps(value, sort_keys=True)
    if "token_hash" in serialized or "raw_token" in serialized:
        raise SystemExit("failure disclosed credential material")


def agent_environment(token_file: Path) -> dict[str, str]:
    """Return a complete explicit-Token environment for one Agent.

    Args:
        token_file: Protected Token file provisioned by the Human.

    Returns:
        Isolated process environment selecting exactly that Token.

    """
    environment = dict(safe_environment)
    environment["WORKAHOLIC_TOKEN_FILE"] = str(token_file)
    return environment


def object_value(value: object, *, label: str) -> dict[str, Any]:
    """Require a JSON object at an expected response field.

    Args:
        value: Candidate response value.
        label: Safe failure label.

    Returns:
        Validated object.

    """
    if not isinstance(value, dict):
        raise SystemExit(f"{label} is not an object")
    return value


success(["up", "--project-key", "ACME", "--project-name", "Acme delivery"])
human_identity = success(["auth", "whoami"])
human_subject = object_value(human_identity["subject"], label="Human Subject")
human_token = object_value(human_identity["token"], label="Human Token")
human_id = human_subject["id"]
human_token_id = human_token["id"]
if human_subject.get("kind") != "human" or human_subject.get("is_instance_admin") is not True:
    raise SystemExit("bootstrap Human authority changed")

success(
    [
        "project",
        "create",
        "--key",
        "DOCS",
        "--name",
        "Documentation",
        "--idempotency-key",
        "phase-five-project-docs",
    ]
)
subjects: dict[str, dict[str, Any]] = {}
for command_name, handle in (
    ("create-human", "project-viewer"),
    ("create-human", "project-operator"),
    ("create-agent", "agent-one"),
    ("create-agent", "agent-two"),
):
    subjects[handle] = success(
        [
            "auth",
            command_name,
            handle,
            "--idempotency-key",
            f"phase-five-subject-{handle}",
        ]
    )

for handle, role in (
    ("project-viewer", "viewer"),
    ("project-operator", "operator"),
    ("agent-one", "agent"),
    ("agent-two", "agent"),
):
    success(
        [
            "auth",
            "grant",
            handle,
            role,
            "--project",
            "ACME",
            "--idempotency-key",
            f"phase-five-grant-{handle}",
        ]
    )
success(
    [
        "auth",
        "grant",
        "project-viewer",
        "viewer",
        "--project",
        "DOCS",
        "--idempotency-key",
        "phase-five-docs-viewer",
    ]
)

token_files = {handle: token_directory / f"{handle}.token" for handle in subjects}
tokens: dict[str, dict[str, Any]] = {}
for handle, token_file in token_files.items():
    tokens[handle] = success(
        [
            "auth",
            "create-token",
            handle,
            "--token-file",
            str(token_file),
            "--idempotency-key",
            f"phase-five-token-{handle}",
        ]
    )
    if token_file.stat().st_mode & 0o777 != 0o600:
        raise SystemExit("Token file permissions changed")

environments = {
    handle: agent_environment(token_file)
    for handle, token_file in token_files.items()
}
for handle, environment in environments.items():
    identity = success(["auth", "whoami"], environment=environment)
    subject = object_value(identity["subject"], label=f"{handle} Subject")
    token = object_value(identity["token"], label=f"{handle} Token")
    if subject.get("id") != subjects[handle].get("id"):
        raise SystemExit("Token authenticated the wrong Subject")
    if token.get("id") != tokens[handle].get("id"):
        raise SystemExit("Token identity changed")

success(["task", "list"], environment=environments["project-viewer"])
failure(
    ["task", "add", "Viewer write must fail"],
    code="PERMISSION_DENIED",
    environment=environments["project-viewer"],
)
failure(
    ["task", "add", "Agent Human write must fail"],
    code="PERMISSION_DENIED",
    environment=environments["agent-one"],
)

for number, title in enumerate(
    ("Agent one delivery", "Agent two delivery", "Human operator delivery"),
    start=1,
):
    created = success(
        [
            "task",
            "add",
            title,
            "--idempotency-key",
            f"phase-five-task-{number}",
        ],
        environment=environments["project-operator"],
    )
    task = object_value(created["task"], label="created Task")
    if task.get("key") != f"ACME-{number}":
        raise SystemExit("Task numbering changed")
success(
    [
        "task",
        "add",
        "Documentation task",
        "--project",
        "DOCS",
        "--idempotency-key",
        "phase-five-docs-task",
    ]
)

visible_projects = success(
    ["project", "list"],
    environment=environments["agent-one"],
)
if [item["key"] for item in visible_projects["projects"]] != ["ACME"]:
    raise SystemExit("Project role filtering changed")
failure(
    ["task", "show", "DOCS-1"],
    code="TASK_NOT_FOUND",
    environment=environments["agent-one"],
)

claims: list[tuple[str, str, dict[str, str]]] = []
for handle in ("agent-one", "agent-two"):
    claimed = success(
        [
            "task",
            "claim",
            "--project",
            "ACME",
            "--idempotency-key",
            f"phase-five-claim-{handle}",
        ],
        environment=environments[handle],
    )
    claim = object_value(claimed["claim"], label="Agent Claim")
    attempt = object_value(claimed["attempt"], label="Agent Attempt")
    claims.append((str(claim["task_key"]), str(attempt["id"]), environments[handle]))

first_task, first_attempt, first_environment = claims[0]
_second_task, _second_attempt, second_environment = claims[1]
failure(
    ["task", "heartbeat", first_task, "--attempt", first_attempt],
    code="LEASE_LOST",
    environment=second_environment,
)

for index, (task_key, attempt_id, environment) in enumerate(claims, start=1):
    submitted = success(
        [
            "task",
            "submit",
            task_key,
            "--attempt",
            attempt_id,
            "--expected-version",
            "1",
            "--result-file",
            "-",
            "--idempotency-key",
            f"phase-five-submit-{index}",
        ],
        environment=environment,
        input_value={
            "summary": f"Agent {index} installed-wheel delivery.",
            "criteria": [],
            "artifacts": [],
            "proposed_follow_ups": [],
        },
    )
    if object_value(submitted["task"], label="submitted Task").get("state") != "done":
        raise SystemExit("Agent submission did not complete the Task")

human_claim = success(
    [
        "task",
        "claim",
        "ACME-3",
        "--idempotency-key",
        "phase-five-human-claim",
    ],
    environment=environments["project-operator"],
)
if human_claim["attempt"] is not None:
    raise SystemExit("Human Claim unexpectedly created an Attempt")
success(
    [
        "task",
        "release",
        "ACME-3",
        "--idempotency-key",
        "phase-five-human-release",
    ],
    environment=environments["project-operator"],
)

success(
    [
        "auth",
        "revoke-token",
        str(tokens["agent-two"]["id"]),
        "--idempotency-key",
        "phase-five-revoke-agent-two",
    ]
)
failure(
    ["auth", "whoami"],
    code="AUTHENTICATION_FAILED",
    environment=environments["agent-two"],
)
success(
    [
        "auth",
        "disable-subject",
        str(subjects["agent-one"]["id"]),
        "--expected-version",
        "1",
        "--idempotency-key",
        "phase-five-disable-agent-one",
    ]
)
failure(
    ["auth", "whoami"],
    code="AUTHENTICATION_FAILED",
    environment=environments["agent-one"],
)

# This command is a new process after all mutations and proves durable attribution.
audit = success(["auth", "events", "--after", "0", "--limit", "100"])
audit_events = audit["events"]
if not isinstance(audit_events, list) or not audit_events:
    raise SystemExit("administrative audit is empty after restart")
event_types = {event["event_type"] for event in audit_events}
required_types = {
    "instance_bootstrapped",
    "project_created",
    "subject_created",
    "project_grant_assigned",
    "token_issued",
    "token_revoked",
    "subject_disabled",
}
if not required_types.issubset(event_types):
    raise SystemExit("administrative audit is incomplete")
if audit_events[0]["actor_subject_id"] != human_id:
    raise SystemExit("bootstrap audit attribution changed")
if any(
    event["actor_token_id"] != human_token_id
    for event in audit_events[2:]
):
    raise SystemExit("authenticated audit Token attribution changed")
serialized_audit = json.dumps(audit, sort_keys=True)
if "token_hash" in serialized_audit or "raw_token" in serialized_audit:
    raise SystemExit("administrative audit disclosed a secret")

legacy_data.mkdir()
legacy_database = legacy_data / "local.db"
connection = sqlite3.connect(legacy_database)
try:
    connection.execute(
        "CREATE TABLE store_metadata (singleton INTEGER, schema_version INTEGER)"
    )
    connection.execute("INSERT INTO store_metadata VALUES (1, 4)")
    connection.commit()
finally:
    connection.close()
legacy_bytes = legacy_database.read_bytes()
legacy_environment = dict(safe_environment)
legacy_environment["WORKAHOLIC_DATA_DIR"] = str(legacy_data)
failure(
    ["up", "--project-key", "LEGACY"],
    code="SCHEMA_UNSUPPORTED",
    environment=legacy_environment,
    cwd=legacy_workspace,
)
if legacy_database.read_bytes() != legacy_bytes:
    raise SystemExit("schema version 4 store changed after rejection")

print(
    json.dumps(
        {
            "audit_event_types": sorted(required_types),
            "cross_attempt_denied": True,
            "cross_project_denied": True,
            "disabled_subject_denied": True,
            "human_attempt_id": None,
            "roles": ["viewer", "agent", "operator", "owner"],
            "schema_version": 5,
            "token_revocation_denied": True,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
)
PY
)

printf '%s\n' "$phase_five_summary"
printf '%s\n' \
  "Verified Phase 5 identity and authorization from workaholic $phase_five_installed_version."
