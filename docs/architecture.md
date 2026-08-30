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

## Current implementation: `0.5.0a1`

The Phase 5 Identity and Authorization Alpha implements the embedded
`LocalSession`, trusted embedded profiles, canonical upward `.workaholic.env`
discovery, multiple named Projects, safe Workspace binding, and disposable
SQLite schema version `5`. It extends the complete Phase 4 Task, Result,
Claim, Attempt, Lease, and TaskEvent behavior with distinct Human and Agent
Subjects, independently revocable bearer Tokens, protected Human credentials,
cumulative Project roles, transactional authentication and authorization,
administrative AuditEvents, and local recovery. Each CLI invocation composes
these adapters in-process, performs one authenticated operation, and exits; no
daemon is started.

The remaining diagrams and decisions describe the accepted v1 destination, not
the current feature inventory. `0.5.0a1` does not implement remote profiles,
`RemoteSession`, a server, JSON/PostgreSQL adapters, capability-based
scheduling, custom roles, SSO/OAuth, Project archival, force interruption,
parent/child Task hierarchy, or schema migration. Proposed Result follow-ups
never create Tasks automatically. Alpha storage and automation remain
disposable.

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
3. Creates one real local Human Subject on first use.
4. Marks that Subject as the Instance administrator.
5. Creates or locates the project and grants that Subject the Owner role.
6. Writes `.workaholic.env` in the exact current working directory.
7. Adds that file to `.git/info/exclude` when appropriate.
8. Returns immediately.

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
    def context(...): ...
    def create_project(...): ...
    def bind_project(...): ...
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

WORKAHOLIC_PROFILE=local
WORKAHOLIC_INSTANCE_ID=ins_01K9M2R6
WORKAHOLIC_PROJECT_ID=prj_01K9M3A8
WORKAHOLIC_PROJECT_KEY=ACME

WORKAHOLIC_WORKSPACE_ROOT=.
```

The file is generated by `workaholic up` and by:

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

Instead, `WORKAHOLIC_PROFILE` refers to a trusted user-level profile. Phase 2
supports only embedded SQLite profiles in a bounded regular non-symlink
`profiles.toml` file in the operating system's Workaholic configuration
directory:

```toml
version = 1
default_profile = "local"

[profiles.local]
mode = "embedded"
data_directory = "/absolute/path/to/workaholic-data"
```

`WORKAHOLIC_CONFIG_DIR` may select an absolute trusted configuration directory.
If `profiles.toml` is absent, the built-in `local` profile uses the existing
absolute `WORKAHOLIC_DATA_DIR` override or the platform user-data default.
Profile names match `[a-z][a-z0-9_-]{0,31}` and map one-to-one to canonical
data directories.

The file allows only integer `version = 1`, optional `default_profile`, and
`[profiles.NAME]` tables containing exactly `mode = "embedded"` and one
absolute `data_directory`. Unknown keys, duplicate semantic values, unsupported
versions, unsafe files, relative paths, non-embedded modes, URLs, credentials,
and Token fields fail explicitly.

Remote profile URLs and credential references begin with authenticated remote
operation in Phases 5 and 6. Phase 2 neither accepts nor reads them.

### Context resolution

Phase 2 resolves a profile in this order:

1. explicit `--profile`;
2. trusted `WORKAHOLIC_PROFILE`;
3. the discovered `.workaholic.env`;
4. configured `default_profile`;
5. built-in `local`.

Project selection then uses explicit `--project`, followed by discovered
context. An explicit Project must be authorized and belong to the already
resolved embedded Instance. It cannot select another profile or Instance. A
missing explicit key returns `PROJECT_NOT_FOUND`; absence of both an explicit
selector and Workspace context returns `CONTEXT_NOT_FOUND`. Commands such as
`project create` and `project list` require only an initialized profile.

Discovery begins at the canonical physical current directory and walks every
physical parent through the filesystem root. Git repository and worktree
boundaries do not stop it. The nearest context file is authoritative. If that
file is malformed, unreadable, unsafe, or unsupported, resolution fails and
does not fall back to a parent.

The context source must be a bounded regular non-symlink file. Its relative
Workspace root resolves from the context file's directory to an existing
directory and must remain contained within that directory after lexical and
symlink resolution. The parser accepts a strict allowlist of keys, never invokes
a shell or performs substitution, and validates the profile, Instance,
Project, and Project key against authoritative trusted configuration and
persistent state before an operation.

A diagnostic command exposes the effective context:

```bash
workaholic context --json
```

```json
{
  "schema": "workaholic.cli/v1",
  "ok": true,
  "data": {
    "mode": "embedded",
    "profile": "local",
    "schema_version": 2,
    "instance": {
      "id": "ins_01K9M2R6"
    },
    "project": {
      "id": "prj_01K9M3A8",
      "key": "ACME",
      "name": "Acme"
    },
    "workspace_root": "/work/acme",
    "subject": {
      "id": "sub_01K9P1",
      "kind": "human",
      "display_name": "Local operator",
      "is_instance_admin": true,
      "project_role": "owner"
    },
    "context_source": "/work/acme/.workaholic.env"
  }
}
```

When explicit Project selection succeeds without discovered context,
`workspace_root` and `context_source` are JSON `null`. Their presence never
reveals a profile file or storage path.

`workaholic project bind KEY [PATH]` defaults to the physical current directory.
Writing the same authoritative binding again is a successful no-op. A different
valid binding fails with `WORKSPACE_BINDING_CONFLICT` unless `--replace` is
explicit. Replacement may atomically replace only a valid regular context file
that did not change between validation and replacement. It never follows or
replaces a symlink, directory, malformed file, or concurrently changed file.
Only after a durable context write may the command update a safe conventional
`.git/info/exclude`; it never changes shared `.gitignore`.

### Delivery boundary for local context

The historical Phase 1 baseline resolved only the exact path
`<current-working-directory>/.workaholic.env`. It does not search a parent
directory, load a user configuration file, or permit an arbitrary profile definition.
`WORKAHOLIC_PROFILE=local` selects the built-in embedded SQLite profile.

The Phase 2 foundation extends this baseline with upward discovery and
configurable trusted embedded profiles using the exact resolution rules above.
It introduced disposable SQLite schema version `2`, named Projects, explicit
same-Instance Project selection, and all-Project Task listing. That layout
rejected schema version `1` unchanged. Phase 3 retains those context rules while
replacing the store with schema version `3` and the complete Human Task
lifecycle. Version `2` is rejected unchanged, and there is no migration or
conversion path.

Remote profiles, endpoints, credentials, Tokens, `RemoteSession`, and network
transport remain deferred to Phases 5 and 6. The repository-local file remains
untrusted and never controls storage, credentials, or endpoints.

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
| Claim             | Current exclusive, expiring Task ownership       |
| Attempt           | One agent’s leased execution attempt             |
| Result            | Structured submitted outcome and review          |
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
    AND (available_at is absent OR available_at <= now)
    AND no active claim
```

```text
running =
    state == open
    AND current claim lease has not expired
```

`scheduled` means an otherwise open Task has a future `available_at`.
`awaiting_review` means stored state `review`. Phase 3 has no Claims, so
`running` and `stale` are always false until expiring Human and Agent Claims
arrive in Phase 4.

A minimal task looks like:

V1 does not model a parent/child Task hierarchy. Decomposition creates ordinary
Tasks and connects them with explicit same-Project dependencies when ordering
or completion constraints apply. Attributable creation events and Results
preserve why follow-up work exists without introducing a second relationship
model. Typed hierarchical relationships remain outside v1 until real workflows
demonstrate a separate need beyond dependencies and provenance.

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
  "approval": "human",

  "depends_on": ["tsk_01K9P..."],
  "blocking_reason": null,
  "current_result_id": null,

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

  "version": 4,

  "created_by": "sub_01K9...",
  "created_at": "2026-07-29T09:00:00Z",
  "updated_at": "2026-07-29T09:20:00Z"
}
```

The Phase 1 creation boundary trims and validates a title of 1–200 characters
and an objective of 1–4,000 characters. When omitted, the objective defaults
to the title. Priority is an integer from 0 through 100 and defaults to `50`;
state defaults to `open`; the initial optimistic version is `1`. Version
increments and stale-update rejection begin with the update commands in
Phase 3.

Every Phase 3 mutation of an existing Task supplies a positive expected
version at the Session, application, and persistence boundaries. Success
increments the version exactly once, even when the semantic operation appends
multiple events. A stale version returns `VERSION_CONFLICT` without changing
the Task, Result, dependency graph, TaskEvents, or idempotency state. Neither
the application nor an official client refreshes and silently retries.

Generic Task update changes only title, objective, priority, availability,
approval requirement, the complete ordered acceptance set, or the complete
ordered context-reference set. It cannot accept state, dependencies, blocking
reason, Result, version, identity, actor, request, event, or timestamp fields.
Those values belong to explicit semantic operations.

Phase 1 task listing is project-scoped and ordered by task number ascending.
It uses an opaque project-bound cursor, defaults to 100 records, and accepts a
maximum limit of 500. Reads do not mutate domain state.

V1 supports blocking dependencies only within one project.

A dependency is satisfied only when it is `done`. Dependency edges are unique,
directed, same-Project, and acyclic; a Task cannot depend on itself. A cancelled
dependency makes the dependent Task non-ready and surfaces an
`UNSATISFIABLE_DEPENDENCY` reason until an operator changes the dependency
graph. Changing an edge versions only the dependant Task. Prerequisite state
changes never rewrite dependant rows.

Ready ordering is priority descending, availability ascending with absent
availability first, then Task number ascending. An all-Project view inserts
immutable Project key before Task number as the final tie-breaker.

## 8. Claims, Attempts, and Leases

The owner-approved Phase 4 model is recorded in
[ADR 0012](adr/0012-phase-four-local-claim-and-execution-model.md).

A task assignment is an exclusive, expiring Claim rather than a permanent
assignee:

```json
{
  "task_uid": "tsk_01K9Q...",
  "subject_id": "sub_01K9A...",
  "attempt_id": "atm_01K9R...",
  "claimed_at": "2026-07-29T14:15:00Z",
  "lease_expires_at": "2026-07-29T14:30:00Z"
}
```

A Human Claim has `attempt_id = null` and a longer Lease window. An Agent Claim
has a non-null current Attempt ID and a shorter Lease window. At most one
unexpired Claim owns a Task. An unexpired Claim prevents every non-owner Task
mutation while reads remain available.

Human claiming is optional and targets one ready Task. The Human owner may
update, block, unblock, change dependencies, release, cancel, or submit without
handling an Attempt ID. Definition, block/unblock, and dependency mutations
retain the Claim; release, expiry, cancellation, and submission end it.

Agent claiming pulls the highest-ranked ready Task and creates an Attempt:

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

Agent claiming a task atomically:

1. Expires any stale Claim that affects selection.
2. Selects the highest-ranked ready task.
3. Creates a new Attempt and Agent Claim.
4. Associates both with the task.
5. Appends a `task_claimed` event.
6. Commits the operation.
7. Returns the task packet and attempt ID.

Default task ordering is deterministic:

```text
priority descending
available_at ascending
task number ascending
```

A Human `task renew` and Agent `task heartbeat` share one renewal operation.
Renewal extends the Lease only when:

* the active Subject owns the Claim;
* any supplied Attempt ID matches the current Agent Claim;
* the Lease remains current and unexpired.

Human renewal supplies no Attempt ID. Agent renewal requires the current
Attempt. Repeating `task claim` for an already owned Task returns the current
Claim without extending it, and normal reads and mutations never renew a Claim
implicitly.

An Agent owner may heartbeat, report progress, release, or submit. It cannot
redefine, block, cancel, or change dependencies on its claimed Task. Workaholic
AI has no force-interrupt command: an operator must stop or coordinate with the
external process, then wait for release or expiry before mutating its Task.

Attempt states are exactly `active`, `released`, `expired`, and `submitted`.
The last three are terminal and populate `ended_at`. Submission always ends
the Claim and moves the Attempt to `submitted`, including when the Task enters
review. Approval and rejection operate on the Result and never revive the old
Attempt.

A stale process cannot submit using an old Attempt ID, even when the same Agent
identity later reclaims the Task.

Correctness does not depend on a background scheduler. Lease validity uses
authoritative transaction time and the half-open rule
`now < lease_expires_at`. Claims and writes evaluate expiry transactionally.
Pure reads only project an expired stored Claim as stale and non-owning; they
never materialize expiry or append events. A server may perform housekeeping,
but it is an optimization rather than a requirement.

Phase 4 local Human and Agent commands use the sole embedded bootstrap Subject.
Human command shape and null Attempt attribution distinguish Human Claims;
Attempt identity distinguishes Agent processes. Distinct Agent Subjects,
Tokens, grants, and authenticated ownership arrive in Phase 5.

Claim, renewal, heartbeat, progress, release, and expiry do not increment the
Task version. Agent claim returns the current version, and Agent submission
requires that expected version plus the current Attempt. Successful Human or
Agent submission increments the Task version once.

Phase 4 Lease text matches `^[1-9][0-9]*(s|m|h|d)$`. Human claim and renewal
default to `8h` and accept `1m` through `30d`; Agent claim and heartbeat
default to `15m` and accept `1s` through `24h`. Renewal derives expiry from
authoritative transaction time rather than extending the prior expiry.

An expired stored Claim may make a Task both `ready` and `stale`. The next
successful write that needs the Task materializes expiry, removes the Claim,
sets an Agent Attempt to `expired` with `ended_at = lease_expires_at`, and
appends `claim_expired`. A stale Agent request returns `LEASE_LOST` and does
not commit its requested operation.

Structured Agent progress is bounded append-only TaskEvent data. It contains
at least one optional message of at most 4,000 characters, integer percentage
from 0 through 100, or at most 50 ordered observations. Observation kinds are
`note`, `risk`, `blocker`, and `question`; a blocker observation does not
change Task state. One request appends `progress_reported` followed by its
`observation_added` events and does not change Task version or `updated_at`.

## 9. Results and review

Submission returns structured evidence. A summary is optional for a Human
manual submission:

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

Attempts are Agent-only. A Phase 3 Human submits directly as the authenticated
Subject, and the Result records `attempt_id = null`. A Human comment and a
structured Result file are independently optional. A Phase 4 Human may first
hold a Claim whose Attempt is also null. Phase 4 Agent submission uses the same
Result model but requires the current owned Attempt and expected Task version;
an Agent cannot submit with a null Attempt.

Proposed follow-ups are inert Result data. They do not create Tasks,
dependencies, or another relationship model.

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

Human submission requires `open` and satisfied dependencies. Availability is a
scheduling constraint and does not prohibit a deliberate Human submission.
Rejection retains the rejected Result for audit, clears it as the current
review selection, and returns the Task to `open` with a typed review event. An
Agent Attempt is already terminal at submission, so rejection never revives or
closes it. The returned Task must be claimed again with a new Attempt.

No-approval submission appends `result_submitted` then `task_completed`.
Approval appends `review_approved` then `task_completed`. Each pair belongs to
one semantic mutation and increments the Task version once.

## 10. Events

Events are append-only and typed:

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

Phase 4 extends that set with Claim and Agent execution events:

```text
task_claimed
claim_renewed
claim_released
claim_expired
progress_reported
observation_added
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

JSON and non-interactive clients read bounded snapshot pages and poll
explicitly with the last cursor. Human `--follow` is a presentation convenience
and does not define a streaming JSON contract.

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
claim_task
claim_next_task
renew_claim
release_claim
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

Phase 4 used disposable SQLite schema version `4` for lifecycle state,
dependencies, Claims, Attempts, Leases, Results, reviews, and expanded events.
The current Phase 5 implementation uses exact schema version `5` and adds
Subjects, Tokens, ProjectGrants, AuditEvents, and authenticated attribution. It
rejects version `4` and every other unsupported version unchanged and provides
no migration, conversion, import, export, or automatic reset.

### PostgreSQL backend

PostgreSQL is used behind a shared server. Its adapter uses database-native transactional locking for atomic claiming and task-number allocation.

### Backend conformance

The same conformance suite runs against every backend and verifies:

* unique project task numbers;
* no double claims;
* correct Human and Agent Claim expiry;
* non-owner mutation lock rejection;
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

The exact Phase 5 security choices in this section are fixed by
[ADR 0013](adr/0013-phase-five-token-and-authorization-model.md).

### Subjects

```json
{
  "id": "sub_01K9...",
  "instance_id": "ins_01K9...",
  "kind": "agent",
  "handle": "billing-code-agent",
  "display_name": "Billing code agent",
  "enabled": true,
  "is_instance_admin": false,
  "version": 1
}
```

Subject kinds:

```text
human
agent
```

Every independently operating Human or Agent receives one Subject. Handles
match `^[a-z][a-z0-9-]{1,62}$`, are unique within an Instance, and are immutable
and non-reusable. Display names are presentation data and may change. Subject
kind is immutable and does not itself grant permission. Shared "all agents"
credentials are not a supported operating model because they make Claim
ownership, revocation, and audit history ambiguous.

### Tokens

Tokens are independently expiring and revocable high-entropy bearer
credentials. One Subject may own several Tokens; Instance administrators issue
Tokens, while a Subject may inspect and revoke its own Token metadata. Subjects
and Tokens are not deleted in v1.

```json
{
  "id": "tok_01K9...",
  "subject_id": "sub_01K9...",
  "status": "active",
  "created_at": "2026-09-01T00:00:00Z",
  "expires_at": "2026-10-01T00:00:00Z",
  "revoked_at": null
}
```

The canonical raw form is `<token-id>.<secret>`. The secret is unpadded
URL-safe base64 for 32 cryptographically random bytes. Workaholic stores only a
lowercase SHA-256 digest of the complete canonical Token and compares it in
constant time after an indexed Token-ID lookup. A raw Token is revealed only
through an explicit protected output boundary during provisioning and cannot
be recovered from persistence.

For Humans, credentials are stored by trusted profile in the operating-system
credential store where available, with a protected `credentials.toml` fallback
under a dedicated account-only configuration directory. An expected Instance
and Subject identity is stored with the credential so profile redirection fails
closed. A keyring operation failure never silently downgrades to a file.

For Agents, credentials are supplied through:

```text
environment variables
mounted secrets
orchestrator secret injection
```

`WORKAHOLIC_TOKEN` and `WORKAHOLIC_TOKEN_FILE` are trusted process inputs and
are mutually exclusive. An explicitly selected source is authoritative: a
malformed, expired, revoked, or otherwise invalid explicit Token never falls
back to a stored Human credential. Tokens never appear in `.workaholic.env`,
`profiles.toml`, task content, Results, events, normal logs, or normal command
arguments.

### Roles

Project roles are cumulative in the exact order
`viewer < agent < operator < owner`:

| Role | Additional permission |
| --- | --- |
| Viewer | Read the Project, Tasks, Results, Claims, Attempts, and TaskEvents |
| Agent | Pull, heartbeat, report progress, release, and submit Agent work |
| Operator | Create and mutate Tasks, use Human Claims, submit, and review |
| Owner | Assign, replace, list, and revoke ProjectGrants |

One Subject has at most one current ProjectGrant per Project. Granting another
role replaces that row through optimistic concurrency. Agent execution also
requires Agent Subject kind; Human Claim, renew, and release also require Human
Subject kind. Other Operator operations are role-controlled regardless of kind.

Instance administrator is a separate Instance-wide flag. It authorizes Project
creation, Subject lifecycle, administrator changes, and Token lifecycle, but it
does not reveal or mutate ordinary Project data without a ProjectGrant. The
Instance must retain an enabled administrator and every Project must retain an
enabled Owner; grant changes and Subject disablement enforce both invariants in
the same transaction.

Every operation authenticates one Token and yields an internal actor containing
Instance, Subject, kind, and Token IDs. Raw credential material ends at the
authentication boundary. Persistence revalidates the active Token, enabled
Subject, selected Instance, and required ProjectGrant in the same transaction
as every read or mutation, so a concurrent revocation, disablement, or grant
removal fails closed.

A current Claim remains an exclusive mutation lock. Administrator or Owner
authority cannot override a foreign Claim. Revoking a Token or disabling its
Subject stops new authenticated operations immediately but does not
force-release a Claim or interrupt a process. Claim ownership belongs to the
Subject, so another active Token for that same Subject may continue the exact
current Attempt.

### Local bootstrap

The first local `workaholic up`:

* creates one real Human Subject with handle `local-operator` and display name
  `Local operator`;
* marks that Subject as the Instance administrator;
* grants that Subject the Owner role on the bootstrapped Project;
* creates a pending Human administrator Token;
* stores its raw credential through the selected Human credential store; and
* activates the Token only after credential storage succeeds.

Normal commands, including `up` against an initialized store, then require one
valid Token. `auth recover-local` is the only tokenless recovery route. It is
restricted to embedded mode under the trusted operating-system account,
requires explicit confirmation of the Instance and bootstrap Subject, revokes
that Subject's existing Tokens, and installs one fresh Human credential. It
does not change Projects, Tasks, Claims, Attempts, grants, or Subject state.

Phase 1 through Phase 4 select the attributed bootstrap Subject without a
Token. Phase 5 extends that record with credentials and does not replace it
with an anonymous or placeholder actor.

Historically, Phase 2 does not create a Token, use an operating-system
credential store, or write credentials to `profiles.toml`; Phase 3 and Phase 4
retain that delivery boundary.

### Audit

Task mutations continue to append TaskEvents with authenticated Subject,
request, and optional Attempt attribution. Instance bootstrap, Project
creation, Subject and administrator lifecycle, ProjectGrant changes, and Token
issue/revocation append a separate ordered `AuditEvent` in the same transaction
as their state change. Audit payloads may contain stable identifiers and
non-secret change facts, but never raw Tokens, Token hashes, credential paths,
environment values, or keyring locators.

Tokenless bootstrap and local recovery are self-attributed to the bootstrap
Human and use null actor Token identity. Every authenticated administrative
event records its actor Token ID.

### Shared bootstrap

An empty server store requires a bootstrap credential supplied through trusted deployment configuration. Once the first administrator exists, normal identity commands take over:

```bash
workaholic auth create-human alice
workaholic auth create-agent code-agent-3
workaholic auth grant code-agent-3 agent --project ACME
workaholic auth create-token code-agent-3 --token-file /secure/agent.token
workaholic auth revoke-token tok_01K9...
```

Remote bearer-token traffic must use HTTPS, normally through an ingress or reverse proxy.

## 13. CLI contract

The CLI is simultaneously:

* the human command interface;
* the supported agent automation interface;
* the entry point for local administration.

Phase 2 implements the public automation contract for `up`, `status`,
`context`, `project create`, `project bind`, `project list`, `task add`,
`task list`, and `task show`, including JSON-only stdout, non-interactive
operation, stable error identifiers, exit-code categories, explicit
profile/Project selection, and documented mutation idempotency. Phase 3 adds
Human lifecycle mutations, structured Task and Result input, derived views,
and TaskEvent history. Phase 4 extends that contract with Agent execution.

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
workaholic task renew
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

The accepted Phase 2 additions have these exact signatures:

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

Every command retains `--json` and `--non-interactive`; existing Task options
remain available. `project create` is idempotent only with an explicit
idempotency key. `project bind` is naturally idempotent for an equivalent
binding. `--project` and `--all-projects` are mutually exclusive.

Serialized Projects add required `name` while retaining immutable `id` and
`key`. Project lists order by key, one-Project Task lists order by Task number,
and all-Project lists order by `(project key, task number)`. Phase 2 cursors use
the `v2.` prefix and bind their canonical payload to profile, Instance, Subject,
selection kind, selected Project when present, and last ordering position.
Cross-binding or noncanonical cursor reuse returns `INVALID_INPUT`.

The accepted Phase 3 lifecycle signatures are:

```text
workaholic task add TITLE [--objective TEXT] [--priority INTEGER]
  [--available-at TIMESTAMP] [--approval none|human]
  [--input-file PATH|-] [--project KEY] [--idempotency-key KEY]
workaholic task update TASK
  [--title TEXT] [--objective TEXT] [--priority INTEGER]
  [--available-at TIMESTAMP | --clear-available-at]
  [--approval none|human] [--input-file PATH|-]
  [--expected-version INTEGER] [--idempotency-key KEY]
workaholic task block TASK --reason TEXT
  [--expected-version INTEGER] [--idempotency-key KEY]
workaholic task unblock TASK
  [--expected-version INTEGER] [--idempotency-key KEY]
workaholic task add-dependency TASK PREREQUISITE
  [--expected-version INTEGER] [--idempotency-key KEY]
workaholic task remove-dependency TASK PREREQUISITE
  [--expected-version INTEGER] [--idempotency-key KEY]
workaholic task submit TASK [--comment TEXT] [--result-file PATH|-]
  [--expected-version INTEGER] [--idempotency-key KEY]
workaholic task approve TASK [--comment TEXT]
  [--expected-version INTEGER] [--idempotency-key KEY]
workaholic task reject TASK --reason TEXT
  [--expected-version INTEGER] [--idempotency-key KEY]
workaholic task cancel TASK [--reason TEXT]
  [--expected-version INTEGER] [--idempotency-key KEY]
workaholic task events TASK [--after INTEGER] [--limit INTEGER] [--follow]
```

All retain JSON, non-interactive, and applicable explicit Project selection.
Every existing-Task mutation has an optional CLI spelling for
`--expected-version` only to support a real terminal Human: when absent, the
CLI reads the current Task, shows its key, state, version, and intended action,
then asks once before sending that exact version. JSON, non-interactive, and
non-terminal invocation require the option. A conflict is returned without
automatic retry.

`task list --view` accepts `all`, `ready`, `scheduled`, `blocked`, `review`,
`done`, or `cancelled`. Phase 3 view cursors use the `v3.` prefix and bind the
profile, Instance, Subject, selection, view, and view-specific ordering
position. `task events` JSON output is a bounded snapshot page. Human
`--follow` cannot be combined with JSON or non-interactive operation.

Phase 2 adds `PROFILE_NOT_FOUND`, `PROFILE_INVALID`,
`PROFILE_UNSUPPORTED`, `PROJECT_NOT_FOUND`, and
`WORKSPACE_BINDING_CONFLICT` to the established CLI exit categories. The
normative [CLI automation contract](cli-contract.md) owns their fixed safe
messages and command-specific surfaces.

Phase 3 adds `VERSION_CONFLICT`, `INVALID_TRANSITION`,
`DEPENDENCY_CONFLICT`, `DEPENDENCY_CYCLE`,
`UNSATISFIABLE_DEPENDENCY`, and `RESULT_INVALID`. These are semantic errors,
not adapter or SQLite exceptions.

The accepted Phase 4 Claim signatures are:

```text
workaholic task claim TASK [--lease DURATION]
  [--project KEY] [--idempotency-key KEY]
workaholic task renew TASK [--lease DURATION]
  [--project KEY] [--idempotency-key KEY]
workaholic task release TASK
  [--project KEY] [--idempotency-key KEY]

workaholic task claim [--lease DURATION]
  [--project KEY] [--idempotency-key KEY]
workaholic task heartbeat TASK --attempt ATTEMPT [--lease DURATION]
  [--project KEY] [--idempotency-key KEY]
workaholic task progress TASK --attempt ATTEMPT --input-file PATH|-
  [--project KEY] [--idempotency-key KEY]
workaholic task release TASK --attempt ATTEMPT
  [--project KEY] [--idempotency-key KEY]
workaholic task submit TASK --attempt ATTEMPT --expected-version INTEGER
  --result-file PATH|- [--project KEY] [--idempotency-key KEY]
```

An explicit Task operand selects the Human Claim path and returns null Attempt
attribution. Omitting it selects the Agent pull-next path and returns a new
Attempt. Human `renew` and Agent `heartbeat` share one renewal operation;
Human commands never require an Attempt ID. Capability filtering is not part
of v1.

Phase 4 embedded Human and Agent operations reuse the bootstrap Subject.
Attempt identity distinguishes Agent processes, while different Human
identities and authenticated Subject ownership arrive in Phase 5.

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
  --expected-version 4 \
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

The root `workaholic.__main__` module is the only exhaustive-layer exclusion.
It is a console entry-point shim that delegates immediately to
`workaholic.composition` and owns no application behavior.

CLI and TUI presentation modules may import Session interfaces. They must not
directly import application services, persistence adapters, private protocol
models, transport clients, or server modules. Indirect dependencies reached
through a Session implementation are expected and do not make presentation
code an adapter composition root.

Composition is restricted to explicit adapter boundaries:

* `workaholic.composition` wires the CLI process to `LocalSession`, the
  application services, exact-directory context, SQLite, clock, and identifier
  adapters;
* `session.remote` may wire Session operations to the official transport
  client;
* `server.main` may wire server routes, application services, authentication,
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

1. **A task is claimed atomically.** Two Humans or Agents cannot successfully claim the same ready task.
2. **Task identity is stable.** `ACME-142` never refers to another task.
3. **Lease expiry needs no daemon.** Expired Human and Agent Claims are invalid based on timestamps and transactional checks.
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
14. **A current Claim is an exclusive mutation lock.** Non-owner writes cannot change its Task.
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
* Atomic Human and Agent Claims with expiring Leases.
* Agent Attempts, Human renewal, heartbeats, releases, and retries.
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
* Capability-based Task scheduling and heterogeneous Agent queue routing.
* Horizontal server scaling guarantees.
* Hosted Workaholic AI service.

## Final architecture statement

> **Workaholic AI is a CLI-first, agent-native task coordination engine that runs directly over local storage for solo developers or behind an authenticated server for distributed teams. Projects are discovered from safe, nontracked working-directory metadata; tasks have stable project-prefixed identities; work is coordinated through atomic exclusive Claims and Agent Attempts; and humans, agents, the future TUI, and later native clients all share one domain and session model.**

The v1 architecture supports immediate `uvx` adoption without committing the product to Python-based client distribution permanently, while preserving a direct path to a containerized server and self-contained CLI/TUI client.

[1]: https://docs.astral.sh/uv/guides/tools/ "Using tools | uv"
[2]: https://docs.astral.sh/uv/guides/tools/?utm_source=chatgpt.com "Using tools | uv - Astral Docs"
