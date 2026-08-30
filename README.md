# Workaholic AI

Workaholic AI is a CLI-first task manager for human operators and autonomous
agents. The current alpha runs locally with SQLite. Humans and Agents use the
same durable Task model while authenticating as distinct Subjects with
least-privilege Project roles.

> [!WARNING]
> Workaholic AI `0.5.0a1` is alpha development software. It implements local
> authentication, Human and Agent Subjects, bearer Tokens, cumulative Project
> roles, exclusive Claims, Agent Attempts, structured progress and Results,
> administrative audit, trusted embedded profiles, multiple Projects,
> Workspace discovery, and SQLite. Pre-release storage and automation remain
> disposable. Servers, remote operation, and distributed teams are not
> implemented.

> [!IMPORTANT]
> Python 3.14 is the only tested development runtime in Phase 5. There is no
> public operating-system support matrix yet, and compatibility is not promised
> before `1.0.0`. Pin the package version and treat alpha data and automation as
> disposable.

## Prerequisites

- [Git](https://git-scm.com/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- CPython 3.14, which uv can install when needed

Source development is public under Apache-2.0, but `0.5.0a1` is not a published
or supported release. Use a source checkout for this alpha.

## Quick start

Clone the repository, change into the checkout, and run this complete
disposable Human/two-Agent path. It uses separate temporary configuration,
credential, data, Token-file, and Workspace roots. Raw Tokens are written only
to protected files and never printed or embedded in a command.

```bash
(
  set -eu
  uv sync --frozen
  export WORKAHOLIC_CONFIG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/workaholic-quickstart-config.XXXXXX")"
  export WORKAHOLIC_CREDENTIAL_BACKEND=file
  export WORKAHOLIC_DATA_DIR="$(mktemp -d "${TMPDIR:-/tmp}/workaholic-quickstart-data.XXXXXX")"
  workaholic_source_directory=$PWD
  workaholic_token_directory="$(mktemp -d "${TMPDIR:-/tmp}/workaholic-quickstart-tokens.XXXXXX")"
  workaholic_workspace_directory="$(mktemp -d "${TMPDIR:-/tmp}/workaholic-quickstart-workspace.XXXXXX")"
  agent_one_token_file="$workaholic_token_directory/agent-one.token"
  agent_two_token_file="$workaholic_token_directory/agent-two.token"
  cd "$workaholic_workspace_directory"
  uv run --project "$workaholic_source_directory" workaholic up --project-key ACME --project-name "Acme delivery"
  uv run --project "$workaholic_source_directory" workaholic auth create-agent agent-one
  uv run --project "$workaholic_source_directory" workaholic auth create-agent agent-two
  uv run --project "$workaholic_source_directory" workaholic auth grant agent-one agent --project ACME
  uv run --project "$workaholic_source_directory" workaholic auth grant agent-two agent --project ACME
  uv run --project "$workaholic_source_directory" workaholic auth create-token agent-one --token-file "$agent_one_token_file"
  uv run --project "$workaholic_source_directory" workaholic auth create-token agent-two --token-file "$agent_two_token_file"
  uv run --project "$workaholic_source_directory" workaholic task add "Agent one delivery"
  uv run --project "$workaholic_source_directory" workaholic task add "Agent two delivery"
  agent_one_attempt="$(WORKAHOLIC_TOKEN_FILE="$agent_one_token_file" uv run --project "$workaholic_source_directory" workaholic task claim --json --non-interactive | uv run --project "$workaholic_source_directory" python -c 'import json,sys; print(json.load(sys.stdin)["data"]["attempt"]["id"])')"
  printf '%s\n' '{"summary":"Agent one completed its delivery."}' | WORKAHOLIC_TOKEN_FILE="$agent_one_token_file" uv run --project "$workaholic_source_directory" workaholic task submit ACME-1 --attempt "$agent_one_attempt" --expected-version 1 --result-file -
  agent_two_attempt="$(WORKAHOLIC_TOKEN_FILE="$agent_two_token_file" uv run --project "$workaholic_source_directory" workaholic task claim --json --non-interactive | uv run --project "$workaholic_source_directory" python -c 'import json,sys; print(json.load(sys.stdin)["data"]["attempt"]["id"])')"
  printf '%s\n' '{"summary":"Agent two completed its delivery."}' | WORKAHOLIC_TOKEN_FILE="$agent_two_token_file" uv run --project "$workaholic_source_directory" workaholic task submit ACME-2 --attempt "$agent_two_attempt" --expected-version 1 --result-file -
  uv run --project "$workaholic_source_directory" workaholic task list
  uv run --project "$workaholic_source_directory" workaholic task events ACME-1
  uv run --project "$workaholic_source_directory" workaholic auth events
)
```

`up` creates the local Instance, the immutable `local-operator` bootstrap Human,
an Owner grant, and a Human credential. The Human then creates two Agent
Subjects, grants each the cumulative `agent` role, and provisions an independent
Token file. Each Agent uses only its own `WORKAHOLIC_TOKEN_FILE`, atomically
pulls one ready Task, and submits through the returned Attempt ID. Human and
Agent commands run in separate short-lived CLI processes over the same SQLite
store.

The temporary directories keep the example away from real Workaholic state and
leave the source checkout clean. The surrounding subshell also prevents its
trusted environment variables from leaking into your current shell. Do not
copy `.env.example` to `.workaholic.env`: the example documents trusted process
variables, while Workaholic creates each strict, credential-free Workspace
context itself.

## Version

Check the installed source version with:

```bash
uv run workaholic --version
```

The version command prints:

```text
workaholic 0.5.0a1
```

## Identity and credentials

Every normal operation authenticates one active Token for one enabled Subject
in the selected Instance. The bootstrap Human handle is `local-operator`.
Additional handles are immutable and match `^[a-z][a-z0-9-]{1,62}$`; display
names may change. Subjects and Tokens are disabled or revoked, never deleted.

Human credential commands are:

- `workaholic auth whoami`
- `workaholic auth login --token-file PATH|-`
- `workaholic auth logout`
- `workaholic auth recover-local --instance INSTANCE`

The default Human backend prefers the operating-system credential store.
`WORKAHOLIC_CREDENTIAL_BACKEND=file` selects the protected local file backend,
which uses mode-0700 directories and a mode-0600 credential file. Explicit
Agent injection uses exactly one of `WORKAHOLIC_TOKEN` or
`WORKAHOLIC_TOKEN_FILE`; if an explicit source exists but is malformed,
ambiguous, unsafe, expired, or revoked, authentication fails without falling
back to the Human credential.

Raw Tokens appear exactly once at generation and are delivered only to the
selected credential sink. Workaholic output, AuditEvents, TaskEvents, SQLite,
logs, and errors never contain raw Tokens, Token hashes, credential paths, or
environment values. Token files must be absolute, regular, non-symlink files
outside the discovered Workspace and Git worktree/repository.

`auth recover-local` is a high-impact, tokenless, embedded-only recovery path.
It requires explicit confirmation of the exact Instance and bootstrap Subject,
revokes every bootstrap-Human Token, and creates a replacement credential. It
does not change Projects, grants, Tasks, Claims, or Attempts. Verify local
filesystem ownership and physical access before using it. It is unavailable
through `RemoteSession`.

## Roles and authorization

Project roles are cumulative:

| Role | Additional permission |
| --- | --- |
| `viewer` | Read the Project and its Tasks |
| `agent` | Pull, heartbeat, report progress, release, and submit Agent work |
| `operator` | Perform Human Claims and Task/review mutations |
| `owner` | Manage ProjectGrants |

The order is `viewer < agent < operator < owner`. Instance administrator status
allows Project and Subject/Token administration but does not bypass Project
roles. Every read checks the current active Token, enabled Subject, selected
Instance, and required ProjectGrant. Every mutation repeats those checks in the
same write transaction as the requested change.

Identity administration includes `auth create-human`, `create-agent`,
`list-subjects`, `update-subject`, `enable-subject`, `disable-subject`,
`grant-admin`, `revoke-admin`, `grant`, `list-grants`, `revoke-grant`,
`create-token`, `list-tokens`, `revoke-token`, and `events`. Non-interactive
updates to an existing Subject or grant require an exact `--expected-version`.
The Instance must retain an enabled administrator, and each Project must retain
an enabled Owner.

## Claims, Attempts, and Task mutation

`workaholic task claim TASK` creates a long Human Claim with a null Attempt.
`workaholic task claim` without a Task atomically pulls the next ready Task for
an Agent and creates an Attempt. Human claim and renewal default to `8h`, with a
`1m` minimum and `30d` maximum. Agent claim and heartbeat default to `15m`, with
a `1s` minimum and `24h` maximum.

A current Claim is an exclusive mutation lock. An owning Human can use the
normal Task workflow. An owning Agent can heartbeat, report progress, release,
or submit only through its exact Attempt. Non-owners receive `TASK_LOCKED`;
stale or foreign Agent writers receive `LEASE_LOST`; an empty ready queue
returns `NO_TASK_AVAILABLE`. Revoking a Token or disabling its Subject prevents
the next operation immediately but does not force-release a Claim. Another
active Token for the same Subject can continue the exact Attempt until normal
submission, release, or Lease expiry.

Existing-Task automation must supply a positive `--expected-version`; stale
input returns `VERSION_CONFLICT` without mutation. Claim acquisition, renewal,
heartbeat, progress, and release do not change the Task version. An interactive
Human may omit `--expected-version`; Workaholic reads once, displays the exact
current version and action, asks for confirmation, and never silently retries.

## Profiles, Workspace context, and reset

Each embedded profile selects one isolated SQLite data directory and local
Instance. Without `profiles.toml`, the built-in `local` profile uses the
absolute `WORKAHOLIC_DATA_DIR` override or the operating system's standard
user-data directory. `WORKAHOLIC_CONFIG_DIR` may select another absolute
trusted configuration directory for tests and managed runtimes. Configured
profiles use exact `mode = "embedded"`; remote modes and endpoints are rejected.
`WORKAHOLIC_PROFILE` may select one trusted configured profile for a process.

Workaholic discovers the nearest canonical `.workaholic.env` while walking
upward from the physical current directory. A malformed, unsafe, unreadable, or
unsupported nearer file fails with `CONTEXT_INVALID`; it never falls back to a
parent. `workaholic project bind KEY [PATH]` may bind only a Project visible to
the authenticated Subject and never stores credentials in Workspace context.

`0.5.0a1` uses disposable SQLite schema version `5`. Version `4` and every
other unsupported version are rejected unchanged with `SCHEMA_UNSUPPORTED`.
There is no migration, conversion, import, export, or automatic reset. Before
an alpha upgrade, preserve anything needed outside Workaholic, verify the exact
selected data and Workspace artifacts, remove only those artifacts, and run
`up` again. Never delete a broad user-data or configuration directory.

## Current CLI

The executable composes a short-lived authenticated embedded `LocalSession`,
trusted profile and credential selection, canonical upward Workspace discovery,
and SQLite schema version `5`. The main command groups are:

- `workaholic up`, `workaholic status`, and `workaholic context`
- `workaholic project create`, `workaholic project bind`, and
  `workaholic project list`
- `workaholic task add`, `workaholic task list`, `workaholic task show`, and
  `workaholic task update`
- `workaholic task block`, `workaholic task unblock`, and
  `workaholic task cancel`
- `workaholic task add-dependency` and `workaholic task remove-dependency`
- `workaholic task claim`, `workaholic task renew`, and
  `workaholic task heartbeat`
- `workaholic task progress`, `workaholic task release`, and
  `workaholic task submit`
- `workaholic task approve`, `workaholic task reject`, and
  `workaholic task events`
- `workaholic auth whoami`, `login`, `logout`, and `recover-local`
- `workaholic auth create-human`, `create-agent`, `list-subjects`,
  `update-subject`, `enable-subject`, and `disable-subject`
- `workaholic auth grant-admin`, `revoke-admin`, `grant`, `list-grants`, and
  `revoke-grant`
- `workaholic auth create-token`, `list-tokens`, `revoke-token`, and `events`
- `workaholic --version`

`workaholic task list --all-projects` lists Tasks only from Projects visible to
the authenticated Subject.

Every command accepts `--json` and `--non-interactive`. JSON mode emits one
closed `workaholic.cli/v1` object with either `{schema, ok, data}` or
`{schema, ok, error}`. Subjects include immutable identity, kind, enabled/admin
state, and version. Token responses contain public ID, Subject, status, and
lifecycle timestamps but no digest or raw credential. TaskEvents record the
real authenticated Subject and immutable Human/Agent kind snapshot plus
request and optional Attempt attribution. Administrative AuditEvents also
record the actor Token, except for tokenless bootstrap and recovery events.

## Phase 5 boundaries

The Identity and Authorization Alpha is embedded, local, SQLite-only, and
single-organization. It does not implement:

- `RemoteSession`, a server, remote profiles, or distributed team coordination;
- public multi-tenant or cross-organization isolation;
- JSON or PostgreSQL persistence adapters;
- schema migration or compatibility across alpha versions;
- capability-based scheduling, custom roles, SSO/OAuth, Project archival, or
  force interruption;
- parent/child Task hierarchies or automatic Task creation from proposed
  follow-ups.

The accepted v1 direction includes authenticated single-organization team
coordination and equivalent JSON, SQLite, and PostgreSQL persistence behavior.
Local task workflows arrive before agent and distributed-team workflows. These
are roadmap commitments, not current features. See the
[product scope](docs/product-scope.md) and [delivery roadmap](docs/roadmap.md).

## Development checks

Before submitting a change, run:

```bash
uv run pre-commit run --all-files
uv run pytest
uv build --no-progress
scripts/smoke-install.sh dist/*.whl
scripts/smoke-phase-5-wheel.sh dist/*.whl
```

## Current clean-state acceptance gate

Run the complete authenticated source-to-wheel exit gate from a clean checkout:

```bash
scripts/verify-phase-5.sh
```

It refuses a dirty checkout, an active virtual environment, pre-existing build
output, or inherited config, credential, data, profile, or Token selectors. It
runs the source checks and the installed Phase 5 identity journey in temporary
config, credential, data, Token-file, and Workspace roots. Earlier milestone
gates remain available as `scripts/verify-phase-0.sh`,
`scripts/verify-phase-1.sh`, `scripts/verify-phase-2.sh`,
`scripts/verify-phase-3.sh`, and `scripts/verify-phase-4.sh`.

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
for bugs, feature proposals, and architecture decisions. Submit security and
conduct reports privately through their respective policies.
