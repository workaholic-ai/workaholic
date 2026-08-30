#!/bin/sh

set -eu

readonly_exit_usage=64
readonly_exit_environment=69

verify_script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
verify_project_root=$(CDPATH='' cd -- "$verify_script_directory/.." && pwd -P)
verify_runtime_directory=

# The gate deletes only the unique temporary root allocated by this process.
cleanup() {
  if [ -n "$verify_runtime_directory" ] && [ -d "$verify_runtime_directory" ]; then
    rm -rf -- "$verify_runtime_directory"
  fi
}

fail_environment() {
  printf '%s\n' "phase-five: $1" >&2
  exit "$readonly_exit_environment"
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "$#" -ne 0 ]; then
  printf '%s\n' "usage: scripts/verify-phase-5.sh" >&2
  exit "$readonly_exit_usage"
fi

if [ -n "${VIRTUAL_ENV:-}" ]; then
  fail_environment "deactivate the active virtual environment before verification"
fi

for verify_selector in \
  WORKAHOLIC_CONFIG_DIR \
  WORKAHOLIC_CREDENTIAL_BACKEND \
  WORKAHOLIC_DATA_DIR \
  WORKAHOLIC_PROFILE \
  WORKAHOLIC_TOKEN \
  WORKAHOLIC_TOKEN_FILE
do
  eval "verify_selector_value=\${$verify_selector:-}"
  if [ -n "$verify_selector_value" ]; then
    fail_environment \
      "unset $verify_selector; the gate owns all identity and data paths"
  fi
done

for verify_command in git uv; do
  if ! command -v "$verify_command" >/dev/null 2>&1; then
    fail_environment "required command is unavailable: $verify_command"
  fi
done

cd "$verify_project_root"

verify_git_root=$(git rev-parse --show-toplevel 2>/dev/null) || {
  fail_environment "verification must run from a Git checkout"
}
verify_git_root=$(CDPATH='' cd -- "$verify_git_root" && pwd -P)
if [ "$verify_git_root" != "$verify_project_root" ]; then
  fail_environment "script path does not match the Git checkout root"
fi

verify_clean_worktree() {
  verify_dirty_paths=$(git status --porcelain=v1 --untracked-files=all)
  if [ -n "$verify_dirty_paths" ]; then
    printf '%s\n' "phase-five: verification requires a clean Git worktree" >&2
    printf '%s\n' "$verify_dirty_paths" >&2
    exit "$readonly_exit_environment"
  fi
}

verify_clean_worktree
for verify_generated_path in .venv dist; do
  if [ -e "$verify_generated_path" ]; then
    fail_environment \
      "remove pre-existing $verify_generated_path before verification"
  fi
done

verify_runtime_directory=$(
  mktemp -d "${TMPDIR:-/tmp}/workaholic-phase-five-gate.XXXXXX"
)
verify_runtime_directory=$(CDPATH='' cd -- "$verify_runtime_directory" && pwd -P)
mkdir -p \
  "$verify_runtime_directory/config" \
  "$verify_runtime_directory/tokens" \
  "$verify_runtime_directory/workspaces"

# All selected product state belongs to this one disposable acceptance root.
unset COVERAGE_FILE PYTHONHOME PYTHONPATH WORKAHOLIC_TOKEN WORKAHOLIC_TOKEN_FILE
export NO_COLOR=1
export WORKAHOLIC_CONFIG_DIR="$verify_runtime_directory/config"
export WORKAHOLIC_CREDENTIAL_BACKEND=file
export WORKAHOLIC_DATA_DIR="$verify_runtime_directory/data"
export WORKAHOLIC_PHASE_0_GATE_RUNNING=1
export WORKAHOLIC_PHASE_1_GATE_RUNNING=1
export WORKAHOLIC_PHASE_2_GATE_RUNNING=1
export WORKAHOLIC_PHASE_3_GATE_RUNNING=1
export WORKAHOLIC_PHASE_4_GATE_RUNNING=1
export WORKAHOLIC_PHASE_5_GATE_RUNNING=1

printf '%s\n' "[1/6] Synchronizing the locked Python environment"
uv sync --frozen

printf '%s\n' "[2/6] Running commit-stage quality controls"
uv run pre-commit run --all-files
verify_clean_worktree

printf '%s\n' "[3/6] Running the complete test suite"
uv run pytest

printf '%s\n' "[4/6] Building the source distribution and wheel"
uv build --no-progress

printf '%s\n' "[5/6] Verifying isolated wheel installation"
scripts/smoke-install.sh dist/*.whl

printf '%s\n' "[6/6] Verifying installed Phase 5 identity and authorization"
scripts/smoke-phase-5-wheel.sh dist/*.whl

verify_clean_worktree
printf '%s\n' "Phase 5 clean-state acceptance gate passed."
