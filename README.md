# Workaholic AI

Workaholic AI is a CLI-first task coordination system for human operators and
autonomous agents. Its v1 direction is to provide the same task semantics for
embedded local work and single-organization team coordination, with a stable
machine-readable CLI as the agent interface.

> [!WARNING]
> Workaholic AI `0.1.0a1` is alpha development software. It implements one
> local Project and persistent Tasks with embedded SQLite. Pre-release storage
> and automation remain disposable. Agent execution, Tokens, remote servers,
> and distributed teams are not implemented.

> [!IMPORTANT]
> Python 3.14 is the only tested development runtime in Phase 1. There is no
> public operating-system support matrix yet, and compatibility is not promised
> before `1.0.0`. Pre-release users should treat data and automation as
> disposable.

## Prerequisites

- [Git](https://git-scm.com/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- CPython 3.14, which uv can install when needed

Source development is public under Apache-2.0, but `0.1.0a1` is not a
published or supported release. Use a source checkout for this alpha.

## Quick start

Clone the repository with your preferred Git transport, change into the
checkout, and run:

```bash
uv sync --frozen
uv run workaholic up --project-key ACME
uv run workaholic task add "First persistent task"
uv run workaholic task list
```

The Task is stored in SQLite and receives the stable key `ACME-1`. Running
`task list` from the same exact directory in a later terminal or process shows
the same Task.

## Version

Check the installed source version with:

```bash
uv run workaholic --version
```

The version command prints:

```text
workaholic 0.1.0a1
```

## Isolated local data

By default, Workaholic uses the operating system's standard user-data
directory. For development and tests, set `WORKAHOLIC_DATA_DIR` to an
absolute, test-owned directory before running `up`:

```bash
export WORKAHOLIC_DATA_DIR=/absolute/path/to/disposable-workaholic-data
```

The override selects trusted storage; `.workaholic.env` does not. The context
file is written only in the exact directory where `up` runs, contains no
credentials, and is treated as untrusted input on every later command.

Phase 1 store schema version `1` is disposable. There are no automatic schema
migrations, import, export, or backend conversion tools. If an alpha upgrade
reports `SCHEMA_UNSUPPORTED`, preserve anything needed outside Workaholic,
remove the exact disposable data store and its Workspace `.workaholic.env`,
then run `up` again. Do not delete a broad user-data directory unless you have
verified that it is dedicated to this alpha.

## Current CLI

The default executable composes a short-lived embedded `LocalSession`, exact
current-directory context, and SQLite storage. It exposes all six Phase 1
Project and Task operations without starting a daemon.

| Invocation | Current behavior |
| --- | --- |
| `uv run workaholic` | Prints command help and exits successfully |
| `uv run workaholic --help` | Prints command help |
| `uv run workaholic --version` | Prints `workaholic 0.1.0a1` |
| `uv run python -m workaholic --version` | Runs the same CLI as a Python module |
| `uv run workaholic up --project-key ACME` | Initializes or reopens local SQLite state and exact-directory context |
| `uv run workaholic status` | Shows exact-directory local status |
| `uv run workaholic project list` | Lists Projects authorized for the local operator |
| `uv run workaholic task add "First persistent task"` | Creates one attributable persistent Task |
| `uv run workaholic task list` | Lists Tasks with deterministic pagination |
| `uv run workaholic task show ACME-1` | Shows a Task by stable key or canonical UID |

All six commands accept `--json` and `--non-interactive`. The `up` and
`task add` mutations also accept `--idempotency-key`; Task creation supports
`--objective` and `--priority`, while Task listing supports `--cursor` and
`--limit`. Their `workaholic.cli/v1` JSON envelopes and documented error exits
are implemented.

## Phase 1 boundaries

The current alpha intentionally supports one embedded local workflow:

- one active Instance and one Project;
- one automatically bootstrapped Human local operator with Owner access;
- exact-current-directory `.workaholic.env` lookup;
- SQLite schema version `1`;
- Project status/listing and persistent Task add/list/show;
- attributable Task creation, deterministic numbering, and idempotent retries;
- human output and closed `workaholic.cli/v1` JSON envelopes.

It does not implement:

- upward context discovery or multiple active Projects;
- Agents, claims, Attempts, Leases, or Result submission;
- Tokens, credential-store integration, or general identity management;
- a server, RemoteSession, authentication, or team coordination;
- JSON or PostgreSQL persistence adapters;
- schema migration or compatibility across alpha versions.

## Planned for v1 (not implemented)

The accepted v1 direction includes embedded local task management, multi-project
context, agent claims and leases, authenticated single-organization team
coordination, and equivalent JSON, SQLite, and PostgreSQL persistence behavior.
Local task workflows arrive before agent and distributed-team workflows.

These capabilities are roadmap commitments, not features in the current
package. See the [product scope](docs/product-scope.md) and
[delivery roadmap](docs/roadmap.md) for their boundaries and sequence.

## Development checks

Before submitting a change, run:

```bash
uv run pre-commit run --all-files
uv run pytest
uv build
scripts/smoke-install.sh dist/*.whl
```

## Phase 1 acceptance gate

From a clean checkout with no active virtual environment and no pre-existing
`.venv` or `dist`, run:

```bash
scripts/verify-phase-1.sh
```

The fail-fast gate runs the locked synchronization, all commit-stage hooks, the
complete test suite, package build, isolated wheel-install smoke, and installed
persistent Task journey. The wheel journey uses its own temporary virtual
environment, `WORKAHOLIC_DATA_DIR`, and Workspace, then removes them on exit.
It never uses the operator's default profile or database.

The Phase 0 foundation gate remains available as
`scripts/verify-phase-0.sh`.

## Project documents

- [Architecture](docs/architecture.md)
- [Roadmap](docs/roadmap.md)
- [CLI automation contract](docs/cli-contract.md)
- [Persistence contract](docs/persistence-contract.md)
- [Glossary](docs/glossary.md)
- [Threat model](docs/threat-model.md)
- [Product scope](docs/product-scope.md)
- [Compatibility policy](docs/compatibility-policy.md)

## Community and project policies

- [Contributing guide](CONTRIBUTING.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Security policy](SECURITY.md)
- [Changelog](CHANGELOG.md)
- [Apache License 2.0](LICENSE)

Use the [repository issue forms](https://github.com/workaholic-ai/workaholic/issues/new/choose)
for bugs, feature proposals, and architecture decisions. Security concerns and
conduct reports must be submitted privately through their respective policies.
