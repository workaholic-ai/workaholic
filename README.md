# Workaholic AI

Workaholic AI is a CLI-first task coordination system for human operators and
autonomous agents. Its v1 direction is to provide the same task semantics for
embedded local work and single-organization team coordination, with a stable
machine-readable CLI as the agent interface.

> [!WARNING]
> Workaholic AI is pre-alpha foundation software at version `0.0.0`. The current
> development revision implements the local Project and persistent Task workflow
> with embedded SQLite. Pre-release storage remains disposable. Agent execution,
> authentication, remote servers, and distributed teams are not implemented.

> [!IMPORTANT]
> Python 3.14 is the only tested development runtime in Phase 0. There is no
> public operating-system support matrix yet, and compatibility is not promised
> before `1.0.0`. Pre-release users should treat data and automation as
> disposable.

## Prerequisites

- [Git](https://git-scm.com/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- CPython 3.14, which uv can install when needed

Source development is public from Phase 0 under Apache-2.0, but the project is
not yet published as an installable or supported release. Use a source checkout
for the current Phase 0 build.

## Quick start

Clone the repository with your preferred Git transport, change into the
checkout, and run:

```bash
uv sync --frozen
uv run pre-commit run --all-files
uv run workaholic --version
uv run pytest
uv build
scripts/smoke-install.sh dist/*.whl
```

The version command prints:

```text
workaholic 0.0.0
```

## Phase 0 acceptance gate

From a fresh clone with no active virtual environment, run:

```bash
scripts/verify-phase-0.sh
```

The gate executes the exact quick-start sequence above, fails on the first
invalid stage, installs the built wheel outside the checkout, and rejects dirty
or pre-generated repository state. It creates only ignored `.venv` and `dist`
paths and does not publish an artifact.

## Current CLI

The default executable composes a short-lived embedded `LocalSession`, exact
current-directory context, and SQLite storage. It exposes all six Phase 1
Project and Task operations without starting a daemon.

| Invocation | Current behavior |
| --- | --- |
| `uv run workaholic` | Prints command help and exits successfully |
| `uv run workaholic --help` | Prints command help |
| `uv run workaholic --version` | Prints `workaholic 0.0.0` |
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

The local workflow is:

```bash
uv run workaholic up --project-key ACME
uv run workaholic task add "First persistent task"
uv run workaholic task list
uv run workaholic task show ACME-1
```

Set `WORKAHOLIC_DATA_DIR` to an absolute test-owned directory when isolating
development or automation state. Without the override, Workaholic uses the
platform user-data directory. Phase 1 reads `.workaholic.env` only from the
exact current directory.

## Planned for v1 (not implemented)

The accepted v1 direction includes embedded local task management, multi-project
context, agent claims and leases, authenticated single-organization team
coordination, and equivalent JSON, SQLite, and PostgreSQL persistence behavior.
Local task workflows arrive before agent and distributed-team workflows.

These capabilities are roadmap commitments, not features in the current
package. See the [product scope](docs/product-scope.md) and
[delivery roadmap](docs/roadmap.md) for their boundaries and sequence.

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
