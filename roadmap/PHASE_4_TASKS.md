# Phase 4 Implementation Tasks

## Purpose

Deliver the Local Agent Alpha: Human operators and autonomous local Agent
processes coordinate through exclusive expiring Claims, Agents execute through
stale-safe Attempts, and both paths use the embedded CLI without pulling
identity management, remote operation, or capability scheduling into Phase 4.

Tasks are ordered by dependency. Each task is independently reviewable and is
intended to be implemented by a separate developer after every concrete input
listed for that task has merged. Every task must preserve the existing Phase
0-3 behavior, public quick start, package boundaries, quality controls, and
protected-branch checks.

## Repository state at planning time

The following deliverables already exist and must be extended rather than
recreated:

- Python 3.14 package metadata, locked dependencies, pre-commit hooks, strict
  Ruff and mypy checks, import-boundary enforcement, least-privilege GitHub
  Actions, source/wheel builds, and clean-checkout acceptance infrastructure;
- dependency-free Task and Result domain models, strict Pydantic application
  and Session boundaries, embedded `LocalSession`, the explicit composition
  root, and the cumulative SQLite repository facade;
- trusted embedded profiles, safe upward Workspace discovery, multiple named
  Projects, stable Task keys, dependency and readiness rules, optimistic Task
  versions, Human Results and review, and append-only TaskEvent history;
- SQLite schema version `3`, atomic and idempotent Phase 0-3 operations,
  deterministic Task and event pagination, and injected authoritative clocks;
- versioned `workaholic.cli/v1` success and error envelopes, JSON-only stdout,
  non-interactive behavior, structured file/stdin input, Human-readable
  rendering, and expected-version safety;
- cumulative persistence and Session conformance suites, real-process golden
  journeys, installed-wheel smoke tests, README execution tests, and Phase 0-3
  clean-state gates;
- accepted Phase 4 ownership decisions in
  `docs/adr/0012-phase-four-local-claim-and-execution-model.md`, aligned
  architecture/roadmap/contracts/threat model, and executable documentation
  assertions in `tests/unit/docs/test_phase_four_contracts.py`;
- a deliberately skipped Agent journey in
  `tests/e2e/golden/test_agent_journey.py` that must be corrected for the
  accepted shared-bootstrap-Subject model before it is enabled.

No duplicate project-bootstrap, quality-control, README-governance, community,
repository-management, profile, Workspace-context, Task-lifecycle, Result,
review, or CI-foundation task is required. Phase 4 introduces no environment
variables, so `.env.example`, Docker configuration, and profile grammar need no
new setting.

## Confirmed Phase 4 decisions

Implementation and documentation must consistently encode these
owner-approved decisions:

- A Claim is the current exclusive, expiring ownership record for one Task. A
  Human Claim has `attempt_id = null`; an Agent Claim has one non-null current
  Attempt ID. At most one unexpired Claim owns a Task.
- Phase 4 does not introduce additional Subjects, Tokens, ProjectGrants, or
  credentials. Human and Agent command paths reuse the embedded bootstrap
  Subject. The owner token is `(subject_id, attempt_id)`, where the Human token
  has a null Attempt and each Agent process has its exact Attempt ID.
- Different Human operators remain indistinguishable in embedded Phase 4.
  Distinct authenticated Human and Agent ownership arrives in Phase 5.
- `TaskEvent.actor_kind` remains the persisted bootstrap Subject kind
  (`human`) in Phase 4. A non-null `attempt_id`, not a fabricated Agent Subject
  kind, identifies Agent execution.
- Human claiming is optional and targets one explicit ready Task. Agent
  claiming omits the Task operand and atomically pulls the highest-ranked ready
  Task in the selected Project.
- Capability filtering is outside v1. Phase 4 does not add capability fields,
  filters, matching rules, release reasons, or authorization behavior.
- A current Claim is an exclusive mutation lock. Non-owner writes fail without
  changing Task, Claim, Attempt, Result, version, idempotency, or TaskEvent
  state. Reads remain available.
- The Human owner may use existing Human update, block, unblock, dependency,
  cancel, and submit operations without an Attempt ID. Definition,
  block/unblock, and dependency mutations retain the Claim; cancellation and
  submission end it.
- The Agent owner may only heartbeat, report progress, release, or submit using
  its exact current Attempt. It cannot update definition, block, unblock,
  cancel, or change dependencies while executing.
- Workaholic does not force-interrupt an external process. Operators coordinate
  externally, then wait for explicit release or Lease expiry.
- Human `task renew` and Agent `task heartbeat` are presentation wrappers over
  one renewal semantic operation. Claiming, normal reads, and unrelated
  mutations never renew a Lease implicitly.
- Repeating an owned targeted Human Claim returns the current Claim without
  extending it. Agent pull retries return the same outcome only through an
  equivalent idempotency-key replay; an unkeyed pull is a new request.
- Claim, renew, heartbeat, progress, release, and expiry do not change Task
  version or `updated_at`. Successful Human and Agent submission increments
  Task version exactly once.
- Agent claim returns the Task version. Agent submission requires both the
  exact current Attempt and that expected Task version.
- Attempt states are exactly `active`, `released`, `expired`, and `submitted`.
  The final three are terminal and require `ended_at`. Submission always makes
  the Attempt `submitted`, including when the Task enters review. Review never
  revives or closes that Attempt; rejected work requires a new Claim and
  Attempt.
- Lease validity uses authoritative transaction time and the half-open rule
  `now < lease_expires_at`. Correctness does not require a scheduler or client
  clock.
- Persistence changes advance disposable SQLite schema version from `3` to
  exact version `4`. Version `3` is rejected unchanged; Phase 4 adds no
  migration, conversion, import, export, or silent reset.

## Exact Phase 4 behavioral baseline

The implementation tasks use the following concrete contracts so no developer
must invent a boundary while coding:

- Lease duration text matches `^[1-9][0-9]*(s|m|h|d)$`; compound, fractional,
  signed, zero, whitespace-padded, or unitless values are invalid.
- A Human Claim defaults to `8h` and accepts `1m` through `30d`. An Agent Claim
  or heartbeat defaults to `15m` and accepts `1s` through `24h`. Renewal sets
  `lease_expires_at = authoritative_now + resolved_duration`; it never adds to
  the previous expiry.
- Claim acquisition returns the Task, active Claim, nullable Attempt, and
  ordered TaskEvents. Renew/heartbeat returns the same shape with the updated
  Claim and Attempt. Release returns the Task, `claim = null`, nullable terminal
  Attempt, and its release event.
- Agent progress input is a closed object with optional trimmed `message`
  (maximum 4,000 characters), optional real integer `percent_complete` from 0
  through 100, and at most 50 ordered observations. At least one field must be
  present.
- Each observation is a closed object with `kind` exactly `note`, `risk`,
  `blocker`, or `question` and trimmed `text` of at most 4,000 characters.
  Observations are inert audit data; `blocker` does not change Task state.
- One progress request appends `progress_reported` first and then one
  `observation_added` event per input observation. It stores no separate
  progress table and does not change the Task or its version.
- Phase 4 adds TaskEvent types `task_claimed`, `claim_renewed`,
  `claim_released`, `claim_expired`, `progress_reported`, and
  `observation_added`. Explicit release alone emits `claim_released`;
  submission and cancellation end a Claim through their own existing events.
- Pure reads never materialize expiry or append events. An expired stored Claim
  is semantically stale, does not block readiness, and may make a Task both
  `ready` and `stale`. The next successful write that needs the Task
  materializes expiry before its requested operation.
- Materialized expiry sets an Agent Attempt to `expired` with
  `ended_at = lease_expires_at`, removes the Claim, and appends
  `claim_expired`. The event records the current authenticated Subject/request
  that observed expiry, uses the expired Attempt when present, and carries the
  authoritative `lease_expires_at` in its payload.
- An expired or stale Agent operation returns `LEASE_LOST` without committing
  its requested operation. Expiry may instead be materialized by a later
  successful claim or Human mutation.
- Claim and execution idempotency fingerprints include Project, Task selector
  where present, nullable Attempt, resolved Lease duration, expected version
  where present, and the complete structured payload. Equivalent replay returns
  the original closed outcome; conflicting key reuse returns the existing
  `IDEMPOTENCY_CONFLICT`.
- Phase 4 adds these exact public errors:

  | Code | Exit | Retryable | Exact safe message |
  | --- | ---: | :---: | --- |
  | `NO_TASK_AVAILABLE` | 3 | true | `No ready Task is available to claim.` |
  | `TASK_LOCKED` | 4 | true | `The Task has a current Claim owned by another execution.` |
  | `LEASE_LOST` | 4 | false | `The Claim is no longer current.` |

- Missing Attempt flags, malformed durations, invalid structured progress, and
  mutually inconsistent CLI operands use `INVALID_INPUT`. Unknown Attempt IDs
  and expired, released, submitted, or superseded Attempts collapse to
  `LEASE_LOST` rather than disclosing execution history.
- Ready ordering remains priority descending, availability ascending with
  absent availability first, then Task number ascending. Claims are Project
  scoped; Phase 4 does not add an all-Project Agent pull.
- The Phase 4 local alpha is SQLite-only and embedded-only. JSON/PostgreSQL
  adapters, Tokens, identity administration, remote profiles,
  `RemoteSession`, server operation, and public APIs remain deferred.

### Task 1: Complete the exact Phase 4 delivery contracts

- Deliverables:
  - `docs/architecture.md`
  - `docs/cli-contract.md`
  - `docs/persistence-contract.md`
  - `docs/glossary.md`
  - `docs/threat-model.md`
  - `docs/roadmap.md`
  - `docs/adr/0012-phase-four-local-claim-and-execution-model.md`
  - `tests/unit/docs/test_phase_four_contracts.py`
- Description: Extend the already accepted Claim/Attempt decision with exact
  duration grammar and bounds, closed success objects, progress input, errors,
  event payloads, idempotency fingerprints, stale-read behavior, schema version
  `4`, and the shared-bootstrap attribution limitation. Keep README on verified
  Phase 3 behavior until the Phase 4 golden journey passes.
- Public interface changes:
  - Define exact command signatures for Human `claim`, `renew`, and `release`;
    Agent pull `claim`, `heartbeat`, `progress`, `release`, and Attempt-backed
    `submit`.
  - Define closed JSON schemas for `TaskClaim`, `TaskAttempt`, claim results,
    progress results, extended Task details, and Agent submission.
  - Add the three exact errors and duration/progress rules from the behavioral
    baseline.
  - Define SQLite schema version `4` rejection/reset behavior without a
    migration promise.
- Inputs:
  - Accepted ADR 0012 and the cumulative Phase 3 CLI, persistence, security,
    and compatibility contracts.
- Outputs:
  - One implementation contract covering every Phase 4 success, no-op,
    conflict, expiry, replay, and presentation boundary.
- Tests:
  - Assert all normative documents agree on durations, commands, error codes,
    Claim/Attempt attribution, terminal states, event order, version behavior,
    readiness, and schema version.
  - Assert no Phase 4 document introduces capability scheduling, additional
    identities, Tokens, credentials, network services, parent/child Tasks, or
    schema migration.
- Acceptance criteria:
  - Later developers can implement every public and persistence boundary
    without making a new product or concurrency decision.

### Task 2: Add dependency-free Claim, Attempt, Lease, and progress domain rules

- Deliverables:
  - `src/workaholic/domain/identifiers.py`
  - `src/workaholic/domain/enums.py`
  - `src/workaholic/domain/models.py`
  - `src/workaholic/domain/rules.py`
  - `src/workaholic/domain/__init__.py`
  - `tests/unit/domain/test_identifiers.py`
  - `tests/unit/domain/test_models.py`
  - `tests/unit/domain/test_rules.py`
  - `tests/unit/domain/test_phase_four_execution.py`
- Description: Add immutable Claim and Agent-execution value objects plus pure
  validation and readiness rules without importing Pydantic, persistence,
  Sessions, Typer, or clocks with hidden global state.
- Public interface changes:
  - Add `AttemptId` with the opaque `atm_` prefix.
  - Add `AttemptStatus`, `ObservationKind`, `ProgressObservation`,
    `TaskProgress`, `TaskClaim`, and `TaskAttempt`.
  - Change `TaskResult.attempt_id` and TaskEvent attribution from untyped
    strings/null-only placeholders to `AttemptId | None`.
  - Replace `ACTIVE_ATTEMPT` and `STALE_ATTEMPT` readiness reasons with
    `ACTIVE_CLAIM` and `STALE_CLAIM` before compatibility freezes.
  - Add pure functions for duration bounds, owner-token equality, half-open
    Lease validity, Attempt transitions, claimability, and readiness from an
    optional Claim and authoritative timestamp.
- Inputs:
  - Exact Phase 4 duration, ownership, progress, terminal-state, and readiness
    contracts in the canonical documentation.
- Outputs:
  - A dependency-free source of truth for valid Claims, Attempts, Leases,
    progress payloads, and operational views.
- Tests:
  - Exhaust identifier/type validation, UTC and half-open time boundaries,
    Human/Agent Claim invariants, active/terminal Attempt combinations,
    ended-at requirements, duration minimums/maximums, immutable copies,
    progress bounds, observation order/kinds, and stale-plus-ready behavior.
  - Prove no domain module imports Pydantic, SQLite, Session, CLI, or wall-clock
    functions.
- Acceptance criteria:
  - Pure domain tests express every Claim and Attempt invariant without I/O.

### Task 3: Define Phase 4 application commands, results, errors, and ports

- Deliverables:
  - `src/workaholic/application/commands.py`
  - `src/workaholic/application/results.py`
  - `src/workaholic/application/errors.py`
  - `src/workaholic/application/ports.py`
  - `src/workaholic/application/__init__.py`
  - `tests/unit/application/test_commands.py`
  - `tests/unit/application/test_errors.py`
  - `tests/unit/application/test_phase_four_contracts.py`
- Description: Introduce strict Pydantic intent/mutation/result models and
  semantic repository protocols before any Session or CLI exposure. Keep Human
  and Agent wrappers explicit while sharing renewal/release persistence models.
- Public interface changes:
  - Add `ClaimTaskMutation`, `ClaimNextTaskMutation`, `RenewClaimMutation`,
    `ReleaseClaimMutation`, `ReportTaskProgressMutation`, and
    `SubmitAgentResultMutation`.
  - Every mutation carries Project, bootstrap Subject, request identity,
    authoritative time, optional idempotency key, and exact event identities.
    Agent mutations require `AttemptId`; Agent submission additionally requires
    a positive expected Task version and candidate `ResultId`.
  - Add `TaskClaimResult` (`task`, nullable `claim`, nullable `attempt`, ordered
    `events`) and `TaskProgressResult`; extend `TaskDetails` with active
    Claim/Attempt and extend `TaskSubmissionResult` with nullable submitted
    Attempt.
  - Add `NO_TASK_AVAILABLE`, `TASK_LOCKED`, and `LEASE_LOST` to
    `ApplicationErrorCode` with the fixed exit/retry/message mapping.
  - Extend repository protocols with `claim_task`, `claim_next_task`,
    `renew_claim`, `release_claim`, `report_task_progress`, and
    `submit_agent_result` semantic operations.
- Inputs:
  - Dependency-free Phase 4 domain exports and the existing Phase 3
    application conventions.
- Outputs:
  - Runtime-validated, adapter-neutral contracts for every Phase 4 operation.
- Tests:
  - Reject unknown fields, booleans as integers, missing Agent Attempts,
    invalid duration seconds, inconsistent Task/Project/Subject identities,
    malformed event batches, impossible Claim/Attempt result combinations,
    unsafe messages, and invalid progress/event counts.
  - Prove ports expose semantic operations rather than generic CRUD,
    connections, cursors, or transactions.
- Acceptance criteria:
  - Fake repositories can type-check the complete Phase 4 application surface
    without importing SQLite, Sessions, or CLI modules.

### Task 4: Introduce disposable SQLite schema version 4

- Deliverables:
  - `src/workaholic/persistence/sqlite/schema.py`
  - `src/workaholic/persistence/sqlite/_claim_records.py`
  - `src/workaholic/persistence/sqlite/_event_records.py`
  - `src/workaholic/persistence/sqlite/_result_records.py`
  - `src/workaholic/persistence/sqlite/_records.py`
  - `src/workaholic/context/models.py`
  - `tests/integration/persistence/test_sqlite_schema.py`
  - `tests/unit/persistence/test_sqlite_records.py`
  - cumulative schema-version assertions under `tests/`
- Description: Replace the disposable version `3` clean-store layout with an
  exact version `4` layout that can persist one current Claim per Task and
  durable Agent Attempt history. Preserve all Phase 3 operations against a
  newly initialized version `4` store.
- Public interface changes:
  - Set reported embedded schema version to integer `4` and reject version `3`
    unchanged.
  - Add `task_attempts` with Attempt ID, Task/Project/bootstrap Subject,
    status, start/end timestamps, and last Lease expiry. Enforce active/null
    `ended_at`, terminal/non-null `ended_at`, and composite ownership keys.
  - Add `task_claims` keyed by Task with Project, owner Subject, nullable unique
    Attempt, claim timestamp, and Lease expiry. A composite foreign key ensures
    an Agent Claim's Attempt belongs to the same Task, Project, and Subject.
  - Add Attempt foreign keys and constraints to Agent Results and TaskEvents;
    retain nullable Attempt attribution for Human rows.
  - Extend event types and idempotency operation names for Claim and execution
    mutations. Add indexes supporting ready selection, active Claim lookup,
    Attempt history, and Lease expiry.
- Inputs:
  - Phase 4 domain/application record shapes and existing strict SQLite schema
    conventions.
- Outputs:
  - A validated clean-store representation with no migration or silent reset.
- Tests:
  - Cover every table, index, foreign key, CHECK constraint, composite ownership
    invariant, terminal Attempt state, nullable Human Attempt, event type,
    idempotency operation, reopen, unsupported-version rejection, and rollback.
  - Prove malformed version `3` or `4` files remain unchanged after failure.
- Acceptance criteria:
  - SQLite cannot represent two current Claims for one Task, mismatched
    Agent Claim ownership, or an active Attempt with `ended_at`.

### Task 5: Hydrate Claim projections and Phase 4 readiness views

- Deliverables:
  - `src/workaholic/persistence/sqlite/_claim_records.py`
  - `src/workaholic/persistence/sqlite/_claim_state.py`
  - `src/workaholic/persistence/sqlite/_queries.py`
  - `src/workaholic/persistence/sqlite/_task_views.py`
  - `src/workaholic/persistence/sqlite/repository.py`
  - `src/workaholic/application/results.py`
  - `tests/integration/persistence/test_sqlite_queries.py`
  - `tests/integration/persistence/test_sqlite_claim_queries.py`
- Description: Add read-only Claim/Attempt hydration and derive `ready`,
  `running`, and `stale` from authoritative query time. Reads must remain
  strictly non-mutating even when stored Lease data has expired.
- Public interface changes:
  - `TaskDetails` returns an active `claim` and Agent `attempt` only while
    `now < lease_expires_at`; expired stored rows are represented through the
    stale readiness projection rather than returned as current ownership.
  - `running` means an unexpired Human or Agent Claim. `stale` means an expired
    stored Claim awaits write-side materialization.
  - An otherwise eligible Task with an expired Claim is both ready and stale;
    an active Claim makes it running and not ready.
  - Ready and view pagination retain existing ordering and cursor binding.
- Inputs:
  - SQLite schema version `4`, Claim record codecs, pure Phase 4 readiness
    rules, and the injected repository clock.
- Outputs:
  - Stable non-mutating Task detail/list projections for current and expired
    Claim state.
- Tests:
  - Cover exact expiry boundary, Human and Agent Claims, ready-plus-stale,
    running, blocked/scheduled/review interactions, dependencies, pagination,
    reopen, malformed rows, and zero writes/events/version changes during
    reads.
- Acceptance criteria:
  - Query behavior remains correct without a scheduler and cannot mistake an
    expired row for a current mutation lock.

### Task 6: Implement atomic Human and Agent Claim acquisition

- Deliverables:
  - `src/workaholic/persistence/sqlite/_claim_state.py`
  - `src/workaholic/persistence/sqlite/_task_claims.py`
  - `src/workaholic/persistence/sqlite/_event_records.py`
  - `src/workaholic/persistence/sqlite/repository.py`
  - `tests/integration/persistence/test_sqlite_claims.py`
  - `tests/integration/persistence/test_sqlite_concurrency.py`
- Description: Implement targeted Human Claim and ordered Agent pull as
  single `BEGIN IMMEDIATE` semantic transactions. Selection, stale
  materialization, Claim/Attempt creation, events, and idempotency must commit
  or roll back together.
- Public interface changes:
  - `claim_task(ClaimTaskMutation) -> TaskClaimResult` targets one ready Task
    and creates a null-Attempt Human Claim.
  - `claim_next_task(ClaimNextTaskMutation) -> TaskClaimResult` selects one
    ready Task in the Project and creates a new active Attempt plus Agent Claim.
  - Repeating a current owned Human target returns the existing Claim with no
    Lease extension, TaskEvent, Task version, or `updated_at` change.
  - A current foreign owner token returns `TASK_LOCKED` for targeted Human
    claim. Agent pull skips currently claimed Tasks and returns
    `NO_TASK_AVAILABLE` when no candidate remains.
  - A selected expired Claim is materialized with `claim_expired` before the
    new `task_claimed` event. Agent reclaim always creates a new Attempt ID.
- Inputs:
  - Exact Phase 4 mutations/results, schema version `4`, Claim state helpers,
    readiness ordering, event codecs, and idempotency storage.
- Outputs:
  - Atomic Claim acquisition safe under independent SQLite connections and
    processes.
- Tests:
  - Cover Human/Agent success, deterministic ordering, dependencies,
    availability, active locks, exact expiry, stale cleanup, same-owner Human
    no-op, Agent reclaim, Task-version stability, event order, idempotent replay
    and conflict, injected rollback, restart, and real contention.
- Acceptance criteria:
  - Exactly one contender can own one ready Task, and no failed contender leaves
    an Attempt, Claim, event, idempotency row, or numbering gap.

### Task 7: Implement renewal, heartbeat, explicit release, and lazy expiry

- Deliverables:
  - `src/workaholic/persistence/sqlite/_claim_state.py`
  - `src/workaholic/persistence/sqlite/_task_claims.py`
  - `src/workaholic/persistence/sqlite/repository.py`
  - `src/workaholic/application/results.py`
  - `tests/integration/persistence/test_sqlite_claim_leases.py`
- Description: Implement the shared renewal/release semantics for Human Claims
  and Agent Attempts without hidden renewal, background workers, or Task
  version changes.
- Public interface changes:
  - `renew_claim(RenewClaimMutation) -> TaskClaimResult` accepts a null Attempt
    for Human renewal and exact current Attempt for Agent heartbeat.
  - Renewal replaces expiry with `now + duration`, updates both active Agent
    Attempt and Claim Lease fields atomically, and appends `claim_renewed`.
  - `release_claim(ReleaseClaimMutation) -> TaskClaimResult` deletes the Claim,
    changes an Agent Attempt to `released` with `ended_at = now`, and appends
    `claim_released`; Human release returns a null Attempt.
  - Missing, foreign, expired, released, submitted, or superseded Agent
    Attempts return `LEASE_LOST`. A Human path against an Agent Claim returns
    `TASK_LOCKED`.
- Inputs:
  - Atomic Claim acquisition, duration rules, version `4` records, and exact
    Phase 4 error/idempotency contracts.
- Outputs:
  - Explicit Lease lifecycle operations sharing one persistence implementation.
- Tests:
  - Cover both defaults and every bound, `now == lease_expires_at`, no implicit
    extension, replacement rather than additive renewal, Task-version and
    timestamp stability, release terminal fields, idempotent replay/conflict,
    restart, stale/foreign IDs, and injected rollback.
- Acceptance criteria:
  - Only the exact current owner token can renew or release, and Lease
    correctness is entirely transactional.

### Task 8: Enforce the exclusive Claim lock across Human Task mutations

- Deliverables:
  - `src/workaholic/persistence/sqlite/_claim_state.py`
  - `src/workaholic/persistence/sqlite/_tasks.py`
  - `src/workaholic/persistence/sqlite/_task_lifecycle.py`
  - `src/workaholic/persistence/sqlite/_task_dependencies.py`
  - `src/workaholic/persistence/sqlite/_task_results.py`
  - `src/workaholic/application/results.py`
  - `tests/integration/persistence/test_sqlite_claim_locks.py`
  - existing mutation tests under `tests/integration/persistence/`
- Description: Centralize current-Claim guarding at the SQLite helper boundary
  and apply it to every existing-Task Human mutation. Do not duplicate lock SQL
  or rely on presentation checks.
- Public interface changes:
  - Human update, block, unblock, dependency add/remove, cancellation, and
    submission present owner token `(bootstrap_subject_id, null)`.
  - The same current Human Claim permits update/block/unblock/dependency
    changes and retains ownership. Cancellation and successful Human submission
    delete it in the same transaction.
  - Any active Agent Claim or other owner token returns `TASK_LOCKED` before
    version/transition evaluation and commits nothing.
  - An expired stored Claim does not block a successful Human mutation. That
    transaction first materializes `claim_expired`, then applies the existing
    optimistic mutation; result validators allow the ordered expiry prefix
    without confusing it with the Task-versioning event.
- Inputs:
  - Shared Claim state helper, existing Phase 3 mutation transactions, and
    exact lock/expiry event rules.
- Outputs:
  - One persistence-owned mutation lock consistently protecting all Task write
    paths.
- Tests:
  - Parameterize every Human mutation over unclaimed, owned Human, active Agent,
    and expired Agent Claim states. Assert lock-before-version precedence,
    Claim retention/end rules, event order, one Task version increment only,
    idempotency, no partial writes, and process contention.
- Acceptance criteria:
  - No existing Task mutation path can bypass or inconsistently interpret a
    current Claim.

### Task 9: Persist structured Agent progress and observations

- Deliverables:
  - `src/workaholic/persistence/sqlite/_task_execution.py`
  - `src/workaholic/persistence/sqlite/_event_records.py`
  - `src/workaholic/persistence/sqlite/repository.py`
  - `src/workaholic/application/results.py`
  - `tests/integration/persistence/test_sqlite_task_progress.py`
- Description: Record Agent progress as bounded attributable TaskEvents under
  the exact current Attempt, without introducing a mutable progress entity or
  changing Task lifecycle state.
- Public interface changes:
  - `report_task_progress(ReportTaskProgressMutation) -> TaskProgressResult`
    validates the active Agent owner token and current Lease.
  - Append one `progress_reported` event followed by ordered
    `observation_added` events using the same request and authoritative time.
  - Return the unchanged Task, active Claim/Attempt, validated progress input,
    and committed events.
- Inputs:
  - Progress domain/application models, current Claim validation, TaskEvent
    persistence, and idempotency storage.
- Outputs:
  - Durable polling-visible Agent activity with no separate storage lifecycle.
- Tests:
  - Cover all observation kinds, ordering, empty/oversized/unknown input,
    percent boundaries, Task/version/timestamp stability, stale and foreign
    Attempts, Human Claims, idempotent replay/conflict, event pagination,
    restart, and rollback at every event boundary.
- Acceptance criteria:
  - Progress is attributable and replay-safe but cannot redefine, block, or
    otherwise mutate its Task.

### Task 10: Implement Agent Result submission and terminal Attempt behavior

- Deliverables:
  - `src/workaholic/persistence/sqlite/_task_results.py`
  - `src/workaholic/persistence/sqlite/_result_records.py`
  - `src/workaholic/persistence/sqlite/_claim_state.py`
  - `src/workaholic/persistence/sqlite/repository.py`
  - `src/workaholic/domain/rules.py`
  - `src/workaholic/application/results.py`
  - `tests/integration/persistence/test_sqlite_agent_results.py`
  - `tests/integration/persistence/test_sqlite_task_results.py`
- Description: Extend the established Result/review transaction to Agent
  submission through the exact current Attempt and expected Task version.
- Public interface changes:
  - `submit_agent_result(SubmitAgentResultMutation) -> TaskSubmissionResult`
    requires an active unexpired Agent Claim, matching Attempt, open Task,
    satisfied dependencies, valid Result, and exact expected version.
  - Successful submission stores `TaskResult.attempt_id`, sets Attempt status
    to `submitted` with `ended_at = now`, deletes the Claim, increments Task
    version once, and appends existing Result/completion events.
  - Submission requiring approval still ends the Attempt and Claim while the
    Task enters `review`. Approval/rejection never changes that Attempt;
    rejection returns the Task to `open` for a new Claim.
  - Version conflict, invalid Result/transition, or lost Lease leaves the
    active Claim and Attempt unchanged. Equivalent idempotent replay returns the
    original terminal outcome.
- Inputs:
  - Existing Human Result/review implementation, Claim lock helpers, Agent
    execution models, and Phase 4 submission contract.
- Outputs:
  - One shared Result model with null Human Attempt and exact Agent Attempt
    attribution.
- Tests:
  - Cover done and review submissions, expected-version race, criteria,
    dependencies, exact expiry, stale/released/superseded IDs, rollback,
    idempotency, rejection/new Attempt, approval, event order, one version
    increment, and persistent terminal history after restart.
- Acceptance criteria:
  - No old Agent process can submit after expiry, release, reclaim, or a
    successful earlier submission.

### Task 11: Implement Claim and execution application services

- Deliverables:
  - `src/workaholic/application/task_claims.py`
  - `src/workaholic/application/task_execution.py`
  - `src/workaholic/application/__init__.py`
  - `tests/unit/application/test_task_claims.py`
  - `tests/unit/application/test_task_execution.py`
- Description: Add small application services that resolve defaults, sample the
  authoritative clock exactly once, generate owned identities/events, and
  delegate one validated semantic operation to repository ports.
- Public interface changes:
  - Add `TaskClaimApplication.claim_task`, `claim_next_task`, `renew_claim`, and
    `release_claim`.
  - Add `TaskExecutionApplication.report_progress` and `submit_result`.
  - Extend the identifier dependency with `new_attempt_id`; generate no Agent
    Subject, Token, grant, Claim ID, progress ID, or capability identity.
  - Services derive Actor/Attempt attribution from trusted Session inputs and
    never accept actor, timestamp, request, event, Result, or Attempt identities
    from structured user payloads.
- Inputs:
  - Phase 4 application models/ports, domain duration rules, and implemented
    repository semantics.
- Outputs:
  - Presentation-neutral use cases suitable for LocalSession and the later
    authenticated RemoteSession.
- Tests:
  - Use strict fake repositories/clocks/identifier factories to assert exact
    defaults, one clock sample, identity generation order, Human/Agent wrapper
    mapping, error preservation, malformed dependency outcomes, and no retry or
    hidden refresh.
- Acceptance criteria:
  - Application services contain orchestration only; Claim correctness remains
    in pure rules and semantic persistence transactions.

### Task 12: Extend WorkaholicSession and embedded composition

- Deliverables:
  - `src/workaholic/session/models.py`
  - `src/workaholic/session/base.py`
  - `src/workaholic/session/local.py`
  - `src/workaholic/session/__init__.py`
  - `src/workaholic/composition.py`
  - `tests/unit/session/fakes.py`
  - `tests/unit/session/test_phase_four_models.py`
  - `tests/unit/session/test_phase_four_local_session.py`
  - `tests/unit/test_composition.py`
- Description: Expose explicit Human and Agent request models through the
  transport-neutral Session while reusing the sole embedded bootstrap Subject.
  Keep command-path distinctions visible instead of overloading one ambiguous
  generic request.
- Public interface changes:
  - Add `HumanTaskClaimRequest`, `AgentTaskClaimRequest`,
    `HumanClaimRenewRequest`, `AgentHeartbeatRequest`,
    `HumanClaimReleaseRequest`, `AgentReleaseRequest`, `AgentProgressRequest`,
    and `AgentSubmitRequest`.
  - Add Session methods `claim_task`, `claim_next_task`, `renew_claim`,
    `heartbeat_attempt`, `release_claim`, `release_attempt`, `report_progress`,
    and `submit_agent_result` with explicit result types.
  - Extend `LocalRuntime` with Claim/execution application services and
    `_Uuid7IdentifierFactory` with `atm_` generation.
  - Continue selecting only the initialized bootstrap Subject; do not read or
    write new identity configuration.
- Inputs:
  - Implemented application services, cumulative Session selection/context,
    production SQLite repository, UTC clock, and UUID7 factory.
- Outputs:
  - Fully composed local use cases with no CLI dependency and no Phase 5
    identity surface.
- Tests:
  - Validate closed requests, duration seconds, expected versions, structured
    progress, Result input, method-to-service routing, Project selection,
    deterministic time/IDs, errors, restart, and same-bootstrap attribution.
  - Assert composition adds no network, Token, capability, or credential
    dependency and preserves import-weight boundaries.
- Acceptance criteria:
  - A Session fake and production LocalSession expose identical Phase 4 method
    signatures and outcomes.

### Task 13: Expose Claim acquisition, renewal, heartbeat, and release CLI commands

- Deliverables:
  - `src/workaholic/cli/durations.py`
  - `src/workaholic/cli/task_claims.py`
  - `src/workaholic/cli/task.py`
  - `src/workaholic/cli/options.py`
  - `src/workaholic/cli/serialization.py`
  - `src/workaholic/cli/rendering.py`
  - `tests/unit/cli/test_durations.py`
  - `tests/unit/cli/test_task_claims.py`
  - `tests/unit/cli/test_envelopes.py`
- Description: Add the Human-friendly and Agent-safe ownership commands with
  one explicit dispatch rule: a Task operand selects Human claim, while its
  absence selects Agent pull. Attempt presence similarly selects Agent release.
- Public interface changes:
  - Implement:

    ```text
    workaholic task claim [TASK] [--lease DURATION]
      [--project KEY] [--idempotency-key KEY]
    workaholic task renew TASK [--lease DURATION]
      [--project KEY] [--idempotency-key KEY]
    workaholic task heartbeat TASK --attempt ATTEMPT [--lease DURATION]
      [--project KEY] [--idempotency-key KEY]
    workaholic task release TASK [--attempt ATTEMPT]
      [--project KEY] [--idempotency-key KEY]
    ```

  - Parse duration text to validated seconds without accepting compound values
    or relying on shell utilities.
  - Emit closed JSON Claim/Attempt/event objects; Human output never requires
    or invents an Attempt ID.
  - Preserve JSON-only stdout, diagnostics-only stderr, no prompts under
    `--non-interactive`, and stable error exits.
- Inputs:
  - Phase 4 Session requests/methods, exact CLI contract, existing envelope and
    Project-selection helpers.
- Outputs:
  - Complete local ownership lifecycle commands for Human and Agent paths.
- Tests:
  - Cover help/signatures, every duration form/bound, operand dispatch,
    selected Project, null/non-null Attempt output, Human summaries, all three
    new errors, idempotency keys, unknown fields, stdout/stderr separation, and
    redaction of raw exceptions/paths.
- Acceptance criteria:
  - Scripts can claim and maintain Agent Leases entirely through stable CLI JSON
    while Humans use targeted commands without copying Attempt IDs.

### Task 14: Expose Agent progress and Attempt-backed submission

- Deliverables:
  - `src/workaholic/cli/task_execution.py`
  - `src/workaholic/cli/task_results.py`
  - `src/workaholic/cli/task.py`
  - `src/workaholic/cli/structured_input.py`
  - `src/workaholic/cli/serialization.py`
  - `src/workaholic/cli/rendering.py`
  - `tests/unit/cli/test_task_progress.py`
  - `tests/unit/cli/test_task_submit.py`
  - `tests/unit/cli/test_envelopes.py`
- Description: Add bounded structured progress and extend `task submit` to
  dispatch explicitly to Agent submission when `--attempt` is present, without
  weakening the existing Human convenience or automation version rules.
- Public interface changes:
  - Implement:

    ```text
    workaholic task progress TASK --attempt ATTEMPT --input-file PATH|-
      [--project KEY] [--idempotency-key KEY]
    workaholic task submit TASK --attempt ATTEMPT --expected-version INTEGER
      --result-file PATH|- [--project KEY] [--idempotency-key KEY]
    ```

  - Agent progress requires explicit file/stdin input and Agent submission
    requires structured Result input plus explicit positive expected version in
    every mode.
  - Human submission remains selected by absent `--attempt`, keeps optional
    comment/Result input, and retains interactive expected-version convenience.
  - Serialize current/terminal Attempt data and ordered events without exposing
    database paths, payload echoes in errors, or fabricated Agent identity.
- Inputs:
  - Existing Human submission CLI, Phase 4 execution Session methods, structured
    input limits, and closed output schemas.
- Outputs:
  - Complete Agent execution and submission workflow through public CLI JSON.
- Tests:
  - Cover valid progress/results, stdin/files, unknown/oversized/nested input,
    missing Attempt/result/version, Human/Agent dispatch, Lease loss, version
    conflicts, review outcomes, terminal Attempt output, idempotent retries,
    output streams, and help text.
- Acceptance criteria:
  - An Agent never needs an internal Python API, database access, or interactive
    prompt to report or submit work safely.

### Task 15: Add cumulative Phase 4 conformance and process-concurrency suites

- Deliverables:
  - `tests/contract/phase_four.py`
  - `tests/contract/test_phase_four_persistence.py`
  - `tests/contract/test_phase_four_session.py`
  - `tests/integration/persistence/test_sqlite_concurrency.py`
  - `tests/contract/README.md`
  - concrete SQLite factory wiring under `tests/`
- Description: Add reusable backend-neutral persistence and Session contracts
  for Claims, Attempts, Leases, locks, progress, and Agent Results. Use real
  spawned processes against one SQLite file for double-claim and stale-writer
  proofs; thread-only tests are insufficient for the Phase 4 gate.
- Public interface changes:
  - Add `PhaseFourIdentifierFactory`, `PhaseFourRepositoryFactory`, and
    `PhaseFourSessionFactory` protocols plus deterministic mutation/request
    builders and scoped transaction-failure points.
  - Cumulative Phase 4 contracts inherit all Phase 1-3 behavior and run against
    every later adapter without adapter-specific assertions.
- Inputs:
  - Complete domain, application, SQLite, Session, and CLI-independent Phase 4
    behavior.
- Outputs:
  - One executable semantic contract for current and future persistence/Session
    implementations.
- Tests:
  - Cover Human/two-Agent races, deterministic pull ordering, active and stale
    locks, every owner operation, exact expiry, reclaim, terminal states,
    version stability, Agent submission race, review/rejection, event order,
    idempotency, rollback, restart, and read non-mutation.
  - Assert losers receive only documented typed outcomes and no duplicate
    Claim, Attempt, Result, event, or idempotency state is committed.
- Acceptance criteria:
  - The SQLite adapter passes the full cumulative contract using independent
    connections and real process contention.

### Task 16: Enable the Human-and-Agent Claim golden journey

- Deliverables:
  - `tests/e2e/golden/test_agent_journey.py`
  - `tests/golden.py`
  - `tests/unit/test_golden_contract_helpers.py`
  - `tests/unit/test_golden_journey_inventory.py`
  - `tests/e2e/golden/README.md`
- Description: Replace the outdated skipped Agent specification that provisions
  Phase 5 identities with a real embedded Phase 4 journey using the same
  bootstrap Subject and fresh CLI processes. Exercise both Human and Agent paths
  plus a real Human/two-Agent race.
- Public interface changes:
  - Initialize one isolated SQLite Instance through `workaholic up`; do not call
    future `GoldenInstance` identity orchestration or inject Agent environment
    variables.
  - Run a targeted Human Claim/renew/mutate/submit scenario, an Agent
    claim/heartbeat/progress/submit scenario, an expiry/reclaim scenario, and a
    simultaneous Human-plus-two-Agent race for one Task.
  - Each Agent process retains only its returned Attempt and claimed Task
    version. Agent submission includes both.
- Inputs:
  - Complete Phase 4 CLI, real-process runner, isolated config/data/Workspace
    roots, and cumulative conformance evidence.
- Outputs:
  - Executable evidence for the Local Agent Alpha exit gate and corrected
    pre-Phase-5 attribution semantics.
- Tests:
  - Assert one race winner, loser errors, Human null Attempt UX, Agent non-null
    Attempt, Lease extension, structured progress/event order, lock rejection,
    version stability, stale `LEASE_LOST`, new Attempt on reclaim, terminal
    submission, Result attribution, and process restart.
- Acceptance criteria:
  - `uv run pytest -m golden` passes solo, multi-project, and Agent journeys and
    reports exactly three future-phase skips.

### Task 17: Publish Phase 4 README, documentation, and alpha metadata

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
  - `tests/unit/docs/test_phase_four_contracts.py`
  - `tests/unit/test_package_metadata.py`
- Description: Replace Phase 3 Agent limitations with verified local Claim and
  execution behavior, publish an executable Human/Agent quick start, explain
  schema version `4` reset, and set package metadata to `0.4.0a1`. Keep Phase 5
  identity and all later capabilities visibly deferred.
- Public interface changes:
  - Package version becomes `0.4.0a1`.
  - README documents Human Claim UX, Agent claim/heartbeat/progress/release and
    expected-version submission, exact Lease defaults/bounds, new errors,
    shared-bootstrap attribution, and schema version `3` reset requirements.
  - README quick start uses isolated temporary config/data/Workspace paths and
    exercises both null-Attempt Human and Attempt-backed Agent execution.
- Inputs:
  - Passing Phase 4 golden journey, stable source/wheel behavior, and finalized
    public objects/errors.
- Outputs:
  - Public documentation that describes only tested Phase 4 behavior and
    remains executable as a regression test.
- Tests:
  - Execute the literal README quick start in isolation.
  - Assert package version, schema/reset notices, command inventory, JSON
    fields, errors, Lease behavior, Claim lock, and attribution agree.
  - Assert README does not claim distinct Agent identities, Tokens,
    authentication, remote operation, server support, capabilities,
    JSON/PostgreSQL adapters, migrations, hierarchy, or force interruption.
- Acceptance criteria:
  - A new Human operator and a local Agent process can complete the documented
    workflow without reading architecture documents or touching real state.

### Task 18: Execute the Phase 4 clean-state acceptance gate

- Deliverables:
  - `scripts/verify-phase-4.sh`
  - `scripts/smoke-phase-4-wheel.sh`
  - `tests/e2e/test_phase_4_distribution.py`
  - `tests/unit/scripts/test_verify_phase_four.py`
  - `tests/unit/scripts/test_smoke_phase_four_wheel.py`
  - `.pre-commit-config.yaml`
  - `README.md`
  - `CHANGELOG.md`
  - Phase 4 GitHub epic, milestone, and implementation issues
- Description: Add one fail-fast aggregate acceptance command and execute it
  from a fresh clone with empty temporary config, data, and Workspace roots.
  Validate source and installed-wheel Human/Agent behavior without using the
  operator's real profiles, database, credentials, or GitHub identity.
- Public interface changes:
  - Acceptance command: `scripts/verify-phase-4.sh`.
  - Required clean-state sequence:

    ```bash
    uv sync --frozen
    uv run pre-commit run --all-files
    uv run pytest
    uv build --no-progress
    scripts/smoke-install.sh dist/*.whl
    scripts/smoke-phase-4-wheel.sh dist/*.whl
    ```

  - The wheel smoke journey exercises Human Claim/renew/release, Agent
    claim/heartbeat/progress/release/submit, lock rejection, expiry/reclaim,
    expected-version conflict, review submission, event history, and restart.
- Inputs:
  - Complete Phase 4 implementation, conformance, golden evidence, README, and
    `0.4.0a1` metadata.
- Outputs:
  - Reproducible evidence for the Local Agent Alpha exit gate.
  - A closed Phase 4 epic/milestone only after protected `main` is green.
- Tests:
  - Prove version `3` stores, malformed durations/progress, double claims,
    non-owner writes, stale/foreign Attempts, wrong expected versions,
    idempotency conflicts, failing tests, and malformed wheels fail at the
    documented boundaries.
  - Assert the gate rejects an active virtual environment, dirty tracked files,
    pre-existing build output, or config/data paths outside its owned temporary
    root.
- Acceptance criteria:
  - Required `quality`, `tests`, `build`, and `wheel-smoke` checks pass on the
    protected `main` merge commit.
  - Solo, multi-project, and Agent golden journeys pass; exactly three future
    journeys remain skipped.
  - Source and wheel runs produce identical Claim, Attempt, version, Result,
    event, error, expiry, and idempotency behavior.
  - Acceptance evidence is linked from the Phase 4 epic before closing its
    milestone.

## Operational instructions

1. Create one Phase 4 epic and Tasks 1-18 as implementation issues in a Local
   Agent Alpha milestone. Copy each task's deliverables and acceptance criteria
   into its issue and preserve the order with explicit dependency links.
2. Implement each task from an up-to-date protected `main` on its own narrowly
   named branch, open a PR, wait for all required checks, and merge before the
   next dependent task begins. Do not push directly to `main` or stack schema,
   domain, application, Session, and CLI ownership in unrelated branches.
3. Parallel work is allowed only after shared prerequisites have merged and
   deliverables are disjoint. The exact contract, domain exports, application
   ports, schema, shared Claim-state helper, Session protocol, and CLI
   serializers are sequential ownership boundaries.
4. Every developer installs and runs the existing quality stages:

   ```bash
   uv sync --frozen
   uv run pre-commit install --hook-type pre-commit --hook-type pre-push
   uv run pre-commit run --all-files
   uv run pytest
   uv build --no-progress
   ```

5. Every manual, concurrency, integration, smoke, and acceptance run uses
   absolute test-owned `WORKAHOLIC_CONFIG_DIR` and `WORKAHOLIC_DATA_DIR` values
   plus an isolated Workspace. Never load or modify the operator's default
   profile registry or database.
6. SQLite schema version `4` intentionally rejects Phase 3 version `3`.
   Preserve any development data needed outside Workaholic, verify the exact
   selected data path, and remove only explicitly confirmed disposable data.
   Do not add a migration, broad recursive cleanup, conversion, or silent reset.
7. Use injected authoritative clocks for Lease tests. Wall-clock sleeps are
   permitted only in the final real-process smoke/golden proof and must include
   bounded timing margins; unit, application, Session, and persistence tests
   remain deterministic.
8. Every PR that changes commands, JSON objects, errors, structured input,
   Lease semantics, persistence, or support status updates the corresponding
   normative contracts. Update README only when the changed behavior is
   executable and verified; never publish planned behavior as current.
9. Never accept Subject, actor kind, Attempt, Result, request, event,
   authoritative timestamp, cursor, or Lease-expiry identity from
   repository-controlled context or structured progress/Result input. Generate
   and derive these values through trusted Session/application dependencies.
10. Do not create additional Subjects, Agent credentials, Tokens, grants,
    capability labels/filters, release reasons, remote profiles, endpoints,
    servers, network transport, JSON/PostgreSQL adapters, parent/child Tasks, or
    force-interrupt behavior in Phase 4.
11. Preserve the package dependency direction: domain depends on nothing;
    application depends on domain; persistence and Session implement
    application-owned ports; CLI depends on Session; composition alone selects
    concrete adapters.
12. Before merging the final task, execute `scripts/verify-phase-4.sh` from a
    fresh clone, smoke the built wheel, confirm all protected checks on the
    exact merge commit, and only then tag or publish `0.4.0a1` according to the
    repository's release procedure.
