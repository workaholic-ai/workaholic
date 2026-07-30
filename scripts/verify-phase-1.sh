#!/bin/sh

set -eu

readonly_exit_usage=64
readonly_exit_environment=69

verify_script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
verify_project_root=$(CDPATH='' cd -- "$verify_script_directory/.." && pwd -P)
verify_runtime_directory=

cleanup() {
  if [ -n "$verify_runtime_directory" ] && [ -d "$verify_runtime_directory" ]; then
    rm -rf -- "$verify_runtime_directory"
  fi
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "$#" -ne 0 ]; then
  printf '%s\n' "usage: scripts/verify-phase-1.sh" >&2
  exit "$readonly_exit_usage"
fi

if [ -n "${VIRTUAL_ENV:-}" ]; then
  printf '%s\n' \
    "phase-one: deactivate the active virtual environment before verification" \
    >&2
  exit "$readonly_exit_environment"
fi

for verify_command in git uv; do
  if ! command -v "$verify_command" >/dev/null 2>&1; then
    printf '%s\n' "phase-one: required command is unavailable: $verify_command" >&2
    exit "$readonly_exit_environment"
  fi
done

cd "$verify_project_root"

verify_git_root=$(git rev-parse --show-toplevel 2>/dev/null) || {
  printf '%s\n' "phase-one: verification must run from a Git checkout" >&2
  exit "$readonly_exit_environment"
}
verify_git_root=$(CDPATH='' cd -- "$verify_git_root" && pwd -P)
if [ "$verify_git_root" != "$verify_project_root" ]; then
  printf '%s\n' "phase-one: script path does not match the Git checkout root" >&2
  exit "$readonly_exit_environment"
fi

verify_clean_worktree() {
  verify_dirty_paths=$(git status --porcelain=v1 --untracked-files=all)
  if [ -n "$verify_dirty_paths" ]; then
    printf '%s\n' "phase-one: verification requires a clean Git worktree" >&2
    printf '%s\n' "$verify_dirty_paths" >&2
    exit "$readonly_exit_environment"
  fi
}

verify_clean_worktree
for verify_generated_path in .venv dist; do
  if [ -e "$verify_generated_path" ]; then
    printf '%s\n' \
      "phase-one: remove pre-existing $verify_generated_path before verification" \
      >&2
    exit "$readonly_exit_environment"
  fi
done

verify_runtime_directory=$(
  mktemp -d "${TMPDIR:-/tmp}/workaholic-phase-one-gate.XXXXXX"
)
mkdir -p "$verify_runtime_directory/workspace"

unset PYTHONHOME PYTHONPATH
export NO_COLOR=1
export WORKAHOLIC_DATA_DIR="$verify_runtime_directory/data"
export WORKAHOLIC_PHASE_1_GATE_RUNNING=1

printf '%s\n' "[1/6] Synchronizing the locked Python environment"
uv sync --frozen

printf '%s\n' "[2/6] Running commit-stage quality controls"
uv run pre-commit run --all-files
verify_clean_worktree

printf '%s\n' "[3/6] Running the complete test suite"
uv run pytest

printf '%s\n' "[4/6] Building the source distribution and wheel"
uv build

printf '%s\n' "[5/6] Verifying isolated wheel installation"
scripts/smoke-install.sh dist/*.whl

printf '%s\n' "[6/6] Verifying the installed Phase 1 persistent journey"
scripts/smoke-phase-1-wheel.sh dist/*.whl

verify_clean_worktree
printf '%s\n' "Phase 1 clean-state acceptance gate passed."
