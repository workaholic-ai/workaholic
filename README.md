# Workaholic AI

Workaholic AI is a CLI-first task coordination system for human operators and
autonomous agents. Its v1 direction is to provide the same task semantics for
embedded local work and single-organization team coordination, with a stable
machine-readable CLI as the agent interface.

> [!WARNING]
> Workaholic AI `0.4.0a1` is alpha development software. It implements local
> Human and Agent Claims, exclusive leased ownership, Agent progress and
> submission, trusted embedded profiles, multiple Projects, Workspace
> discovery, and SQLite. Pre-release storage and automation remain disposable.
> Tokens, authentication, remote operation, servers, and distributed teams are
> not implemented.

> [!IMPORTANT]
> Python 3.14 is the only tested development runtime in Phase 4. There is no
> public operating-system support matrix yet, and compatibility is not promised
> before `1.0.0`. Pre-release users should pin the package version and treat
> data and automation as disposable.

## Prerequisites

- [Git](https://git-scm.com/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- CPython 3.14, which uv can install when needed

Source development is public under Apache-2.0, but `0.4.0a1` is not a published
or supported release. Use a source checkout for this alpha.

## Quick start

Clone the repository with your preferred Git transport, change into the
checkout, and run this complete disposable Human path:

```bash
uv sync --frozen
export WORKAHOLIC_CONFIG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/workaholic-quickstart-config.XXXXXX")"
export WORKAHOLIC_CREDENTIAL_BACKEND=file
export WORKAHOLIC_DATA_DIR="$(mktemp -d "${TMPDIR:-/tmp}/workaholic-quickstart-data.XXXXXX")"
workaholic_source_directory=$PWD
workaholic_workspace_directory="$(mktemp -d "${TMPDIR:-/tmp}/workaholic-quickstart-workspace.XXXXXX")"
(
  cd "$workaholic_workspace_directory"
  uv run --project "$workaholic_source_directory" workaholic up --project-key ACME --project-name "Acme delivery"
  uv run --project "$workaholic_source_directory" workaholic task add "Human-owned delivery"
  uv run --project "$workaholic_source_directory" workaholic task claim ACME-1 --lease 8h
  uv run --project "$workaholic_source_directory" workaholic task renew ACME-1 --lease 12h
  uv run --project "$workaholic_source_directory" workaholic task update ACME-1 --priority 80 --expected-version 1
  uv run --project "$workaholic_source_directory" workaholic task submit ACME-1 --comment "Implemented manually." --expected-version 2
  uv run --project "$workaholic_source_directory" workaholic task show ACME-1
  uv run --project "$workaholic_source_directory" workaholic task events ACME-1
)
```

`up` creates and binds the ACME Project. The targeted Human Claim has a null
Attempt and a long Lease. Its owner can renew the Claim, use the normal Human
Task workflow, and submit with an optional comment. The final commands show the
retained Result and attributable event history. Agent identity provisioning is
not included in this interim quick start; an Agent must never reuse the
bootstrap Human credential.

Every versioned existing-Task mutation in the block uses the version produced
by the previous mutation. Claim acquisition, renewal, heartbeat, progress, and
release do not change the Task version. An Agent submission always requires
`--expected-version`. This is mandatory because a shell script is non-terminal.
An interactive terminal Human may omit `--expected-version`; Workaholic shows
the selected Task key, current state and version, describes the action, and asks
once before submitting that exact version. It never refreshes and silently
retries a stale mutation.

The temporary configuration, data, and Workspace directories keep the example
away from real Workaholic state and leave the source checkout clean. Keep their
exact values if you want to continue the disposable example in another
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
workaholic 0.4.0a1
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

The grammar is intentionally closed. It accepts embedded mode and absolute data
directories only. `profiles.toml` must be a bounded regular non-symlink file;
profile names match `[a-z][a-z0-9_-]{0,31}`, and configured names map one-to-one
to canonical data directories. Remote URLs, credentials, Tokens, secret
references, and executable paths are invalid. `.workaholic.env` can name a
trusted profile but cannot define storage or redirect a credential or endpoint.

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

The `0.4.0a1` SQLite store uses the disposable Phase 5 schema version `5`
foundation. Phase 4 schema version `4` and every other unsupported version are
rejected unchanged with `SCHEMA_UNSUPPORTED`; prior disposable contracts also
used Phase 3 schema version `3` and Phase 2 schema version `2`.
There is no migration, conversion, import, export, or automatic reset. Before
an alpha upgrade, preserve anything needed outside Workaholic. For an explicit
disposable-development reset, first verify the exact selected profile data
directory and Workspace contexts belong only to that alpha, then remove only
those verified artifacts and run `up` again. Never delete a broad user-data or
configuration directory without verifying its ownership and contents.

## Current CLI

The default executable composes a short-lived authenticated embedded
`LocalSession`, trusted profile selection, upward Workspace discovery, and
SQLite schema version `5`.
It exposes 24 Project, context, Task, Claim, and Agent execution operations
without starting a daemon.

| Invocation | Current behavior |
| --- | --- |
| `workaholic up --project-key ACME` | Initializes or reopens one embedded profile and binds the initial Project |
| `workaholic status` / `workaholic context` | Shows the selected Project or complete safe local context |
| `workaholic project create` | Creates another authorized Project |
| `workaholic project bind` | Binds an authorized Project to another Workspace |
| `workaholic project list` | Lists authorized Projects |
| `workaholic task add`, `list`, `show` | Defines and reads Tasks, including readiness views and current Results |
| `workaholic task list --all-projects` | Reads authorized Tasks across Projects in stable order |
| `workaholic task update` | Replaces explicitly selected Task definition fields |
| `workaholic task block` | Blocks one open Task with a Human reason |
| `workaholic task unblock` | Returns one blocked Task to open |
| `workaholic task cancel` | Cancels one mutable Task |
| `workaholic task add-dependency` | Adds one same-Project prerequisite |
| `workaholic task remove-dependency` | Removes one same-Project prerequisite |
| `workaholic task claim [TASK]` | Claims one selected Task as a Human, or atomically pulls the next ready Task and creates an Agent Attempt when `TASK` is omitted |
| `workaholic task renew` | Renews the current Human Claim without requiring an Attempt ID |
| `workaholic task heartbeat` | Renews one exact active Agent Attempt's Claim |
| `workaholic task progress` | Appends bounded structured progress for one exact active Agent Attempt |
| `workaholic task release` | Releases a Human Claim or one exact Agent Attempt |
| `workaholic task submit` | Persists a Human or Agent Result and completes or requests review |
| `workaholic task approve` | Approves a pending Human review and completes its Task |
| `workaholic task reject` | Rejects a pending Human review and reopens its Task |
| `workaholic task events` | Reads or follows attributable ordered Task history |
| `workaholic --version` | Prints `workaholic 0.4.0a1` |

Every command accepts `--json` and `--non-interactive`. JSON mode emits one
closed `workaholic.cli/v1` envelope on stdout. Existing-Task automation must
supply a positive `--expected-version`; stale input returns
`VERSION_CONFLICT` without mutation. Lifecycle mutations support optional
idempotency keys. Run `workaholic COMMAND --help` for complete input bounds and
selection options.

## Phase 4 boundaries

The Local Agent Alpha supports:

- multiple trusted embedded profiles and multiple named Projects;
- one bootstrapped Human local operator with Owner access;
- canonical upward Workspace discovery and safe binding;
- complete Task definitions, explicit same-Project dependencies, and
  deterministic readiness views;
- exclusive Human and Agent Claims: targeted Human Claims have null Attempts,
  untargeted ready-Task Agent Claims have non-null Attempts, and every claimed
  Task has one exclusive mutation lock;
- explicit Human renewal and Agent heartbeat, plus Agent progress and submission,
  safe release, and exact expiry and reclaim;
- `open`, `blocked`, `review`, `done`, and `cancelled` stored states;
- optimistic versions, idempotent lifecycle mutations, structured Human
  Results, review, and append-only attributable TaskEvents;
- SQLite schema version `5` through embedded `LocalSession`;
- Human-readable output and closed `workaholic.cli/v1` JSON envelopes.

Human claim and renewal default to `8h` with a `1m` minimum and `30d` maximum.
Agent claim and heartbeat default to `15m` with a `1s` minimum and `24h`
maximum. Lease text must match `^[1-9][0-9]*(s|m|h|d)$`. An owning Human can use
the normal mutation workflow. An owning Agent can heartbeat, report progress,
release, or submit; non-owning mutations return `TASK_LOCKED`, while stale or
foreign Agent ownership returns `LEASE_LOST`. An empty ready queue returns
`NO_TASK_AVAILABLE`.

Phase 4 deliberately reuses the one bootstrapped Human Subject. Human Claims
and Results record `attempt_id = null`; a non-null Attempt identifies local
Agent execution while TaskEvent actor kind remains `human`. This protects
process ownership and stale execution, but it does not distinguish different
Human operators or distinct Agents sharing the embedded operating-system
account. Proposed follow-ups remain inert provenance data and do not create
Tasks, dependencies, or a hierarchy automatically.

Phase 5 introduces distinct Subjects, Tokens, grants, and authenticated
ownership. Remote operation and distributed coordination remain later roadmap
work.

It does not implement:

- distinct Agent identities, Tokens, credentials, remote profiles, or general
  identity management;
- `RemoteSession`, a server, authentication, or team coordination;
- JSON or PostgreSQL persistence adapters;
- schema migration or compatibility across alpha versions;
- capability-based scheduling, Project archival, force interruption, or
  parent/child Task hierarchies;
- automatic Task creation from proposed follow-ups.
- automatic Task creation from proposed follow-ups.

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
uv build --no-progress
scripts/smoke-install.sh dist/*.whl
```

## Phase 4 acceptance gate

From a clean checkout with no active virtual environment, pre-existing
`.venv` or `dist`, or inherited Workaholic config/data/profile selectors, run:

```bash
scripts/verify-phase-4.sh
```

The gate synchronizes the locked environment, runs all pre-commit controls and
tests, builds the distribution, verifies isolated wheel installation, and
executes installed-wheel Human and Agent execution through
`scripts/smoke-phase-4-wheel.sh dist/*.whl`. It owns and removes temporary
config, data, and Workspace roots, and refuses a dirty checkout or any caller
state that could redirect persistence. The source suite and wheel journey
jointly pin Claims, Attempts, Lease expiry and reclaim, Task versions, Results,
lock failures, idempotency, review, restart behavior, and attributable ordered
TaskEvents.

Earlier milestone gates remain available as
`scripts/verify-phase-0.sh`, `scripts/verify-phase-1.sh`, and
`scripts/verify-phase-2.sh`, and `scripts/verify-phase-3.sh`. Each uses temporary
state and validates its own milestone from a clean checkout.

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
