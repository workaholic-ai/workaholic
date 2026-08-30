# Workaholic AI Threat Model

- Status: Accepted v1 model through Phase 5 with Phase 5 implementation
- Decision date: 2026-07-29
- Scope: Embedded and shared-server behavior required for v1
- Security contact: [pg@ithesion.com](mailto:pg@ithesion.com)

## Purpose

This threat model turns the accepted v1 security boundary into explicit
engineering constraints and verification targets. It covers planned behavior;
the current `0.5.0a1` development package implements trusted embedded profiles
and credentials, canonical upward Workspace discovery, safe binding, distinct
Human and Agent Subjects, hash-only bearer Token storage, cumulative
ProjectGrants, transactional authentication and authorization, local SQLite
schema version `5`, the complete Task/Claim/Attempt workflow, attributable
TaskEvents, administrative AuditEvents, and local recovery. It rejects schema
version `4` unchanged and does not implement remote profiles, `RemoteSession`,
network services, public multi-tenancy, or cross-organization isolation.

Terms such as Subject, ProjectGrant, Token, Claim, Attempt, Lease, TaskEvent, and
AuditEvent use their canonical definitions in the [glossary](glossary.md).

## Security objectives

Workaholic AI must:

- authenticate every independently operating Human and Agent Subject;
- authorize every operation against instance and Project roles;
- limit a compromised Subject or stolen Token to that Subject's grants;
- preserve Task, Claim, Attempt, Lease, Result, and TaskEvent integrity;
- prevent stale Task versions from overwriting accepted mutations;
- attribute every mutation to the authenticated Subject and request;
- prevent non-owners and stale or foreign Attempts from mutating claimed work;
- keep credentials out of repository-controlled context and normal output;
- apply the same authorization and audit rules through LocalSession and
  RemoteSession;
- fail explicitly on unsupported context, protocol, or persistence versions;
- remain predictably available under bounded single-organization workloads.

## Assets

Security-sensitive assets include:

- raw bearer Tokens and bootstrap credentials;
- trusted user profiles and their selected embedded data directories;
- Project membership and ProjectGrants;
- Task content, state, versions, dependencies, and stable identities;
- Human Result attribution, review disposition, and external artifact
  references;
- exclusive Claim ownership, Attempt identity, Lease expiry, Results, and
  idempotency records;
- attributable, append-only TaskEvents and their Instance ordering;
- attributable, append-only administrative AuditEvents;
- persisted state and backend credentials;
- local Workspace paths and context;
- server, CLI, and persistence availability.

External artifact contents are not stored by Workaholic AI. Their
confidentiality and authorization remain the responsibility of the referenced
artifact system.

## Trust boundaries and assumptions

### Instance administration and deployment

Each v1 Instance serves one organization. The Instance administrator and the
infrastructure controlling the Workaholic AI process, host, TLS termination,
secrets, clock, and persistence service are trusted.

A party with administrative host, process, secret-store, or database access can
bypass application controls and is outside the application's isolation
boundary. Backups, host hardening, network policy, and backend-native recovery
are operator responsibilities.

### Project authorization

Organization membership does not make a Human or Agent trusted for every
Project. Every read and mutation is constrained by an Instance role or a
ProjectGrant. Viewer, Agent, Operator, and Owner permissions apply only to the
named Project and are cumulative in that order. Instance-administrator status
is separate and does not reveal ordinary Project data without a ProjectGrant.

Subject kind and Project role are independent. Capability-based scheduling is
outside v1 and must not be mistaken for an authorization boundary if added
later. LocalSession and RemoteSession must call the same application
authorization policy.

Phase 4 embedded Human and Agent commands reuse the sole bootstrap Subject.
Human command shape and null Attempt attribution distinguish Human Claims;
Attempt identity distinguishes local Agent processes. Phase 5 introduces
distinct Subjects, Tokens, ProjectGrants, and authenticated ownership. Phase 4
therefore prevents stale-process and non-owner command-path mutations but does
not claim to distinguish different Human operators sharing the embedded
operating-system account.

Persisted TaskEvent actor kind remains the bootstrap Subject kind `human` in
Phase 4. A non-null Attempt ID, not a fabricated Agent Subject, attributes
Agent execution. Structured progress cannot supply identity, Attempt, request,
event, Result, cursor, or authoritative timestamp fields.

Phase 5 authenticates each normal operation as exactly one distinct Subject and
records the real Human or Agent kind. Every query and mutation revalidates the
active Token, enabled Subject, selected Instance, and required ProjectGrant
within its persistence transaction. An authentication result cached by Session
or CLI is not authorization evidence for a later transaction.

Agent execution additionally requires Agent Subject kind and an Agent-or-
stronger grant. Human Claim operations additionally require Human Subject kind
and an Operator-or-stronger grant. Other Operator operations are role-based.
Owner or administrator authority does not override a foreign current Claim.

Another active Token for the same Subject may continue the exact current
Attempt. Revocation or disablement does not force-release the Claim.

The Instance must retain one enabled administrator and every Project one
enabled Owner. Subject disablement, administrator removal, Owner demotion, and
Owner removal enforce those safeguards atomically. Revoking a Token or
disabling its Subject does not force-release a Claim or interrupt an external
process; the existing Lease boundary remains authoritative.

### Local filesystem and credential storage

The operating-system account running an embedded client is trusted to protect
its local persistence files, user configuration, process environment, and
Workspace. Another process with that account's privileges is outside the
embedded runtime's application isolation boundary.

Phase 2 trusted configuration contains data-only embedded profile definitions.
Its `profiles.toml` must be a bounded regular non-symlink file in the
operating-system user-configuration directory, or in an absolute
operator-controlled directory selected by `WORKAHOLIC_CONFIG_DIR`. Every
configured profile selects one canonical absolute data directory, and two
profile names cannot alias the same directory. Profile names match
`[a-z][a-z0-9_-]{0,31}`, and every profile has exact
`mode = "embedded"`. The file cannot contain remote URLs, credentials, Tokens,
secret references, executable paths, or other profile modes.

Profile selection is deterministic:

1. explicit `--profile`;
2. trusted `WORKAHOLIC_PROFILE`;
3. the discovered Workspace context;
4. configured `default_profile`;
5. built-in `local`.

If `profiles.toml` is absent, only the built-in `local` profile is available.

Human credential storage begins in Phase 5 local mode. Credentials are scoped
to a trusted profile and bind expected Instance and Subject identities. The
default uses the operating-system credential store through an available
keyring backend. It falls back only when no keyring backend exists, not after
an operational keyring failure. The explicit
file backend stores bounded non-symlink `credentials/credentials.toml` at mode
`0600` in a dedicated mode-`0700` configuration directory outside repositories.
It is limited to 1,048,576 bytes. Non-POSIX platforms must verify equivalent
current-user-only ACLs or reject the fallback.

Agent credentials use `WORKAHOLIC_TOKEN`, an absolute path in
`WORKAHOLIC_TOKEN_FILE`, or orchestrator injection. The two variables are
mutually exclusive, an explicit source never falls back, and mounted-secret
symlinks resolve to a regular target of at most 512 bytes that is not
group/world writable. Non-POSIX access must be equivalently account-restricted.
Deployers must prevent those channels from being exposed to untrusted sibling
processes, crash reports, shell history, or logs.

`auth login` accepts a Token only from an explicit bounded file or stdin and
does not echo it. Token creation writes only to a new protected non-symlink file
outside the discovered Workspace and any Git worktree/repository and never
returns the secret in normal output. File-backed stored or Agent credentials
inside a Workspace or Git repository are rejected. Raw Tokens and hashes do not
cross into task, event, public idempotency outcome, or diagnostic models.

Local tokenless recovery is intentionally inside the embedded filesystem trust
boundary. `auth recover-local` requires explicit Instance and bootstrap-Subject
confirmation, revokes that Subject's existing Tokens, and changes no domain
work or grants. It is unavailable to a remote client. A party able to invoke it
with the trusted operating-system account already controls embedded persistence
and is outside the local application isolation boundary.

### Repository-controlled context

Every `.workaholic.env` file is untrusted input, even in a trusted
organization's repository. Discovery starts from the canonical physical
current directory and visits every physical parent through the filesystem
root; Git repository and worktree boundaries do not stop it. The nearest file
is authoritative, and an invalid or unreadable nearer file fails instead of
falling back to a parent.

The context source must be a bounded regular non-symlink file. It may identify
a context version, trusted profile name, Instance, Project, Project key, and
relative Workspace root only through a strict allowlist. The Workspace root
must resolve from the file's directory to an existing directory contained by
that directory after lexical and symlink resolution.

The parser must never invoke a shell, perform variable or command substitution,
load executable paths, accept credentials, or accept storage or endpoint
configuration. A context can name but never define a profile. The selected
profile, Instance, Project, and Project key must match trusted configuration
and authoritative persistent state before any read or mutation.

Binding an equivalent context is a successful no-op. A different valid
binding requires explicit `--replace`, which may atomically replace only a
regular non-symlink context that remains unchanged during validation. Binding
never replaces a malformed file, directory, symlink, or concurrently changed
file, and it never changes a shared `.gitignore`.

### Remote transport

Phase 2 has no remote profiles, endpoints, credentials, Tokens,
`RemoteSession`, or network transport. Phase 3 and Phase 4 retain that boundary.
Phase 5 adds local credentials and Tokens but still has no remote profiles,
endpoints, `RemoteSession`, or network transport. It authenticates embedded
commands and rejects configuration that attempts to introduce a remote target.
Authenticated remote operation begins in Phase 6.

When delivered, remote bearer-token traffic uses HTTPS through trusted
deployment infrastructure. A trusted profile owns the server URL and expected
Instance identity. RemoteSession must reject an unexpected Instance and
incompatible protocol before sending a mutation.

The private protocol is supported only between official clients and servers.
Calling internal routes directly does not create a public security or
compatibility contract.

## Threat actors and scenarios

The model considers:

- a compromised Agent process using its valid Token;
- an attacker who has stolen a Human or Agent Token;
- a malicious or compromised repository controlling `.workaholic.env`;
- an authenticated Subject attempting operations outside its role or Project;
- a non-owner attempting to mutate a Task with a current unexpired Claim;
- a stale Agent process attempting to mutate a reclaimed Task;
- a stale Human or automation process attempting to overwrite a newer Task;
- a network attacker observing, redirecting, replaying, or altering traffic;
- a client submitting forged actor, event, time, or Attempt information;
- a client supplying oversized, recursive, forged-identity, or executable Task
  and Result input;
- a client or workload exhausting process, persistence, or event resources;
- accidental operator misconfiguration.

A malicious Instance administrator, compromised deployment host, or database
administrator is outside the v1 application boundary.

## Threats and required mitigations

| Threat | Scenario | Required mitigations | Verification target |
| --- | --- | --- | --- |
| Compromised Agent | An Agent tries to read or mutate unrelated Projects or perform Operator actions. | From Phase 5, use one Subject per independent Agent and enforce ProjectGrant permissions on every application operation; in Phase 4, constrain the Agent command path to its current Attempt operations and record attribution. | Phase 4 command-path denial tests, then cross-Project and role-denial tests through LocalSession and RemoteSession. |
| Stolen Token | An attacker replays a bearer Token until it expires or is revoked. | Store only SHA-256 of a canonical 256-bit-random Token; compare in constant time; revalidate active Token, enabled Subject, Instance, and grant inside every operation transaction; use narrow grants and independent expiries. | Exact-expiry, revocation/mutation race, disablement, multiple-Token, and least-privilege tests. |
| Credential downgrade | A failing keyring or unsafe fallback redirects or exposes a Human Token. | Fall back only when no keyring backend exists; bind credentials to profile and expected Instance/Subject; require a dedicated mode-`0700` directory, mode-`0600` non-symlink file, bounded crash-safe replacement, and redacted errors. | Backend-unavailable versus backend-failure, mode, symlink, malformed-file, atomicity, and redaction tests. |
| Token provisioning failure | A process crash makes an undisclosed Token valid or leaves a partial secret file. | Persist Tokens as non-authenticating pending records; durably write a protected exclusive output before atomic activation; revoke pending state and remove only the just-created file on bounded compensation. | Sink/activation failure injection, crash, retry, existing-file, and secret-scan tests. |
| Authentication race | Revocation, Subject disablement, or grant removal commits after Session selection but before a mutation. | Treat Session authentication as identity discovery only; revalidate Token, Subject, Instance, permission, Claim, and Attempt in the same persistence transaction as idempotency and mutation. | Independent-process revoke/disable/grant-versus-read/write races. |
| Administrative lockout | Concurrent changes remove the final usable Instance administrator or Project Owner. | Atomically reject Subject disablement, administrator revoke, Owner demotion, and Owner revoke when no enabled replacement remains. | Concurrent last-administrator and last-Owner tests with rollback assertions. |
| Recovery abuse | A tokenless recovery route becomes a remote bypass or silently alters work. | Restrict recovery to embedded mode and trusted OS-account boundary; require exact Instance/bootstrap-Subject confirmation; revoke old bootstrap Tokens; never alter Subject, grants, Tasks, Claims, or Attempts. | Remote denial, confirmation, scope, revocation, and unchanged-domain-state tests. |
| Profile redirection | A repository or unsafe profile file redirects embedded storage to attacker-controlled state. | Forbid storage paths and profile definitions in `.workaholic.env`; read only a bounded regular non-symlink trusted profile file; require absolute canonical one-to-one data directories; validate context identities against selected persistence. | Hostile-context, unsafe-profile, aliasing, and authoritative-identity tests. |
| Workspace path escape | A context uses `..` or a symlink to claim a Workspace outside its binding directory. | Canonicalize physical discovery; require an existing relative root contained by the context directory after lexical and symlink resolution; fail on the nearest invalid context. | Parent traversal, symlink escape, deep-directory, and invalid-nearer tests. |
| Token redirection | A repository changes context so a later remote client sends its Token to an attacker endpoint. | Forbid URLs and credentials in `.workaholic.env`; reject all remote configuration in Phase 2; in Phases 5 and 6 resolve only a named trusted remote profile, require HTTPS, and compare the server's Instance identity before mutations. | Phase 2 remote-rejection tests, then hostile-context and unexpected-Instance tests in Phase 6. |
| Secret exposure | Credentials appear in arguments, task data, events, logs, errors, or repository files. | Reject secrets in context; never accept Tokens in normal command arguments; redact diagnostics and structured logs; exclude raw Tokens from domain models and persistence; protect credential files. | Redaction tests and repository/history secret scans. |
| Command injection | Context or task input triggers shell expansion or execution. | Parse context with a strict data parser and key allowlist; reject substitution and executable-path keys; never source `.workaholic.env`; use argument-vector subprocess calls at trusted adapter boundaries. | Malformed context, metacharacter, substitution, and unknown-key tests. |
| Unauthorized Claim or Attempt mutation | A non-owner changes a claimed Task, or a process heartbeats, releases, reports, or submits against another, expired, or superseded Attempt. | Atomically verify Project access, current Claim owner, Lease, command path, and current Attempt ID and status where applicable; reject non-owner and stale mutations without partial writes. Phase 5 additionally authenticates distinct Subject ownership. | Human/Agent claim races, non-owner mutation, foreign owner, expiry, reclaim, and stale-submission tests. |
| Event forgery | A client supplies another actor, false timestamp, event type, or inconsistent TaskEvent/AuditEvent. | Create events only inside authenticated application transactions; derive actor, Token attribution where applicable, request, and time internally; validate typed payloads; commit state and event atomically; exclude credentials. | Actor spoofing, invalid event, rollback, ordering, attribution, and secret-exclusion tests. |
| Concurrent mutation overwrite | A stale Human or process updates an existing Task after another accepted mutation. | Reject non-owner writes while a Claim is current; otherwise require a positive expected version at every trusted mutation boundary, increment once per semantic mutation, return `VERSION_CONFLICT` without writes, and never refresh and silently retry. | Claimed-Task lock, two-writer, stale-version, multi-event single-increment, and no-retry CLI tests. |
| Structured Task or Result abuse | Input attempts resource exhaustion, identity forgery, secret persistence, path execution, or automatic Task creation. | Require explicit bounded UTF-8 JSON input; cap bytes, depth, collections, and text; reject actor, Attempt, request, event, Result, cursor, and timestamp identities; treat URIs as inert references; never execute or fetch artifacts or proposed follow-ups. | Oversize, nesting, forged-field, metacharacter, artifact, and proposed-follow-up tests. |
| Mutation replay | A lost response causes a client to repeat a state-changing request. | Require idempotency keys for retryable mutations; authenticate and authorize before lookup; bind non-secret outcomes to actor, operation, target, and versions; combine idempotency with optimistic versions and Attempt checks. | Duplicate/conflicting reuse plus revoked-credential and removed-grant replay tests. |
| Persistence tampering or confusion | A process reads an unknown schema or exposes inconsistent state after a partial write. | Validate schema versions before access; fail without modifying unsupported stores; use transactional adapter operations and crash-safe JSON replacement; keep backend credentials outside task data. | Unknown-version, rollback, interrupted-write, and backend-contract tests. |
| Denial of service | A client sends large payloads, expensive queries, rapid heartbeats, connection floods, or unbounded event reads. | Bound payload sizes, pagination, timeouts, retries, concurrency, and transaction duration; apply deployment-level request limits; provide backpressure and actionable errors; keep housekeeping optional for Lease correctness. | Limit, timeout, concurrency-load, and large-history tests. |

## Compromised-Agent containment

An Agent is not trusted merely because its Token is valid. The Agent role may
claim, heartbeat, report progress, release, and submit only within granted
Projects and only for a Claim and Attempt it owns. It cannot redefine, block,
cancel, or change dependencies on the claimed Task; grant roles; impersonate
another Subject; choose event attribution; or mutate another owner's Claim or
Attempt.

Compromise can still expose every Project and operation legitimately granted to
that Agent until its Token is revoked or expires. Operators should therefore
issue individual short-lived credentials, minimize ProjectGrants, avoid shared
Agent identities, and revoke or disable a suspected Subject promptly.

## Availability boundary

V1 supports one server process per Instance and does not promise horizontal
scaling or protection against an administrator or infrastructure operator.
Application limits should preserve service for expected single-organization
load, but deployment-level network controls, process supervision, storage
capacity, and recovery remain required.

Lease correctness must not depend on a scheduler continuing to run during
overload. Claims, Human renewals, Agent heartbeats, submissions, mutations, and
writes evaluate expiry transactionally using the authoritative runtime clock
and the half-open rule `now < lease_expires_at`. Pure reads do not materialize
expiry or append events; they project an expired Claim as stale and non-owning.
Phase 4 Lease inputs use the closed duration grammar and bounded Human and Agent
windows defined in the CLI contract.

## Verification by delivery phase

- Phase 2 tests strict context parsing, canonical physical discovery,
  Workspace-root containment, safe binding replacement, trusted embedded
  profile storage ownership, schema version `1` rejection without mutation,
  remote-configuration rejection, and malicious `.workaholic.env` input.
- Phase 3 uses exact SQLite schema version `3`, rejects version `2` unchanged,
  and tests optimistic Task versions, transition and dependency atomicity,
  Human Result attribution with null Attempt, bounded structured input, review
  behavior, event ordering, and idempotent lifecycle replay.
- Phase 4 tests atomic Human and Agent Claims, exclusive mutation locks, Human
  renewal, current Attempt ownership, Lease expiry, version stability, stale
  submissions, terminal Attempt states, idempotent Results, and bounded Agent
  payloads. It uses exact SQLite schema version `4`, rejects version `3`
  unchanged, and adds no migration or credential surface.
- Phase 5 tests canonical Token generation and hash-only storage, pending
  activation, exact expiry, revocation and disablement races, credential-source
  precedence and protected storage, local recovery, cumulative roles,
  last-administrator/Owner safeguards, Project isolation, real Agent ownership,
  AuditEvent attribution, and compromised-Agent containment. It uses exact
  SQLite schema version `5` and rejects version `4` unchanged.
- Phase 6 tests authenticated RemoteSession behavior, expected Instance
  identity, protocol rejection, timeouts, and safe retries.
- Phase 7 runs authorization, event, concurrency, and failure contracts against
  every persistence adapter.
- Phase 8 exercises load limits, large histories, clock differences, server
  restarts, credential revocation during Attempts, and operational recovery.

## Out of scope for v1

The following are not v1 security guarantees:

- isolation between different organizations in one Instance;
- a public multi-tenant hosted service;
- protection from a malicious Instance administrator or deployment operator;
- protection after compromise of the host, process, database administrator, or
  operating-system account controlling embedded state;
- confidentiality or availability of externally referenced artifact contents;
- direct third-party use of the private client/server protocol;
- SSO, OAuth, enterprise identity providers, custom roles, or a policy
  language;
- horizontal server scaling or volumetric denial-of-service absorption.

These exclusions must not weaken Project-role isolation among Subjects inside
the one organization served by an Instance.

## Related documents

- [Architecture](architecture.md)
- [Delivery roadmap](roadmap.md)
- [Product scope](product-scope.md)
- [Compatibility policy](compatibility-policy.md)
- [ADR 0011: Phase 3 Task Mutation and Human Submission](adr/0011-phase-three-task-mutation-and-human-submission.md)
- [ADR 0012: Phase 4 Local Claim and Execution Model](adr/0012-phase-four-local-claim-and-execution-model.md)
- [ADR 0013: Phase 5 Token and Authorization Model](adr/0013-phase-five-token-and-authorization-model.md)
- [Glossary](glossary.md)
- [Security reporting policy](../SECURITY.md)
