# Workaholic AI Persistence Contract

- Status: Accepted v1 contract with Phase 2 SQLite implementation
- Decision date: 2026-07-29
- Contract scope: Observable semantics shared by JSON, SQLite, and PostgreSQL
- Public API status: Internal architecture contract, not a third-party API

## Current implementation notice

This document specifies persistence semantics implemented incrementally across
v1. The current `0.2.0a1` development package implements the Phase 2 SQLite
adapter and disposable schema version `2`, including multiple Projects,
independent Task-number allocation, all-Project ordering, and selection-bound
cursors. JSON and PostgreSQL adapters and schema migration remain unavailable.

An unsupported alpha store, including Phase 1 schema version `1`, is rejected
unchanged. Preserve any needed information outside Workaholic, verify the
exact disposable profile data and Workspace contexts, remove those alpha
artifacts, and run `workaholic up` again. There is no in-place reset, automatic
migration, backend conversion, import, or export command in Phase 2.

## Normative language

The terms **must**, **must not**, **should**, and **may** define conformance
requirements. Physical schemas, SQL, lock primitives, filenames, serialization
helpers, and driver APIs are intentionally outside this contract.

Canonical terms are defined in the [glossary](glossary.md).

## Contract boundary

Persistence exposes semantic, transaction-scoped operations to the application
layer. It does not expose generic table, document, or key-value CRUD to CLI,
Session, protocol, or presentation code.

An adapter may choose backend-native implementation details, but every adapter
must produce identical externally observable outcomes for:

- stable identifiers and task-number allocation;
- Task lifecycle and optimistic versions;
- readiness ordering and atomic claims;
- Attempt ownership and Lease expiry;
- Results and review transitions;
- idempotent mutations;
- append-only attributable TaskEvents;
- schema-version validation;
- queries, ordering, and pagination;
- committed and rejected failure behavior.

The persistence interface and physical layouts are internal and may be
refactored when this semantic behavior remains unchanged.

## Supported adapter roles

| Adapter | Embedded LocalSession | Shared server | V1 role |
| --- | --- | --- | --- |
| JSON | Yes | Yes, one server process | Inspection, demos, and small use |
| SQLite | Yes | Yes, one server process | Default local and small-team use |
| PostgreSQL | No | Yes | Recommended distributed-team store |

PostgreSQL is never accessed directly by CLI clients.

## Phase 1 SQLite baseline

Phase 1 implements only the embedded LocalSession SQLite adapter and schema
version `1`. Its empty-store bootstrap atomically persists:

- one Instance;
- one enabled Human Subject named `Local operator`;
- Instance administrator status for that Subject;
- one Project;
- one Owner ProjectGrant for that Subject and Project;
- the next task-number allocation state.

Bootstrap does not persist a Token and does not append a TaskEvent: TaskEvents
require a Task identity and bootstrap does not invent one. The first accepted
Task creation atomically allocates the Project number, creates the Task at
version `1`, appends one attributable `task_created` event with a generated
request identity, and records the idempotent outcome when a caller key is
present.

The Phase 1 adapter provides semantic operations for bootstrap, project
listing, Task creation, Task lookup, and Task listing. Task listing is scoped
to one Project, ordered by task number ascending, and paged with an opaque
project-bound cursor. The default limit is 100 and the maximum is 500. These
reads do not change persisted state.

Phase 1 supports optional durable idempotency for `up` and `task add`.
Phase 2 extends context discovery, Phase 3 adds Task updates and their version
increments, Phase 4 adds Attempts and Leases, and Phase 5 adds Tokens and
general identity management. These deferrals do not weaken the Phase 1
Subject, ProjectGrant, TaskEvent, schema-validation, or atomicity guarantees.

## Phase 2 SQLite contract

Phase 2 replaces the disposable Phase 1 layout with clean-store SQLite schema
version `2`. It does not migrate or reinterpret schema version `1`. Before any
normal read or mutation, exact version `2` is required. A version `1`, missing,
malformed, or future store returns `SCHEMA_UNSUPPORTED` without changing any
byte, schema object, allocation value, or domain record.
It provides no migration, conversion, import, export, or automatic reset.

One initialized version `2` store represents one embedded Instance and includes:

- one enabled bootstrap Human Subject and Instance-administrator status;
- one or more named Projects;
- one Owner ProjectGrant for the bootstrap Human in every Project created in
  Phase 2;
- independent next Task-number allocation state for each Project;
- durable idempotency outcomes for `up`, `project.create`, and `task.add`.

Every persisted Project contains immutable `id`, `instance_id`, `key`, required
normalized `name`, and `created_at`. Keys remain unique within an Instance and
cannot be reused. Names contain 1 through 200 Unicode characters after
normalization but are not identifiers.

Profile resolution is outside persistence. A trusted embedded profile selects
one exact database path before the repository opens. Repository-controlled
context can never supply or redirect that path. The cumulative internal SQLite
adapter is named `SQLiteRepository`; the Phase 1-specific adapter name is not a
compatibility surface.

### Project creation transaction

One `project.create` transaction:

1. verifies the target Instance is the selected initialized Instance;
2. verifies the creator is the enabled bootstrap local Human and Instance
   administrator;
3. validates and reserves the immutable Project key and normalized display
   name;
4. creates the Project and its independent Task-number allocation state;
5. grants the creator Owner in that Project;
6. records the optional idempotency fingerprint and committed Project-plus-
   grant outcome;
7. commits one atomic outcome.

Project creation never fabricates a Task or TaskEvent. Equivalent replay with
the same idempotency key returns the original Project and grant. Different
input returns `IDEMPOTENCY_CONFLICT`. An existing or reserved key returns
`PROJECT_KEY_CONFLICT`. Any rejected, failed, or rolled-back creation leaves no
Project, ProjectGrant, idempotency outcome, or visible key reservation.
Concurrent same-key creation commits once; concurrent distinct-key creation
may commit both without sharing allocation state.

### Phase 2 queries and cursors

Project lookup is constrained by `instance_id`, `subject_id`, immutable key,
and an active ProjectGrant. Project lists include only authorized Projects in
the selected Instance and order by Project key ascending.

One-Project Task lists retain Task-number ascending order. Instance-scoped
all-Project Task lists include only Projects authorized for the Subject and
order by `(project key, task number)` ascending. Reads remain non-mutating.

Phase 2 Task cursors use exact prefix `v2.` with an unpadded URL-safe base64
canonical JSON payload. The closed payload binds integer version `2`, trusted
profile name, Instance identity, Subject identity, selection kind, selected
Project identity when present, and the last ordering position. Project scope
records the last Task number. All-Project scope records the last
`(project key, task number)` tuple.

Malformed, padded, noncanonical, unsupported-version, cross-profile,
cross-Instance, cross-Subject, cross-Project, or cross-selection reuse is an
`INVALID_INPUT` outcome. Traversal of unchanged records neither duplicates nor
omits a Task.

## Store opening and schema version

Every store records a backend-independent schema version. Before the first
normal read or mutation, an adapter must:

1. open the backend without changing domain state;
2. read and validate the schema version;
3. either accept the exact supported version or fail explicitly;
4. leave an unsupported store unchanged.

Missing, malformed, newer, or older unsupported versions must not be guessed,
partially interpreted, or automatically migrated.

Initialization of an empty store is an explicit semantic operation. It creates
one valid schema version atomically or fails without presenting a partially
initialized Instance.

## Transaction invariants

One semantic mutation is one atomic backend transaction. A successful
transaction commits all of:

- validated state changes;
- optimistic version increments;
- Attempt and Lease changes where applicable;
- required TaskEvents;
- ordered event cursor allocation;
- idempotency record and outcome where applicable.

A rejected or failed mutation commits none of them. No adapter may report
success before the durable outcome required by that backend is complete.

All preconditions that protect concurrency are checked in the same transaction
as the mutation. Reading state in one transaction and relying on it for an
unconditional later write is nonconforming.

## Identity and allocation

### Project keys

Project keys are uppercase, immutable, unique within an Instance, and never
reused after archival. Creating a conflicting Project fails without allocating
another Project with that key.

### Task identities

Creating a Task atomically:

1. verifies the target Project;
2. allocates the next monotonically increasing Project task number;
3. creates a globally unique Task UID;
4. derives the stable human key such as `ACME-42`;
5. creates the initial Task at version `1` and state `open`;
6. appends the attributable `task_created` TaskEvent;
7. commits one outcome.

Concurrent successful creates in one Project receive distinct numbers. Numbers
and keys are never reused. Gaps are valid and must not be interpreted as
missing or corrupt Tasks.

Relationships persist Task UIDs as canonical identities. A Task cannot change
Projects.

## Time

The application supplies one authoritative UTC transaction time to operations
that depend on time. The adapter uses that value consistently for state,
TaskEvents, availability, and Lease decisions within the transaction.

Remote operation uses server-authoritative time. Client clocks never decide
Lease validity. Adapters persist timestamps in a form that round-trips to
RFC 3339 UTC values without changing their ordering or meaning.

## Optimistic Task versions

Every mutable Task has an integer optimistic version. A conditional update
supplies the expected version.

Creation initializes the version to `1`. Version increments and conflict
checks begin when versioned update commands arrive in Phase 3; creation itself
does not perform a synthetic increment.

- If the expected version matches, the adapter validates the transition,
  commits the mutation and TaskEvent, and increments the version exactly once.
- If it does not match, the adapter returns a version-conflict outcome and
  leaves state and events unchanged.
- Idempotent replay of an already committed mutation returns its recorded
  outcome rather than incrementing the version again.

Adapters must not implement last-write-wins behavior for versioned updates.

## Readiness and atomic claims

Readiness is derived from Task state and related data. A ready Task is open,
available at the authoritative time, has satisfied blocking dependencies, and
has no current unexpired Attempt.

Default claim ordering is deterministic:

1. priority descending;
2. `available_at` ascending;
3. task number ascending.

One `claim_next_task` transaction:

1. validates the authenticated Subject's Project access and claim permission;
2. evaluates relevant expired Attempts at the transaction time;
3. selects the highest-ranked eligible Task matching documented filters;
4. verifies that no current unexpired Attempt owns it;
5. creates a new Attempt with a new identifier and Lease;
6. associates that Attempt with the Task;
7. appends any required expiry event and one `task_claimed` TaskEvent;
8. records idempotency where supplied;
9. commits and returns the claimed task packet.

Two concurrent claims cannot successfully claim the same Task. Correctness must
not depend on a background scheduler. Every reclaim creates a new Attempt,
including a reclaim by the same Agent.

## Attempt and Lease mutations

Heartbeat, progress, release, and Result submission atomically verify:

- target Project and Task;
- authenticated Subject;
- current Attempt identifier;
- Attempt ownership by that Subject;
- active status;
- Lease validity at the transaction time;
- operation-specific Task state and optimistic preconditions.

A foreign, expired, ended, or superseded Attempt returns a Lease-lost or
authorization outcome as appropriate and commits no state or TaskEvent.

A heartbeat extends only the current valid Attempt. Release ends the current
Attempt and makes the Task eligible according to normal readiness rules.
Submission records the Result and either completes the Task or moves it to
review according to its approval requirement.

An old process cannot submit through an earlier Attempt after reclaim, even
when the same Agent owns the new Attempt.

## Results and review

A successful submission stores structured Result data and external artifact
references, not artifact contents. It validates acceptance-criterion
identifiers and the current Attempt in the same transaction.

- Submission without required approval moves the Task to done.
- Submission requiring approval moves the Task to review.
- Approval moves a review Task to done.
- Rejection closes the reviewed Attempt and returns the Task to open according
  to the documented workflow.

Each accepted transition appends its required attributable TaskEvent. An invalid
transition leaves Result, Task, Attempt, version, and event state unchanged.

## TaskEvents

TaskEvents are typed, attributable, append-only records. Each committed event
contains:

- globally unique event identity;
- monotonically ordered Instance cursor;
- Task identity;
- authenticated Subject identity and kind;
- Attempt identity when applicable;
- request identity;
- event type;
- validated structured payload;
- authoritative timestamp.

Clients cannot choose actor identity, authoritative timestamp, or Instance
cursor. Mutations and their events commit atomically. An adapter must not expose
a committed state change without its required event or an event for a rolled
back change.

Reading events after a cursor returns a stable ascending sequence with
documented bounds. Event records are never updated or deleted by normal domain
operations.

## Idempotent mutations

An idempotency record is scoped to authenticated Subject, logical operation,
and caller key. It stores a canonical request fingerprint and the committed
logical outcome.

- The first valid use commits the domain mutation, TaskEvents, and idempotency
  record atomically.
- A repeat with the same semantic input returns the original outcome and emits
  no additional mutation or TaskEvent.
- Reuse with different semantic input returns an idempotency-conflict outcome
  and changes nothing.
- Concurrent first uses of the same scope produce one committed logical
  mutation.

Adapters must persist idempotency records in durable Instance state. An
in-memory process cache is not sufficient.

## Queries and pagination

Equivalent queries return equivalent records and ordering across adapters.
Every collection query defines deterministic tie-breakers and bounded page
size. Pagination must not duplicate or omit unchanged records when traversed
with a documented stable cursor.

Filters use domain meaning rather than backend expressions. Backend-specific
query syntax, row identifiers, sort order, and null behavior must not leak into
CLI JSON.

Reads must not mutate domain state except where a documented semantic operation
transactionally materializes required expiry behavior. Housekeeping may improve
performance but cannot be required for correctness.

## Semantic failure outcomes

Adapters return typed semantic outcomes rather than driver exceptions for
expected conditions, including:

- missing Instance, Project, Task, Attempt, or Subject;
- conflicting Project key;
- optimistic version conflict;
- invalid lifecycle transition;
- no eligible Task to claim;
- Lease lost through expiry, release, or supersession;
- idempotency-key conflict;
- unsupported schema version;
- authorization lookup or grant mismatch where the operation requires it.

Application boundaries map those outcomes consistently to CLI or private
protocol errors. Unexpected backend failures remain operational errors, are
redacted, and must not expose SQL, filesystem secrets, credentials, or raw
driver details through public JSON.

## Adapter-specific constraints

### JSON

JSON uses an inter-process lock and crash-safe whole-state replacement. A
mutation reads and validates current state, applies one semantic transaction,
writes and flushes a temporary file, atomically replaces the canonical state,
and then releases the lock.

### SQLite

SQLite uses short-lived connections for CLI invocations. Compound operations,
including task-number allocation, claims, events, and idempotency, use one write
transaction with bounded lock handling.

### PostgreSQL

PostgreSQL runs only behind the server and uses database-native transactions
and locking for atomic claims and allocation. Connection loss and retry behavior
must not duplicate a mutation.

These constraints do not make physical layout part of the contract.

## Conformance suite

Every supported adapter must pass the same observable-behavior suite. The suite
must cover:

- empty-store initialization and schema validation;
- unsupported schema rejection without writes;
- Project-key uniqueness and non-reuse;
- concurrent task-number allocation;
- Task UID and human-key stability;
- optimistic update conflicts;
- dependency and availability readiness;
- deterministic claim ordering;
- real concurrent double-claim prevention;
- Lease expiry without a scheduler;
- foreign, stale, and superseded Attempt rejection;
- heartbeat, release, submission, approval, and rejection transitions;
- idempotent replay, conflicting reuse, and concurrent first use;
- Task and TaskEvent atomic consistency;
- actor, request, Attempt, timestamp, and cursor attribution;
- event ordering and bounded cursor reads;
- rollback on validation and injected backend failures;
- equivalent filters, ordering, and pagination;
- authorization lookup behavior;
- crash-safe JSON replacement and supported backend-specific recovery cases.

Tests must assert domain outcomes, public error mapping, and committed state
rather than adapter internals.

## Compatibility and schema policy

Breaking schema and semantic changes are allowed through Phase 7 when reviewed
and reflected in this contract. The persisted schema freezes at the Phase 8
exit gate and is validated unchanged by the release candidate.

The formal compatibility guarantee begins at `1.0.0`. The `1.0.x` line must not
change the persisted schema. A later v1 schema change requires an accepted
upgrade policy and migration path before release.

V1 provides no automatic migrations, backend conversion, import, or export.
Unsupported stores fail unchanged. See
[ADR 0009](adr/0009-no-storage-migrations-in-v1.md).

## Related decisions and documents

- [ADR 0005: Semantic Persistence Interface](adr/0005-semantic-persistence-interface.md)
- [ADR 0008: Stable Task-Key Allocation](adr/0008-stable-task-key-allocation.md)
- [ADR 0009: No Storage Migrations in v1](adr/0009-no-storage-migrations-in-v1.md)
- [ADR 0010: Single-Process, Single-Instance Server](adr/0010-single-process-single-instance-server.md)
- [Compatibility policy](compatibility-policy.md)
- [Threat model](threat-model.md)
- [Architecture](architecture.md)
