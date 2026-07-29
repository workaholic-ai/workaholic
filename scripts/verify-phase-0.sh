#!/bin/sh

set -eu

readonly_exit_usage=64
readonly_exit_environment=69

verify_script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
verify_project_root=$(CDPATH='' cd -- "$verify_script_directory/.." && pwd -P)

# The gate proves a clean-checkout journey, so inherited project environments
# and pre-existing build state are rejected instead of being silently reused.
if [ "$#" -ne 0 ]; then
  printf '%s\n' "usage: scripts/verify-phase-0.sh" >&2
  exit "$readonly_exit_usage"
fi

if [ -n "${VIRTUAL_ENV:-}" ]; then
  printf '%s\n' \
    "phase-zero: deactivate the active virtual environment before verification" \
    >&2
  exit "$readonly_exit_environment"
fi

for verify_command in git uv; do
  if ! command -v "$verify_command" >/dev/null 2>&1; then
    printf '%s\n' "phase-zero: required command is unavailable: $verify_command" >&2
    exit "$readonly_exit_environment"
  fi
done

cd "$verify_project_root"

verify_git_root=$(git rev-parse --show-toplevel 2>/dev/null) || {
  printf '%s\n' "phase-zero: verification must run from a Git checkout" >&2
  exit "$readonly_exit_environment"
}
verify_git_root=$(CDPATH='' cd -- "$verify_git_root" && pwd -P)
if [ "$verify_git_root" != "$verify_project_root" ]; then
  printf '%s\n' "phase-zero: script path does not match the Git checkout root" >&2
  exit "$readonly_exit_environment"
fi

verify_clean_worktree() {
  verify_dirty_paths=$(git status --porcelain=v1 --untracked-files=all)
  if [ -n "$verify_dirty_paths" ]; then
    printf '%s\n' "phase-zero: verification requires a clean Git worktree" >&2
    printf '%s\n' "$verify_dirty_paths" >&2
    exit "$readonly_exit_environment"
  fi
}

verify_clean_worktree
for verify_generated_path in .venv dist; do
  if [ -e "$verify_generated_path" ]; then
    printf '%s\n' \
      "phase-zero: remove pre-existing $verify_generated_path before verification" \
      >&2
    exit "$readonly_exit_environment"
  fi
done

unset PYTHONHOME PYTHONPATH
export WORKAHOLIC_PHASE_0_GATE_RUNNING=1

printf '%s\n' "[1/6] Synchronizing the locked Python environment"
uv sync --frozen

printf '%s\n' "[2/6] Running commit-stage quality controls"
uv run pre-commit run --all-files
verify_clean_worktree

printf '%s\n' "[3/6] Verifying the source-checkout CLI"
uv run workaholic --version

printf '%s\n' "[4/6] Running the complete test suite"
uv run pytest

printf '%s\n' "[5/6] Building the source distribution and wheel"
uv build

printf '%s\n' "[6/6] Installing and running the built wheel outside the checkout"
scripts/smoke-install.sh dist/*.whl

verify_clean_worktree
printf '%s\n' "Phase 0 clean-checkout acceptance gate passed."
