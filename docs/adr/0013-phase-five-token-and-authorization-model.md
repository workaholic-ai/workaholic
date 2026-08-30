# ADR 0013: Phase 5 Token and Authorization Model

- Status: Accepted
- Decision date: 2026-08-28
- Deciders: Pavels Gurskis
- Supersedes: None
- Superseded by: None

## Context

Phase 4 makes local Human and Agent execution safe against concurrent Claim and
Attempt races, but both command paths still use one bootstrap Human Subject.
That limitation prevents independent Agent revocation, Project isolation, real
Agent TaskEvent attribution, and a reusable authentication boundary for the
Phase 6 server.

V1 serves one organization per Instance. Single-organization scope does not
make every Subject trusted for every Project. Local filesystem access remains
inside the embedded trust boundary, but application authorization must match
the later remote server so local behavior does not become a privileged bypass.

The model must balance autonomous-Agent safety with Human CLI usability. It
must also coordinate an external credential sink with SQLite without making an
undisclosed secret valid after a partial failure.

## Decision

### Subject identity

Each independently operating Human or Agent has one durable Subject. The
Subject contains:

- opaque `sub_` identifier and owning Instance;
- kind exactly `human` or `agent`;
- immutable Instance-scoped handle matching
  `^[a-z][a-z0-9-]{1,62}$`;
- mutable display name from 1 through 200 printable characters;
- enabled and Instance-administrator state;
- positive optimistic version; and
- creator plus authoritative UTC creation/update timestamps.

Handles are compared byte-for-byte, cannot be renamed, and are never reused.
Display names are not lookup keys. Subjects are not deleted and their kind does
not change in v1. Additional Subjects start enabled, non-administrative, and at
version `1`. The bootstrap Human receives handle `local-operator` and
self-attribution as its creator.

### Cumulative Project roles

One Subject has at most one current ProjectGrant in one Project. Roles are
cumulative in exact order:

```text
viewer < agent < operator < owner
```

- Viewer reads the Project, Tasks, Results, Claims, Attempts, and TaskEvents.
- Agent adds Agent pull, heartbeat, progress, release, and submission.
- Operator adds Task creation and mutation, Human Claims, Human submission, and
  review.
- Owner adds ProjectGrant administration.

Assigning another role replaces the grant through optimistic concurrency; it
does not stack permission rows. New grants start at version `1`. Automated
replacement and revocation supply an expected version. Interactive Humans may
omit it; the CLI reads once, submits that exact state once, and never retries a
conflict.

Instance-administrator state is separate. It authorizes Project creation,
Subject and administrator lifecycle, Token lifecycle, and grant administration
across the Instance, but it does not reveal ordinary Project data without a
ProjectGrant.

The Instance must retain at least one enabled administrator. Each Project must
retain at least one enabled Owner. Subject disablement, administrator removal,
Owner demotion, and Owner removal enforce all affected invariants atomically.
Disabled Subjects cannot receive new grants or Tokens.

Subject kind does not grant permission, but it constrains execution semantics.
Agent Claim and Attempt commands require Agent kind plus Agent-or-stronger
permission. Human Claim, renew, and release require Human kind plus
Operator-or-stronger permission. Other Operator operations are role-controlled
regardless of kind.

### Token credential

One Subject may have multiple independently expiring and revocable Tokens. The
public Token ID uses `tok_`. Canonical raw form is `<token-id>.<secret>`:

```text
<token-id>.<secret>
```

`secret` is unpadded URL-safe base64 for exactly 32 bytes from a
cryptographically secure random generator. Persistence stores only lowercase
SHA-256 of the complete canonical raw Token. Authentication performs an indexed
Token-ID lookup and constant-time digest comparison. Random-token entropy,
rather than password stretching, protects against offline guessing; Phase 5
does not introduce a database pepper.

Token lifecycle projections are `pending`, `active`, `expired`, and `revoked`.
A Token authenticates only when activated, not revoked, its Subject is enabled,
its Instance matches, and authoritative transaction time satisfies
`now < expires_at`. Expiry is not extended and needs no background mutation.
Renewal means issuing another Token. Tokens are not deleted in v1.

Human Tokens default to `30d` and accept `1h` through `365d`. Agent Tokens
default to `24h` and accept `5m` through `30d`. Durations use the existing
single-unit grammar `^[1-9][0-9]*(s|m|h|d)$`.

An Instance administrator may issue a Token to any enabled Subject, including
itself. A Subject may list and revoke its own Token metadata. An administrator
may list and revoke any Token. Raw Tokens and hashes never enter normal public
models, stdout, diagnostics, application Task commands, Results, events,
idempotency records, or logs.

### Credential provisioning

`auth create-token` requires a protected absolute output path, not a raw Token
argument or stdout response. The target must not exist or be a symlink, must be
outside the discovered Workspace and any Git worktree/repository discovered
from its ancestors, and is atomically created at mode `0600` under an existing
parent that is not group/world writable.

Provisioning has two durable phases:

1. persist a pending non-authenticating Token hash;
2. durably write the raw Token to the selected credential sink;
3. atomically activate the Token and append `token_issued`.

On a sink or activation failure, bounded compensation revokes the pending Token
and removes only the just-created output. A retry generates a new Token ID and
secret. This design prevents an undisclosed pending secret from authenticating
and avoids a cross-store distributed transaction.

The public idempotency key is consumed only by successful activation. A failed,
compensated attempt may reuse it for a new Token. After a crash, an existing
protected output is accepted only with the same idempotency key and only when
its parsed Token ID and digest match the pending or committed record. The client
then resumes activation or returns committed metadata without rewriting the
file. An absent output after committed activation cannot be reconstructed;
recovery requires listing and revoking that Token, then issuing another.

The first `workaholic up` uses the same process to install a Human bootstrap
credential. `up` against an initialized store authenticates normally.

### Human credential storage

Human credentials are scoped by trusted profile and include expected Instance
and Subject IDs. The default adapter uses an available operating-system
keyring. It falls back to a protected file only when no keyring backend exists,
not after an operational keyring error. Trusted process configuration may
select `auto`, `keyring`, or `file` explicitly.

The file backend stores bounded non-symlink
`credentials/credentials.toml` below the trusted configuration root. The
dedicated directory is mode `0700`; the file is atomically replaced at mode
`0600` and is limited to 1,048,576 bytes. It contains no endpoint, executable,
task, or remote-profile data. A platform without POSIX mode bits must verify an
equivalent current-user-only ACL or fail with `CREDENTIAL_UNAVAILABLE`.
The selected configuration and credential paths must also remain outside a
discovered Workspace or Git worktree/repository.

`auth login --token-file PATH|-` reads one explicit bounded Token, authenticates
it, requires Human kind, stores it, and never echoes it. `auth logout` removes
only the profile credential and does not revoke the Token.

### Agent credential sources

Agent and orchestrated processes use trusted `WORKAHOLIC_TOKEN` or
`WORKAHOLIC_TOKEN_FILE`. The variables are mutually exclusive. Empty values are
absent. An explicit source has priority over the Human store and never falls
back after a malformed file, invalid Token, expiry, revocation, disabled
Subject, or Instance mismatch.

A Token file is absolute, bounded UTF-8, and contains exactly one canonical
Token plus an optional final newline. Mounted-secret symlinks are resolved;
the final target must be a regular file, no larger than 512 bytes, and not
group/world writable. A non-POSIX platform verifies equivalent current-user
access or fails closed. Authentication does not modify it. Orchestrator
integration is secret injection only and adds no Phase 5 protocol.
File-based Agent credentials inside a discovered Workspace or Git repository
are rejected.

No credential or secret reference is permitted in `.workaholic.env` or
`profiles.toml`.

### Authentication and transaction-time authorization

Normal operations authenticate exactly one Token and produce an internal
`AuthenticatedActor` with Instance, Subject, immutable kind, and Token IDs. Raw
credential material ends at this boundary. An absent credential returns
`AUTHENTICATION_REQUIRED`. Missing rows, wrong digests, pending/expired/revoked
Tokens, disabled Subjects, and Instance mismatches collapse to
`AUTHENTICATION_FAILED`; an invalid explicit credential never falls back.

Every persistence query revalidates active Token, enabled Subject, selected
Instance, and required ProjectGrant in one read transaction. Every mutation
repeats those checks in the write transaction before idempotency lookup,
ownership checks, or state changes. A Session-time check is not sufficient.

Project lists expose only granted Projects. Unauthorized lookups and ownership
failures do not disclose targets outside the actor's scope.

### Claim and Attempt interaction

A current Claim remains an exclusive mutation lock. The owning Human uses the
null-Attempt path; the owning Agent uses the exact current Attempt. A foreign
Operator, Owner, or Instance administrator cannot override the lock.

Claim ownership belongs to Subject identity rather than a particular Token.
Another valid Token for the same Subject may continue the exact Attempt.
Revoking one Token or disabling the Subject stops new authenticated operations
immediately but does not force-release the Claim, mutate Attempt history, or
interrupt an external process. Existing explicit release, submission,
cancellation, and Lease-expiry semantics remain the only endings.

An Agent Subject explicitly granted Operator may mutate an unclaimed Task and
may review after its Claim ends. It still cannot use an Operator command to
bypass its current Agent Claim; it must use the exact Attempt path first.

### Local recovery

`auth recover-local` is the only tokenless post-bootstrap route. It is available
only for an embedded profile under the trusted operating-system account. It
requires interactive confirmation or exact non-interactive confirmation of the
Instance ID and bootstrap handle. It revokes every Token for the bootstrap
Subject and installs one fresh Human Token.

Recovery changes no Subject state, administrator status, ProjectGrant, Project,
Task, Claim, Attempt, Result, or TaskEvent. It is unavailable through
RemoteSession. An attacker controlling the local OS account already controls
the embedded database and lies outside the local application isolation
boundary.

### Administrative audit

Task mutations continue to append TaskEvents with real authenticated Subject,
kind, request, and optional Attempt attribution. Instance bootstrap, Project
creation, Subject lifecycle, administrator changes, grant changes, and Token
issue/revocation append a separate ordered AuditEvent in the same transaction
as state and idempotency.

AuditEvents use `aev_` IDs and contain Instance, actor Subject/kind, nullable
actor Token, request, type, timestamp, and a closed non-secret payload.
Tokenless bootstrap and local recovery are self-attributed to the bootstrap
Human and have null actor Token. Every authenticated event identifies its actor
Token. Payloads never contain raw Tokens, Token hashes, credential paths,
environment values, keyring locators, or Task content.

Event types are exactly `instance_bootstrapped`, `project_created`,
`subject_created`, `subject_updated`, `subject_enabled`, `subject_disabled`,
`instance_admin_granted`, `instance_admin_revoked`,
`project_grant_assigned`, `project_grant_revoked`, `token_issued`, and
`token_revoked`.

The exact closed payloads are:

- `instance_bootstrapped`: `instance_id`, `subject_id`, `project_id`,
  `project_key`, and `grant_role`;
- `project_created`: `project_id`, `project_key`, and `owner_subject_id`;
- `subject_created`: `subject_id`, `handle`, `kind`, and `version`;
- `subject_updated`: `subject_id`, `changed_fields`, and `version`;
- `subject_enabled` or `subject_disabled`: `subject_id` and `version`;
- `instance_admin_granted` or `instance_admin_revoked`: `subject_id` and
  `version`;
- `project_grant_assigned`: `project_id`, `subject_id`, `role`, and `version`;
- `project_grant_revoked`: `project_id`, `subject_id`, `previous_role`, and
  `previous_version`;
- `token_issued`: `token_id`, `subject_id`, and `expires_at`; and
- `token_revoked`: `token_id` and `subject_id`.

`changed_fields` is the sorted array containing only `display_name` in Phase 5.
Recovery appends one `token_revoked` for each previously non-revoked bootstrap
Token and one `token_issued` for the replacement, all with null actor Token.

Administrative idempotency authenticates and authorizes before replay lookup.
Fingerprints bind actor, operation, target identities, requested state, and
expected versions but exclude secret material. A revoked Token or removed grant
cannot replay a formerly authorized result.

### Delivery boundary

Phase 5 implements these semantics through embedded `LocalSession` and SQLite
schema version `5`. Version `4` is rejected unchanged. Phase 5 adds no migration,
server, remote profile, `RemoteSession`, network transport, capability
filtering, SSO/OAuth, refresh Token, custom role, Subject/Token deletion,
parent/child Task, or process interruption.

Phase 6 transports the same authenticated application semantics over a private
protocol. It must not introduce a second authorization policy.

## Alternatives considered

### Keep implicit local identity until the server phase

This would make Phase 5 identity commands unauthenticated locally and require a
semantic rewrite in Phase 6. It would also leave local Agents unable to prove
real Project isolation and attribution.

### Store one Token per Subject

This simplifies storage but couples rotation, parallel workers, incident
response, and Human sessions. Multiple independent Tokens preserve one Subject
identity while allowing narrow revocation.

### Put raw Tokens in SQLite

This would make a database read sufficient to impersonate every Subject.
Hash-only storage preserves online verification without turning backups into a
credential store.

### Return a new Token in JSON or accept it as an option

This simplifies automation but exposes secrets to terminal capture, shell
history, logs, and generic envelope tooling. Explicit file/stdin boundaries make
secret handling deliberate and testable.

### Fall back after every keyring failure

This would silently downgrade security during transient or policy errors and
could leave multiple diverging credentials. Only true backend unavailability
permits automatic fallback.

### Make Instance administrators implicit Project Owners

This would defeat least privilege and allow an infrastructure administrator to
read all task content. The roles remain separate; administration may manage a
grant without using that grant for ordinary data access.

### Release Claims on revocation or disablement

Workaholic cannot safely interrupt the external process. Releasing the logical
lock while it may still run would permit overlapping execution. The Claim
therefore remains until its normal semantic end or Lease expiry.

### Add capability filtering with Agent roles

Capability describes suitability, not authorization. Combining them would
confuse scheduling and security. Capability filtering remains post-v1.

## Consequences

- Local and remote operation share one explicit authentication and
  authorization model.
- A compromised Agent is contained to its Subject, active Tokens, granted
  Projects, role, and current owned Attempt.
- Credential sinks add crash, permission, and availability failure modes that
  require dedicated adapters and failure-injection tests.
- Token rotation does not change Claim ownership because ownership is
  Subject-scoped.
- Last-administrator and last-Owner guards prevent application-level lockout
  but do not replace external database and host recovery.
- Local recovery is powerful by design and belongs to the already trusted
  operating-system boundary.
- SQLite schema version `5` is intentionally incompatible with version `4`
  while pre-v1 storage remains disposable.
- Administrative AuditEvents provide security history without overloading
  TaskEvents or storing secrets.

## References

- [Architecture](../architecture.md)
- [CLI automation contract](../cli-contract.md)
- [Persistence contract](../persistence-contract.md)
- [Threat model](../threat-model.md)
- [Delivery roadmap](../roadmap.md)
- [ADR 0007: Human and Agent Identity Model](0007-human-and-agent-identity-model.md)
- [ADR 0012: Phase 4 Local Claim and Execution Model](0012-phase-four-local-claim-and-execution-model.md)
