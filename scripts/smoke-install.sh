#!/bin/sh

set -eu

readonly_exit_usage=64
readonly_exit_data=65
readonly_exit_missing=66
readonly_python_version=3.14

smoke_script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
smoke_project_root=$(CDPATH='' cd -- "$smoke_script_directory/.." && pwd)
smoke_directory=

# Remove only the unique directory created by mktemp in this process.
cleanup() {
  if [ -n "$smoke_directory" ] && [ -d "$smoke_directory" ]; then
    rm -rf -- "$smoke_directory"
  fi
}

# Keep cleanup reliable when the process is interrupted.
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "$#" -ne 1 ]; then
  printf '%s\n' "usage: scripts/smoke-install.sh <wheel-path>" >&2
  exit "$readonly_exit_usage"
fi

smoke_wheel_argument=$1
if [ ! -f "$smoke_wheel_argument" ]; then
  printf '%s\n' "smoke-install: wheel file does not exist: $smoke_wheel_argument" >&2
  exit "$readonly_exit_missing"
fi

smoke_wheel_name=$(basename -- "$smoke_wheel_argument")
case "$smoke_wheel_name" in
  *.whl) ;;
  *)
    printf '%s\n' "smoke-install: expected a .whl file: $smoke_wheel_argument" >&2
    exit "$readonly_exit_data"
    ;;
esac

smoke_wheel_directory=$(CDPATH='' cd -- "$(dirname -- "$smoke_wheel_argument")" && pwd -P)
smoke_wheel_path=$smoke_wheel_directory/$smoke_wheel_name
smoke_expected_version=$(CDPATH='' cd -- "$smoke_project_root" && uv version --short)

smoke_directory=$(mktemp -d "${TMPDIR:-/tmp}/workaholic-wheel-smoke.XXXXXX")
smoke_environment=$smoke_directory/venv
smoke_outside_checkout=$smoke_directory/outside
mkdir -p "$smoke_outside_checkout"

uv venv \
  --no-project \
  --python "$readonly_python_version" \
  "$smoke_environment"

smoke_python=$smoke_environment/bin/python
smoke_command=$smoke_environment/bin/workaholic

uv pip install \
  --python "$smoke_python" \
  --strict \
  "$smoke_wheel_path"

smoke_installed_version=$(
  PYTHONNOUSERSITE=1 "$smoke_python" -c \
    'from importlib.metadata import version; print(version("workaholic-ai"))'
)
if [ "$smoke_installed_version" != "$smoke_expected_version" ]; then
  printf '%s\n' \
    "smoke-install: expected version $smoke_expected_version, installed $smoke_installed_version" \
    >&2
  exit "$readonly_exit_data"
fi

smoke_output=$(
  unset PYTHONPATH VIRTUAL_ENV
  export PYTHONNOUSERSITE=1
  CDPATH='' cd -- "$smoke_outside_checkout"
  "$smoke_command" --version
)
smoke_expected_output="workaholic $smoke_expected_version"
if [ "$smoke_output" != "$smoke_expected_output" ]; then
  printf '%s\n' \
    "smoke-install: expected '$smoke_expected_output', received '$smoke_output'" \
    >&2
  exit "$readonly_exit_data"
fi

printf '%s\n' "Verified $smoke_expected_output from an isolated wheel install."

