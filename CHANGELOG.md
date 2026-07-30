# Changelog

All notable changes to Workaholic AI will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The project intends to follow [Semantic Versioning](https://semver.org/) when
public versioning begins.

## Unreleased

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
