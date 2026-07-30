# Workaholic AI

Workaholic AI is a CLI-first task coordination system for human operators and
autonomous agents. Its v1 direction is to provide the same task semantics for
embedded local work and single-organization team coordination, with a stable
machine-readable CLI as the agent interface.

> [!WARNING]
> Workaholic AI `0.2.0a1` is alpha development software. It implements
> multi-project local task management with trusted embedded profiles, Workspace
> discovery, and SQLite. Pre-release storage and automation remain disposable.
> Agent execution, Tokens, remote operation, servers, and distributed teams are
> not implemented.

> [!IMPORTANT]
> Python 3.14 is the only tested development runtime in Phase 2. There is no
> public operating-system support matrix yet, and compatibility is not promised
> before `1.0.0`. Pre-release users should treat data and automation as
> disposable.

## Prerequisites

- [Git](https://git-scm.com/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- CPython 3.14, which uv can install when needed

Source development is public under Apache-2.0, but `0.2.0a1` is not a published
or supported release. Use a source checkout for this alpha.

## Quick start

Clone the repository with your preferred Git transport, change into the
checkout, and run this complete disposable journey:

```bash
uv sync --frozen
export WORKAHOLIC_CONFIG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/workaholic-quickstart-config.XXXXXX")"
export WORKAHOLIC_DATA_DIR="$(mktemp -d "${TMPDIR:-/tmp}/workaholic-quickstart-data.XXXXXX")"
workaholic_source_directory=$PWD
workaholic_workspace_directory="$(mktemp -d "${TMPDIR:-/tmp}/workaholic-quickstart-workspaces.XXXXXX")"
mkdir -p "$workaholic_workspace_directory/acme/src/app"
mkdir -p "$workaholic_workspace_directory/docs/guides/draft"
(
  cd "$workaholic_workspace_directory/acme"
  uv run --project "$workaholic_source_directory" workaholic up --project-key ACME --project-name "Acme delivery"
  uv run --project "$workaholic_source_directory" workaholic project create --key DOCS --name "Documentation"
  uv run --project "$workaholic_source_directory" workaholic project bind DOCS ../docs
  cd src/app
  uv run --project "$workaholic_source_directory" workaholic task add "First ACME task"
)
(
  cd "$workaholic_workspace_directory/docs/guides/draft"
  uv run --project "$workaholic_source_directory" workaholic task add "First DOCS task"
  uv run --project "$workaholic_source_directory" workaholic task list
)
uv run workaholic task list --all-projects
```

`up` creates the initial ACME Project and binds its exact Workspace.
`project create` adds DOCS to the same Instance, and `project bind` binds the
second directory. The two `task add` commands run from deep descendants without
a Project flag: upward discovery selects the nearest `.workaholic.env`. The
Projects allocate independent stable keys, so the final list contains
`ACME-1` and `DOCS-1` in that order.

The temporary configuration, data, and Workspace directories keep the example
away from your real Workaholic state and leave the source checkout clean. Keep
their exact values if you want to return to the disposable example from another
terminal. The quick start never reads or writes the operating system's default
Workaholic directories.

Do not copy `.env.example` to `.workaholic.env`. The example file documents
trusted process variables. Workaholic creates each strict, credential-free
Workspace context file itself.

## Version

Check the installed source version with:

```bash
uv run workaholic --version
```

The version command prints:

```text
workaholic 0.2.0a1
```

## Trusted local profiles

Each embedded profile selects one isolated SQLite data directory and therefore
one local Instance. Without a profile file, the built-in `local` profile uses
the absolute `WORKAHOLIC_DATA_DIR` override or the operating system's standard
user-data directory.

For managed local profiles, place `profiles.toml` in the standard Workaholic
user-configuration directory. Tests and managed runtimes may select another
absolute trusted directory with `WORKAHOLIC_CONFIG_DIR`:

```toml
version = 1
default_profile = "local"

[profiles.local]
mode = "embedded"
data_directory = "/absolute/path/to/workaholic-local-data"

[profiles.sandbox]
mode = "embedded"
data_directory = "/absolute/path/to/workaholic-sandbox-data"
```

Select a configured profile explicitly with `--profile sandbox` or through the
trusted process variable `WORKAHOLIC_PROFILE=sandbox`. Explicit command
selection wins, followed by trusted `WORKAHOLIC_PROFILE`, discovered Workspace
context, configured `default_profile`, and the built-in `local` fallback.

The Phase 2 grammar is intentionally closed. It accepts embedded mode and
absolute data directories only. `profiles.toml` must be a bounded regular
non-symlink file; profile names match `[a-z][a-z0-9_-]{0,31}`, and configured
names map one-to-one to canonical data directories. Remote URLs, credentials,
Tokens, secret references, and executable paths are invalid.
`.workaholic.env` can name a trusted profile but cannot define storage or
redirect a credential or endpoint.

## Workspace context and safe reset

Workaholic searches from the canonical physical current directory through the
filesystem root. The nearest `.workaholic.env` is authoritative. A malformed,
unsafe, unreadable, or unsupported nearer file fails with `CONTEXT_INVALID`;
Workaholic never falls back to a valid parent or overwrites hostile input.

`workaholic project bind KEY [PATH]` binds an existing Project to another
Workspace. Repeating the same binding is a no-op. A different valid binding
requires `--replace`; even then, Workaholic will not replace a malformed file,
directory, symlink, or concurrently changed context. It may update the local
`.git/info/exclude`, but it never changes a shared `.gitignore`.

The `0.2.0a1` SQLite store uses disposable schema version `2`. Phase 1 schema
version `1` and every other unsupported version are rejected unchanged with
`SCHEMA_UNSUPPORTED`. There is no automatic migration, conversion, import,
export, or reset. Before an alpha upgrade, preserve anything needed outside
Workaholic. To reset disposable state, verify the exact profile data directory
and Workspace context files belong only to the alpha, remove those exact
artifacts, and run `up` again. Never delete a broad user-data or configuration
directory without verifying its ownership and contents.

## Current CLI

The default executable composes a short-lived embedded `LocalSession`, trusted
profile selection, upward Workspace discovery, and SQLite storage. It exposes
the nine Phase 2 Project, context, and Task operations without starting a
daemon.

| Invocation | Current behavior |
| --- | --- |
| `uv run workaholic` | Prints command help and exits successfully |
| `uv run workaholic --help` | Prints command help |
| `uv run workaholic --version` | Prints `workaholic 0.2.0a1` |
| `uv run python -m workaholic --version` | Runs the same CLI as a Python module |
| `uv run workaholic up --project-key ACME` | Initializes or reopens one embedded profile and binds the initial Project |
| `uv run workaholic status` | Shows status for an explicit or discovered Project |
| `uv run workaholic context` | Shows the effective profile, Project, actor, and safe Workspace paths |
| `uv run workaholic project create --key DOCS --name "Documentation"` | Creates another Project in the selected Instance |
| `uv run workaholic project bind DOCS [PATH]` | Binds an existing Project to a Workspace |
| `uv run workaholic project list` | Lists Projects authorized for the local operator |
| `uv run workaholic task add "First task"` | Creates a persistent Task in the explicit or discovered Project |
| `uv run workaholic task list --all-projects` | Lists Tasks across authorized Projects in stable order |
| `uv run workaholic task show ACME-1` | Shows a Task by stable key or canonical UID |

Every command accepts `--json` and `--non-interactive`. Mutations support the
documented idempotency options. Task creation supports `--objective`,
`--priority`, and `--project`; Task list supports `--project`,
`--all-projects`, `--cursor`, and `--limit`; Task show supports `--project`.
Their `workaholic.cli/v1` JSON envelopes and documented error exits are
implemented.

## Phase 2 boundaries

The current alpha intentionally supports an embedded multi-project workflow:

- multiple trusted local profiles, each owning one embedded Instance;
- multiple named Projects per Instance with immutable unique keys;
- one automatically bootstrapped Human local operator with Owner access;
- canonical physical upward `.workaholic.env` discovery and safe binding;
- SQLite schema version `2`;
- Project create, bind, and list plus persistent Task add, list, and show;
- context-default, explicit-Project, and all-Project Task selection;
- independent per-Project Task numbering, attribution, and idempotent retries;
- human output and closed `workaholic.cli/v1` JSON envelopes.

It does not implement:

- Agents, claims, Attempts, Leases, or Result submission;
- Tokens, credentials, remote profiles, or general identity management;
- `RemoteSession`, a server, authentication, or team coordination;
- JSON or PostgreSQL persistence adapters;
- schema migration or compatibility across alpha versions;
- Project archival, Task updates, or the later Task lifecycle.

## Planned for v1 (not implemented)

The accepted v1 direction includes embedded local task management,
multi-project context, agent claims and leases, authenticated
single-organization team coordination, and equivalent JSON, SQLite, and
PostgreSQL persistence behavior. Local task workflows arrive before agent and
distributed-team workflows.

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
`.venv` or `dist`, the latest completed aggregate gate remains:

```bash
scripts/verify-phase-1.sh
```

It runs locked synchronization, all commit-stage hooks, the complete test suite,
package build, isolated wheel-install smoke, and the installed persistent Task
journey. Its temporary virtual environment, `WORKAHOLIC_DATA_DIR`, and
Workspace are isolated. The gate never uses the operator's default profile or
database. The Phase 2 gate will supersede it only after the multi-project wheel
journey passes from a clean checkout.

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
