# Changelog

All notable changes to Workaholic AI will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The project intends to follow [Semantic Versioning](https://semver.org/) when
public versioning begins.

## Unreleased

## [0.5.0a1] - 2026-08-30

### Added

- Distinct Human and Agent Subjects, independently expiring and revocable
  bearer Tokens, protected Human credential enrollment, and embedded local
  recovery.
- Cumulative Viewer, Agent, Operator, and Owner Project roles plus independent
  Instance-administrator authority and atomic last-administrator/Owner
  safeguards.
- Identity, Token, grant, recovery, and administrative audit CLI commands with
  stable JSON envelopes and explicit optimistic versions.
- Cumulative Phase 5 repository and `LocalSession` contracts plus a
  fresh-process golden journey covering a Human operator and two independently
  authenticated Agents.

### Changed

- Replaced the disposable Phase 4 store with clean-store SQLite schema version
  `5`; schema version `4` is rejected unchanged and has no migration path.
- Authenticated every normal operation as one active Token and enabled Subject,
  with authorization revalidated inside each persistence transaction.
- Recorded real Subject identity and immutable Human or Agent kind in TaskEvent
  history while retaining exact Claim and Attempt ownership semantics.

### Security

- Store only SHA-256 Token digests and reveal each raw Token once through a
  protected credential sink; normal output, events, persistence projections,
  and diagnostics exclude raw credentials and hashes.
- Enforce explicit Agent credential-source precedence, account-only file modes,
  immediate revocation/disablement/grant effects, Project isolation, and
  non-disclosing authentication failures.
- Preserve active Claims when credentials are revoked or Subjects disabled;
  ownership remains exclusive until normal release, submission, or Lease
  expiry.

### Known limitations

- The alpha remains embedded-only, SQLite-only, single-organization software.
  It has no server, remote profile, `RemoteSession`, or distributed-team mode.
- JSON/PostgreSQL adapters, schema migration, capability scheduling, custom
  roles, SSO/OAuth, Project archival, force interruption, and parent/child Task
  hierarchy remain unavailable.
- Stores and pre-release automation remain disposable.

## [0.4.0a1] - 2026-08-27

### Added

- Exclusive local Human and Agent Claims with bounded Leases, explicit Human
  renewal, Agent heartbeat, safe release, and atomic ready-Task acquisition.
- Agent-only Attempts with active, released, expired, and submitted terminal
  states plus exact stale-owner rejection.
- Bounded structured Agent progress and observations retained as attributable
  `progress_reported` and `observation_added` TaskEvents.
- Cumulative Phase 4 repository and `LocalSession` conformance suites plus a
  fresh-process golden journey covering Human ownership, Agent execution,
  expiry and reclaim, lock enforcement, and simultaneous Claim races.
- A fail-fast Phase 4 clean-state gate and isolated installed-wheel journey
  covering Claim, Attempt, Lease, lock, progress, Result, review, restart,
  idempotency, and disposable-schema boundaries.

### Changed

- Replaced the disposable Phase 3 store with clean-store SQLite schema version
  `4`; schema version `3` is rejected unchanged and has no migration path.
- Expanded the local CLI from 19 to 24 Project, context, Task, Claim, and Agent
  execution operations.
- Reused the single bootstrap Subject for both local command paths. Human
  Claims keep a null Attempt; a non-null Attempt identifies Agent execution.
- Made a current Claim an exclusive mutation lock: owning Humans retain the
  normal Task workflow, while owning Agents may heartbeat, report progress,
  release, or submit with an exact expected Task version.

### Security

- Claim acquisition and expiry are transactional and use the persistence
  adapter's authoritative clock; pure reads never silently transfer ownership.
- Current non-owners receive `TASK_LOCKED`, stale or foreign Agent owners
  receive `LEASE_LOST`, and rejected operations preserve Task, Claim, Attempt,
  Result, event, and idempotency state.
- Lease durations use a closed grammar with separate Human and Agent defaults
  and bounds. Progress payloads cannot forge identity, Attempt, request, event,
  cursor, Result, or authoritative timestamp fields.

### Known limitations

- The alpha remains embedded-only with one bootstrap Human Subject reused by
  Human and Agent command paths. It does not distinguish Agent identities or
  different Human operators sharing the same operating-system account.
- Tokens, authentication, remote profiles, `RemoteSession`, servers,
  JSON/PostgreSQL adapters, schema migration, capability scheduling, Project
  archival, force interruption, and parent/child hierarchy are unavailable.
- Stores and pre-release automation remain disposable.

## [0.3.0a1] - 2026-08-01

### Added

- Complete Human-operated Task definitions and lifecycle commands for update,
  block, unblock, cancellation, same-Project dependencies, submission, review,
  and ordered event inspection.
- Deterministic readiness, scheduling, blocked, review, done, and cancelled
  views with stable view-bound cursors.
- Structured Human Results with acceptance evidence, artifact references,
  proposed follow-up provenance, and explicit review disposition.
- Optimistic Task versions, idempotent lifecycle mutations, stable typed
  TaskEvents, and complete actor, request, timestamp, and cursor attribution.
- Cumulative Phase 3 SQLite and `LocalSession` conformance suites plus the
  complete fresh-process Human lifecycle golden journey.
- A fail-fast Phase 3 clean-state gate and isolated installed-wheel lifecycle
  journey that prove source/wheel parity, reject contaminated operator state,
  and exercise the documented lifecycle and failure boundaries.

### Changed

- Replaced the disposable Phase 2 store with clean-store SQLite schema version
  `3`; schema version `2` is rejected unchanged and has no migration path.
- Expanded the local CLI from the nine Phase 2 operations to 19 Project,
  context, and Task operations.
- Made an explicit expected Task version mandatory for automation while
  allowing a terminal Human to confirm one displayed current version and
  semantic action.
- Removed the speculative parent/child Task hierarchy from planned v1.
  Decomposition uses explicit same-Project dependencies, while attributable
  events and Results preserve the provenance of follow-up work.

### Security

- Bounded structured Task and Result input rejects forged identities, unknown
  fields, recursive or oversized content, executable interpretation, and
  ambiguous file/inline values before mutation.
- Existing-Task writes reject stale versions without refresh or silent retry;
  transaction rollback preserves Task, Result, dependency, event, and
  idempotency state.
- Human submissions derive actor and request attribution through the trusted
  Session and always persist a null Attempt identity.
- Golden CLI processes inherit only a small platform-runtime allowlist and
  strip credentials, Tokens, Python paths, and arbitrary environment state.

### Known limitations

- The alpha remains embedded-only with one bootstrapped Human operator per
  profile and SQLite persistence.
- Stores and automation remain disposable. Agents, Attempts, Leases, Tokens,
  remote profiles, `RemoteSession`, servers, JSON/PostgreSQL adapters, schema
  migration, Project archival, and parent/child hierarchy are unavailable.
- Proposed Result follow-ups are provenance only and do not create Tasks.

## [0.2.0a1] - 2026-07-30

### Added

- Trusted embedded `profiles.toml` registries with deterministic profile
  selection through command options, process configuration, and Workspace
  context.
- Multiple named Projects per Instance, Project creation and binding commands,
  canonical upward Workspace discovery, and a safe explicit replacement
  boundary.
- Explicit Project and all-Project Task selection with independent stable
  `PROJECT-NUMBER` sequences, deterministic ordering, and scope-bound cursors.
- Cumulative Phase 2 repository and Session conformance suites plus the enabled
  fresh-process multi-project golden journey.
- Public multi-project quick start and current architecture, CLI, persistence,
  and threat-model documentation.
- A fail-fast Phase 2 clean-state gate and isolated installed-wheel journey
  that prove source/wheel parity, independent Project numbering, and safe
  configuration, data, and Workspace ownership.

### Changed

- Replaced the disposable Phase 1 store with clean-store SQLite schema version
  `2`; schema version `1` is rejected unchanged and has no migration path.
- Expanded the local CLI from the six Phase 1 commands to the nine Phase 2
  Project, context, and Task operations.

### Security

- Context discovery walks canonical physical parents, treats the nearest file
  as authoritative, and refuses fallback or replacement when nearer input is
  malformed, unsafe, symlinked, or concurrently changed.
- Repository-controlled context can name but never define a profile. Phase 2
  rejects remote URLs, credentials, Tokens, secret references, executable
  paths, and non-embedded profile modes.
- Golden CLI processes pin test-owned configuration and data roots, strip
  inherited Workaholic and Python-path selectors, and reject undocumented
  environment injection.

### Known limitations

- The alpha remains embedded-only with one bootstrapped Human operator per
  profile and SQLite persistence.
- Stores and automation remain disposable. Agents, Tokens, remote profiles,
  `RemoteSession`, servers, JSON/PostgreSQL adapters, schema migration, Project
  archival, and Task updates are unavailable.

## [0.1.0a1] - 2026-07-30

### Added

- Accepted product scope, compatibility policy, and package naming decision.
- Apache-2.0 licensing and Python 3.14 project metadata.
- Installable `workaholic-ai` package with the `workaholic` bootstrap CLI.
- Reproducible local linting, formatting, typing, testing, and build controls.
- Public quick start, contribution guidance, and repository impact checks.
- Canonical architecture, roadmap, glossary, and v1 threat model.
- Foundational architecture decision records and the CLI automation and
  persistence delivery contracts.
- Executable specifications for all six golden user journeys, with a strict
  pytest taxonomy and phase-specific enablement gates.
- Exhaustive package dependency contracts and isolated CLI import-weight
  checks that protect domain, application, Session, and adapter boundaries.
- Community conduct, vulnerability disclosure, code ownership, structured
  issue intake, pull-request review, and bounded dependency-update policies.
- Least-privilege continuous integration with immutable action pins, locked
  quality and test jobs, inspectable build artifacts, and isolated wheel smoke
  verification.
- Public source development from Phase 0, while package publication and
  supported releases remain gated by the release-candidate phases.
- A fail-fast Phase 0 acceptance gate that proves the locked source checkout,
  full quality and test suite, package build, and isolated wheel installation
  from a clean clone.
- Immutable Phase 1 domain entities, validated application commands, and
  transport-neutral Session contracts.
- Exact-directory `.workaholic.env` context, disposable SQLite schema version
  `1`, atomic local bootstrap, and attributable idempotent Task creation.
- Embedded `LocalSession` composition and the six local Project and Task CLI
  operations with stable `workaholic.cli/v1` JSON envelopes.
- Backend-neutral repository and Session conformance suites, separate-connection
  SQLite concurrency coverage, and the enabled persistent solo golden journey.
- A fail-fast Phase 1 clean-state gate and isolated installed-wheel journey that
  prove persistent Task behavior without using source or operator state.

### Security

- Local CLI subprocess tests strip inherited `WORKAHOLIC_*` selections and pin
  storage to test-owned directories.
- Phase 1 context contains no credentials, authorization is revalidated for
  every operation, and unsupported stores fail unchanged.

### Known limitations

- This internal alpha implements one exact-directory Project with one
  bootstrapped Human operator and embedded SQLite only.
- Stores and automation are disposable; schema migration, Agents, Tokens,
  RemoteSession, servers, JSON/PostgreSQL adapters, and team coordination are
  not available.
