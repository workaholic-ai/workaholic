# Changelog

All notable changes to Workaholic AI will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The project intends to follow [Semantic Versioning](https://semver.org/) when
public versioning begins.

## Unreleased

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
