# Phase 3 Implementation Tasks

## Purpose

Deliver the Human Workflow Alpha: a Human operator can define complete Tasks,
coordinate them with same-Project dependencies, perform every non-Agent
lifecycle transition safely, submit structured Results, review work, and read
the attributable audit history through the embedded CLI.

Tasks are ordered by dependency. Each task is independently reviewable and is
intended to be implemented by a separate developer after all listed inputs have
merged. Every task must leave the existing supported behavior, tests, and build
green.

## Repository state at planning time

The following deliverables already exist and must be extended rather than
recreated:

- Python 3.14 package metadata, locked dependencies, pre-commit hooks, strict
  linting and typing, dependency-boundary checks, least-privilege CI, package
  builds, and clean-install smoke infrastructure;
- dependency-free domain models, strict Pydantic application and Session
  boundaries, embedded `LocalSession`, the explicit composition root, and the
  cumulative SQLite repository facade;
- trusted embedded profiles, safe upward Workspace discovery, multiple named
  Projects, explicit Project selection, and all-Project Task listing;
- disposable SQLite schema version `2`, atomic Task creation, stable
  Project-local Task numbering, initial Task version `1`, one attributable
  `task_created` event, durable idempotency, and deterministic cursors;
- versioned `workaholic.cli/v1` success and error envelopes, Human-readable
  rendering, JSON-only stdout, non-interactive mode, and the Phase 2 Project
  and Task commands;
- cumulative persistence and Session contract suites, fresh-process golden
  journeys, installed-wheel tests, public documentation tests, and Phase 0-2
  clean-state gates;
- accepted architecture, persistence, security, compatibility, and CLI
  contracts, including the decision to omit parent/child Task hierarchy from
  v1.

No duplicate quality-control, package-bootstrap, licensing, community,
repository-management, profile, context-discovery, or CI-foundation task is
required for Phase 3. The existing controls remain mandatory for every task
below.

## Confirmed Phase 3 decisions

Implementation and documentation must consistently encode these owner-approved
decisions:

- Every existing-Task mutation carries an optimistic `expected_version` at the
  Session, application, and persistence boundaries. A successful semantic
  mutation increments the Task version exactly once, even when it appends more
  than one TaskEvent.
- JSON mode, `--non-interactive`, and any non-terminal invocation require an
  explicit `--expected-version`. An interactive Human may omit it; the CLI
  reads the current Task, displays the Task key, state, version, and intended
  action, then asks for confirmation before sending that exact version.
- A version conflict is returned unchanged. The CLI never refreshes and
  silently retries a rejected mutation.
- Attempts are Agent-only. A Human submits work directly with `task submit` and
  no Attempt. A Human Result stores `attempt_id = null`; Phase 4 Agent
  submission will require the current valid Attempt.
- `task submit` accepts an optional Human comment and an optional structured
  Result file. A Human may submit without either. Without required approval,
  submission changes `open` to `done`; with Human approval, it changes `open`
  to `review`.
- Completion is a semantic submission or approval outcome. Generic
  `task update` cannot change lifecycle state, satisfy review, or mark a Task
  done.
- V1 has no parent/child Task hierarchy. Decomposition creates ordinary Tasks;
  same-Project dependencies express blocking constraints, while attributable
  TaskEvents and Results preserve provenance.
- Phase 3 remains embedded, local, SQLite-only, and Human-operated. Agent
  Subjects, Attempts, Leases, claims, heartbeats, Tokens, remote profiles,
  `RemoteSession`, servers, and JSON/PostgreSQL adapters remain deferred.
- Phase 3 introduces disposable SQLite schema version `3`. Version `2` is
  rejected unchanged, with no migration, conversion, import, export, or
  automatic reset path.

## Phase 3 behavioral baseline

The implementation tasks use these concrete semantics:

- Stored Task states are `open`, `blocked`, `review`, `done`, and `cancelled`.
  `done` and `cancelled` are terminal in Phase 3.
- Generic field updates, dependency changes, and submission are allowed only
  where their explicit transition rule permits them; no command accepts a raw
  state value.
- Task fields comprise title, objective, priority, optional `available_at`,
  ordered acceptance criteria, ordered context references, approval
  requirement, same-Project dependencies, lifecycle state, optional blocking
  reason, optimistic version, and attribution timestamps.
- Approval requirement is exactly `none` or `human`. Acceptance-criterion IDs
  are stable within one Task. Context and artifact records contain references
  and integrity metadata only; Workaholic never reads or stores artifact
  contents.
- A dependency is satisfied only when the prerequisite is `done`. A cancelled
  prerequisite makes the dependant non-ready with
  `UNSATISFIABLE_DEPENDENCY`; it does not implicitly cancel or mutate the
  dependant.
- Dependencies are directed, same-Project, unique, and acyclic. A Task cannot
  depend on itself. Adding or removing an edge versions only the dependant
  Task.
- `ready` means state `open`, `available_at` absent or not in the future, every
  dependency done, and no active Attempt. Phase 3 has no Attempts, so `running`
  and `stale` are always false. `scheduled` and `awaiting_review` are derived
  from availability and stored state.
- Ready ordering is priority descending, `available_at` ascending with absent
  availability first, then Task number ascending. Cross-Project ready output
  adds immutable Project key before Task number as the final tie-breaker.
- Manual submission requires state `open` and satisfied dependencies;
  `available_at` affects scheduling and selection but does not prohibit a
  deliberate Human submission.
- A structured Result may contain an optional summary, criterion outcomes,
  artifact references, and proposed follow-ups. Proposed follow-ups are data;
  they do not automatically create Tasks or relationships.
- Rejected Results remain auditable. Rejection changes `review` to `open`,
  clears the current review selection, and requires a new Result for the next
  submission.
- Phase 3 event types are `task_created`, `task_updated`, `task_blocked`,
  `task_unblocked`, `result_submitted`, `review_approved`, `review_rejected`,
  `task_completed`, and `task_cancelled`. One semantic mutation may append
  consecutive events sharing actor, request, and authoritative timestamp.
- Events are immutable and ordered by one monotonically increasing Instance
  cursor. Snapshot pagination is the JSON automation contract. Human
  `--follow` may poll; JSON and non-interactive clients poll explicitly with
  the returned cursor.
- Every mutation except creation accepts an optimistic expected version and an
  optional idempotency key. Equivalent replay returns the original committed
  outcome without another version increment or TaskEvent.

### Task 1: Align Phase 3 lifecycle and automation contracts

- Deliverables:
  - `docs/roadmap.md`
  - `docs/architecture.md`
  - `docs/cli-contract.md`
  - `docs/persistence-contract.md`
  - `docs/glossary.md`
  - `docs/threat-model.md`
  - `docs/adr/0011-phase-three-task-mutation-and-human-submission.md`
  - `tests/unit/docs/test_phase_three_contracts.py`
- Description: Record one exact, noncontradictory Phase 3 contract before code
  changes. Reconcile the interactive optimistic-version convenience, strict
  automation behavior, Human submission without Attempts, Result review,
  dependency removal, derived views, event pagination, schema reset, and
  deferred Agent behavior. Keep README on verified Phase 2 behavior until the
  Phase 3 golden journey passes.
- Public interface changes:
  - Define exact signatures for:

    ```text
    workaholic task add TITLE [--objective TEXT] [--priority INTEGER]
      [--available-at TIMESTAMP] [--approval none|human]
      [--input-file PATH|-] [--project KEY] [--idempotency-key KEY]
    workaholic task update TASK [field options | --input-file PATH|-]
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

  - Retain `--json`, `--non-interactive`, and optional explicit `--project` on
    applicable commands. Define `task list --view` filters and cursor binding
    for `all`, `ready`, `scheduled`, `blocked`, `review`, `done`, and
    `cancelled`.
  - Define closed JSON objects for expanded Task data, readiness reasons,
    Results, TaskEvents, mutation outcomes, and event pages.
  - Add exact safe errors and exits for `VERSION_CONFLICT`,
    `INVALID_TRANSITION`, `DEPENDENCY_CONFLICT`, `DEPENDENCY_CYCLE`,
    `UNSATISFIABLE_DEPENDENCY`, and `RESULT_INVALID`.
- Inputs:
  - Accepted owner decisions and the cumulative Phase 2 contracts.
- Outputs:
  - A complete implementation contract that later tasks can encode without
    reopening Human, Agent, concurrency, hierarchy, or automation semantics.
- Tests:
  - Assert all normative documents agree on command shapes, version behavior,
    transitions, event sets, schema version, Result attribution, and Phase 4
    deferrals.
  - Assert no document permits last-write-wins, automatic conflict retry,
    Human Attempts, generic state updates, parent/child relationships, remote
    operation, or schema migration in Phase 3.
- Acceptance criteria:
  - Every Phase 3 success, rejection, prompt boundary, and JSON shape has one
    exact documented owner before production code changes.

### Task 2: Extend the dependency-free Task domain and lifecycle rules

- Deliverables:
  - `src/workaholic/domain/identifiers.py`
  - `src/workaholic/domain/models.py`
  - `src/workaholic/domain/rules.py`
  - `src/workaholic/domain/__init__.py`
  - `tests/unit/domain/test_identifiers.py`
  - `tests/unit/domain/test_models.py`
  - `tests/unit/domain/test_rules.py`
  - `tests/unit/domain/test_phase_three_lifecycle.py`
- Description: Add immutable Phase 3 value objects and pure rules without
  importing Pydantic, persistence, Sessions, or CLI code. Preserve defaults so
  existing Task construction remains valid while later tasks wire persistence
  and presentation.
- Public interface changes:
  - Add `ResultId` with the opaque `res_` prefix.
  - Expand `TaskState` and `TaskEventType` to the Phase 3 values.
  - Add `ApprovalRequirement`, `AcceptanceCriterion`, `ContextReference`,
    `CriterionOutcome`, `ArtifactReference`, `ProposedFollowUp`, `TaskResult`,
    `TaskOperationalView`, and `TaskReadiness`.
  - Extend `Task` with optional availability, approval, acceptance, context,
    dependencies, blocking reason, and current Result identity using immutable
    tuple/value semantics.
  - Add pure validation for bounded text, RFC 3339 UTC timestamps, URI
    references, media types, lowercase SHA-256 digests, stable criterion IDs,
    and bounded recursive JSON values.
  - Add pure transition, dependency, submission, and readiness functions that
    accept explicit state and authoritative time and return validated domain
    outcomes without I/O.
- Inputs:
  - Task 1 contracts and existing Phase 2 domain rules.
- Outputs:
  - One dependency-free source of truth for Task validity, lifecycle legality,
    Result structure, and derived readiness.
- Tests:
  - Exhaust every allowed and rejected transition, terminal-state behavior,
    dependency satisfaction, cancelled prerequisites, future availability,
    ordering keys, criterion/result consistency, immutable defensive copies,
    Unicode bounds, malformed URIs/hashes, booleans-as-integers, and timezone
    handling.
- Acceptance criteria:
  - Domain tests can prove every Phase 3 invariant without opening a database
    or importing Pydantic, Typer, Session, or adapter modules.

### Task 3: Define Phase 3 application commands, results, errors, and ports

- Deliverables:
  - `src/workaholic/application/commands.py`
  - `src/workaholic/application/results.py`
  - `src/workaholic/application/errors.py`
  - `src/workaholic/application/ports.py`
  - `src/workaholic/application/__init__.py`
  - `tests/unit/application/test_commands.py`
  - `tests/unit/application/test_errors.py`
  - `tests/unit/application/test_phase_three_contracts.py`
- Description: Introduce strict Pydantic intent, mutation, query, and result
  models plus semantic repository protocols. This task defines boundaries but
  does not yet expose new Session or CLI methods.
- Public interface changes:
  - Add operation-specific inputs and mutations for field update, block,
    unblock, cancel, dependency add/remove, Human submission, approval,
    rejection, ready listing, Task details, and event pagination.
  - Every existing-Task mutation model requires positive `expected_version`,
    actor Subject, request identity, authoritative timestamp, and optional
    bounded idempotency key. Result submission also carries a generated
    `ResultId`; all mutations carry the exact required TaskEvent IDs.
  - Add `TaskDetails`, `TaskMutationResult`, `TaskSubmissionResult`,
    `TaskEventPage`, and view-bound `TaskPage` result models with cross-object
    consistency validators.
  - Extend `ApplicationErrorCode` and fixed exit/retry mapping with the six
    Phase 3 errors defined by Task 1.
  - Extend repository protocols with explicit semantic methods such as
    `update_task_if_version`, `block_task`, `unblock_task`, `cancel_task`,
    `add_task_dependency`, `remove_task_dependency`, `submit_human_result`,
    `approve_result`, `reject_result`, `list_ready_tasks`, and
    `read_task_events_after`.
- Inputs:
  - Tasks 1-2 and existing application strictness/idempotency conventions.
- Outputs:
  - Runtime-validated, adapter-independent contracts for every Phase 3
    operation.
- Tests:
  - Reject missing/zero/boolean versions, empty patches, raw state changes,
    malformed structured values, invalid event counts, inconsistent Result or
    Task identities, unsafe error messages, and unexpected fields.
  - Prove protocol signatures contain semantic operations rather than generic
    CRUD or transaction handles.
- Acceptance criteria:
  - A fake repository can type-check every Phase 3 application contract with
    no SQLite, Session, or CLI dependency.

### Task 4: Introduce disposable SQLite schema version 3

- Deliverables:
  - `src/workaholic/persistence/sqlite/schema.py`
  - `src/workaholic/persistence/sqlite/_task_records.py`
  - `src/workaholic/persistence/sqlite/_event_records.py`
  - `src/workaholic/persistence/sqlite/_result_records.py`
  - `src/workaholic/persistence/sqlite/_records.py`
  - `src/workaholic/application/results.py`
  - `src/workaholic/context/models.py`
  - `tests/integration/persistence/test_sqlite_schema.py`
  - `tests/unit/persistence/test_sqlite_records.py`
  - cumulative schema-version assertions under `tests/`
- Description: Replace the disposable version `2` clean-store layout with an
  exact version `3` layout capable of representing Phase 3 Tasks, dependencies,
  Results, reviews, events, and idempotent outcomes. Keep existing Phase 2
  operations functional against a newly initialized version `3` store.
- Public interface changes:
  - Set the reported embedded schema version to exact integer `3`.
  - Extend `tasks` with availability, approval, blocking, and current-Result
    state while retaining immutable identity and version constraints.
  - Add ordered acceptance-criterion and context-reference storage,
    `task_dependencies`, `task_results`, and normalized review metadata.
  - Expand `task_events` for every Phase 3 type, Subject-kind snapshot, nullable
    future Attempt identity, bounded canonical payload, and Instance ordering.
  - Expand idempotency operation constraints for every Phase 3 mutation and
    add readiness, dependency, Result, and event query indexes.
- Inputs:
  - Tasks 1-3 and the exact Phase 2 schema-validation behavior.
- Outputs:
  - One strictly constrained clean-store schema with explicit codecs and no
    migration path.
- Tests:
  - Validate every table, column, key, check, foreign key, index, JSON bound,
    enum value, and timestamp round trip.
  - Assert version `2`, malformed, missing, and future stores are rejected
    byte-for-byte unchanged; concurrent initialization still yields one valid
    version `3` store.
- Acceptance criteria:
  - Existing bootstrap, Project, context, and basic Task flows pass unchanged
    on a fresh version `3` database, while version `2` is never interpreted or
    modified.

### Task 5: Persist complete Task creation and detail queries

- Deliverables:
  - `src/workaholic/application/tasks.py`
  - `src/workaholic/application/queries.py`
  - `src/workaholic/persistence/sqlite/_tasks.py`
  - `src/workaholic/persistence/sqlite/_queries.py`
  - `src/workaholic/persistence/sqlite/_task_records.py`
  - `src/workaholic/persistence/sqlite/repository.py`
  - `src/workaholic/session/models.py`
  - `src/workaholic/session/local.py`
  - `tests/unit/application/test_create_task.py`
  - `tests/integration/persistence/test_sqlite_create_task.py`
  - `tests/integration/persistence/test_sqlite_queries.py`
- Description: Extend the existing Task-create transaction and read paths to
  persist and hydrate availability, approval, acceptance criteria, and context
  references. Preserve all Phase 2 defaults and idempotency behavior.
- Public interface changes:
  - `CreateTaskInput`, `TaskCreationMutation`, and `TaskCreateRequest` accept
    optional `available_at`, `approval`, ordered `acceptance`, and ordered
    `context` values.
  - Task detail reads return the complete Task plus derived Phase 3 details;
    ordinary list pages retain deterministic ordering and include the expanded
    public Task shape.
  - Creation still starts in `open` at version `1`, with no dependencies,
    blocking reason, current Result, or synthetic update.
- Inputs:
  - Tasks 1-4 and the existing atomic allocation/idempotency transaction.
- Outputs:
  - Durable complete Task definitions that survive process restart and exact
    idempotent replay.
- Tests:
  - Cover defaults, full structured input, ordering, duplicate criterion IDs,
    empty context, timestamp precision, restart, idempotent replay/conflict,
    concurrent allocation, injected rollback, and no partial child rows.
- Acceptance criteria:
  - Creating and reading the same complete Task produces equal domain and JSON
    values without changing stable UID, key, number, event, or version rules.

### Task 6: Implement optimistic Task field updates

- Deliverables:
  - `src/workaholic/application/task_lifecycle.py`
  - `src/workaholic/application/commands.py`
  - `src/workaholic/persistence/sqlite/_task_lifecycle.py`
  - `src/workaholic/persistence/sqlite/repository.py`
  - `tests/unit/application/test_task_lifecycle.py`
  - `tests/integration/persistence/test_sqlite_task_updates.py`
  - `tests/integration/persistence/test_sqlite_concurrency.py`
- Description: Implement one atomic `update_task_if_version` semantic
  operation for editable Task definition fields. Generic update never accepts
  state, blocking reason, dependencies, Result, version, identity, or
  attribution fields.
- Public interface changes:
  - Editable fields are title, objective, priority, optional availability,
    approval requirement, the complete ordered acceptance set, and the complete
    ordered context-reference set.
  - A patch must change at least one field and must distinguish omission from
    intentionally clearing optional availability or replacing an ordered set
    with empty.
  - Success writes the normalized definition, sets one authoritative
    `updated_at`, increments version exactly once, appends one `task_updated`
    event with a bounded field-change payload, and records idempotency.
- Inputs:
  - Tasks 1-5.
- Outputs:
  - A reusable optimistic update path below the Session boundary.
- Tests:
  - Cover each field alone and together, no-op/empty patch rejection, stale
    versions, two-writer races with one winner, idempotent replay/conflict,
    terminal/review-state refusal, event attribution, and injected rollback
    across Task, child rows, event, and idempotency data.
- Acceptance criteria:
  - No concurrent or retried field update can produce last-write-wins, more
    than one version increment, or state without its matching event.

### Task 7: Implement blocking, unblocking, and cancellation transitions

- Deliverables:
  - `src/workaholic/application/task_lifecycle.py`
  - `src/workaholic/persistence/sqlite/_task_lifecycle.py`
  - `src/workaholic/persistence/sqlite/repository.py`
  - `tests/unit/application/test_task_lifecycle.py`
  - `tests/integration/persistence/test_sqlite_task_transitions.py`
- Description: Add explicit semantic operations for operator-controlled stored
  state transitions. Reuse the optimistic transaction core without routing
  state changes through generic update.
- Public interface changes:
  - `block_task`: `open -> blocked` with a required bounded reason.
  - `unblock_task`: `blocked -> open`, clearing the stored blocking reason.
  - `cancel_task`: `open|blocked|review -> cancelled` with an optional reason.
  - `done` and `cancelled` reject every Phase 3 lifecycle mutation.
  - Success increments the Task version once and appends exactly one of
    `task_blocked`, `task_unblocked`, or `task_cancelled` with actor, request,
    timestamp, and safe reason metadata.
- Inputs:
  - Tasks 1-6.
- Outputs:
  - Durable, attributable stored-state transitions with no raw state setter.
- Tests:
  - Exhaust the transition matrix, missing/oversized reasons, stale versions,
    review cancellation, repeated operations, idempotent replay/conflict,
    concurrent block/cancel races, timestamp consistency, and rollback.
- Acceptance criteria:
  - Every accepted state change has one version increment and matching event;
    every rejected transition leaves Task, Result selection, event cursor, and
    idempotency state unchanged.

### Task 8: Implement dependencies and derived readiness

- Deliverables:
  - `src/workaholic/application/task_dependencies.py`
  - `src/workaholic/application/queries.py`
  - `src/workaholic/persistence/sqlite/_task_dependencies.py`
  - `src/workaholic/persistence/sqlite/_queries.py`
  - `src/workaholic/persistence/sqlite/repository.py`
  - `src/workaholic/application/results.py`
  - `tests/unit/application/test_task_dependencies.py`
  - `tests/integration/persistence/test_sqlite_dependencies.py`
  - `tests/integration/persistence/test_sqlite_queries.py`
- Description: Add atomic dependency graph mutations and authoritative derived
  readiness queries. Keep dependency meaning separate from provenance and
  never create hierarchy records.
- Public interface changes:
  - `add_task_dependency` and `remove_task_dependency` require the dependant
    Task's expected version and mutate only that Task's graph/version.
  - Reject self edges, cross-Project edges, missing Tasks, duplicate additions,
    absent removals, cycles of any depth, and changes to review or terminal
    Tasks with exact typed outcomes.
  - Graph changes append `task_updated` with dependency-specific payload.
  - Add `TaskReadiness` and view-bound Task pages, including reason codes for
    stored state, future availability, unsatisfied prerequisite, and cancelled
    prerequisite.
  - Ready pages use the exact priority/availability/key ordering and bind view
    plus ordering position into opaque version `3` cursors.
- Inputs:
  - Tasks 1-7.
- Outputs:
  - Durable acyclic same-Project graphs and deterministic readiness for later
    Phase 4 claims.
- Tests:
  - Cover chains, diamonds, cycles, self/cross-Project edges, cancelled and
    reopened prerequisites, multiple Projects, future time boundaries,
    ordering ties, pagination, cursor scope, idempotency, concurrent graph
    edits, restart, and no read-side mutation.
- Acceptance criteria:
  - Equivalent graphs and authoritative times return equivalent readiness and
    ordering, and a prerequisite state change never rewrites dependant rows.

### Task 9: Implement Human Results and review transitions

- Deliverables:
  - `src/workaholic/application/task_results.py`
  - `src/workaholic/persistence/sqlite/_task_results.py`
  - `src/workaholic/persistence/sqlite/_result_records.py`
  - `src/workaholic/persistence/sqlite/repository.py`
  - `src/workaholic/application/results.py`
  - `tests/unit/application/test_task_results.py`
  - `tests/integration/persistence/test_sqlite_task_results.py`
- Description: Persist structured Human Results and implement submission,
  approval, and rejection as atomic semantic transitions. Make the future
  Attempt field nullable but refuse Agent/Attempt submission until Phase 4.
- Public interface changes:
  - `submit_human_result` accepts optional comment and optional validated Result
    content, requires an enabled Human actor, stores `attempt_id = null`, and
    validates criterion outcome IDs against the Task definition.
  - Submission requires `open` with satisfied dependencies. Approval `none`
    yields `done`; approval `human` yields `review`.
  - `approve_result` requires `review` and changes it to `done` with an optional
    reviewer comment. `reject_result` requires `review`, records a required
    reason, changes it to `open`, and clears the current review selection while
    retaining the rejected Result for audit.
  - No-approval submission appends `result_submitted` then `task_completed`;
    approval appends `review_approved` then `task_completed`; rejection appends
    `review_rejected`. Each semantic operation increments the Task version once.
- Inputs:
  - Tasks 1-8.
- Outputs:
  - Durable Human completion and review behavior that Phase 4 can later extend
    with Attempt validation without changing Human semantics.
- Tests:
  - Cover empty manual submission, comments, complete structured evidence,
    criterion mismatch, artifact metadata, proposed follow-ups without Task
    creation, approval/no-approval paths, rejection/resubmission, Actor kind,
    null Attempt, stale versions, idempotency, concurrent review, consecutive
    event order, single version increments, restart, and rollback.
- Acceptance criteria:
  - Human completion never creates or requires an Attempt, and no generic Task
    update can reproduce submission or review effects.

### Task 10: Implement attributable TaskEvent history queries

- Deliverables:
  - `src/workaholic/application/queries.py`
  - `src/workaholic/application/results.py`
  - `src/workaholic/persistence/sqlite/_event_records.py`
  - `src/workaholic/persistence/sqlite/_queries.py`
  - `src/workaholic/persistence/sqlite/repository.py`
  - `tests/unit/application/test_queries.py`
  - `tests/integration/persistence/test_sqlite_events.py`
- Description: Expose bounded, stable, authorized TaskEvent history below the
  Session boundary. Treat the cursor as Instance ordering while requiring the
  selected Task and Project for every returned record.
- Public interface changes:
  - `read_task_events_after` accepts Task identity, actor authorization context,
    optional nonnegative `after` cursor, and limit from 1 through 500.
  - Return `TaskEventPage(events, next_cursor)` in strict cursor order. An empty
    page is successful; `next_cursor` is the greatest observed cursor and never
    moves backward.
  - Hydrated events contain event/task/project identity, actor identity and
    kind, nullable Attempt identity, request identity, type, immutable payload,
    and authoritative timestamp.
- Inputs:
  - Tasks 1-9.
- Outputs:
  - One polling-friendly audit query used by the CLI, future TUI, and later
    remote Session.
- Tests:
  - Cover all Phase 3 event types, multi-event mutations, cursor gaps,
    pagination, concurrent Tasks, Project authorization, task scoping, restart,
    payload immutability, malformed bounds, and proof that reads never append,
    update, or delete records.
- Acceptance criteria:
  - Replaying pages from cursor zero yields every committed event exactly once
    in stable order and exposes no event for a rejected or rolled-back mutation.

### Task 11: Extend WorkaholicSession and the composition root

- Deliverables:
  - `src/workaholic/session/models.py`
  - `src/workaholic/session/base.py`
  - `src/workaholic/session/local.py`
  - `src/workaholic/session/__init__.py`
  - `src/workaholic/composition.py`
  - `tests/unit/session/test_phase_three_models.py`
  - `tests/unit/session/test_local_session.py`
  - `tests/unit/test_composition.py`
- Description: Wire every completed Phase 3 semantic operation through the
  presentation-independent Session boundary. Resolve profile, Instance,
  Project, Human actor, and authoritative Task selection once per request and
  never accept actor, request, event, Result, or timestamp identities from CLI
  input.
- Public interface changes:
  - Add strict Session requests and methods for update, block, unblock, cancel,
    dependency add/remove, Human submit, approve, reject, ready/view listing,
    Task details, and event history.
  - Session requests require `expected_version` for every existing-Task
    mutation. Interactive convenience remains exclusively a CLI concern.
  - Extend identifier factories with Result IDs and the number of event IDs
    required by one operation; keep the clock call singular per mutation.
  - `LocalSession` maps domain/application errors without adapter leakage and
    preserves explicit/discovered same-Instance Project selection.
- Inputs:
  - Tasks 3 and 5-10.
- Outputs:
  - Complete embedded Phase 3 behavior behind the same Session abstraction a
    future `RemoteSession` must implement.
- Tests:
  - Cover strict runtime validation, Project selection, cross-Project refusal,
    wrong Task-key prefix, actor derivation, clock/identifier ownership, exact
    repository calls, safe dependency failures, invalid dependency outputs,
    and no direct SQLite imports from CLI-facing interfaces.
- Acceptance criteria:
  - Every Phase 3 operation can run through a fake Session and real
    `LocalSession` with identical request/result meaning and no presentation
    dependency in application or persistence code.

### Task 12: Expose Task definition, state, dependency, and readiness commands

- Deliverables:
  - `src/workaholic/cli/task.py`
  - `src/workaholic/cli/task_mutations.py`
  - `src/workaholic/cli/structured_input.py`
  - `src/workaholic/cli/options.py`
  - `src/workaholic/cli/serialization.py`
  - `src/workaholic/cli/errors.py`
  - `tests/unit/cli/test_task_add.py`
  - `tests/unit/cli/test_task_update.py`
  - `tests/unit/cli/test_task_transitions.py`
  - `tests/unit/cli/test_task_dependencies.py`
  - `tests/unit/cli/test_task_list.py`
  - `tests/unit/cli/test_structured_input.py`
  - `tests/integration/cli/test_local_cli.py`
- Description: Expose complete Task creation, optimistic field update,
  block/unblock/cancel, dependency mutation, detail, and derived-view listing
  through `WorkaholicSession`. Centralize bounded structured input and
  interactive version confirmation so commands cannot diverge.
- Public interface changes:
  - Implement the Task 1 signatures for `task add`, `task update`, `task block`,
    `task unblock`, `task cancel`, `task add-dependency`,
    `task remove-dependency`, `task list --view`, and expanded `task show`.
  - In a real interactive terminal, missing expected version performs one read,
    prints Task key/state/version plus intended action, and prompts once. In
    JSON, non-interactive, redirected, or otherwise non-terminal execution,
    omission returns `INVALID_INPUT` without a mutation.
  - Explicit expected version skips convenience fetching. Any version conflict
    is rendered once and never retried.
  - `--input-file -` reads standard input only when explicitly requested.
    Enforce byte, nesting, collection, and text bounds before Pydantic parsing;
    reject overlap or conflict between file and scalar options.
- Inputs:
  - Tasks 1, 5-8, and 11.
- Outputs:
  - Human-friendly and automation-safe access to Phase 3 Task definition,
    state, dependency, and readiness behavior.
- Tests:
  - Assert exact requests, prompts, decline behavior, non-TTY behavior, JSON
    envelopes, stdout/stderr separation, stale conflicts without retries,
    file/stdin bounds, symlink/directory/read failures, view ordering/cursors,
    error exits/redaction, and fresh-process persistence.
- Acceptance criteria:
  - Humans can use safe confirmation without manually copying versions, while
    automation cannot mutate an existing Task without an explicit version.

### Task 13: Expose Human submission, review, and event-history commands

- Deliverables:
  - `src/workaholic/cli/task.py`
  - `src/workaholic/cli/task_results.py`
  - `src/workaholic/cli/task_events.py`
  - `src/workaholic/cli/structured_input.py`
  - `src/workaholic/cli/serialization.py`
  - `tests/unit/cli/test_task_submit.py`
  - `tests/unit/cli/test_task_review.py`
  - `tests/unit/cli/test_task_events.py`
  - `tests/integration/cli/test_local_cli.py`
- Description: Expose Human Result submission, approval/rejection, and audit
  history through the same version and structured-input helpers as Task 12.
  Do not introduce `--attempt` until Phase 4.
- Public interface changes:
  - Implement `task submit`, `task approve`, `task reject`, and `task events`
    exactly as contracted by Task 1.
  - Human submit allows neither comment nor Result file, either one, or both;
    structured file data never supplies actor, Attempt, Task, version, request,
    event, or timestamp identities.
  - JSON event reads are bounded snapshot pages. Human `--follow` polls from
    the last cursor until interruption, emits each event once, and cannot be
    combined with JSON or non-interactive mode.
  - Result and event JSON include `attempt_id: null` for Human Phase 3 records.
- Inputs:
  - Tasks 1, 9-12.
- Outputs:
  - Complete public CLI access to Human completion, review, and audit history.
- Tests:
  - Cover empty/manual and structured submission, comments, invalid Result
    files, criterion mismatch, approval/rejection, no `--attempt`, version
    confirmation, no retry, exact multi-event ordering, paginated history,
    follow polling with injected wait/interrupt, JSON restrictions, redaction,
    restart, and immutable rejected Results.
- Acceptance criteria:
  - A Human can complete the full review workflow and inspect every event
    without any Attempt, database access, or hidden actor input.

### Task 14: Add reusable Phase 3 persistence and Session conformance suites

- Deliverables:
  - `tests/contract/phase_three.py`
  - `tests/contract/test_phase_three_persistence.py`
  - `tests/contract/test_phase_three_session.py`
  - `tests/contract/README.md`
  - `tests/contract/fixtures/README.md`
- Description: Add cumulative adapter-neutral behavioral contracts for Task
  lifecycle, optimistic versions, dependencies, readiness, Results, review,
  events, and idempotency. Reuse Phase 1-2 factories/assertions instead of
  duplicating their identity and multi-Project coverage.
- Public interface changes:
  - `PhaseThreeRepositoryFactory` provides an exact-version clean store,
    deterministic IDs/time, independent connections, and injected transaction
    failure hooks.
  - `PhaseThreeSessionFactory` provides isolated profiles, Projects, Human
    actors, Workspaces, and deterministic lifecycle dependencies.
- Inputs:
  - Tasks 4-13.
- Outputs:
  - Executable observable-behavior specifications for future JSON,
    PostgreSQL, and `RemoteSession` implementations.
- Tests:
  - Cover every transition and invalid transition, optimistic and idempotency
    races, graph cycles, cancelled prerequisites, readiness time boundaries,
    stable ordering/cursors, Result review, multi-event single-version
    mutations, attribution, rollback, restart, authorization, and no read-side
    mutation.
- Acceptance criteria:
  - SQLite and `LocalSession` pass cumulative Phase 1, Phase 2, and Phase 3
    contracts without adapter-specific expected outcomes.

### Task 15: Enable the complete Human lifecycle golden journey

- Deliverables:
  - `tests/e2e/golden/test_solo_journey.py`
  - `tests/golden.py`
  - `tests/conftest.py`
  - `tests/e2e/golden/README.md`
  - `tests/unit/test_golden_contract_helpers.py`
  - `tests/unit/test_golden_journey_inventory.py`
- Description: Extend the existing solo golden journey into the Phase 3 exit
  journey using fresh real CLI processes and isolated profile, data, and
  Workspace directories. Preserve the multi-project journey and all four
  future-phase skips.
- Public interface changes:
  - Golden journey:

    ```text
    bootstrap Project -> create prerequisite and reviewed Task
      -> add dependency -> verify non-readiness
      -> block and unblock prerequisite -> submit prerequisite as Human
      -> verify reviewed Task becomes ready -> submit structured evidence
      -> approve review -> inspect complete ordered TaskEvent history
    ```

  - The runner permits only documented test-owned environment selectors and
    still rejects URLs, credentials, Tokens, Python-path injection, and
    arbitrary environment propagation.
- Inputs:
  - Tasks 11-14.
- Outputs:
  - Executable evidence for the Human Workflow Alpha exit gate.
- Tests:
  - Assert stable keys, version increments, readiness changes, null Attempt,
    Result round trip, review transition, actor/request attribution, event
    order, process restart, exact JSON-only stdout, and Human-readable output.
- Acceptance criteria:
  - `uv run pytest -m golden` passes the expanded solo and multi-project
    journeys and reports exactly four future-phase skips.

### Task 16: Publish Phase 3 documentation and alpha metadata

- Deliverables:
  - `README.md`
  - `CHANGELOG.md`
  - `pyproject.toml`
  - `uv.lock`
  - `src/workaholic/__init__.py`
  - `docs/architecture.md`
  - `docs/cli-contract.md`
  - `docs/persistence-contract.md`
  - `docs/threat-model.md`
  - `tests/unit/docs/test_public_documentation.py`
  - `tests/unit/docs/test_phase_three_contracts.py`
  - `tests/unit/test_package_metadata.py`
- Description: Replace Phase 2 Task-lifecycle limitations with the verified
  Human workflow, document version confirmation and schema version `3` reset,
  and set pre-release metadata to `0.3.0a1`. Keep Agent execution and remote
  capabilities prominently deferred.
- Public interface changes:
  - Package version: `0.3.0a1`.
  - README quick start demonstrates dependency, readiness, Human submission,
    review, and event inspection with isolated test-owned state.
  - Document interactive version confirmation separately from the mandatory
    explicit automation contract.
  - Document that Attempts are Agent-only and unavailable, Human Results have
    null Attempt identity, proposed follow-ups do not create Tasks, and schema
    version `2` data requires an explicit disposable-development reset.
- Inputs:
  - Verified behavior and golden evidence from Tasks 12-15.
- Outputs:
  - Public documentation that accurately describes the Human Workflow Alpha
    and remains executable as a regression test.
- Tests:
  - Execute the exact README quick start in isolated directories.
  - Assert package version, command inventory, JSON objects, error names,
    schema/reset notice, Human/Agent semantics, and current limitations agree.
  - Assert README does not claim Agents, Attempts, claims, Leases, Tokens,
    remote operation, servers, alternate adapters, migrations, hierarchy, or
    automatic follow-up creation exist.
- Acceptance criteria:
  - A new operator can complete the Human workflow quick start without reading
    internal architecture documents or touching their real Workaholic data.

### Task 17: Execute the Phase 3 clean-state acceptance gate

- Deliverables:
  - `scripts/verify-phase-3.sh`
  - `scripts/smoke-phase-3-wheel.sh`
  - `tests/e2e/test_phase_3_distribution.py`
  - `tests/unit/scripts/test_verify_phase_three.py`
  - `tests/unit/scripts/test_smoke_phase_three_wheel.py`
  - `.pre-commit-config.yaml`
  - `README.md`
  - `CHANGELOG.md`
  - Phase 3 GitHub epic and implementation issues
- Description: Add one fail-fast aggregate acceptance command and execute it
  from a fresh clone with empty temporary config, data, and Workspace
  directories. Validate source and installed-wheel lifecycle behavior without
  reading or writing the operator's real profiles or databases.
- Public interface changes:
  - Acceptance command: `scripts/verify-phase-3.sh`.
  - Required clean-state sequence:

    ```bash
    uv sync --frozen
    uv run pre-commit run --all-files
    uv run pytest
    uv build
    scripts/smoke-install.sh dist/*.whl
    scripts/smoke-phase-3-wheel.sh dist/*.whl
    ```

  - The wheel smoke journey exercises Task definition, optimistic update,
    blocking, dependencies, readiness, Human Result submission, approval,
    event history, stale-version rejection, and process restart.
- Inputs:
  - Tasks 1-16.
- Outputs:
  - Reproducible evidence for the Human Workflow Alpha exit gate.
  - A closed Phase 3 epic and milestone only after protected `main` is green.
- Tests:
  - Prove schema version `2`, stale versions, invalid transitions, dependency
    cycles, cancelled prerequisites, malformed Results, idempotency conflicts,
    failing tests, and malformed wheels fail at documented boundaries.
  - Assert the gate rejects an active virtual environment, pre-existing build
    output, dirty tracked files, or config/data paths outside its owned
    temporary root.
- Acceptance criteria:
  - Required `quality`, `tests`, `build`, and `wheel-smoke` checks pass on the
    merge commit on protected `main`.
  - Solo and multi-project golden journeys pass; exactly four future journeys
    remain skipped.
  - Source and wheel runs produce the same Task versions, readiness, Results,
    state transitions, errors, and TaskEvent order.
  - Acceptance evidence is linked from the Phase 3 epic before closing its
    milestone.

## Operational instructions

1. Before Task 1 begins, create one Phase 3 epic and issues for Tasks 1-17 in a
   dedicated Human Workflow Alpha milestone. Copy each task's acceptance
   criteria into its issue and preserve task order through explicit dependency
   links.
2. Work on a dedicated Phase 3 branch. Implement and commit Tasks 1-17 in
   order, using each task title in its commit message. Do not begin the next
   task until the previous task's changes are committed and its required tests
   are green.
3. Tasks may run in parallel only after every shared input has merged and their
   deliverables are disjoint. The schema, domain, application contracts,
   Session, and shared CLI helpers are sequential ownership boundaries.
4. Every developer installs and runs the repository's existing quality stages:

   ```bash
   uv sync --frozen
   uv run pre-commit install --hook-type pre-commit --hook-type pre-push
   uv run pre-commit run --all-files
   uv run pytest
   uv build
   ```

5. Every manual, integration, smoke, and acceptance run uses absolute,
   test-owned `WORKAHOLIC_CONFIG_DIR` and `WORKAHOLIC_DATA_DIR` values. Never
   load the operator's default `profiles.toml` or database in a test.
6. Phase 3 schema version `3` intentionally rejects Phase 2 version `2`.
   Preserve any development data needed outside Workaholic, then remove only
   the explicitly selected disposable test/development data after verifying
   its path. Do not add a migration, broad recursive cleanup, or silent reset.
7. Every pull request that changes commands, JSON objects, errors, structured
   input, prerequisites, storage, or support status updates README and the
   relevant contract documentation in the same change. README must not
   describe a capability before its acceptance tests pass.
8. Do not add Agent Subjects, Attempts, Leases, claims, heartbeats, Tokens,
   URL/credential configuration, remote profiles, network transport, servers,
   JSON/PostgreSQL adapters, or parent/child Task relationships in Phase 3.
9. Never accept actor, Subject kind, Attempt, TaskEvent, Result, request,
   authoritative timestamp, or cursor identities from repository-controlled
   context or structured CLI input. Derive them through the trusted Session and
   application boundaries.
10. Keep Result payloads and TaskEvent payloads bounded. Store artifact URIs,
    media types, and hashes only; never read, copy, execute, or persist artifact
    contents.
11. The `0.3.0a1` version is pre-release metadata, not a compatibility promise.
    Do not upload to PyPI or create a GitHub release without separate explicit
    owner authorization. Built artifacts remain CI and acceptance evidence.
12. After Task 17 merges, rerun the gate from the protected `main` merge
    commit, attach clean-state and CI evidence to the Phase 3 epic, and only
    then close the epic and milestone.
