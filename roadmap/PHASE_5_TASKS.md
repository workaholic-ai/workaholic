# Phase 5 Implementation Tasks

## Purpose

Deliver authenticated local coordination: every independently operating Human
or Agent uses a distinct Subject and revocable credential, every Project read
and mutation is authorized through a cumulative role, and existing Claim and
Attempt ownership is enforced against the authenticated Subject.

Tasks are ordered by dependency. Each task is independently reviewable and is
intended to produce one focused commit on the local Phase 5 branch after every
concrete input listed for that task has landed. Every task must preserve the
existing Phase 0-4 behavior, public quick start, package boundaries, quality
controls, and protected-branch checks.

## Repository state at planning time

The following deliverables already exist and must be extended rather than
recreated:

- Python 3.14 package metadata, locked dependencies, pre-commit hooks, strict
  Ruff and mypy checks, import-boundary enforcement, least-privilege GitHub
  Actions, source/wheel builds, and clean-checkout acceptance infrastructure;
- dependency-free cumulative domain models, strict Pydantic application and
  Session boundaries, embedded `LocalSession`, an explicit composition root,
  and the cumulative SQLite repository facade;
- one real bootstrap Human Subject, Instance-administrator status, one Owner
  ProjectGrant, authenticated-subject-shaped application commands, and
  attributable TaskEvents without bearer credentials;
- trusted embedded profiles, safe upward Workspace discovery, multiple named
  Projects, stable Task keys, dependencies, optimistic Task versions, Results,
  review, exclusive Claims, Agent Attempts, Leases, progress, and event history;
- SQLite schema version `4`, atomic and idempotent Phase 0-4 operations,
  deterministic pagination, lazy Lease expiry, and injected authoritative
  clocks;
- versioned `workaholic.cli/v1` success and error envelopes, JSON-only stdout,
  non-interactive behavior, structured file/stdin input, Human-readable
  rendering, and interactive expected-version convenience;
- cumulative persistence and Session conformance suites, real-process golden
  journeys, installed-wheel smoke tests, executable README checks, and Phase
  0-4 clean-state gates;
- accepted identity direction in
  `docs/adr/0007-human-and-agent-identity-model.md` and the owner-approved Phase
  5 decisions captured below; and
- an empty `workaholic.auth` package boundary that must be populated rather
  than replaced or bypassed.

No duplicate package bootstrap, quality-control, README-governance, community,
repository-management, profile, Workspace-context, Task-lifecycle, Result,
Claim, Attempt, Lease, or CI-foundation task is required. Phase 5 remains
embedded and SQLite-only; network transport, a server, `RemoteSession`, remote
profiles, and protocol authentication remain Phase 6 work.

## Confirmed Phase 5 decisions

Implementation and documentation must consistently encode these
owner-approved decisions:

- Normal operations authenticate exactly one bearer Token after bootstrap or
  explicit local recovery. An absent credential fails with
  `AUTHENTICATION_REQUIRED`; an invalid, expired, revoked, or disabled
  credential fails closed with `AUTHENTICATION_FAILED`. An invalid explicit
  credential never falls back to another identity.
- Phase 5 supports one organization per Instance. Organization membership is
  not a Project authorization grant, and Instance-administrator status is
  separate from Project roles.
- Each independently operating Human or Agent has one Subject. Subject handles
  are unique within the Instance, immutable, stable automation identifiers, and
  distinct from mutable display names. Subject kind is immutable.
- Project roles are cumulative in the exact order
  `viewer < agent < operator < owner`. One Subject has at most one stored role
  per Project; assigning another role replaces that grant through optimistic
  concurrency rather than stacking rows.
- An Instance administrator can manage Projects, Subjects, administrator
  status, and Tokens across the Instance. That status does not grant ordinary
  Task data access; a ProjectGrant is still required.
- Viewer can read Project Tasks, Results, Claims, Attempts, and TaskEvents.
  Agent adds Agent claim, heartbeat, progress, release, and submission.
  Operator adds Task creation and Human definition, dependency, state, Claim,
  Result, and review mutations. Owner adds ProjectGrant administration.
- Subject kind does not grant a role. Agent execution commands require an
  Agent Subject as well as the Agent permission. Human Claim/renew/release
  commands require a Human Subject as well as Operator permission. Other
  Operator operations are role-controlled, regardless of Subject kind.
- A current Claim remains an exclusive mutation lock. Its owning Subject may
  continue only through the correct Human null-Attempt or Agent exact-Attempt
  path. A non-owning authenticated Subject cannot mutate it, even when that
  Subject has Operator, Owner, or Instance-administrator authority.
- Revoking one Token or disabling its Subject stops new authenticated
  operations immediately. Neither action force-releases a Claim or interrupts
  an external process. The Claim becomes available only through existing
  explicit release, submission, cancellation, or Lease-expiry semantics.
- Claim ownership belongs to the Subject, not to one of its Tokens. Another
  valid Token for the same Subject may continue an owned Attempt when it also
  supplies the exact current Attempt ID.
- A Project must retain at least one enabled Owner, and the Instance must
  retain at least one enabled Instance administrator. Grant changes,
  administrator removal, and Subject disablement enforce both invariants
  atomically.
- One Subject may have multiple independently expiring and revocable Tokens.
  Raw secrets are revealed only through an explicit protected output boundary,
  are never recoverable from persistence, and are never stored in Workspace
  context, task data, Results, events, diagnostics, or normal logs.
- A Subject may inspect and revoke its own Token metadata. An Instance
  administrator may issue, inspect, and revoke Tokens for any Subject. No
  Subject or Token deletion exists in v1.
- Human credentials use an operating-system credential store when available
  with an account-protected file fallback. Agents use trusted process
  environment, mounted-secret files, or orchestrator injection. Phase 5 does
  not add an identity field to `.workaholic.env` or allow credentials in
  `profiles.toml`.
- Capability labels and filtering remain on the post-v1 backlog. An Agent with
  a Project grant is assumed capable of work it pulls.
- Persistence changes advance disposable SQLite schema version from `4` to
  exact version `5`. Version `4` is rejected unchanged; Phase 5 adds no
  migration, conversion, import, export, or silent reset.

## Exact Phase 5 behavioral baseline

The implementation tasks use the following concrete contracts so no developer
must invent a security, credential, or concurrency boundary while coding.

### Subject and grant contracts

- Subject handles match `^[a-z][a-z0-9-]{1,62}$`, are compared byte-for-byte,
  and are unique within one Instance. Handles are never renamed or reused.
  Display names are trimmed printable text from 1 through 200 characters and
  may be updated. The bootstrap handle is `local-operator`.
- A Subject stores its Instance, kind, handle, display name, enabled state,
  Instance-administrator state, positive version, creator, and UTC creation and
  update timestamps. Create returns version `1`; update, enable/disable, and
  administrator changes each increment the Subject version once.
- Human and Agent Subjects are created enabled and non-administrative. Only an
  enabled Instance administrator may create or update Subjects, grant or revoke
  Instance-administrator status, or issue a Token for another Subject.
- A ProjectGrant stores one cumulative role, positive version, granting actor,
  and UTC creation/update timestamps. First assignment creates version `1`;
  role replacement increments once; assigning the already-current role is an
  idempotent no-op only through equivalent idempotency replay.
- Grant replacement and revocation require the current grant version for JSON,
  non-interactive, or non-terminal automation. An interactive Human may omit
  it; the CLI performs one read, submits that exact version once, and never
  refreshes or retries a conflict. New grant creation has no expected version.
- `auth grant` and `auth revoke-grant` require Owner on the target Project or
  Instance-administrator status. An ordinary Project Owner cannot grant or
  revoke Instance-administrator status, manage Tokens, or manage a different
  Project.
- Disabling a Subject is rejected if that action would leave no enabled
  Instance administrator or no enabled Owner in any affected Project. Revoking
  administrator status or an Owner grant applies the corresponding same guard.
  Disabled Subjects cannot receive new grants or Tokens until re-enabled.

### Token and authentication contracts

- Token IDs use the existing opaque identifier convention with prefix `tok_`.
  A raw Token has exact serialized form `<token-id>.<secret>`, where `secret`
  is unpadded URL-safe base64 for 32 bytes generated by `secrets.token_bytes`.
  Parsing is bounded and rejects whitespace, alternate alphabets, extra
  separators, malformed IDs, and non-canonical base64.
- Persistence stores only lowercase SHA-256 of the complete canonical raw Token
  and uses constant-time digest comparison after an indexed Token-ID lookup.
  The 256-bit random secret is the defense against offline guessing; Phase 5
  does not introduce a database-resident pepper or password-hashing semantics.
- Tokens store Subject and Instance IDs, creator, UTC creation, activation,
  expiry, and optional revocation timestamps and actor. A Token authenticates
  only when activated, not revoked, its Subject is enabled, and authoritative
  transaction time satisfies `now < expires_at`.
- Token creation defaults to `30d` for a Human and `24h` for an Agent. Human
  Tokens accept `1h` through `365d`; Agent Tokens accept `5m` through `30d`.
  Duration text uses the existing single-unit grammar
  `^[1-9][0-9]*(s|m|h|d)$`. Renewal means issue a new Token; expiry is never
  extended in place.
- Token creation requires `--token-file ABSOLUTE_PATH`. The target must not
  exist, must be outside the discovered Workspace and any detected Git
  worktree/repository, and is created atomically without following a symlink at
  mode `0600` under a non-group/world-writable parent. Raw Tokens are never
  written to stdout, JSON, diagnostics, or a caller-supplied normal positional
  argument. A non-POSIX platform must verify equivalent current-user-only
  access or fail closed.
- Provisioning uses pending, active, expired, and revoked lifecycle
  projections. A pending Token cannot authenticate. The credential sink is
  written before activation; activation and its audit event are atomic. On
  failure, the pending Token is revoked and any just-created credential file
  is removed by bounded compensation. A retry after completed compensation
  receives a new Token ID and secret.
- The public Token-provisioning idempotency key is consumed only by activation.
  After a crash, a same-key retry may validate an existing protected Token file
  and resume its matching pending Token or replay activated metadata without
  rewriting the file. A compensated failure may reuse the key for a new Token.
  An absent output after committed activation cannot be reconstructed and must
  be listed, revoked, and replaced.
- Normal credential-source precedence is: non-empty `WORKAHOLIC_TOKEN`, then
  non-empty `WORKAHOLIC_TOKEN_FILE`, then the selected profile's Human
  credential store. Supplying both process variables is invalid. Empty values
  count as absent. A selected explicit source is authoritative: parse, file,
  authentication, expiry, revocation, or Subject failure never falls through.
- `WORKAHOLIC_TOKEN_FILE` must be an absolute path to a bounded readable UTF-8
  regular file. Symlinks are resolved to support orchestrator-mounted secrets;
  the resolved target must be regular, at most 512 bytes, and not group/world
  writable. The file contains exactly one canonical Token plus an optional
  final newline and is never modified by authentication. Non-POSIX access must
  be equivalently account-restricted.
- Human credential entries are scoped by trusted profile and include expected
  Instance and Subject IDs. Instance mismatch fails closed. The default
  backend selects an available operating-system keyring and falls back to
  `credentials.toml` only when no keyring backend exists, never after a keyring
  operation error. `WORKAHOLIC_CREDENTIAL_BACKEND=auto|keyring|file` may make
  the trusted process choice explicit.
- The fallback credential file lives in a dedicated `credentials` directory
  beneath the resolved Workaholic configuration directory. It is a bounded
  non-symlink UTF-8 TOML file created atomically with mode `0600`, and its
  dedicated directory is created at mode `0700`. It is limited to 1,048,576
  bytes and may contain raw Human Tokens but no task, URL, executable, or
  remote-profile data. A platform that cannot verify equivalent account-only
  access returns `CREDENTIAL_UNAVAILABLE`.
- File-backed Human and Agent credentials are rejected when their resolved path
  is inside a discovered Workspace or Git worktree/repository.
- `auth login --token-file PATH|-` explicitly reads one Token, authenticates it
  against the selected profile, requires a Human Subject, and stores it without
  echoing it. `-` means explicit bounded stdin. `auth logout` removes only that
  profile's Human credential. Logout and local credential replacement do not
  revoke the server-side Token.
- `up` on a new store bootstraps `local-operator`, an Owner grant, and a pending
  Human administrator Token, stores the credential, then activates it. After
  initialization, `up` uses normal authentication. An explicit
  `auth recover-local` route is the only tokenless recovery path: it works only
  for an embedded profile under the trusted operating-system account, requires
  confirmation of the exact Instance ID and bootstrap handle in
  non-interactive mode, revokes every existing Token for that bootstrap
  Subject, and installs one fresh Human Token. Recovery never changes Subject,
  administrator, Owner, Project, Task, Claim, or Attempt state.

### Authorization and audit contracts

- Authentication yields an immutable `AuthenticatedActor` containing exact
  Instance, Subject, Subject kind, and Token IDs. Raw Token material does not
  cross into application command models, Sessions, repositories, TaskEvents,
  idempotency records, or result envelopes.
- Every persistence query checks the actor's enabled Subject, active Token at
  authoritative time, selected Instance, and required ProjectGrant in one read
  transaction. Every mutation repeats those checks in the same write
  transaction as the domain mutation so revocation, disablement, or grant
  removal cannot race an already-composed command.
- Project listing returns only Projects with a current grant. Project and Task
  queries require Viewer. Project binding is a local filesystem action but may
  bind only a Project visible to the authenticated Subject. Project creation
  requires Instance-administrator status and grants the creator Owner.
- Agent execution requires Agent or stronger Project role, Agent Subject kind,
  and exact current Claim/Attempt ownership. Human Claim operations require
  Operator or stronger role, Human Subject kind, and null Attempt ownership.
  Task and review mutations require Operator; grant management requires Owner.
- Task definition or lifecycle mutation by an Agent Subject with Operator role
  is permitted only when no current Claim blocks it. An Agent-owned Claim must
  still be released or submitted through its exact Attempt path first.
- Token status is rechecked for every operation, but Claim ownership is stored
  by Subject. Revoking one Token does not invalidate another active Token for
  that Subject and does not rewrite Claim or Attempt history.
- Existing TaskEvents persist the authenticated Subject ID and immutable kind
  snapshot plus request and optional Attempt attribution. Phase 5 adds an
  append-only `AuditEvent` stream for Instance bootstrap, Project creation,
  Subject lifecycle, administrator changes, grant changes, and Token issue or
  revocation. Audit payloads contain identifiers and non-secret change facts,
  never a raw Token, Token hash, credential path, environment value, or keyring
  locator.
- Tokenless bootstrap and recovery AuditEvents are self-attributed to the
  bootstrap Human and have null actor Token. Every authenticated event records
  its actor Token. Exact closed payloads are the identifier, version, role,
  expiry, and changed-field sets fixed by ADR 0013 and the CLI/persistence
  contracts; Phase 5 `changed_fields` contains only `display_name`.
- Security-administration mutations accept idempotency keys. Their
  fingerprints bind operation, authenticated Subject, target Instance,
  Subject/Project/Token identities, requested role/state/version, and complete
  non-secret payload. Idempotency never bypasses fresh authentication or
  authorization: replay first validates the current actor and then returns the
  original closed outcome only for an equivalent request.
- `auth events` is bounded, cursor-paginated, ordered by increasing cursor, and
  restricted to Instance administrators. It reuses the TaskEvent-style
  nonnegative `after`/`next_cursor` contract. Subject, grant, and Token listings
  use actor/scope-bound canonical `v5.` cursors, stable handle/creation ordering,
  and never expose Token hashes or raw secrets.
- Authentication failures deliberately collapse missing Token, wrong digest,
  pending/expired/revoked Token, disabled Subject, and Instance mismatch into
  the same public failure. Authorization failures do not disclose Projects,
  Claims, Attempts, Subjects, or Tokens outside the actor's administrative or
  Project scope.

### Public command baseline

Phase 5 adds or completes these commands:

```text
workaholic auth whoami
workaholic auth login --token-file PATH|-
workaholic auth logout
workaholic auth recover-local --instance INSTANCE --subject local-operator
workaholic auth create-human HANDLE [--display-name NAME]
workaholic auth create-agent HANDLE [--display-name NAME]
workaholic auth list-subjects
workaholic auth update-subject SUBJECT --display-name NAME
workaholic auth enable-subject SUBJECT
workaholic auth disable-subject SUBJECT
workaholic auth grant-admin SUBJECT
workaholic auth revoke-admin SUBJECT
workaholic auth grant SUBJECT viewer|agent|operator|owner --project PROJECT
workaholic auth list-grants --project PROJECT
workaholic auth revoke-grant SUBJECT --project PROJECT
workaholic auth create-token SUBJECT --token-file PATH [--expires-in DURATION]
workaholic auth list-tokens [SUBJECT]
workaholic auth revoke-token TOKEN
workaholic auth events [--after CURSOR] [--limit LIMIT]
```

Every authenticated mutation command supports `--idempotency-key`. Existing
global `--profile`, `--json`, and `--non-interactive` behavior remains in force.
Subject operands accept an exact handle or opaque Subject ID; Project operands
accept an exact key or opaque Project ID; Token operands accept only the
non-secret Token ID.

Phase 5 adds these exact public errors in addition to the existing cumulative
set:

| Code | Exit | Retryable | Exact safe message |
| --- | ---: | :---: | --- |
| `AUTHENTICATION_REQUIRED` | 5 | false | `Authentication is required.` |
| `AUTHENTICATION_FAILED` | 5 | false | `The supplied credential is not valid.` |
| `SUBJECT_NOT_FOUND` | 3 | false | `The Subject was not found.` |
| `SUBJECT_HANDLE_CONFLICT` | 4 | false | `The Subject handle is already in use.` |
| `TOKEN_NOT_FOUND` | 3 | false | `The Token was not found.` |
| `GRANT_NOT_FOUND` | 3 | false | `The ProjectGrant was not found.` |
| `IDENTITY_VERSION_CONFLICT` | 4 | false | `The identity or grant changed after the expected version.` |
| `LAST_INSTANCE_ADMIN` | 4 | false | `The Instance must retain an enabled administrator.` |
| `LAST_PROJECT_OWNER` | 4 | false | `The Project must retain an enabled Owner.` |
| `CREDENTIAL_UNAVAILABLE` | 10 | false | `The credential store is unavailable.` |

`PERMISSION_DENIED` remains the non-disclosing authorization outcome. Malformed
handles, Tokens, durations, versions, credential paths, ambiguous credential
sources, and structured inputs use `INVALID_INPUT`. Credential filesystem and
keyring failures use `CREDENTIAL_UNAVAILABLE` without exposing a secret or
private path.

### Task 1: Complete the exact Phase 5 delivery contracts

- Deliverables:
  - `docs/architecture.md`
  - `docs/cli-contract.md`
  - `docs/persistence-contract.md`
  - `docs/glossary.md`
  - `docs/threat-model.md`
  - `docs/roadmap.md`
  - `docs/adr/0007-human-and-agent-identity-model.md`
  - `docs/adr/0013-phase-five-token-and-authorization-model.md`
  - `tests/unit/docs/test_phase_five_contracts.py`
- Description: Record the owner-approved identity, credential, role, Claim,
  recovery, and lifecycle decisions plus every exact baseline above before
  implementation. Keep README on verified Phase 4 behavior until the Phase 5
  golden journey passes.
- Public interface changes:
  - Normatively define the command signatures, closed result objects, error
    codes, role matrix, Token format/lifetimes, credential precedence, audit
    events, and schema version `5` behavior.
  - Record explicit local bootstrap/recovery as the only tokenless paths and
    preserve Phase 6 as transport-only work over the same authorization core.
- Inputs:
  - ADR 0007, cumulative Phase 4 contracts, and all confirmed decisions and
    behavioral baselines in this plan.
- Outputs:
  - One implementation contract covering authentication, authorization,
    provisioning, recovery, concurrency, redaction, and presentation.
- Tests:
  - Assert every normative document agrees on handles, roles, permission
    matrix, credential sources, Token lifecycle, lock behavior, audit, errors,
    and schema version.
  - Assert Phase 5 does not add network services, remote profiles, capabilities,
    SSO/OAuth, custom roles, hierarchy, schema migration, or force interruption.
- Acceptance criteria:
  - Later tasks can implement each boundary without making another product or
    security decision.

### Task 2: Add dependency-free identity and authorization domain rules

- Deliverables:
  - `src/workaholic/domain/identifiers.py`
  - `src/workaholic/domain/enums.py`
  - `src/workaholic/domain/models.py`
  - `src/workaholic/domain/rules.py`
  - `src/workaholic/domain/__init__.py`
  - `tests/unit/domain/test_identifiers.py`
  - `tests/unit/domain/test_models.py`
  - `tests/unit/domain/test_rules.py`
  - `tests/unit/domain/test_phase_five_identity.py`
- Description: Extend the pure domain with full Subjects, Tokens, cumulative
  roles, explicit permissions, authenticated actor context, and administrative
  audit records without importing Pydantic, keyring, persistence, Sessions,
  Typer, environment state, or clocks with hidden global state.
- Public interface changes:
  - Add `TokenId` and `AuditEventId` opaque identifiers and extend identifier
    generation contracts later through application ports.
  - Add Agent Subject kind; Viewer, Agent, and Operator roles; `Permission`,
    `TokenStatus`, `AuditEventType`, `Token`, `TokenSummary`, `AuditEvent`, and
    `AuthenticatedActor`.
  - Extend `Subject` and `ProjectGrant` with the exact Instance, handle,
    version, attribution, and timestamp fields in the baseline.
  - Add pure handle normalization, role implication, permission checks, Token
    lifecycle projection, expiry, last-admin/last-owner inputs, and safe audit
    payload validation.
- Inputs:
  - Merged Task 1 contracts and existing Claim/Attempt/Task domain invariants.
- Outputs:
  - A dependency-free source of truth for every Phase 5 identity and
    authorization invariant.
- Tests:
  - Exhaust handle boundaries, Unicode/control rejection, immutable identity,
    role ordering, permission matrix, UTC and half-open expiry, version and
    attribution constraints, closed audit payloads, and defensive immutability.
  - Prove no domain module imports Pydantic, keyring, SQLite, Session, CLI, or
    process environment APIs.
- Acceptance criteria:
  - Pure tests express every role, Subject, Token, and audit invariant without
    I/O or backend assumptions.

### Task 3: Define Phase 5 application commands, results, errors, and ports

- Deliverables:
  - `src/workaholic/application/commands.py`
  - `src/workaholic/application/results.py`
  - `src/workaholic/application/errors.py`
  - `src/workaholic/application/ports.py`
  - `src/workaholic/application/__init__.py`
  - `tests/unit/application/test_commands.py`
  - `tests/unit/application/test_phase_five_contracts.py`
- Description: Add strict Pydantic boundaries and narrow dependency-inversion
  ports for authentication, Subjects, Tokens, grants, administrator status,
  audit queries, and transactional authorization rechecks. Keep raw Token and
  credential storage outside normal application command/result models.
- Public interface changes:
  - Add commands for every public identity mutation plus `AuthenticateToken`,
    `GetCurrentIdentity`, Subject/Token listing, and audit pagination.
  - Add closed results for identity, Subject pages, Token metadata pages,
    ProjectGrant changes, and AuditEvent pages; no result contains a raw Token
    or Token hash.
  - Add exact public errors from the baseline and internal typed outcomes for
    invalid authentication, invariant guards, credential provisioning, and
    authorization races.
  - Add narrow `AuthenticationRepository`, `SubjectRepository`,
    `TokenRepository`, `GrantRepository`, `AuditRepository`, and identifier
    factory protocols. Existing Task ports accept `AuthenticatedActor` instead
    of a caller-supplied Subject ID.
- Inputs:
  - Merged Task 2 domain exports and exact Task 1 CLI/persistence contracts.
- Outputs:
  - Stable, transport-neutral application interfaces implementable by SQLite
    now and a Phase 6 server without semantic changes.
- Tests:
  - Reject raw secrets, actor overrides, unknown fields, untyped identifiers,
    invalid versions, ambiguous targets, unbounded pagination, and unsafe audit
    payloads at model construction.
  - Assert error exit/retry/message mappings and that raw credential types
    cannot enter Task or identity-administration result models.
- Acceptance criteria:
  - Persistence and Session tasks can compile against explicit ports without
    importing CLI or credential adapters.

### Task 4: Introduce disposable SQLite schema version 5

- Deliverables:
  - `src/workaholic/persistence/sqlite/schema.py`
  - `src/workaholic/persistence/sqlite/connection.py`
  - `tests/unit/persistence/test_sqlite_schema.py`
  - `tests/contract/test_phase_five_persistence.py`
- Description: Replace the disposable Phase 4 schema with exact version `5`,
  extending existing tables and adding Token and administrative-audit storage.
  Preserve strict SQLite constraints, foreign-key direction, and fail-closed
  version validation without migration code.
- Public interface changes:
  - `subjects` gains Instance, handle, version, creator, and timestamps and
    accepts Human or Agent kind with Instance-scoped handle uniqueness.
  - `project_grants` gains Instance, all four roles, version, attribution, and
    timestamps with cross-Instance grants prevented by composite foreign keys.
  - Add `tokens` with unique hash, pending/activation/expiry/revocation
    constraints and no raw secret column.
  - Add append-only `audit_events`, stable cursor index, and bounded JSON
    payload. Extend TaskEvents to accept both Subject kinds and retain immutable
    actor-kind snapshots.
  - Extend idempotency operations for Subject, Token, grant, administrator,
    Project, and recovery mutations without persisting secret-bearing request
    data or outcomes.
- Inputs:
  - Merged Tasks 1-3 contracts and models plus the exact Phase 4 schema.
- Outputs:
  - A fresh schema capable of enforcing structural Phase 5 invariants before
    repository code runs.
- Tests:
  - Inspect every table, index, check, unique constraint, and foreign key;
    exercise malformed kinds/roles/handles/hashes/timestamps/versions and
    cross-Instance relationships directly.
  - Prove exact version `4`, older/newer/missing/malformed stores are rejected
    byte-for-byte unchanged and that no migration/reset path exists.
- Acceptance criteria:
  - An empty database initializes as exact version `5`; unsupported databases
    cannot be read or mutated.

### Task 5: Implement canonical Token generation, parsing, and hashing

- Deliverables:
  - `src/workaholic/auth/tokens.py`
  - `src/workaholic/auth/models.py`
  - `src/workaholic/auth/errors.py`
  - `src/workaholic/auth/__init__.py`
  - `tests/unit/auth/test_tokens.py`
  - `tests/unit/auth/test_models.py`
- Description: Populate the existing auth boundary with small explicit raw
  credential types and stateless Token primitives. Keep secrets out of
  repr/str, exceptions, normal serialization, application models, and logs.
- Public interface changes:
  - Add an opaque `RawToken` secret wrapper with redacted representation and an
    explicit zero-copy access method limited to credential/authentication
    adapters.
  - Add `generate_token`, `parse_token`, `hash_token`, and
    `verify_token_digest` implementing the exact canonical format and
    constant-time comparison contract.
  - Add injectable random-byte generation for deterministic tests while the
    production default uses `secrets.token_bytes(32)`.
- Inputs:
  - Merged Token domain/application contracts and `TokenId` generation.
- Outputs:
  - One reusable, fully tested secret primitive used by bootstrap, login,
    provisioning, environment, file, and persistence authentication paths.
- Tests:
  - Cover entropy length, canonical base64, malformed separators/IDs/padding,
    empty/oversized/non-ASCII/whitespace values, deterministic hashing,
    constant-time API use, and redacted repr/errors.
  - Assert captured logs, tracebacks, Pydantic dumps, and common formatting do
    not contain the raw secret.
- Acceptance criteria:
  - No later component reimplements Token parsing, generation, hashing, or
    redaction.

### Task 6: Resolve and validate Agent credential sources

- Deliverables:
  - `src/workaholic/auth/sources.py`
  - `src/workaholic/context/paths.py`
  - `.env.example`
  - `tests/unit/auth/test_sources.py`
  - `tests/unit/context/test_paths.py`
  - `tests/unit/test_package_metadata.py`
- Description: Implement strict process-environment and mounted-file credential
  discovery before composing an authenticated runtime. Preserve trusted profile
  selection and forbid any secret lookup through Workspace context.
- Public interface changes:
  - Add exact support for `WORKAHOLIC_TOKEN`, `WORKAHOLIC_TOKEN_FILE`, and
    `WORKAHOLIC_CREDENTIAL_BACKEND` with the precedence and ambiguity behavior
    in the baseline.
  - Add a bounded Token-file reader that resolves orchestrator symlinks,
    validates the final file, and returns only `RawToken`.
  - Document the variables in `.env.example` as trusted process inputs with
    empty safe defaults; explicitly prohibit copying them into
    `.workaholic.env`.
- Inputs:
  - Merged Task 5 Token primitive and existing trusted context/path helpers.
- Outputs:
  - One deterministic explicit credential source or an exact absence/error
    outcome, with no persistence or authentication side effect.
- Tests:
  - Cover precedence, both-variable rejection, empty values, type/path/null
    errors, relative paths, symlink targets, FIFOs/directories, permissions,
    UTF-8, final newline, byte limit, TOCTOU-resistant open behavior, and
    environment non-mutation.
  - Assert hostile `.workaholic.env` and `profiles.toml` Token fields remain
    rejected and no secret appears in diagnostics.
- Acceptance criteria:
  - Agent processes can consume direct or mounted credentials safely without
    adding a repository-controlled credential surface.

### Task 7: Implement profile-scoped Human credential storage

- Deliverables:
  - `src/workaholic/auth/credentials.py`
  - `src/workaholic/auth/keyring_store.py`
  - `src/workaholic/auth/file_store.py`
  - `src/workaholic/context/models.py`
  - `src/workaholic/context/paths.py`
  - `pyproject.toml`
  - `uv.lock`
  - `tests/unit/auth/test_credentials.py`
  - `tests/unit/auth/test_keyring_store.py`
  - `tests/unit/auth/test_file_store.py`
- Description: Add an explicit credential-store protocol, an operating-system
  keyring adapter using a locked battle-tested dependency, and a crash-safe
  protected TOML fallback. Treat fallback as a controlled downgrade, not as a
  retry after an operational keyring failure.
- Public interface changes:
  - Add `HumanCredential`, `CredentialStore`, and a selector implementing
    `auto`, `keyring`, and `file` behavior.
  - Extend local config paths with exact `credentials.toml` ownership without
    changing `profiles.toml` grammar or storing endpoints/secrets there.
  - Store one credential per trusted profile with expected Instance and Human
    Subject identities; replace and delete entries explicitly for login/logout.
- Inputs:
  - Merged Tasks 5-6 auth primitives, environment contract, and path boundary.
- Outputs:
  - Tested keyring and protected-file backends with stable error/redaction
    behavior and no CLI dependency.
- Tests:
  - Use fake keyring and real temporary files to cover unavailable versus
    failing backends, profile isolation, replacement, missing entries, Instance
    mismatch metadata, atomic writes, fsync/replace failures, exact modes,
    symlink/race rejection, malformed TOML, permissions, limits, and cleanup.
  - Assert no credential material enters logs, exceptions, repr, test snapshots,
    `profiles.toml`, or Workspace context.
- Acceptance criteria:
  - Humans can persist an authenticated profile safely on supported desktop and
    headless systems through one adapter-neutral contract.

### Task 8: Persist Subject lifecycle and administrator invariants

- Deliverables:
  - `src/workaholic/persistence/sqlite/_subjects.py`
  - `src/workaholic/persistence/sqlite/_authorization.py`
  - `src/workaholic/persistence/sqlite/_records.py`
  - `src/workaholic/persistence/sqlite/repository.py`
  - `tests/unit/persistence/test_sqlite_subjects.py`
  - `tests/contract/test_phase_five_persistence.py`
- Description: Implement transactional Subject create/list/get/update,
  enable/disable, and Instance-administrator grant/revoke operations. Centralize
  identity lookups and last-enabled-administrator enforcement for reuse.
- Public interface changes:
  - Resolve Subject operands by exact handle or typed ID within the actor's
    Instance; never interpret display name as identity.
  - Store stable versions/attribution and apply optimistic concurrency to every
    existing-Subject state mutation.
  - Enforce enabled-administrator authority and the last-administrator guard in
    the same write transaction as the change.
- Inputs:
  - Merged schema, domain, and application ports. Authentication may use a
    fixture actor until Task 9 lands.
- Outputs:
  - Atomic Subject persistence semantics with no deletion or handle/kind
    mutation path.
- Tests:
  - Cover Human/Agent creation, uniqueness races, pagination/order, display
    changes, enable/disable, admin promotion/demotion, stale versions,
    idempotent replay/conflict, last-admin races, disabled targets, rollback,
    restart, and cross-Instance denial.
- Acceptance criteria:
  - Concurrent administrators cannot remove the final enabled administrator or
    create ambiguous Subject identities.

### Task 9: Persist Token lifecycle and transaction-time authentication

- Deliverables:
  - `src/workaholic/persistence/sqlite/_tokens.py`
  - `src/workaholic/persistence/sqlite/_authentication.py`
  - `src/workaholic/persistence/sqlite/_authorization.py`
  - `src/workaholic/persistence/sqlite/repository.py`
  - `tests/unit/persistence/test_sqlite_tokens.py`
  - `tests/integration/persistence/test_sqlite_authentication_races.py`
  - `tests/contract/test_phase_five_persistence.py`
- Description: Implement pending issue/activate, list, revoke, and digest-based
  authentication while keeping raw Tokens outside SQLite. Add one shared
  transaction-time actor revalidation helper for every later query/mutation.
- Public interface changes:
  - Token issue accepts only a canonical hash and metadata; activation is a
    distinct authenticated semantic operation used by the provisioning
    coordinator. Token hash and pending state are never publicly projected.
  - Authentication parses the Token ID outside persistence, performs one
    indexed row lookup, verifies the digest, and returns `AuthenticatedActor`
    only for an active Token and enabled Subject in the expected Instance.
  - Self list/revoke and administrator list/revoke apply non-disclosing target
    semantics and fresh actor authentication in one transaction.
- Inputs:
  - Merged Tasks 4-5 schema/token primitives, Subject repository, and injected
    authoritative clock.
- Outputs:
  - A complete durable bearer-credential lifecycle and reusable fresh-actor
    revalidation operation.
- Tests:
  - Cover pending, activation, exact expiry, revocation, disabled Subject,
    multiple Tokens, wrong digest/ID/Instance, hash uniqueness, metadata
    redaction, self/admin access, idempotency, restart, rollback, and no
    background expiry writes.
  - Use independent processes/connections to race authenticate with revoke,
    disable, activation, and expiry; no mutation may commit after the
    transaction's authentication check loses.
- Acceptance criteria:
  - Revocation, expiry, and disablement are immediate at the next operation and
    no stored value can reconstruct a raw Token.

### Task 10: Persist ProjectGrant lifecycle and ownership safeguards

- Deliverables:
  - `src/workaholic/persistence/sqlite/_grants.py`
  - `src/workaholic/persistence/sqlite/_authorization.py`
  - `src/workaholic/persistence/sqlite/repository.py`
  - `tests/unit/persistence/test_sqlite_grants.py`
  - `tests/integration/persistence/test_sqlite_authorization_races.py`
  - `tests/contract/test_phase_five_persistence.py`
- Description: Implement grant create/replace/list/revoke and a reusable
  cumulative permission lookup. Enforce Project isolation and the last-enabled
  Owner invariant atomically with Subject-disable and grant changes.
- Public interface changes:
  - Add stable grant queries and mutation outcomes with exact version,
    attribution, and role; no caller-supplied role is trusted as authorization
    evidence.
  - Add one pure-policy-backed SQLite authorization helper used inside the
    transaction of every Project operation.
  - Instance administrators may administer grants but require their own grant
    for ordinary Project data.
- Inputs:
  - Merged Task 9 actor revalidation, Task 8 Subject lifecycle, and the full
    domain role matrix.
- Outputs:
  - Atomic cumulative authorization and grant persistence shared by all
    existing repositories.
- Tests:
  - Cover each role/permission, role replacement and demotion, stale versions,
    absent grants, self changes, disabled Subjects, cross-Project/Instance
    targets, idempotency, last-Owner races, concurrent disable/revoke, rollback,
    and restart.
- Acceptance criteria:
  - No committed state can leave a Project without an enabled Owner, and no
    administrative role implicitly grants Task access.

### Task 11: Persist append-only administrative audit events

- Deliverables:
  - `src/workaholic/persistence/sqlite/_audit_events.py`
  - `src/workaholic/persistence/sqlite/_event_records.py`
  - `src/workaholic/persistence/sqlite/repository.py`
  - `tests/unit/persistence/test_sqlite_audit_events.py`
  - `tests/contract/test_phase_five_persistence.py`
- Description: Attach one or more typed AuditEvents to every accepted Instance,
  Project, Subject, administrator, grant, and Token mutation in the same
  transaction. Preserve TaskEvents as the authoritative Task history.
- Public interface changes:
  - Add bounded cursor pagination and exact event payload schemas for all audit
    event types in the baseline.
  - Include actor Subject, actor kind snapshot, Token ID, request ID, target
    identifiers, UTC occurrence time, and non-secret change facts.
  - Bootstrap emits one `instance_bootstrapped` event plus the documented
    created identities; Project creation and credential recovery are auditable.
- Inputs:
  - Merged Subject, Token, grant, authorization, schema, and audit domain
    contracts.
- Outputs:
  - Durable security-administration history with atomic state/event consistency.
- Tests:
  - Cover exact event type/order/payload, cursor boundaries, stable pagination,
    actor kind, Token/request attribution, idempotent replay, rollback, restart,
    and concurrent cursor allocation.
  - Scan persisted payloads, outcomes, errors, and logs for raw Tokens, hashes,
    credential paths, and keyring identifiers.
- Acceptance criteria:
  - Every accepted administrative mutation is attributable without turning the
    audit stream into a secret store.

### Task 12: Apply Viewer authorization to Projects and all read paths

- Deliverables:
  - `src/workaholic/persistence/sqlite/_projects.py`
  - `src/workaholic/persistence/sqlite/_queries.py`
  - `src/workaholic/persistence/sqlite/_task_views.py`
  - `src/workaholic/persistence/sqlite/_event_queries.py`
  - `src/workaholic/persistence/sqlite/_task_records.py`
  - `tests/unit/persistence/test_sqlite_query_authorization.py`
  - `tests/contract/test_phase_five_persistence.py`
- Description: Replace Phase 4 bootstrap-Owner assumptions with authenticated
  Viewer-or-stronger checks across status, Project list/get/bind, Task
  list/get/details/views, Results, Claims, Attempts, and TaskEvents.
- Public interface changes:
  - Project list filters to current grants; Project-scoped reads fail without a
    current grant and do not reveal whether an unauthorized target exists.
  - Status/context/whoami return the real authenticated Subject and its current
    grant, not a singular local actor selected from storage.
  - Every query revalidates Token, Subject, Instance, and grant in one read
    transaction using authoritative time.
- Inputs:
  - Merged Tasks 9-11 authentication, grant, and audit persistence.
- Outputs:
  - Complete least-privilege read behavior with unchanged deterministic
    ordering and pagination for authorized records.
- Tests:
  - Exercise all four roles, no grant, revoked/expired Token, disabled Subject,
    grant removal races, cross-Project identifiers, context mismatch, filtering,
    pagination, read non-mutation, and non-disclosing errors.
- Acceptance criteria:
  - A Viewer sees every documented read in granted Projects and nothing from an
    ungranted Project.

### Task 13: Apply Operator authorization to Human and Task mutations

- Deliverables:
  - `src/workaholic/persistence/sqlite/_tasks.py`
  - `src/workaholic/persistence/sqlite/_task_lifecycle.py`
  - `src/workaholic/persistence/sqlite/_task_dependencies.py`
  - `src/workaholic/persistence/sqlite/_task_results.py`
  - `src/workaholic/persistence/sqlite/_task_claims.py`
  - `tests/unit/persistence/test_sqlite_operator_authorization.py`
  - `tests/contract/test_phase_five_persistence.py`
- Description: Require authenticated Operator permission for Task creation,
  definition/state/dependency mutations, Human Claims, Human Results, and
  review while preserving expected-version, idempotency, event, and Claim-lock
  behavior.
- Public interface changes:
  - Human Claim/renew/release additionally requires Human Subject kind; other
    Operator operations remain role-controlled and may be used by an Agent
    Subject explicitly granted Operator.
  - Existing Claim ownership compares authenticated Subject and nullable
    Attempt path. Administrator/Owner authority cannot override a foreign lock.
  - Project creation is restricted to authenticated Instance administrators
    and atomically creates an Owner grant and audit event.
- Inputs:
  - Merged Viewer authorization and shared transaction-time permission helper.
- Outputs:
  - Fully authenticated Human/operator workflow without Phase 4 singular-owner
    assumptions.
- Tests:
  - Matrix every operation against Viewer/Agent/Operator/Owner/admin-without-
    grant, Human/Agent kind, foreign/current/stale Claims, revocation, disable,
    grant races, versions, idempotency, rollback, and event attribution.
- Acceptance criteria:
  - Only an authorized Operator using the correct ownership path can commit a
    Human or Task mutation.

### Task 14: Apply Agent authorization to Claim and Attempt mutations

- Deliverables:
  - `src/workaholic/persistence/sqlite/_task_claims.py`
  - `src/workaholic/persistence/sqlite/_task_execution.py`
  - `src/workaholic/persistence/sqlite/_claim_state.py`
  - `src/workaholic/persistence/sqlite/_claim_records.py`
  - `src/workaholic/persistence/sqlite/_result_records.py`
  - `tests/unit/persistence/test_sqlite_agent_authorization.py`
  - `tests/contract/test_phase_five_persistence.py`
- Description: Bind Agent pull, heartbeat, progress, release, and submission to
  an authenticated Agent Subject with Agent-or-stronger permission and its
  exact current Attempt. Remove the shared-bootstrap attribution limitation.
- Public interface changes:
  - Agent Claim and Attempt rows use the authenticated Agent Subject; clients
    cannot supply an actor identity or kind.
  - Fresh Token and grant state is checked in the same write transaction as
    ownership, Lease, expected version, Result, and TaskEvent state.
  - A different active Token for the same Subject may continue the exact
    Attempt; another Subject with any role receives the existing non-disclosing
    lock or Lease-lost outcome.
- Inputs:
  - Merged Tasks 9-13 actor, role, Query, and Operator authorization semantics.
- Outputs:
  - Distinct, attributable Agent execution with unchanged Lease and Attempt
    state machines.
- Tests:
  - Cover two Agent Subjects, two Tokens for one Subject, Human with Agent role,
    Agent with Operator/Owner role, foreign Attempt, revoked Token, disabled
    owner, removed grant, live/stale Claim, reclaim, progress, submission,
    review, idempotency, races, rollback, and real actor-kind TaskEvents.
- Acceptance criteria:
  - An Agent can mutate only its own current execution in an authorized Project,
    and credential changes never rewrite or force-release execution history.

### Task 15: Implement identity and authorization application services

- Deliverables:
  - `src/workaholic/application/authentication.py`
  - `src/workaholic/application/subjects.py`
  - `src/workaholic/application/tokens.py`
  - `src/workaholic/application/grants.py`
  - `src/workaholic/application/audit.py`
  - `src/workaholic/application/__init__.py`
  - `tests/unit/application/test_authentication.py`
  - `tests/unit/application/test_subjects.py`
  - `tests/unit/application/test_tokens.py`
  - `tests/unit/application/test_grants.py`
  - `tests/unit/application/test_audit.py`
- Description: Orchestrate validated identity commands, identifiers, clocks,
  repository ports, and safe results. Token generation and hashing, credential
  output, and Human secret storage remain in the auth/Session/composition
  boundary rather than application services or result models.
- Public interface changes:
  - Add explicit services for authenticate/whoami, Subject lifecycle,
    administrator lifecycle, Token pending/activation/revocation, grant
    lifecycle, and audit queries.
  - Ensure every mutation generates one request identity, applies the complete
    idempotency fingerprint, and returns a closed non-secret outcome.
  - Expose explicit pending-token and activation use cases so the higher
    Session/composition boundary can coordinate a credential sink without
    serializing a raw Token through a normal application result.
- Inputs:
  - Merged application ports and complete SQLite semantics from Tasks 8-14.
- Outputs:
  - Backend-neutral use cases ready for LocalSession and future Phase 6 server
    reuse.
- Tests:
  - Use strict fakes to assert validated command construction, identifier/clock
    ownership, permission failures, invariant failures, idempotency, error
    mapping, repository call order, secret non-propagation, and no hidden I/O.
- Acceptance criteria:
  - Each Phase 5 use case is callable without CLI, SQLite, keyring, or process
    environment imports.

### Task 16: Compose authenticated LocalSession, bootstrap, and recovery

- Deliverables:
  - `src/workaholic/session/models.py`
  - `src/workaholic/session/base.py`
  - `src/workaholic/session/local.py`
  - `src/workaholic/session/_phase_five.py`
  - `src/workaholic/session/__init__.py`
  - `src/workaholic/composition.py`
  - `tests/unit/session/test_phase_five_local_session.py`
  - `tests/unit/test_composition.py`
  - `tests/contract/test_phase_five_session.py`
- Description: Replace implicit `SQLiteLocalActorSelector` operation with
  credential resolution and authenticated actor context. Add identity Session
  methods, safe Token provisioning coordination, first-up bootstrap, and the
  explicit embedded-only recovery path.
- Public interface changes:
  - Extend `WorkaholicSession` with typed requests/results for every auth
    command while preserving all existing Task method signatures at the CLI
    boundary.
  - Compose profile and database first, select exactly one credential source,
    authenticate it, then create the command-scoped runtime. No operational
    method accepts a Subject or raw Token override.
  - Implement pending credential write/activation compensation for bootstrap
    and Token creation. `up` is unauthenticated only for a genuinely empty
    store; initialized stores require authentication.
  - Implement local recovery confirmation, all-bootstrap-Token revocation, and
    fresh Human credential installation without altering claims/tasks.
- Inputs:
  - Merged auth/credential adapters, application services, and complete
    persistence authorization.
- Outputs:
  - One authenticated embedded runtime whose application semantics can be
    reused unchanged behind Phase 6 transport.
- Tests:
  - Cover source precedence, absent/invalid credentials, Instance mismatch,
    bootstrap concurrency, keyring/file errors, activation/compensation,
    initialized `up`, login replacement, logout, recovery confirmation,
    recovery races, all service delegation, and secret redaction.
  - Run cumulative Session conformance for every role and two distinct Agents.
- Acceptance criteria:
  - No normal local command can reach application data before successful
    authentication and fresh authorization.

### Task 17: Expose login, logout, recovery, and current identity CLI

- Deliverables:
  - `src/workaholic/cli/auth.py`
  - `src/workaholic/cli/main.py`
  - `src/workaholic/cli/options.py`
  - `src/workaholic/cli/serialization.py`
  - `src/workaholic/cli/errors.py`
  - `tests/unit/cli/test_auth_credentials.py`
  - `tests/unit/cli/test_error_mapping.py`
- Description: Add safe credential enrollment/removal, explicit local recovery,
  and `whoami` presentation before exposing broader identity administration.
  Keep CLI stdout envelope rules and non-interactive safety unchanged.
- Public interface changes:
  - Implement `auth login`, `logout`, `recover-local`, and `whoami` with exact
    signatures and results from the baseline.
  - Login accepts Token only through explicit bounded file/stdin, validates it
    before storage, requires Human kind, and never echoes it. Recovery requires
    interactive confirmation or exact non-interactive Instance and Subject
    confirmation.
  - Map new auth/credential errors to exact exit categories and safe messages;
    redact argv, paths, source values, keyring details, and raw exceptions.
- Inputs:
  - Merged Task 16 Session/composition behavior and existing envelope/rendering
    helpers.
- Outputs:
  - Human-friendly and automation-safe access to authenticated local profiles
    and recovery.
- Tests:
  - Cover stdin/file input, no implicit stdin, JSON/human/non-interactive modes,
    missing/ambiguous sources, login as Agent denial, replacement, logout,
    recovery confirmation, initialized/uninitialized behavior, stream
    separation, help text, and hostile secrets in every failure path.
- Acceptance criteria:
  - A Human can establish, inspect, remove, and recover local authentication
    without putting a Token in visible arguments or output.

### Task 18: Expose Subject, Token, grant, and audit CLI commands

- Deliverables:
  - `src/workaholic/cli/auth.py`
  - `src/workaholic/cli/serialization.py`
  - `src/workaholic/cli/rendering.py`
  - `src/workaholic/cli/main.py`
  - `tests/unit/cli/test_auth_subjects.py`
  - `tests/unit/cli/test_auth_tokens.py`
  - `tests/unit/cli/test_auth_grants.py`
  - `tests/unit/cli/test_auth_events.py`
- Description: Expose the complete Phase 5 identity-administration surface over
  `WorkaholicSession`, including secure one-time Token-file provisioning. Reuse
  shared options, pagination, expected-version convenience, and envelopes.
- Public interface changes:
  - Implement all Subject, administrator, ProjectGrant, Token, and audit
    commands in the public command baseline.
  - Resolve display names and identifiers explicitly; human output may show
    handles and Token metadata but never hashes, raw Tokens, or private
    credential paths.
  - Require explicit expected versions for automated Subject/grant updates;
    interactive omission performs one read and one mutation with no retry.
  - Token file creation is exclusive, atomic, protected, outside Workspace, and
    compensated if server-side activation fails.
- Inputs:
  - Complete Task 17 auth CLI and Task 16 Session identity surface.
- Outputs:
  - Complete Human- and automation-facing local identity management through the
    installed CLI.
- Tests:
  - Cover every role and command, operand forms, ordering/pagination, versions,
    idempotency, last-admin/Owner errors, file permissions/symlinks/existence,
    provision compensation, error envelopes, help, and secret-free output.
- Acceptance criteria:
  - An administrator can safely provision two independent Agents and
    least-privilege grants without database access or an internal Python API.

### Task 19: Add cumulative Phase 5 conformance and authentication-race suites

- Deliverables:
  - `tests/contract/phase_five.py`
  - `tests/contract/test_phase_five_persistence.py`
  - `tests/contract/test_phase_five_session.py`
  - `tests/integration/persistence/test_sqlite_authentication_races.py`
  - `tests/integration/persistence/test_sqlite_authorization_races.py`
  - `tests/contract/README.md`
  - concrete SQLite and Session factory wiring under `tests/`
- Description: Add reusable backend-neutral persistence and Session contracts
  for identity, Tokens, grants, authorization, and administrative audit. Use
  independent processes against one SQLite file for revoke/disable/grant/Claim
  races; mocked or thread-only evidence is insufficient for the Phase 5 gate.
- Public interface changes:
  - Add `PhaseFiveIdentifierFactory`, `PhaseFiveRepositoryFactory`, and
    `PhaseFiveSessionFactory` protocols plus deterministic subjects, Tokens,
    clocks, credential stores, and scoped transaction-failure points.
  - Cumulative Phase 5 contracts inherit all Phase 1-4 behavior and can run
    against later adapters without adapter-specific assertions.
- Inputs:
  - Complete Phase 5 domain, auth, application, SQLite, Session, and
    CLI-independent behavior.
- Outputs:
  - One executable semantic contract for SQLite now and JSON/PostgreSQL plus
    RemoteSession later.
- Tests:
  - Cover Token lifecycle and redaction, every role/operation, Project
    isolation, two Agents, multiple Tokens, last-admin/Owner guards,
    credential-store failures, audit, idempotency, rollback, restart, and exact
    auth-versus-mutation races.
  - Assert race losers receive only documented errors and no partial Task,
    Claim, Attempt, Token, grant, audit, TaskEvent, or idempotency state.
- Acceptance criteria:
  - SQLite passes the full cumulative contract using independent connections
    and real process contention.

### Task 20: Upgrade the local Agent golden journey to distinct identities

- Deliverables:
  - `tests/e2e/golden/test_agent_journey.py`
  - `tests/golden.py`
  - `tests/unit/test_golden_contract_helpers.py`
  - `tests/unit/test_golden_journey_inventory.py`
  - `tests/e2e/golden/README.md`
- Description: Replace Phase 4 shared-bootstrap Agent attribution with one real
  Human operator and two independently authenticated Agent Subjects in fresh
  CLI processes. Keep the remote team journey skipped until Phase 6.
- Public interface changes:
  - Extend local `GoldenInstance` orchestration to bootstrap/login the Human,
    create two Agent Subjects, issue protected Token files, assign disjoint and
    shared Project grants, and supply each Agent's trusted environment.
  - Exercise Viewer denial, Agent pull/heartbeat/progress/submit, foreign
    Attempt rejection, Token revocation, alternate same-Subject Token
    continuity, disabled Subject behavior, and Project filtering.
  - Assert all TaskEvents and AuditEvents contain the real authenticated actor.
- Inputs:
  - Complete Phase 5 CLI, real-process runner, isolated credential/config/data
    roots, and cumulative conformance evidence.
- Outputs:
  - Executable evidence for the Phase 5 exit gate without remote transport.
- Tests:
  - Assert each Agent sees only granted Projects, exactly one wins a shared
    Task, neither mutates the other's Attempt, revoked/disabled credentials fail
    immediately, Claims remain until normal end/expiry, and no secret reaches
    outputs/events/context.
  - `uv run pytest -m golden` passes cumulative local journeys and leaves only
    future Phase 6+ journeys skipped.
- Acceptance criteria:
  - A Human provisions two locally authenticated Agents and observes durable,
    least-privilege, correctly attributed execution end to end.

### Task 21: Publish Phase 5 README, documentation, and alpha metadata

- Deliverables:
  - `README.md`
  - `CHANGELOG.md`
  - `pyproject.toml`
  - `uv.lock`
  - `src/workaholic/__init__.py`
  - `.env.example`
  - `docs/architecture.md`
  - `docs/cli-contract.md`
  - `docs/persistence-contract.md`
  - `docs/threat-model.md`
  - `tests/unit/docs/test_public_documentation.py`
  - `tests/unit/docs/test_phase_five_contracts.py`
  - `tests/unit/test_package_metadata.py`
- Description: Replace Phase 4 shared-identity limitations with verified Phase
  5 authentication and authorization behavior, publish a safe Human/two-Agent
  quick start, explain schema version `5` reset, and set package metadata to
  `0.5.0a1`. Keep Phase 6 networking and all later capabilities deferred.
- Public interface changes:
  - Package version becomes `0.5.0a1`.
  - README documents bootstrap credential storage, login/logout/recovery,
    Subject handles, cumulative roles, Agent Token files, role/lock errors,
    expiry/revocation, and local filesystem security assumptions.
  - README quick start uses isolated temporary configuration, credential, data,
    Token-file, and Workspace roots; it never prints or embeds a raw Token.
- Inputs:
  - Passing Phase 5 golden journey, stable source/wheel behavior, and finalized
    public commands/errors.
- Outputs:
  - Public documentation describing only tested Phase 5 behavior and executable
    as a regression test.
- Tests:
  - Execute the literal README quick start in isolation with a forced file
    credential backend and verify exact modes plus secret-free captured output.
  - Assert version, schema/reset notice, commands, environment variables,
    permission matrix, recovery warning, JSON fields, errors, Claim semantics,
    and attribution agree across public documents.
  - Assert README does not claim server/remote support, capabilities,
    SSO/OAuth, custom roles, JSON/PostgreSQL adapters, migrations, hierarchy, or
    force interruption.
- Acceptance criteria:
  - A new operator can securely provision and run two local Agents using only
    the public README and installed CLI.

### Task 22: Execute the Phase 5 clean-state acceptance gate

- Deliverables:
  - `scripts/verify-phase-5.sh`
  - `scripts/smoke-phase-5-wheel.sh`
  - `tests/e2e/test_phase_5_distribution.py`
  - `tests/unit/scripts/test_verify_phase_five.py`
  - `tests/unit/scripts/test_smoke_phase_five_wheel.py`
  - `.pre-commit-config.yaml`
  - `.github/workflows/ci.yml`
  - `README.md`
  - `CHANGELOG.md`
  - Phase 5 GitHub epic, milestone, and implementation issues
- Description: Add one fail-fast aggregate acceptance command and execute it
  from a fresh clone with empty temporary config, credential, data, Token-file,
  and Workspace roots. Validate source and installed-wheel authentication
  without using the operator's real profiles, keyring, database, credentials,
  or GitHub identity.
- Public interface changes:
  - Acceptance command: `scripts/verify-phase-5.sh`.
  - Required clean-state sequence:

    ```bash
    uv sync --frozen
    uv run pre-commit run --all-files
    uv run pytest
    uv build --no-progress
    scripts/smoke-install.sh dist/*.whl
    scripts/smoke-phase-5-wheel.sh dist/*.whl
    ```

  - The wheel smoke journey bootstraps a Human, provisions two Agents, applies
    Viewer/Agent/Operator/Owner grants, exercises cross-Project and
    cross-Attempt denial, revokes/disables credentials, and inspects audit
    attribution after restart.
- Inputs:
  - Complete Phase 5 implementation, conformance, golden evidence, README, and
    `0.5.0a1` metadata.
- Outputs:
  - Reproducible evidence for the authenticated-local Phase 5 exit gate.
  - A closed Phase 5 epic/milestone only after protected `main` is green.
- Tests:
  - Prove schema version `4`, malformed/ambiguous credentials, unsafe files,
    unauthorized Projects/roles, last-admin/Owner removal, foreign Claims,
    expired/revoked Tokens, disabled Subjects, failing tests, and malformed
    wheels fail at documented boundaries without secret leakage.
  - Assert the gate rejects an active virtual environment, dirty tracked files,
    pre-existing build output, inherited Token variables, or config/data paths
    outside its owned temporary root.
- Acceptance criteria:
  - Required `quality`, `tests`, `build`, and `wheel-smoke` checks pass on the
    protected `main` merge commit.
  - Cumulative local golden journeys pass and only future-phase journeys remain
    skipped.
  - Source and wheel runs produce identical identity, role, Token, Claim,
    Attempt, audit, error, expiry, revocation, and idempotency behavior.
  - Acceptance evidence is linked from the Phase 5 epic before closing its
    milestone.

## Operational instructions

1. Implement Tasks 1-22 sequentially on local branch
   `agent/phase-5-implementation-tasks`, with one focused commit per completed
   task. Do not push the branch or open intermediate pull requests merely to
   advance between dependent tasks.
2. Maintain one Phase 5 epic and Tasks 1-22 as implementation issues in an
   Identity and Authorization Alpha milestone. Preserve task order with
   dependency links, but batch rate-limited GitHub App writes and avoid polling
   or duplicate comments.
3. Push the completed phase branch once, open one Phase 5 pull request against
   protected `main`, wait for all required checks, and merge only when green.
   Never push directly to `main` or bypass its ruleset.
4. Parallel work is allowed only after shared prerequisites have merged into
   the local branch and deliverables are disjoint. Contracts, domain exports,
   application ports, schema, auth primitives, authorization helper, Session
   protocol, serializers, and composition are sequential ownership boundaries.
5. Every task commit runs the focused tests named by that task plus all
   applicable local quality hooks. At stable integration points and before the
   final PR, run:

   ```bash
   uv sync --frozen
   uv run pre-commit install --hook-type pre-commit --hook-type pre-push
   uv run pre-commit run --all-files
   uv run pytest
   uv build --no-progress
   ```

6. Every manual, concurrency, integration, smoke, and acceptance run uses
   absolute test-owned `WORKAHOLIC_CONFIG_DIR` and `WORKAHOLIC_DATA_DIR` values,
   `WORKAHOLIC_CREDENTIAL_BACKEND=file`, isolated Token files, and an isolated
   Workspace. Unset inherited `WORKAHOLIC_TOKEN` and
   `WORKAHOLIC_TOKEN_FILE`. Never load or modify the operator's default keyring,
   profile registry, credential file, or database.
7. SQLite schema version `5` intentionally rejects Phase 4 version `4`.
   Preserve any development data needed outside Workaholic, verify the exact
   selected data path, and remove only explicitly confirmed disposable data.
   Do not add a migration, broad recursive cleanup, conversion, or silent reset.
8. Use injected authoritative clocks and deterministic entropy in unit and
   contract tests. Wall-clock sleeps and production randomness are permitted
   only in bounded final process/smoke proofs; ordinary tests remain
   deterministic and non-flaky.
9. Never place a raw Token in a shell command argument, source file, fixture,
   snapshot, diagnostic, test node ID, event, idempotency record, environment
   dump, or GitHub artifact. Tests generate secrets at runtime and compare only
   bounded hashes or redacted projections where possible.
10. Every operation must derive actor Subject and kind from the successfully
    authenticated Token. Repository-controlled context, structured input, and
    client payloads may never supply actor, Token, request, event, authoritative
    time, Claim owner, or Attempt owner identities.
11. Every authenticated query and mutation revalidates Token, Subject,
    Instance, and grant at its persistence transaction boundary. A Session-only
    or CLI-only permission check is never sufficient evidence of authorization.
12. Preserve the package dependency direction: domain depends on nothing;
    application and auth depend on domain but not on each other; persistence,
    Session, context, and credential adapters implement lower-owned ports; CLI
    depends on Session; composition alone selects concrete adapters.
13. Before adding a helper, inspect the existing domain normalization,
    structured-file, atomic-write, safe-path, event-record, authorization,
    idempotency, clock, and serialization helpers. Extract shared logic at its
    owning boundary instead of duplicating security-sensitive implementations.
14. Every new or changed module, model, class, function, and method retains
    strict type hints, runtime validation at boundaries, current Google-style
    docstrings, explicit exports, and tests that show intent and failure
    behavior.
15. Every commit that changes commands, JSON objects, errors, credential
    behavior, permissions, persistence, or support status updates the relevant
    normative contracts. Update README only after the changed behavior is
    executable and verified; never publish planned behavior as current.
16. Do not add network listeners, remote endpoints/profiles, `RemoteSession`,
    HTTP authentication, capabilities, SSO/OAuth, refresh Tokens, custom roles,
    Subject/Token deletion, schema migration, parent/child Tasks, or process
    interruption in Phase 5.
17. Before merging the final task, execute `scripts/verify-phase-5.sh` from a
    fresh clone, smoke the built wheel, confirm all protected checks on the
    exact merge commit, and only then tag or publish `0.5.0a1` according to the
    repository release procedure.
