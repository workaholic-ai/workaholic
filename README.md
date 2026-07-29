# Workaholic AI

Workaholic AI is a CLI-first task coordination system for human operators and
autonomous agents. Its v1 direction is to provide the same task semantics for
embedded local work and single-organization team coordination, with a stable
machine-readable CLI as the agent interface.

> [!WARNING]
> Workaholic AI is pre-alpha foundation software at version `0.0.0`. The current
> package is a runnable CLI skeleton and does not manage tasks, agents,
> persistence, projects, authentication, or remote servers.

> [!IMPORTANT]
> Python 3.14 is the only tested development runtime in Phase 0. There is no
> public operating-system support matrix yet, and compatibility is not promised
> before `1.0.0`. Pre-release users should treat data and automation as
> disposable.

## Prerequisites

- [Git](https://git-scm.com/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- CPython 3.14, which uv can install when needed

The project is not yet published as an installable release. Use a source
checkout for the current Phase 0 build.

## Quick start

Clone the repository with your preferred Git transport, change into the
checkout, and run:

```bash
uv sync
uv run workaholic --version
uv run pytest
```

The version command prints:

```text
workaholic 0.0.0
```

## Current CLI

Only the bootstrap interface below is implemented.

| Invocation | Current behavior |
| --- | --- |
| `uv run workaholic` | Prints command help and exits successfully |
| `uv run workaholic --help` | Prints command help |
| `uv run workaholic --version` | Prints `workaholic 0.0.0` |
| `uv run python -m workaholic --version` | Runs the same CLI as a Python module |

There are no task-management commands or supported machine-readable task
responses yet.

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
