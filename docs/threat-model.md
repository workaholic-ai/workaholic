# Workaholic AI Threat Model

- Status: Accepted Phase 0 baseline
- Decision date: 2026-07-29
- Scope: Embedded and shared-server behavior required for v1
- Security contact: [pg@ithesion.com](mailto:pg@ithesion.com)

## Purpose

This threat model turns the accepted v1 security boundary into explicit
engineering constraints and verification targets. It covers planned behavior;
the current `0.0.0` development package implements exact-directory Project
context, local SQLite persistence, and bootstrap-Human attribution. It does not
yet implement bearer authentication, Agent execution, or network services.

Terms such as Subject, ProjectGrant, Attempt, Lease, and TaskEvent use their
canonical definitions in the [glossary](glossary.md).

## Security objectives

Workaholic AI must:

- authenticate every independently operating Human and Agent Subject;
- authorize every operation against instance and Project roles;
- limit a compromised Subject or stolen Token to that Subject's grants;
- preserve Task, Attempt, Lease, Result, and TaskEvent integrity;
- attribute every mutation to the authenticated Subject and request;
- prevent stale or foreign Attempts from mutating work;
- keep credentials out of repository-controlled context and normal output;
- apply the same authorization and audit rules through LocalSession and
  RemoteSession;
- fail explicitly on unsupported context, protocol, or persistence versions;
- remain predictably available under bounded single-organization workloads.

## Assets

Security-sensitive assets include:

- raw bearer Tokens and bootstrap credentials;
- trusted user profiles and their selected server endpoints;
- Project membership and ProjectGrants;
- Task content, state, versions, dependencies, and stable identities;
- Attempt ownership, Lease expiry, Results, and idempotency records;
- attributable, append-only TaskEvents and their Instance ordering;
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
named Project.

Subject kind and Project role are independent. Agent Capabilities affect
scheduling only and never authorization. LocalSession and RemoteSession must
call the same application authorization policy.

### Local filesystem and credential storage

The operating-system account running an embedded client is trusted to protect
its local persistence files, user configuration, process environment, and
Workspace. Another process with that account's privileges is outside the
embedded runtime's application isolation boundary.

Human credentials use the operating-system credential store where available.
A configuration-file fallback must be stored outside repositories with
permissions limited to the account. Agent credentials may be injected through
environment variables, mounted secret files, or an orchestrator secret
mechanism. Deployers must prevent those channels from being exposed to
untrusted sibling processes or logs.

### Repository-controlled context

Every `.workaholic.env` file is untrusted input, even in a trusted
organization's repository. It may identify a context version, trusted profile,
Instance, Project, project key, and relative Workspace root only through a
strict allowlist.

The parser must never invoke a shell, perform variable or command substitution,
load executable paths, or accept credentials. A context file must not select an
arbitrary server endpoint or override the endpoint owned by a trusted user
profile. Relative paths are resolved from the context file's directory.

### Remote transport

Remote bearer-token traffic uses HTTPS through trusted deployment
infrastructure. A trusted profile owns the server URL and expected Instance
identity. RemoteSession must reject an unexpected Instance and incompatible
protocol before sending a mutation.

The private protocol is supported only between official clients and servers.
Calling internal routes directly does not create a public security or
compatibility contract.

## Threat actors and scenarios

The model considers:

- a compromised Agent process using its valid Token;
- an attacker who has stolen a Human or Agent Token;
- a malicious or compromised repository controlling `.workaholic.env`;
- an authenticated Subject attempting operations outside its role or Project;
- a stale Agent process attempting to mutate a reclaimed Task;
- a network attacker observing, redirecting, replaying, or altering traffic;
- a client submitting forged actor, event, time, or Attempt information;
- a client or workload exhausting process, persistence, or event resources;
- accidental operator misconfiguration.

A malicious Instance administrator, compromised deployment host, or database
administrator is outside the v1 application boundary.

## Threats and required mitigations

| Threat | Scenario | Required mitigations | Verification target |
| --- | --- | --- | --- |
| Compromised Agent | An Agent tries to read or mutate unrelated Projects or perform Operator actions. | Use one Subject per independent Agent; enforce ProjectGrant permissions on every application operation; treat Capabilities as scheduling labels only; record attribution. | Cross-Project and role-denial tests through LocalSession and RemoteSession. |
| Stolen Token | An attacker replays a bearer Token until it expires or is revoked. | Store only Token hashes; support expiry, revocation, and Subject disablement; use narrow ProjectGrants and separate Agent identities; audit every accepted mutation. | Expiry, revocation, disablement, and least-privilege tests. |
| Token redirection | A repository changes context so a client sends its Token to an attacker endpoint. | Forbid URLs and credentials in `.workaholic.env`; resolve only a named trusted profile; require HTTPS remotely; compare the server's Instance identity before mutations. | Hostile-context and unexpected-Instance tests. |
| Secret exposure | Credentials appear in arguments, task data, events, logs, errors, or repository files. | Reject secrets in context; never accept Tokens in normal command arguments; redact diagnostics and structured logs; exclude raw Tokens from domain models and persistence; protect credential files. | Redaction tests and repository/history secret scans. |
| Command injection | Context or task input triggers shell expansion or execution. | Parse context with a strict data parser and key allowlist; reject substitution and executable-path keys; never source `.workaholic.env`; use argument-vector subprocess calls at trusted adapter boundaries. | Malformed context, metacharacter, substitution, and unknown-key tests. |
| Unauthorized Attempt mutation | A Subject heartbeats, releases, reports, or submits against another, expired, or superseded Attempt. | Authenticate the Subject; atomically verify Project access, Attempt owner, current Attempt ID, status, and Lease before mutation; reject stale Attempts without partial writes. | Concurrent claim, foreign owner, expiry, reclaim, and stale-submission tests. |
| Event forgery | A client supplies another actor, false timestamp, event type, or inconsistent TaskEvent. | Create TaskEvents only inside authenticated application transactions; derive actor and authoritative time server-side; validate typed payloads; commit state and event atomically; allocate ordered cursors in persistence. | Actor spoofing, invalid event, rollback, and ordering contract tests. |
| Mutation replay | A lost response causes a client to repeat a state-changing request. | Require idempotency keys for retryable mutations; bind stored outcomes to the authenticated operation; combine idempotency with optimistic Task versions and Attempt checks. | Duplicate-request and conflicting-reuse tests. |
| Persistence tampering or confusion | A process reads an unknown schema or exposes inconsistent state after a partial write. | Validate schema versions before access; fail without modifying unsupported stores; use transactional adapter operations and crash-safe JSON replacement; keep backend credentials outside task data. | Unknown-version, rollback, interrupted-write, and backend-contract tests. |
| Denial of service | A client sends large payloads, expensive queries, rapid heartbeats, connection floods, or unbounded event reads. | Bound payload sizes, pagination, timeouts, retries, concurrency, and transaction duration; apply deployment-level request limits; provide backpressure and actionable errors; keep housekeeping optional for Lease correctness. | Limit, timeout, concurrency-load, and large-history tests. |

## Compromised-Agent containment

An Agent is not trusted merely because its Token is valid. The Agent role may
claim, heartbeat, report progress, release, and submit only within granted
Projects and only for an Attempt it owns. It cannot grant roles, impersonate
another Subject, choose event attribution, or mutate another Agent's Attempt.

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
overload. Claims, heartbeats, submissions, and relevant reads evaluate expiry
transactionally using the authoritative runtime clock.

## Verification by delivery phase

- Phase 2 tests strict context parsing, trusted-profile endpoint ownership, and
  malicious `.workaholic.env` input.
- Phase 4 tests atomic claims, current Attempt ownership, Lease expiry, stale
  submissions, idempotent Results, and bounded agent payloads.
- Phase 5 tests Token storage, expiry, revocation, redaction, ProjectGrant
  isolation, and compromised-Agent containment.
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
- [Glossary](glossary.md)
- [Security reporting policy](../SECURITY.md)
