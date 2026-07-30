# Workaholic AI v1 delivery roadmap

I recommend **eleven gated phases, numbered 0–10**. Every phase ends with a runnable product increment, a demonstrable user journey, and a tagged pre-release once packaging begins.

The critical sequencing principle is:

> Build one usable SQLite-backed local workflow first. Stabilize domain semantics through real use. Add agents, authentication, and remote operation next. Only then implement the remaining persistence backends and release hardening.

This avoids spending months building abstractions, server plumbing, and three storage implementations before Workaholic AI can track its first real task.

## Roadmap at a glance

| Phase | GitHub milestone      | Working result                                           | Suggested release |
| ----- | --------------------- | -------------------------------------------------------- | ----------------- |
| 0     | Foundation            | Public repository builds, tests, and runs a CLI skeleton | No package        |
| 1     | Local Alpha           | Solo developer can create and inspect persistent tasks   | `0.1.0a1`         |
| 2     | Multi-project Alpha   | Working-directory discovery and stable `ACME-1` IDs      | `0.2.0a1`         |
| 3     | Workflow Alpha        | Full human task lifecycle, dependencies, results, events | `0.3.0a1`         |
| 4     | Agent Alpha           | Agents claim, heartbeat, release, and submit through CLI | `0.4.0a1`         |
| 5     | Identity Alpha        | Humans and agents authenticate with project roles        | `0.5.0a1`         |
| 6     | Team Alpha            | Remote CLI and shared server work end to end             | `0.6.0a1`         |
| 7     | Persistence Beta      | JSON, SQLite, and PostgreSQL pass the same contract      | `0.7.0b1`         |
| 8     | Feature-complete Beta | UX, reliability, packaging, documentation, schema freeze | `0.8.0b1`         |
| 9     | Release Candidate     | Hardened public repository and installable RC             | `1.0.0rc1`        |
| 10    | v1 Release            | Fully supported public v1                                | `1.0.0`           |

The first meaningfully useful product arrives in **Phase 1**. The first product suitable for autonomous local agents arrives in **Phase 4**. Distributed teams can begin using it after **Phase 6**.

The [glossary](glossary.md) defines shared terminology, and the
[threat model](threat-model.md) maps the accepted security boundary to
phase-specific verification.

---

# GitHub operating model

## Repository strategy

Start with one public repository in a GitHub organization rather than a
personal account:

```text
workaholic-ai/workaholic
```

Suggested naming:

```text
Product:          Workaholic AI
Repository:       workaholic
PyPI package:     workaholic-ai
Python package:   workaholic
Executable:       workaholic
```

The product and package names remain provisional until package-name availability and basic legal/name clearance are completed.

Source development is public from Phase 0 under Apache-2.0. Public repository
visibility does not make a Phase 0 commit a supported release: package
publication, release artifacts, and compatibility commitments remain gated by
the later release phases.

Use a trunk-based workflow:

```text
main
 ├─ short-lived feature branch
 ├─ short-lived fix branch
 └─ release tags
```

Avoid a permanent `develop` branch. `main` should remain releasable, even during alpha development.

Configure a GitHub ruleset or branch protection for `main` that requires pull requests and passing checks, blocks force pushes, and requires conversations to be resolved. GitHub supports required status checks and force-push blocking through repository rulesets and protected branches. ([GitHub Docs][1])

## GitHub work hierarchy

Use:

```text
Milestone  = one delivery phase
Epic issue = phase tracking issue
Issue      = one independently reviewable deliverable
Pull request = implementation of one or a small number of issues
```

Suggested labels:

```text
area:domain
area:cli
area:context
area:auth
area:server
area:storage
area:release
area:docs

kind:feature
kind:bug
kind:refactor
kind:test
kind:security
kind:decision

priority:p0
priority:p1
priority:p2
priority:p3

status:blocked
status:needs-design
status:ready
```

Use GitHub issue forms for bugs, feature proposals, and architecture decisions. Use `CODEOWNERS` once there is more than one maintainer. GitHub supports YAML issue forms under `.github/ISSUE_TEMPLATE` and repository-level ownership through a `CODEOWNERS` file. ([GitHub Docs][2])

## GitHub versus Workaholic AI dogfooding

Once Phase 2 is usable:

* GitHub Issues remain the public roadmap, contributor interface, and release-planning system.
* Workaholic AI becomes the execution system for implementation tasks and agents.
* A Workaholic task may reference a GitHub issue number.
* GitHub synchronization is not part of v1.

This avoids creating a GitHub integration before the core task manager itself is stable.

---

# Phase 0 — Foundation and delivery contract

## Goal

Turn an empty repository into a reproducible development environment with the architecture and product boundaries committed to source control.

## Deliverables

### Repository skeleton

```text
.github/
  ISSUE_TEMPLATE/
  workflows/
  CODEOWNERS
  pull_request_template.md
  dependabot.yml

docs/
  architecture.md
  product-scope.md
  glossary.md
  cli-contract.md
  persistence-contract.md
  threat-model.md
  adr/

src/
  workaholic/
    cli/
    domain/
    application/
    session/
    context/
    auth/
    persistence/
    protocol/
    client/
    server/

tests/
  unit/
  contract/
  integration/
  e2e/

pyproject.toml
uv.lock
README.md
LICENSE
CONTRIBUTING.md
CODE_OF_CONDUCT.md
SECURITY.md
CHANGELOG.md
```

Some directories may initially contain placeholders, but the intended dependency boundaries should be visible.

### Architecture decision records

At minimum:

```text
ADR-001  Package and executable naming
ADR-002  LocalSession versus RemoteSession
ADR-003  CLI JSON as the public automation contract
ADR-004  Private versioned client/server protocol
ADR-005  Semantic persistence interface
ADR-006  Project-context trust model
ADR-007  Human and agent identity model
ADR-008  Stable task-key allocation
ADR-009  No storage migrations in v1
ADR-010  Server is single-process/single-instance in v1
```

### Golden user journeys

Write these as executable-test specifications, even though most initially remain skipped:

1. **Solo journey:** initialize a project, create tasks, exit, reopen, and see persisted state.
2. **Multi-project journey:** enter two directories and automatically select the correct project.
3. **Agent journey:** claim, heartbeat, submit, and handle an expired lease using only CLI JSON.
4. **Team journey:** two remote users and an agent use one shared server.
5. **Backend journey:** run equivalent operations against JSON, SQLite, and PostgreSQL.
6. **Clean-install journey:** run the released package through `uvx` on a clean environment.

### CI foundation

Every pull request should initially run:

```text
format/lint
type checking
unit tests
package build
wheel installation smoke test
```

GitHub Actions workflows should have minimal explicit permissions. Publishing workflows will later use OIDC rather than a long-lived package-registry credential. GitHub documents OIDC and least-privilege workflow practices for deployments. ([GitHub Docs][3])

### Development storage policy

Because migrations are outside v1:

* Pre-release stores are disposable.
* Alpha and beta releases may require a reset after schema changes.
* Every store contains a schema version.
* Unsupported schema versions fail explicitly rather than being partially interpreted.
* The persisted schema freezes before `1.0.0rc1`.
* The `1.0.x` line must not introduce storage-schema changes.

A developer-only reset command may exist:

```bash
workaholic dev reset --yes
```

It should not be presented as migration tooling.

## Exit gate

A clean checkout must support:

```bash
uv sync
uv run workaholic --version
uv run pytest
uv build
```

CI passes, the architecture documents agree with one another, and the initial GitHub milestones exist.

---

# Phase 1 — Local SQLite vertical slice

## Goal

Deliver the smallest genuinely usable Workaholic AI product for one developer and one project.

## Scope

Implement:

```text
Instance
Project
Subject
ProjectGrant
Task
TaskEvent
LocalSession
SQLite persistence
.workaholic.env in the exact current directory
```

Tasks initially need:

```text
internal UID
project ID
project task number
human key
title
objective
state
priority
version
created/updated timestamps
creator
```

Every accepted Task mutation appends an attributable TaskEvent in the same
transaction. Bootstrap creates Instance, Subject, ProjectGrant, and Project
records directly; it does not fabricate TaskEvents for entities that are not
Tasks.

## Commands

```bash
workaholic up --project-key ACME
workaholic status
workaholic project list
workaholic task add "Implement retry policy"
workaholic task list
workaholic task show ACME-1
```

All six commands accept `--json` and `--non-interactive`. `up` and `task add`
also accept an optional `--idempotency-key`. Phase 1 establishes the versioned
JSON envelopes, stable error identifiers, and exit-code categories documented
in the [CLI automation contract](cli-contract.md); Phase 4 extends that
contract with agent-execution commands.

`workaholic up` should:

1. Create the default local instance.
2. Initialize SQLite.
3. Create one real local Human Subject on first use.
4. Mark that Subject as the Instance administrator.
5. Create the project and grant that Subject the Owner role.
6. Write a strict `.workaholic.env` file in the exact current directory.
7. Return without starting a daemon.

Phase 1 does not create Tokens, use an operating-system credential store,
search parent directories for context, or load configurable trusted profiles.
Those capabilities arrive in Phases 5 and 2 respectively. The built-in
`local` profile name in the Phase 1 context file selects the embedded local
instance only.

## Architecture introduced

The CLI calls a `WorkaholicSession` abstraction.

For now:

```text
CLI → LocalSession → Application layer → SQLite adapter
```

The CLI must not call SQL directly.

The SQLite implementation should already use semantic operations such as:

```text
create_project
create_task_and_allocate_number
get_task
list_tasks
append_event
```

It should not expose generic SQL-oriented repository behavior to the application.

## Testing

Required tests:

* task number allocation;
* persistence across separate CLI invocations;
* duplicate project-key rejection;
* stable task lookup by UID and `ACME-1`;
* task creation event;
* initial task version `1`;
* clean database initialization;
* incompatible schema rejection.

Version increments and stale-update rejection arrive with update commands in
Phase 3.

## Exit gate

This full workflow works from a clean directory:

```bash
workaholic up --project-key ACME
workaholic task add "First task"
workaholic task list
```

Closing the terminal and rerunning `task list` produces the same task.

At this point, Workaholic AI is a basic but working local task tracker.

---

# Phase 2 — Multi-project context and working-directory discovery

## Goal

Deliver the Multi-project Alpha: one embedded Workaholic Instance can contain
several named Projects, while safe Workspace discovery selects the normal
Project without requiring an option on every command.

## Scope

Phase 2 implements:

```text
upward `.workaholic.env` discovery
trusted user-level embedded SQLite profiles
multiple projects per instance
stable project-prefixed task IDs
workspace-root resolution
explicit Project overrides and all-Project Task listing
```

Phase 2 extends the strict context file written by Phase 1; it does not
retroactively introduce that file. It uses disposable SQLite schema version
`2`. A schema version `1` store is rejected unchanged with
`SCHEMA_UNSUPPORTED`; Phase 2 adds no migration, conversion, import, export, or
automatic reset.

Remote profiles, URLs, credentials, login, Tokens, `RemoteSession`, and network
transport remain deferred to Phases 5 and 6. JSON and PostgreSQL adapters remain
deferred to Phase 7.

## Trusted embedded profiles

Trusted configuration is the bounded regular non-symlink `profiles.toml` file
in the operating system's Workaholic user-configuration directory.
`WORKAHOLIC_CONFIG_DIR` may replace that directory only with an absolute,
operator- or test-owned path.

The exact Phase 2 grammar is:

```toml
version = 1
default_profile = "local"

[profiles.local]
mode = "embedded"
data_directory = "/absolute/path/to/workaholic-data"
```

The top level allows only integer `version = 1`, optional
`default_profile`, and the `profiles` table. Each `[profiles.NAME]` table
allows exactly `mode = "embedded"` and one absolute `data_directory`. Profile
names match `[a-z][a-z0-9_-]{0,31}`. Unknown keys, duplicate semantic values,
unsupported versions, relative data directories, unsafe files, non-embedded
modes, URL fields, credential fields, and Token fields fail explicitly.

Configured profile names map one-to-one to canonical data directories. Two
names cannot alias one embedded Instance. If `profiles.toml` is absent, the
built-in `local` profile remains available and uses the existing absolute
`WORKAHOLIC_DATA_DIR` override or the platform user-data default.

Profile precedence is:

1. explicit `--profile`;
2. trusted `WORKAHOLIC_PROFILE`;
3. the discovered Workspace context;
4. configured `default_profile`;
5. built-in `local`.

## Workspace and Project selection

Discovery starts from the canonical physical current directory and visits each
physical parent through the filesystem root. Git repository and worktree
boundaries do not stop the walk. The nearest `.workaholic.env` is
authoritative; an invalid or unreadable nearer file fails instead of falling
back to a parent.

A context file must be bounded, regular, and non-symlink. Its
`WORKAHOLIC_WORKSPACE_ROOT` is relative to the context file's directory,
resolves to an existing directory, and must remain contained by that directory
after lexical and symlink resolution. Context-supplied profile, Instance,
Project, and Project-key values must match authoritative profile and database
state before any read or mutation.

Example:

```dotenv
WORKAHOLIC_CONTEXT_VERSION=1

WORKAHOLIC_PROFILE=local
WORKAHOLIC_INSTANCE_ID=ins_01...
WORKAHOLIC_PROJECT_ID=prj_01...
WORKAHOLIC_PROJECT_KEY=ACME
WORKAHOLIC_WORKSPACE_ROOT=.
```

The parser:

* accepts only the exact known keys;
* never invokes a shell or performs substitution;
* rejects credentials, Tokens, storage paths, executable paths, and endpoints;
* never obtains a profile definition from repository content.

Project precedence is explicit `--project`, then the discovered context. An
explicit Project must be authorized and belong to the same resolved Instance;
it never changes the profile or Instance. An explicit missing key returns
`PROJECT_NOT_FOUND`. A command that requires one Project but receives neither
an explicit selector nor context returns `CONTEXT_NOT_FOUND`.

`project create` and `project list` require only a resolved initialized
profile. `up` initializes an empty profile and its first Project; later Projects
use `project create`. Omitting `--project-name` from `up` uses the normalized
Project key as the display name. Project names contain 1 through 200 Unicode
characters after trimming, and Project keys continue to match
`[A-Z][A-Z0-9]{1,15}`.

`project bind` defaults `PATH` to the current directory. An equivalent binding
is a successful no-op. It never silently replaces a different Project,
Instance, or profile binding. `--replace` may atomically replace only an
otherwise valid regular context file; malformed files, directories, symlinks,
and concurrently changed files are never replaced.

## Commands

```text
workaholic up --project-key KEY [--project-name NAME] [--profile PROFILE]
workaholic status [--profile PROFILE] [--project KEY]
workaholic context [--profile PROFILE] [--project KEY]
workaholic project create --key KEY --name NAME
  [--profile PROFILE] [--idempotency-key KEY]
workaholic project bind KEY [PATH] [--profile PROFILE] [--replace]
workaholic project list [--profile PROFILE]
workaholic task add TITLE [--project KEY]
workaholic task list [--project KEY | --all-projects]
workaholic task show TASK [--project KEY]
```

Every command also accepts `--json` and `--non-interactive`. Existing Task
options, pagination options, and idempotency options remain available where
documented by the CLI contract. `--project` and `--all-projects` are mutually
exclusive.

`context` success data contains `mode`, `profile`, `schema_version`,
`instance`, `project`, `workspace_root`, `subject`, and `context_source`.
Serialized Projects add required `name` while retaining `id` and `key`.

Phase 2 adds these exact failure identifiers to the existing exit categories:

* `PROFILE_NOT_FOUND`;
* `PROFILE_INVALID`;
* `PROFILE_UNSUPPORTED`;
* `PROJECT_NOT_FOUND`;
* `WORKSPACE_BINDING_CONFLICT`.

The [CLI automation contract](cli-contract.md) owns their exact messages,
retryability, exits, command surfaces, and success shapes.

`project bind` adds `.workaholic.env` to a safe conventional
`.git/info/exclude` when appropriate and only after the context write is
durable. It never changes the shared `.gitignore`.

## Task ID invariants

Phase 2 enforces:

* Project keys are immutable and unique within an Instance;
* Task numbers are monotonic per Project and never reused;
* Projects cannot be archived or deleted, so Project-key reuse is impossible;
* `ACME-42` always resolves to the same Task in its Instance;
* the internal UID remains canonical for relationships;
* Tasks cannot be moved between Projects.

Project lists order by immutable Project key. One-Project Task lists order by
Task number. `task list --all-projects` includes only authorized Projects,
orders by `(project key, task number)` ascending, and uses a new versioned
opaque cursor with exact prefix `v2.`. A cursor is bound to the profile,
Instance, Subject, selection scope, selected Project when present, and last
ordering position. Reuse across any different binding returns `INVALID_INPUT`.

## Testing

Include:

* absent and hostile profile configuration;
* embedded-profile isolation and duplicate canonical data directories;
* nearest-context-file wins and invalid-nearer hard failure;
* nested repositories and invocation from a deep directory;
* physical canonicalization, symlinks, relative roots, and root containment;
* equivalent binding, conflicting binding, and safe explicit replacement;
* two Projects with independent number sequences;
* one Project bound at two paths;
* two unrelated Instances with the same Project key;
* explicit Project override and all-Project ordering;
* cursor rejection across profiles, Instances, Subjects, Projects, and scopes;
* schema version `1` rejection without mutation;
* proof that repository context cannot define storage, an endpoint, or a
  credential.

## Exit gate

From two directories:

```text
/work/acme  → creates ACME-1
/work/docs  → creates DOCS-1
```

No `--project` flag is required in normal usage.

This is the point where Workaholic AI should begin dogfooding its own development tasks.

---

# Phase 3 — Complete task lifecycle and audit model

## Goal

Make the product useful for complete human-operated workflows before introducing agent concurrency.

## Scope

Complete the task model:

```text
title
objective
priority
available_at
acceptance criteria
context references
parent/child relationship
dependencies
approval requirement
structured result
```

Implement stored states:

```text
open
blocked
review
done
cancelled
```

Implement derived views:

```text
ready
running
scheduled
stale
awaiting_review
```

`running` and `stale` will become meaningful once attempts arrive in Phase 4.

## Commands

```bash
workaholic task update ACME-1 --expected-version 3
workaholic task block ACME-1 --reason missing-input
workaholic task unblock ACME-1
workaholic task add-dependency ACME-2 ACME-1
workaholic task submit ACME-1 --result-file result.json
workaholic task approve ACME-1
workaholic task reject ACME-1 --reason "Evidence is incomplete"
workaholic task cancel ACME-1
workaholic task events ACME-1
```

## Reliability features

Introduce now:

* task version increments and stale-update rejection;
* request attribution for the additional lifecycle commands;
* idempotency keys for the additional mutation commands;
* additional append-only typed events beyond `task_created`;
* deterministic ready-task ordering;
* expanded injected-clock rules for lifecycle and readiness tests.

Phase 1 already establishes optional idempotency for `up` and `task add`,
generated request IDs, attributable `task_created` events, authoritative
transaction timestamps, and initial Task version `1`.

Typed events include:

```text
task_created
task_updated
task_blocked
task_unblocked
result_submitted
review_approved
review_rejected
task_completed
task_cancelled
```

## Testing

Prioritize state-transition tests:

* blocked tasks are not ready;
* future tasks are not ready;
* unsatisfied dependencies prevent readiness;
* cancelled dependencies make dependants unsatisfiable;
* review is required when configured;
* stale task versions are rejected;
* duplicate idempotency keys return the original result;
* event ordering is stable;
* invalid state transitions fail without partial writes.

## Exit gate

A human operator can create a small dependency graph, block and unblock work, submit structured evidence, approve the result, and inspect the complete audit history.

---

# Phase 4 — Agent execution and extended JSON CLI contract

## Goal

Make Workaholic AI usable by autonomous local agents without exposing a public API.

## Scope

Implement:

```text
Attempt
atomic claim
lease expiry
heartbeat
release
reclaim
structured progress
structured submission
capability filtering
```

An attempt contains:

```text
attempt ID
task UID
subject ID
lease expiry
status
start/end timestamps
```

## Agent commands

```bash
workaholic task claim \
  --capability code \
  --lease 15m \
  --json \
  --non-interactive
```

```bash
workaholic task heartbeat ACME-42 \
  --attempt atm_01... \
  --json \
  --non-interactive
```

```bash
workaholic task progress ACME-42 \
  --attempt atm_01... \
  --input-file progress.json \
  --json \
  --non-interactive
```

```bash
workaholic task release ACME-42 \
  --attempt atm_01... \
  --reason capability-mismatch \
  --json \
  --non-interactive
```

```bash
workaholic task submit ACME-42 \
  --attempt atm_01... \
  --result-file result.json \
  --idempotency-key run-238-submit \
  --json \
  --non-interactive
```

## CLI automation contract extension

Phase 4 extends the JSON, non-interactive, idempotency, error, and exit-code
contract established for the Phase 1 commands. It does not retrofit automation
support onto an interactive-only CLI.

Every agent-facing command must guarantee:

* JSON-only stdout under `--json`;
* diagnostics only on stderr;
* no prompts under `--non-interactive`;
* stable error identifiers;
* stable exit-code categories;
* versioned JSON envelopes;
* stdin and file input for large payloads;
* idempotency for mutations;
* no secrets in visible command arguments.

Example:

```json
{
  "schema": "workaholic.cli/v1",
  "ok": false,
  "error": {
    "code": "LEASE_LOST",
    "message": "The attempt for ACME-42 is no longer active.",
    "retryable": false
  }
}
```

## Concurrency invariants

The test suite must prove:

1. Two concurrent agents cannot claim the same task.
2. An expired attempt cannot heartbeat or submit.
3. A stale attempt cannot submit after the task is reclaimed.
4. The same agent reclaiming a task still receives a new attempt ID.
5. An idempotently retried submission creates one result and one logical completion.
6. Lease expiry correctness does not depend on a background scheduler.

Use actual concurrent processes against SQLite, not only mocked unit tests.

## Exit gate

Two local agent processes race for one task. Exactly one receives it. That agent heartbeats and submits. A stale process receives a structured `LEASE_LOST` response.

This is the first agent-usable Workaholic AI release.

---

# Phase 5 — Identity, authentication, and authorization

## Goal

Extend the attributable local bootstrap identity into the finalized human and
agent credential-management model.

## Scope

Implement:

```text
Token
additional Human and Agent Subjects
Viewer, Agent, and Operator ProjectGrants
credential storage
identity-management commands
full authorization policy
```

Phase 1 already creates one real Human Subject, grants Instance-administrator
status and the Owner role, and attributes Task mutations to that Subject.
Phase 5 adds bearer Tokens, secure credential storage, additional identities,
and general grant management. It does not replace anonymous bootstrap records.

Subject kinds:

```text
human
agent
```

Project roles:

```text
viewer
agent
operator
owner
```

Tokens are high-entropy bearer values. Store only token hashes. Tokens belong to subjects, and subjects receive project grants.

## Commands

```bash
workaholic auth whoami
workaholic auth create-human alice
workaholic auth create-agent code-agent-3
workaholic auth grant code-agent-3 agent --project ACME
workaholic auth create-token code-agent-3
workaholic auth revoke-token tok_01...
workaholic auth disable-subject code-agent-3
```

## Local credential behavior

Humans:

```text
OS credential store where available
protected user configuration fallback
```

Agents:

```text
WORKAHOLIC_TOKEN environment variable
mounted secret file
orchestrator secret injection
```

Never store tokens in:

```text
.workaholic.env
task payloads
events
normal logs
visible process arguments
```

## Authorization behavior

The authenticated subject, not a supplied `actor_id`, owns an attempt.

For example:

```text
Agent token A cannot heartbeat Agent token B’s attempt.
Viewer cannot create or claim tasks.
Agent cannot approve its own review unless explicitly granted operator rights.
Operator cannot change project ownership grants.
Disabled subjects cannot authenticate.
Revoked tokens stop working immediately.
```

Local mode still treats filesystem access as part of the security boundary, but the application layer must apply the same authorization rules used by the remote server.

## Exit gate

A human operator creates two agent identities. Each agent sees only authorized projects, claims under its own identity, and cannot mutate another agent’s attempt.

Audit events identify the real subject behind every mutation.

---

# Phase 6 — Shared server and remote CLI

## Goal

Support distributed teams while preserving the exact same CLI commands used locally.

## Scope

Implement:

```text
workaholic server
private HTTP/JSON protocol
RemoteSession
profile/login management
server capability negotiation
request authentication
request IDs
health/readiness checks
graceful shutdown
structured logging
```

Architecture:

```text
CLI/TUI
   │
WorkaholicSession
   ├─ LocalSession  → application → local storage
   └─ RemoteSession → private protocol → server → application → server storage
```

The private protocol should start versioned:

```text
/workaholic/v1/...
```

but should not be promoted as a public integration API.

## Commands

Server:

```bash
workaholic server --config /etc/workaholic/config.toml
```

Client:

```bash
workaholic login https://tasks.example.internal
workaholic profile list
workaholic profile use team
workaholic project bind ACME .
workaholic task list
```

Agents still use:

```bash
workaholic task claim --json --non-interactive
```

They do not call HTTP endpoints directly.

## Initial deployment model

For v1, support:

```text
one Workaholic server process
one SQLite or PostgreSQL store
many remote CLI clients
many human and agent identities
```

Horizontal server scaling remains out of scope.

Before an official container exists, production-like deployment uses:

```bash
uv tool install 'workaholic-ai==<version>'
workaholic server --config /etc/workaholic/config.toml
```

under a process supervisor. TLS should be terminated by an ingress or reverse proxy.

Include reference deployment documentation for:

* a Linux service manager;
* configuration via environment and file;
* log collection;
* graceful restart;
* backup of SQLite state;
* reverse-proxy/TLS setup.

## Remote reliability

Implement:

* connect and request timeouts;
* bounded retries only for safe/idempotent operations;
* request idempotency propagation;
* server-authoritative timestamps;
* protocol capability negotiation;
* explicit incompatible-client errors;
* endpoint identity check against `WORKAHOLIC_INSTANCE_ID`.

## Testing

End-to-end tests must start a real server subprocess and invoke the real CLI as a subprocess.

Test:

* two operators;
* two agents;
* project-scoped grants;
* expired token;
* revoked token;
* wrong instance context;
* network interruption during an idempotent submission;
* older supported client protocol;
* unsupported client protocol.

## Exit gate

A server running on another machine can be used entirely through the same commands as local mode. Two developers and an agent collaborate on one project without any user calling the network protocol directly.

---

# Phase 7 — Persistence parity

## Goal

Fulfil the swappable-persistence requirement for new instances.

## Ordering

Implement in this order:

1. Extract and stabilize the conformance suite from SQLite behavior.
2. Add PostgreSQL.
3. Add JSON.
4. Run identical behavioral tests against all three.

PostgreSQL comes before JSON because it is on the critical path for serious shared-team use.

## Persistence contract

Required semantic operations include:

```text
create_project
create_task_and_allocate_number
update_task_if_version
claim_next_task
renew_attempt
release_attempt
submit_result
approve_result
reject_result
append_event
record_idempotent_result
read_events_after
```

No client or application command should know which backend implements them.

## PostgreSQL backend

Implement:

* atomic project-number allocation;
* transactional task claiming;
* optimistic task updates;
* event and materialized-state consistency;
* idempotency records;
* server-only usage;
* connection validation and health reporting.

## JSON backend

Canonical layout:

```text
instance-directory/
  state.json
  state.lock
```

A mutation must:

1. Acquire an inter-process lock.
2. Read and validate current state.
3. Apply one logical domain transaction.
4. Write a temporary file.
5. Flush it.
6. Atomically replace `state.json`.
7. Release the lock.

JSON is intended for inspection, demos, and lightweight local usage—not high-throughput coordination.

## Conformance suite

Every backend must prove:

* no duplicate project task numbers;
* no double claim;
* deterministic task ordering;
* lease expiry and reclaim;
* stale-attempt rejection;
* optimistic version conflicts;
* idempotent mutation behavior;
* ordered event cursor;
* task/event transactional consistency;
* authorization-related lookup consistency;
* restart and recovery behavior.

GitHub Actions should run PostgreSQL as an integration dependency and run JSON/SQLite tests on the supported client operating systems.

## Explicit non-features

This phase does not include:

```text
backend conversion
export/import
schema migration
SQLite-to-PostgreSQL transfer
JSON-to-SQLite transfer
automatic upgrades
```

“Swappable” means selecting a backend for a new instance.

## Exit gate

The same golden scenario produces equivalent externally visible results against:

```text
json://...
sqlite://...
postgres://...
```

Backend-specific implementation details never appear in CLI JSON.

---

# Phase 8 — Feature-complete beta and hardening

## Goal

Stop adding v1 domain features and turn the implementation into a supportable product.

## CLI and UX completion

Complete:

```bash
workaholic --help
workaholic status
workaholic doctor
workaholic context
workaholic auth whoami
workaholic server --check-config
workaholic task events --follow
```

Add:

* clear human-readable error messages;
* stable machine-readable errors;
* shell completion where supported;
* redaction of credentials;
* actionable lock and connectivity errors;
* consistent date/time rendering;
* table output that degrades well in narrow terminals;
* documented JSON schemas.

## Operational hardening

Test and document:

* abrupt server shutdown;
* interrupted JSON write;
* locked SQLite database;
* PostgreSQL reconnect;
* client timeout;
* duplicate submission after lost response;
* clock differences between agent and server;
* token revocation during an attempt;
* server restart with active leases;
* large event histories;
* many ready tasks;
* concurrent claim load.

The server’s clock is authoritative for leases. Clients should display lease times but not decide whether a lease remains valid.

## Supported-platform matrix

Freeze and publish an explicit matrix for:

```text
local embedded mode
remote CLI
server
JSON backend
SQLite backend
PostgreSQL backend
credential storage
```

CI must exercise every claimed supported combination.

## Documentation

Required guides:

```text
Five-minute local quickstart
Solo-developer workflow
Agent CLI integration
Shared-team deployment
Project and working-directory binding
Authentication administration
Backup and recovery
Troubleshooting
CLI JSON reference
Private protocol compatibility policy
Known limitations
```

## Packaging

Build and test both the source distribution and wheel. `uv build` supports generating these package artifacts, and uv’s own packaging guidance recommends testing the built distributions rather than only the source checkout. ([Astral Docs][4])

CI should verify:

```text
install from wheel
install from source distribution
run --version
initialize local instance
create/list one task
start server
connect a remote CLI
```

## Schema and contract freeze

At the end of this phase, freeze:

```text
persisted schema version
.workaholic.env version 1
CLI JSON schema workaholic.cli/v1
private protocol workaholic/v1
task/event identifiers
documented error codes
```

No persisted-schema change should be accepted after this gate without removing it from v1 or introducing migration support.

## Exit gate

All v1 features are present. Remaining work is bug fixing, documentation correction, security hardening, and release engineering.

---

# Phase 9 — Release readiness and release candidate

## Goal

Produce a supported release candidate from the public development repository.

## Repository readiness

Finalize:

```text
LICENSE
README
CONTRIBUTING.md
CODE_OF_CONDUCT.md
SECURITY.md
SUPPORT.md
GOVERNANCE.md
CHANGELOG.md
maintainer list
release process
compatibility policy
```

Make a final decision on:

* open-source license;
* DCO versus CLA;
* package and repository names;
* trademark/name positioning;
* supported Python and operating-system versions;
* maintenance policy.

Because repository development is public from Phase 0, continuously enforce
these controls and repeat the complete audit before the release candidate:

* scan the complete Git history for credentials;
* remove private company URLs and internal names;
* verify dependency licenses;
* inspect issue and pull-request history;
* verify documentation examples contain no secrets;
* confirm all contributors have authority to license their work.

Add a security policy early. GitHub private vulnerability reporting can provide
a structured private disclosure path to maintainers of the public repository.
([GitHub Docs][5])

Enable dependency vulnerability monitoring and controlled dependency-update pull requests. GitHub provides Dependabot alerts, security updates, and configurable version-update behavior. ([GitHub Docs][6])

## Release automation

The tag-driven workflow should:

1. Verify the tag matches the package version.
2. Run the complete CI suite.
3. Build the source distribution and wheel.
4. Install and smoke-test both artifacts.
5. Publish to the registry through PyPI Trusted Publishing.
6. Create the GitHub release.
7. Attach checksums and release metadata.
8. Run a clean `uvx` installation test against the published version.

PyPI Trusted Publishing uses OIDC and avoids storing a long-lived PyPI API token in GitHub. uv documents building and publishing from GitHub Actions using this mechanism. ([PyPI Docs][7])

GitHub can generate release notes from merged pull requests and contributors, although the final release summary should still include curated upgrade notes and known limitations. ([GitHub Docs][8])

## Release-candidate deployment

Publish:

```text
workaholic-ai 1.0.0rc1
```

Test it through the real intended path:

```bash
uvx --from 'workaholic-ai==1.0.0rc1' workaholic --version
```

Run at least these RC environments:

```text
fresh local SQLite project
fresh local JSON project
shared SQLite server
shared PostgreSQL server
human remote client
agent remote client
```

Source development is public from Phase 0 so contributors can inspect delivery
decisions and provide early feedback. Package publication and the first
supported release remain deferred until the release-candidate security,
licensing, compatibility, and installation gates pass.

## Exit gate

No unresolved release-blocking security, data-corruption, task-identity, lease, authentication, or installation defects.

The `1.0.0rc1` package works from a clean environment without using the source checkout.

---

# Phase 10 — v1 release

## Goal

Publish and support Workaholic AI `1.0.0`.

## Final release procedure

1. Close the v1 milestone to feature work.
2. Re-run every golden journey.
3. Verify schema and protocol versions remain unchanged from RC.
4. Update release notes and known limitations.
5. Tag the exact reviewed commit as `v1.0.0`.
6. Publish `workaholic-ai==1.0.0`.
7. Create the GitHub release.
8. Verify installation from the public registry.
9. Verify one local and one shared deployment from published artifacts.
10. Open the v1.1 milestone and move deferred issues into it.

## Public installation acceptance test

Local:

```bash
uvx --from 'workaholic-ai==1.0.0' \
  workaholic up --project-key ACME
```

Regular user:

```bash
uv tool install 'workaholic-ai==1.0.0'
workaholic task list
```

Shared server:

```bash
uv tool install 'workaholic-ai[server,postgres]==1.0.0'
workaholic server --config /etc/workaholic/config.toml
```

Agent:

```bash
workaholic task claim \
  --json \
  --non-interactive
```

## v1 support statement

The release documentation should state clearly:

* CLI JSON is the supported automation interface.
* Direct use of the server protocol is unsupported.
* One server process per instance is supported.
* Horizontal scaling is unsupported.
* Storage conversion and migration are unsupported.
* A v1 store must remain on its supported schema version.
* Agents must use distinct identities.
* Credentials must not be stored in project context files.
* Artifact contents remain outside Workaholic AI.
* Cross-project blocking dependencies are unsupported.

---

# CI progression by phase

| Phase | Required CI additions                             |
| ----- | ------------------------------------------------- |
| 0     | Lint, type check, unit tests, package build       |
| 1     | SQLite integration and local CLI smoke tests      |
| 2     | Context discovery and multi-project tests         |
| 3     | State-machine and event consistency tests         |
| 4     | Multi-process claim and stale-attempt tests       |
| 5     | Authentication and authorization matrix           |
| 6     | Real server plus remote CLI end-to-end tests      |
| 7     | JSON/SQLite/PostgreSQL conformance matrix         |
| 8     | Supported-OS matrix, packaging and recovery tests |
| 9     | Trusted-publishing dry run and RC installation    |
| 10    | Published-artifact acceptance tests               |

Keep expensive stress, recovery, and full backend tests in a scheduled or pre-release workflow when they become too slow for every pull request. Core invariants must remain required PR checks.

---

# Definition of fully releasable v1

Workaholic AI is v1-ready only when all of the following are true:

1. A solo developer can start a local instance and track tasks through `uvx`.
2. Several working directories can bind to distinct projects in one instance.
3. Stable task IDs such as `ACME-42` survive every supported operation.
4. Human operators can use the complete task lifecycle.
5. Agents can claim, heartbeat, release, and submit using only the CLI.
6. Competing agents cannot double-claim or complete from stale attempts.
7. Humans and agents have distinct authenticated identities and project roles.
8. The same CLI works in embedded and remote modes.
9. JSON, SQLite, and PostgreSQL pass one behavioral conformance suite.
10. Clean wheel, source-distribution, and `uvx` installations pass.
11. The persisted schema and CLI JSON contract are frozen.
12. Security, contribution, governance, and support documentation are present.
13. The public repository remains free of secrets and private internal data.
14. The release workflow publishes without long-lived PyPI credentials.
15. The v1 limitations are explicit rather than implied.

## Explicit post-v1 backlog

These items should not delay `1.0.0`:

```text
Storage migration and conversion
Import/export
Official OCI server image
Self-contained native client
TUI
Browser UI
Public API and SDKs
GitHub synchronization
Webhooks
Cross-project dependencies
Horizontal server scaling
SSO and OAuth
Plugin system
Hosted Workaholic AI service
```

The immediate next implementation action is **Phase 0**: create the GitHub repository, add the architecture and ADR skeleton, establish the milestone structure, and make `uv run workaholic --version` pass in CI.

[1]: https://docs.github.com/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches?utm_source=chatgpt.com "About protected branches"
[2]: https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-issue-forms?utm_source=chatgpt.com "Syntax for issue forms"
[3]: https://docs.github.com/en/actions/concepts/security/openid-connect?utm_source=chatgpt.com "OpenID Connect"
[4]: https://docs.astral.sh/uv/guides/package/?utm_source=chatgpt.com "Building and publishing a package | uv - Astral Docs"
[5]: https://docs.github.com/code-security/getting-started/adding-a-security-policy-to-your-repository?utm_source=chatgpt.com "Adding a security policy to your repository"
[6]: https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-options-reference?utm_source=chatgpt.com "Dependabot options reference"
[7]: https://docs.pypi.org/trusted-publishers/using-a-publisher/?utm_source=chatgpt.com "Publishing with a Trusted Publisher"
[8]: https://docs.github.com/en/repositories/releasing-projects-on-github/automatically-generated-release-notes?utm_source=chatgpt.com "Automatically generated release notes"
