#!/bin/sh

set -eu

check_script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
check_project_root=$(CDPATH='' cd -- "$check_script_directory/.." && pwd)

cd "$check_project_root"

uv run --frozen pre-commit run --all-files
uv run --frozen pre-commit run --all-files --hook-stage pre-push
