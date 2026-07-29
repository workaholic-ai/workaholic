# Workaholic AI — finalized v1 architecture

**Workaholic AI** is a lightweight, CLI-first task coordination system designed for both human operators and autonomous agents.

It runs in two modes:

* **Embedded local mode:** no server process; CLI commands operate against local JSON or SQLite through the application core.
* **Shared server mode:** CLI commands communicate with a remotely deployed Workaholic AI server over a private, versioned protocol.

Agents use the CLI exclusively. A future TUI uses the same internal client interface rather than wrapping or parsing the CLI.

Canonical terms are defined in the [glossary](glossary.md). Security assumptions,
trust boundaries, and required mitigations are defined in the
[threat model](threat-model.md).

For the examples below:

```text
Product:        Workaholic AI
Python package: workaholic-ai
Executable:     workaholic
```

## 1. Architectural decisions

| Area                 | V1 decision                                                    |
| -------------------- | -------------------------------------------------------------- |
| Primary interface    | CLI                                                            |
| Agent interface      | Versioned JSON CLI contract                                    |
| Future UI            | TUI over the same session/client library                       |
| Initial distribution | Python package invoked through `uvx`                           |
| Regular installation | `uv tool install`                                              |
| Local runtime        | Embedded; no persistent daemon required                        |
| Shared runtime       | Authenticated server process                                   |
| Future deployment    | Containerized server and self-contained client                 |
| Local persistence    | SQLite by default; JSON optional                               |
| Shared persistence   | PostgreSQL recommended; SQLite supported for a single server   |
| Project discovery    | Nontracked `.workaholic.env` in or above the working directory |
| Human task IDs       | Stable `PROJECT-NUMBER`, such as `ACME-142`                    |
| Internal IDs         | Globally unique opaque IDs                                     |
| Authentication       | Human and agent subjects with bearer tokens and project roles  |
| Storage migration    | Explicitly outside v1                                          |
| Public HTTP API      | None; network transport is an internal client protocol         |

## 2. System shape

```text
                    Human operator
                          │
                    Autonomous agent
                          │
                          ▼
                ┌───────────────────┐
                │  workaholic CLI   │
                └─────────┬─────────┘
                          │
                ┌─────────▼─────────┐
                │  Session interface │◄──────── Future TUI
                └──────┬───────┬────┘
                       │       │
             embedded │       │ remote
                       │       │
          ┌────────────▼──┐  ┌─▼────────────────┐
          │ LocalSession  │  │ RemoteSession     │
          └───────┬───────┘  └────────┬─────────┘
                  │                   │
                  │             private HTTP/JSON
                  │                   │
          ┌───────▼────────┐  ┌──────▼──────────┐
          │ Application    │  │ Workaholic      │
          │ core           │  │ server           │
          └───────┬────────┘  └──────┬──────────┘
                  │                  │
                  │          ┌───────▼────────┐
                  │          │ Application    │
                  │          │ core           │
                  │          └───────┬────────┘
                  │                  │
          ┌───────▼──────────────────▼────────┐
          │ Semantic persistence interface    │
          └──────────┬────────┬───────────────┘
                     │        │
               JSON / SQLite  PostgreSQL
```

The most important boundary is the **Session interface**.

The CLI and TUI do not know whether they are talking to:

* a local application core;
* a server on localhost;
* a remote team server.

They issue the same commands and receive the same result models.

## 3. Distribution and startup

### First-use local command

```bash
uvx --from 'workaholic-ai==0.1.0' workaholic up \
  --project-key ACME
```

The package and executable names differ, so `uvx --from` identifies the package that provides the `workaholic` command. Version constraints can also be supplied there. ([Astral Docs][1])

`workaholic up`:

1. Creates or opens the user’s default local instance.
2. Initializes SQLite unless another backend is selected.
3. Creates the local human operator on first use.
4. Creates or locates the project.
5. Writes `.workaholic.env` in the current working directory.
6. Adds that file to `.git/info/exclude` when appropriate.
7. Returns immediately.

There is no daemon. After that:

```bash
uvx --from 'workaholic-ai==0.1.0' workaholic task list
```

opens the same local instance, performs the operation, and exits.

### Regular installation

Developers and persistent agents should install the same package:

```bash
uv tool install 'workaholic-ai==0.1.0'
```

They can then use:

```bash
workaholic task list
workaholic task claim --json --non-interactive
```

`uvx` environments are cached but treated as disposable, whereas `uv tool install` creates a persistent tool environment. Durable state must therefore live in Workaholic AI’s data directories, never inside a uv environment or cache. ([Astral Docs][2])

### Initial shared deployment

Before an OCI image exists, the same package can run the server:

```bash
uvx --from 'workaholic-ai[server,postgres]==0.1.0' \
  workaholic server \
  --storage "$WORKAHOLIC_STORAGE_URL"
```

That is suitable for evaluation or foreground operation. A long-running deployed server should use a pinned `uv tool install` installation under a process supervisor.

### Later container deployment

The eventual image runs the same command:

```dockerfile
ENTRYPOINT ["workaholic", "server"]
```

Containerization changes:

* package installation;
* process supervision;
* configuration injection;
* network exposure;
* persistent volume configuration.

It does **not** change:

* the server implementation;
* storage interfaces;
* task semantics;
* CLI commands;
* authentication;
* the private client protocol;
* project context files.

### Later self-contained client

A native client package can include:

```text
CLI
future TUI
context discovery
authentication profiles
remote transport
embedded JSON/SQLite mode
```

It can exclude:

```text
HTTP server framework
PostgreSQL driver
server deployment code
```

That transition is release engineering rather than an architectural rewrite.

## 4. Local and remote sessions

The presentation layers use a transport-neutral interface resembling:

```python
class WorkaholicSession:
    def status(...): ...
    def list_projects(...): ...
    def list_tasks(...): ...
    def get_task(...): ...
    def create_task(...): ...
    def update_task(...): ...
    def claim_task(...): ...
    def heartbeat(...): ...
    def release_task(...): ...
    def block_task(...): ...
    def submit_result(...): ...
    def approve_result(...): ...
    def reject_result(...): ...
    def cancel_task(...): ...
    def list_events(...): ...
```

### `LocalSession`

`LocalSession` invokes the application core in the current process.

It is used with:

* JSON;
* SQLite.

It still supplies an authenticated subject context, so authorization and audit behavior remain consistent with remote operation.

### `RemoteSession`

`RemoteSession` translates the same commands into a private HTTP/JSON protocol.

The protocol is versioned from the beginning:

```json
{
  "protocol": "workaholic/v1",
  "server_version": "0.1.0",
  "minimum_client_version": "0.1.0",
  "features": [
    "leases",
    "review",
    "typed-events"
  ]
}
```

The transport is not the public agent interface. It is an implementation detail used by official Workaholic AI clients.

## 5. Project and working-directory context

A project is a logical namespace inside an instance. A working directory is a local binding to that project.

In canonical terminology, each working-directory binding is a **Workspace**.

The same project may be checked out in several places:

```text
/home/alice/projects/acme
/srv/agents/code-agent/acme
/Users/bob/worktrees/acme-refactor
```

Each checkout has its own nontracked context file.

### `.workaholic.env`

```dotenv
WORKAHOLIC_CONTEXT_VERSION=1

WORKAHOLIC_PROFILE=team
WORKAHOLIC_INSTANCE_ID=ins_01K9M2R6
WORKAHOLIC_PROJECT_ID=prj_01K9M3A8
WORKAHOLIC_PROJECT_KEY=ACME

WORKAHOLIC_WORKSPACE_ROOT=.
```

The file is generated by:

```bash
workaholic project bind ACME
```

It must not contain:

```text
authentication tokens
database credentials
private keys
arbitrary shell commands
server-controlled executable paths
```

It also should not contain an arbitrary server URL. A repository-controlled file must not be able to redirect a user’s bearer token to another host.

Instead, `WORKAHOLIC_PROFILE` refers to a trusted user-level profile:

```toml
# User configuration

[profiles.team]
mode = "remote"
url = "https://tasks.example.internal"
credential = "keyring:workaholic/team"

[profiles.local]
mode = "embedded"
storage = "sqlite:///user-data/workaholic/local.db"
credential = "keyring:workaholic/local"
```

For ephemeral agents, the trusted runtime environment can provide:

```bash
WORKAHOLIC_URL=...
WORKAHOLIC_TOKEN=...
```

while `.workaholic.env` supplies the project and workspace identity.

### Context resolution

The shared resolver uses this order:

1. Explicit command arguments.
2. Process environment.
3. Nearest `.workaholic.env`, walking upward.
4. User profile defaults.
5. A structured `CONTEXT_NOT_FOUND` error.

The nearest file wins, allowing nested projects inside a monorepo.

A diagnostic command exposes the effective context:

```bash
workaholic context --json
```

```json
{
  "schema": "workaholic.cli/v1",
  "mode": "remote",
  "profile": "team",
  "instance": {
    "id": "ins_01K9M2R6"
  },
  "project": {
    "id": "prj_01K9M3A8",
    "key": "ACME"
  },
  "workspace_root": "/work/acme",
  "subject": {
    "id": "sub_01K9P1",
    "kind": "agent",
    "name": "code-agent-3"
  },
  "context_source": "/work/acme/.workaholic.env"
}
```

The parser accepts a strict allowlist of keys and never invokes a shell or performs command substitution.

## 6. Core domain model

The primary persistent entities are:

| Entity            | Purpose                                          |
| ----------------- | ------------------------------------------------ |
| Instance          | Identifies one Workaholic AI installation        |
| Project           | Task namespace and stable key prefix             |
| Subject           | Human or agent identity                          |
| Token             | Authentication credential belonging to a subject |
| ProjectGrant      | Subject role within a project                    |
| Task              | Desired outcome and lifecycle state              |
| Attempt           | One agent’s leased execution attempt             |
| TaskEvent         | Append-only audit and activity record            |
| IdempotencyRecord | Deduplicates retried mutations                   |

### Project identifiers

```json
{
  "id": "prj_01K9M3A8",
  "key": "ACME",
  "name": "Acme Billing"
}
```

Rules:

* The project key is uppercase and immutable.
* It is unique within an instance.
* Archived keys are not reused.
* The display name may change freely.
* Two separate instances may both contain an `ACME` project; the instance ID disambiguates them.

### Task identifiers

```json
{
  "uid": "tsk_01K9Q...",
  "project_id": "prj_01K9M3A8",
  "number": 142,
  "key": "ACME-142"
}
```

`uid` is the canonical machine identity.

`ACME-142` is the stable human identity.

Task numbers are:

* allocated atomically per project;
* monotonically increasing;
* never reused;
* allowed to have gaps.

Moving a task to another project is not supported. A new task is created in the destination project and linked with `supersedes` or `related_to`.

## 7. Task lifecycle

The stored lifecycle states are deliberately small:

```text
open
blocked
review
done
cancelled
```

Operational views are derived:

```text
ready
running
scheduled
stale
awaiting_review
```

For example:

```text
ready =
    state == open
    AND dependencies satisfied
    AND available_at <= now
    AND no active attempt
```

```text
running =
    state == open
    AND active attempt lease has not expired
```

A minimal task looks like:

```json
{
  "uid": "tsk_01K9Q...",
  "key": "ACME-142",
  "project_id": "prj_01K9M3A8",
  "number": 142,

  "title": "Analyze cancellation reasons",
  "objective": "Identify the three most actionable cancellation causes.",

  "state": "open",
  "priority": 70,
  "available_at": null,

  "parent_uid": null,
  "depends_on": ["tsk_01K9P..."],

  "requirements": {
    "capabilities": ["sql", "data-analysis"],
    "approval": "human"
  },

  "acceptance": [
    {
      "id": "ac_1",
      "text": "At least 80% of records are categorized.",
      "required": true
    }
  ],

  "context": [
    {
      "uri": "workspace://repo/data/cancellations.csv",
      "version": "git:8f31c12"
    }
  ],

  "active_attempt_id": null,
  "version": 4,

  "created_by": "sub_01K9...",
  "created_at": "2026-07-29T09:00:00Z",
  "updated_at": "2026-07-29T09:20:00Z"
}
```

V1 supports blocking dependencies only within one project.

A dependency is satisfied only when it is `done`. A cancelled dependency makes the dependent task non-ready and surfaces an `UNSATISFIABLE_DEPENDENCY` reason until an operator changes the dependency graph.

## 8. Attempts and leases

A task assignment is an expiring attempt, not a permanent assignee:

```json
{
  "id": "atm_01K9R...",
  "task_uid": "tsk_01K9Q...",
  "subject_id": "sub_01K9A...",
  "status": "active",
  "lease_expires_at": "2026-07-29T14:30:00Z",
  "started_at": "2026-07-29T14:15:00Z",
  "ended_at": null
}
```

Claiming a task atomically:

1. Expires any stale attempt that affects selection.
2. Selects the highest-ranked ready task.
3. Creates a new attempt.
4. Associates it with the task.
5. Appends a `task_claimed` event.
6. Commits the operation.
7. Returns the task packet and attempt ID.

Default task ordering is deterministic:

```text
priority descending
available_at ascending
task number ascending
```

A heartbeat extends the lease only when:

* the authenticated subject owns the attempt;
* the attempt ID is still current;
* the lease has not already been superseded.

A stale process cannot submit using an old attempt ID, even when the same agent identity later reclaims the task.

Correctness does not depend on a background scheduler. Lease expiry is evaluated transactionally during claims, heartbeats, submissions, and relevant queries. A server may perform housekeeping, but it is an optimization rather than a requirement.

## 9. Results and review

Submission returns structured evidence:

```json
{
  "summary": "Payment friction and missing integrations explain 61% of categorized cancellations.",

  "criteria": [
    {
      "criterion_id": "ac_1",
      "status": "passed",
      "evidence": "9,412 of 10,883 records categorized: 86.5%"
    }
  ],

  "artifacts": [
    {
      "uri": "workspace://repo/reports/cancellation-analysis.md",
      "media_type": "text/markdown",
      "sha256": "..."
    }
  ],

  "proposed_follow_ups": [
    {
      "title": "Validate payment-friction finding against support tickets"
    }
  ]
}
```

Workaholic AI stores artifact references and hashes, not large artifact contents.

Submission behavior:

```text
No approval required:
    open → done

Approval required:
    open → review

Approved:
    review → done

Rejected:
    review → open
```

A rejection closes the previous attempt and returns the task to the queue with a typed review event.

## 10. Events

Events are append-only and typed:

```text
task_created
task_updated
task_claimed
lease_renewed
attempt_released
attempt_expired
progress_reported
observation_added
task_blocked
task_unblocked
result_submitted
review_approved
review_rejected
task_completed
task_cancelled
```

Each event records:

```text
event ID
instance event sequence
task ID
subject ID
subject kind
attempt ID, when applicable
request ID
event type
structured payload
timestamp
```

A monotonically ordered event cursor supports:

* CLI watch commands;
* future TUI refresh;
* troubleshooting;
* audit views.

The initial implementation may poll:

```bash
workaholic task events ACME-142 --follow
```

No message broker or WebSocket infrastructure is required in v1.

## 11. Persistence abstraction

The persistence layer exposes domain-level transactional operations, not generic key-value CRUD.

The normative adapter requirements are recorded in the
[persistence contract](persistence-contract.md) and
[ADR 0005](adr/0005-semantic-persistence-interface.md).

Representative operations:

```text
create_project_and_allocate_key
create_task_and_allocate_number
update_task_if_version
claim_next_task
renew_attempt
release_attempt
append_event
submit_result
approve_result
reject_result
record_idempotent_result
query_tasks
read_events_after
```

All backends must implement identical externally observable behavior.

### Deployment matrix

| Backend    | Embedded local |      Shared server | Intended role                           |
| ---------- | -------------: | -----------------: | --------------------------------------- |
| JSON       |            Yes | Yes, single server | Inspection, demos, small local use      |
| SQLite     |            Yes | Yes, single server | Default local and small-team deployment |
| PostgreSQL |             No |                Yes | Distributed-team deployment             |

PostgreSQL is not accessed directly by CLI clients. It always sits behind the server.

### JSON backend

The JSON backend uses one canonical instance-state document:

```text
instance-directory/
  state.json
  state.lock
```

A mutation:

1. Acquires an inter-process lock.
2. Reads and validates the current state.
3. Applies one complete domain transaction.
4. Writes a temporary file.
5. Flushes it.
6. Atomically replaces `state.json`.
7. Releases the lock.

This is intentionally optimized for simplicity and inspection rather than high throughput.

### SQLite backend

SQLite is the default for local use.

Each CLI invocation opens a short-lived connection. Compound operations such as number allocation, claiming, event creation, and idempotency recording occur in one write transaction.

### PostgreSQL backend

PostgreSQL is used behind a shared server. Its adapter uses database-native transactional locking for atomic claiming and task-number allocation.

### Backend conformance

The same conformance suite runs against every backend and verifies:

* unique project task numbers;
* no double claims;
* correct lease expiry;
* stale-attempt rejection;
* optimistic version conflicts;
* idempotent retry behavior;
* event ordering;
* authorization lookup behavior;
* task/event consistency;
* crash-safe JSON replacement.

### Meaning of “swappable” in v1

Swappable means:

> A new instance can be initialized against any supported backend without changing domain or client code.

It does **not** mean:

* changing the backend of an existing instance;
* JSON-to-SQLite conversion;
* SQLite-to-PostgreSQL transfer;
* export/import;
* automatic schema migration.

Each store records a schema version. An incompatible version fails safely with a structured error. Cross-backend and automated storage migration tooling belongs in the backlog.

## 12. Authentication and authorization

### Subjects

```json
{
  "id": "sub_01K9...",
  "kind": "agent",
  "name": "billing-code-agent",
  "disabled": false
}
```

Subject kinds:

```text
human
agent
```

Every independently operating agent receives its own identity. Shared “all agents” credentials are discouraged because they make lease ownership and audit history ambiguous.

### Tokens

Tokens are high-entropy bearer credentials. Only hashes are stored.

```json
{
  "id": "tok_01K9...",
  "subject_id": "sub_01K9...",
  "expires_at": "2026-10-01T00:00:00Z",
  "revoked_at": null
}
```

For humans, credentials are stored in the operating-system credential store where available, with a protected configuration-file fallback.

For agents, credentials are supplied through:

```text
environment variables
mounted secrets
orchestrator secret injection
```

Tokens never appear in `.workaholic.env` or normal command arguments.

### Roles

| Role                   | Main permissions                                                                 |
| ---------------------- | -------------------------------------------------------------------------------- |
| Viewer                 | Read tasks, projects, attempts, and events                                       |
| Agent                  | Claim, heartbeat, report progress, release, submit, create permitted child tasks |
| Operator               | Create and edit tasks, block/unblock, review, cancel                             |
| Owner                  | Operator rights plus project settings and grants                                 |
| Instance administrator | Create projects, subjects, tokens, and instance-wide grants                      |

Subject kind and role are separate. An agent identity normally receives the Agent role, but the authorization model does not hard-code that assumption.

### Local bootstrap

The first local `workaholic up`:

* creates a local human administrator;
* creates a token;
* stores the credential securely;
* records the selected subject in the local profile.

The filesystem remains part of the local security boundary, but application-level authorization and audit behavior are preserved.

### Shared bootstrap

An empty server store requires a bootstrap credential supplied through trusted deployment configuration. Once the first administrator exists, normal identity commands take over:

```bash
workaholic auth create-human alice
workaholic auth create-agent code-agent-3
workaholic auth grant code-agent-3 agent --project ACME
workaholic auth revoke-token tok_01K9...
```

Remote bearer-token traffic must use HTTPS, normally through an ingress or reverse proxy.

## 13. CLI contract

The CLI is simultaneously:

* the human command interface;
* the supported agent automation interface;
* the entry point for local administration.

Representative commands:

```text
workaholic up
workaholic status
workaholic context

workaholic login
workaholic profile list

workaholic project create
workaholic project bind
workaholic project list

workaholic task add
workaholic task list
workaholic task show
workaholic task update
workaholic task claim
workaholic task heartbeat
workaholic task progress
workaholic task block
workaholic task release
workaholic task submit
workaholic task approve
workaholic task reject
workaholic task cancel
workaholic task events

workaholic auth create-human
workaholic auth create-agent
workaholic auth grant
workaholic auth revoke-token

workaholic server
```

### Agent-safe behavior

Every agent-facing command supports:

```text
--json
--non-interactive
--idempotency-key
--input-file
stdin input where appropriate
```

Contract rules:

* JSON mode writes only the response envelope to stdout.
* Logs and diagnostics go to stderr.
* Noninteractive mode never prompts.
* Error codes are stable and machine-readable.
* CLI output schemas are versioned.
* Mutations support idempotency.
* Large payloads are read from files or stdin.
* Credentials are not accepted in visible positional arguments.

The normative public JSON envelope and automation rules are recorded in the
[CLI automation contract](cli-contract.md) and
[ADR 0003](adr/0003-cli-json-automation-contract.md). The client/server
transport is a separate private protocol governed by
[ADR 0004](adr/0004-private-versioned-client-server-protocol.md).

Example error:

```json
{
  "schema": "workaholic.cli/v1",
  "ok": false,
  "error": {
    "code": "LEASE_LOST",
    "message": "The attempt for ACME-142 is no longer active.",
    "retryable": false
  }
}
```

Representative agent workflow:

```bash
workaholic task claim \
  --capability code \
  --lease 15m \
  --json \
  --non-interactive
```

```bash
workaholic task heartbeat ACME-142 \
  --attempt atm_01K9R \
  --json \
  --non-interactive
```

```bash
workaholic task submit ACME-142 \
  --attempt atm_01K9R \
  --result-file result.json \
  --idempotency-key agent-run-238-submit \
  --json \
  --non-interactive
```

For durable agents, the CLI version should be pinned and installed in the agent image or environment. Agents still interact only through shell commands.

## 14. TUI boundary

The future TUI imports:

```text
context resolver
authentication/profile manager
WorkaholicSession
shared request and response models
```

It does not:

* execute CLI subprocesses;
* parse CLI output;
* access SQLite directly;
* import server routes;
* reproduce readiness or lease rules.

Initial TUI views can be:

```text
Ready
Running
Blocked
Review
Recently completed
Agents
```

Polling the event cursor is sufficient initially. Streaming transport can be added later without changing the TUI’s session interface.

## 15. Package structure and enforced dependency boundaries

```text
src/workaholic/
  domain/
    models/
    rules/
    identifiers/

  application/
    commands/
    queries/
    services/
    errors/

  session/
    base.py
    local.py
    remote.py

  context/
    discovery.py
    parser.py
    profiles.py

  auth/
    subjects.py
    tokens.py
    permissions.py
    credentials.py

  persistence/
    base.py
    json_backend.py
    sqlite_backend.py
    postgres_backend.py

  protocol/
    requests.py
    responses.py
    errors.py
    compatibility.py

  client/
    transport.py

  cli/
    main.py
    commands/
    rendering.py

  tui/
    app.py

  server/
    main.py
    routes.py
    middleware.py
```

Dependency direction:

```text
domain
  ▲
application
  ▲
local session / server
  ▲
persistence and transport adapters
```

And separately:

```text
CLI / TUI
    │
    ▼
Session interface
```

These boundaries are executable contracts:

| Layer | Packages | Dependency rule |
| --- | --- | --- |
| Domain | `domain` | May not import any other Workaholic AI package |
| Application and policy | `application`, `auth` | May depend inward on Domain, but not on presentation, Session, context, transport, server, or persistence packages |
| Presentation and adapters | `cli`, `tui`, `server`, `session`, `persistence`, `protocol`, `client`, `context` | May depend inward; sibling dependencies are allowed where an adapter requires them |

The root `workaholic.__main__` module is the only exhaustive-layer exclusion. It
is a console entry-point shim that delegates immediately to `cli.main` and owns
no application behavior.

CLI and TUI presentation modules may import Session interfaces. They must not
directly import application services, persistence adapters, private protocol
models, transport clients, or server modules. Indirect dependencies reached
through a Session implementation are expected and do not make presentation
code an adapter composition root.

Composition is restricted to explicit adapter boundaries:

- `session.local` may wire the application layer to an embedded persistence
  adapter;
- `session.remote` may wire Session operations to the official transport
  client;
- `server.main` may wire server routes, application services, authentication,
  and one configured persistence adapter.

There are currently no ignored import edges. A future exception requires a
narrow source-to-target rule, architecture documentation, and a test proving
why the composition root is necessary; no production package may be exempted
wholesale.

The exhaustive layer contract and direct CLI contract are configured in
`pyproject.toml` and run through Import Linter in pytest and pre-commit. The
normal client startup path is also tested in a fresh isolated Python process.
It must not eagerly import server frameworks, PostgreSQL drivers, scheduling
frameworks, or `workaholic.server`. This keeps CLI startup light and makes later
native packaging easier.

## 16. Correctness invariants

The following should be treated as architectural invariants rather than optional implementation details:

1. **A task is claimed atomically.** Two agents cannot successfully claim the same ready task.
2. **Task identity is stable.** `ACME-142` never refers to another task.
3. **Lease expiry needs no daemon.** Expired attempts are invalid based on timestamps and transactional checks.
4. **Attempt identity prevents stale completion.** An old attempt cannot submit after a reclaim.
5. **Every mutation is attributable.** Subject, request, attempt, and timestamp are recorded.
6. **Every write is idempotency-aware.**
7. **Task updates use optimistic versions.**
8. **Local and remote sessions follow the same domain rules.**
9. **Persistence adapters pass the same behavioral suite.**
10. **Repository-local context never contains credentials or arbitrary endpoints.**
11. **The CLI JSON schema is the public automation contract.**
12. **The network protocol remains private but versioned.**
13. **Artifact contents remain outside the task manager.**
14. **Capabilities affect scheduling, not authorization.**
15. **Cross-project dependencies are not part of v1.**

## 17. V1 scope

### Included

* Python package and `uvx` launch.
* Persistent `uv tool install` option.
* Embedded local mode.
* Shared server mode.
* JSON, SQLite, and PostgreSQL adapters.
* Multiple projects per instance.
* Multiple working-directory bindings per project.
* `.workaholic.env` context discovery.
* Stable Jira-like task keys.
* Human and agent authentication.
* Project-scoped roles.
* Tasks, dependencies, blocking, review, and cancellation.
* Atomic claims and expiring attempts.
* Heartbeats, releases, and retries.
* Structured results and artifact references.
* Typed append-only events.
* Optimistic task versions.
* Idempotent mutations.
* Human-readable and JSON CLI output.
* Private versioned remote protocol.
* Architecture-ready TUI boundary.

### Backlog

* Storage conversion and migration.
* Import/export between backends.
* Automated schema-upgrade tooling.
* TUI implementation.
* Browser UI.
* Public API and SDKs.
* Webhooks.
* Cross-project blocking dependencies.
* SSO, OAuth, and enterprise identity providers.
* Custom roles and policy language.
* Blob or attachment storage.
* Workflow designer.
* Plugin system.
* Automated acceptance checkers.
* Horizontal server scaling guarantees.
* Hosted Workaholic AI service.

## Final architecture statement

> **Workaholic AI is a CLI-first, agent-native task coordination engine that runs directly over local storage for solo developers or behind an authenticated server for distributed teams. Projects are discovered from safe, nontracked working-directory metadata; tasks have stable project-prefixed identities; work is coordinated through atomic expiring attempts; and humans, agents, the future TUI, and later native clients all share one domain and session model.**

The v1 architecture supports immediate `uvx` adoption without committing the product to Python-based client distribution permanently, while preserving a direct path to a containerized server and self-contained CLI/TUI client.

[1]: https://docs.astral.sh/uv/guides/tools/ "Using tools | uv"
[2]: https://docs.astral.sh/uv/guides/tools/?utm_source=chatgpt.com "Using tools | uv - Astral Docs"
