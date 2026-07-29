# ADR 0010: Single-Process, Single-Instance Server

- Status: Accepted
- Decision date: 2026-07-29
- Deciders: Pavels Gurskis
- Supersedes: None
- Superseded by: None

## Context

Distributed-team operation needs one authenticated server reachable by many
official clients. Horizontal workers and multi-Instance hosting would add
distributed coordination, routing, deployment, and isolation requirements
before task and persistence semantics are proven.

JSON and SQLite can safely support a shared deployment only when one application
process owns their mutation path. PostgreSQL provides stronger concurrent
storage but does not by itself solve application-level multi-process scheduling
and lifecycle assumptions.

## Decision

One v1 server process serves exactly one Workaholic AI Instance, one
organization, and one configured persistence backend. A given Instance has one
active Workaholic AI server process.

Many remote CLI clients and many Human and Agent Subjects may use that process
concurrently. Application and persistence transactions, not process-local
background scheduling, enforce atomic claims, Task versions, Attempt ownership,
Lease expiry, idempotency, and TaskEvent consistency.

Shared-server backends are:

- JSON for inspection, demonstrations, and small single-server use;
- SQLite for small-team single-server use;
- PostgreSQL as the recommended distributed-team store.

PostgreSQL is never accessed directly by CLI clients. It remains behind the one
server process.

Production-like deployment uses a process supervisor, trusted configuration,
persistent storage, backups, structured logs, health and readiness checks, and
graceful shutdown. HTTPS is normally terminated by trusted ingress or reverse
proxy infrastructure. The server clock is authoritative for Lease decisions.

The server does not host multiple organizations as isolated public tenants.
Cross-organization isolation, multiple active application workers for one
Instance, horizontal scaling guarantees, and an official hosted service are
outside v1.

## Alternatives considered

### Support multiple workers from the first shared release

This would require distributed coordination for background work, deployment,
shutdown, and possibly event delivery before the core workflow is mature.

### Host multiple Instances in one process

This would add routing and isolation concerns and blur the one-organization
security boundary.

### Require PostgreSQL for every shared server

PostgreSQL is appropriate for team deployment but would make small evaluation
and single-server operation unnecessarily heavy.

### Let clients connect directly to PostgreSQL

Direct access would expose physical schemas, bypass application authorization,
and eliminate the private protocol and Session boundary.

## Consequences

- Initial deployment and correctness assumptions remain explicit and testable.
- JSON and SQLite shared operation require exclusive ownership by one server
  process.
- PostgreSQL improves persistence concurrency without implying horizontal
  application scaling.
- Operators need process supervision and recovery rather than relying on an
  in-process high-availability claim.
- Capacity is bounded by one process and deployment-level limits; volumetric
  denial-of-service absorption is not promised.
- Supporting multiple active workers or public multi-tenancy later requires a
  new ADR and expanded conformance, operational, and threat models.

## References

- [Architecture](../architecture.md)
- [Threat model](../threat-model.md)
- [Delivery roadmap](../roadmap.md)
- [Product scope](../product-scope.md)
- [Persistence contract](../persistence-contract.md)
- [ADR 0005: Semantic Persistence Interface](0005-semantic-persistence-interface.md)

