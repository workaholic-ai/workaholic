# ADR 0005: Semantic Persistence Interface

- Status: Accepted
- Decision date: 2026-07-29
- Deciders: Pavels Gurskis
- Supersedes: None
- Superseded by: None

## Context

V1 requires JSON, SQLite, and PostgreSQL backends with identical observable task
behavior. Core operations combine several state changes: allocating task
numbers, claiming work, validating Leases, recording idempotency, and appending
TaskEvents. A generic CRUD or key-value abstraction would expose storage shape
without expressing the transactions needed for correctness.

Letting each adapter define its own semantics would make task behavior depend on
deployment choice and would duplicate concurrency decisions outside the domain
and application layers.

## Decision

Define a semantic persistence interface whose operations correspond to complete
domain transactions rather than table, document, or key-value CRUD.
Representative operations include:

```text
create_task_and_allocate_number
update_task_if_version
claim_next_task
renew_attempt
release_attempt
submit_result
approve_result
reject_result
record_idempotent_result
read_events_after
```

An operation atomically validates its preconditions, mutates state, appends all
required TaskEvents, and records an idempotent outcome where applicable.
Rejected operations leave state unchanged.

Every adapter validates the backend-independent store schema version before
reading or mutating state. Task-number allocation, ready-task claiming,
optimistic Task versions, current Attempt and Lease checks, ordered append-only
events, and idempotency records follow the normative
[persistence contract](../persistence-contract.md).

JSON, SQLite, and PostgreSQL adapters expose the same semantic outcomes and run
the same conformance suite. Adapter-specific locks, transactions, queries,
schemas, indexes, serialization, and crash-safety mechanisms remain internal.

The persistence interface belongs below the application layer. CLI, Session,
and transport code cannot access a concrete adapter directly. The Python
interface and physical storage layout are not public APIs.

## Alternatives considered

### Use generic repositories with CRUD methods

CRUD methods are familiar but split atomic operations across calls, encouraging
race conditions and partial TaskEvent histories.

### Let application code manage adapter transactions directly

This would leak backend transaction mechanisms into the application layer and
make JSON, SQLite, and PostgreSQL behavior diverge.

### Treat TaskEvents as the only persisted source of truth

Full event sourcing could model history elegantly but would add replay,
snapshot, evolution, and operational complexity not required for v1. V1 stores
current state with an append-only activity and audit record.

### Implement only SQLite and generalize later

This could accelerate the first slice, but without an explicit semantic
boundary the first adapter's physical model would become the accidental
contract for later backends.

## Consequences

- Atomic domain invariants are visible in interface contracts and tests.
- Backends can use their native concurrency and durability mechanisms.
- Conformance tests, rather than shared physical schemas, define swappability.
- The interface contains fewer but richer operations than a CRUD repository.
- Adding a new behavior may require coordinated application, contract, and
  adapter changes.
- A new Instance can choose any supported backend without changing client or
  domain behavior; changing an existing Instance's backend is not implied.

## References

- [Persistence contract](../persistence-contract.md)
- [Architecture](../architecture.md)
- [Threat model](../threat-model.md)
- [Compatibility policy](../compatibility-policy.md)
- [ADR 0009: No Storage Migrations in v1](0009-no-storage-migrations-in-v1.md)

