# ADR 0009: No Storage Migrations in v1

- Status: Accepted
- Decision date: 2026-07-29
- Deciders: Pavels Gurskis
- Supersedes: None
- Superseded by: None

## Context

Workaholic AI needs JSON, SQLite, and PostgreSQL persistence, but implementing
schema migration, cross-backend conversion, import, and export before domain
semantics stabilize would substantially expand v1. Development releases must be
free to change their physical and semantic schema while Attempt, event,
authorization, and idempotency behavior is still being proven.

Silently reading an unknown schema is unsafe. It can corrupt state or interpret
authorization and Lease data under incompatible assumptions.

## Decision

V1 does not provide:

- automatic schema migration;
- backend-to-backend conversion;
- Instance import or export;
- in-place switching of an existing Instance's backend;
- best-effort interpretation of an unknown schema.

Every store records a backend-independent schema version. A process validates
that version before any normal read or mutation. An unsupported version fails
explicitly and leaves the store unchanged.

Pre-release stores are disposable. Through Phase 7, a release may require an
explicit reset after an intentional, documented schema change.

The persisted schema freezes at the Phase 8 exit gate. The release candidate
validates that frozen schema for `1.0.0`. The formal backward-compatibility
promise begins at `1.0.0`, and the `1.0.x` release line must not change the
persisted schema. A later v1 schema change requires a separately accepted
upgrade policy and migration path before it can ship.

"Swappable backend" means a new Instance can be initialized with any supported
adapter while preserving observable behavior. It does not mean an existing
Instance can be converted.

Backups and backend-native recovery remain operator responsibilities. A
developer-only reset command may remove disposable development state when
explicitly confirmed, but it is not migration tooling.

## Alternatives considered

### Build a migration framework before Phase 1

This would support durable early stores but delay usable task behavior and
force migration abstractions around an unstable schema.

### Let each backend migrate independently

Backend-specific migration semantics would undermine observable conformance and
create inconsistent support promises.

### Ignore or partially read unknown versions

Best-effort interpretation risks silent corruption, authorization mistakes, and
irreversible writes.

### Support export and import instead of migrations

Conversion tooling still requires stable semantic mappings, conflict behavior,
credential handling, and operational support that are outside v1.

## Consequences

- Early implementation can change schema deliberately without maintaining old
  development stores.
- Every adapter needs schema-version validation and a non-mutating failure path.
- Pre-release notes and documentation must call out required resets.
- Users cannot rely on pre-release data surviving upgrades.
- Release-candidate stores are expected to carry to `1.0.0` only after the
  Phase 8 freeze.
- Long-lived v1 evolution is constrained until an upgrade and migration policy
  is accepted.

## References

- [Compatibility policy](../compatibility-policy.md)
- [Persistence contract](../persistence-contract.md)
- [Architecture](../architecture.md)
- [Product scope](../product-scope.md)
- [ADR 0005: Semantic Persistence Interface](0005-semantic-persistence-interface.md)

