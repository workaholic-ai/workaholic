# Contract Tests

Contract tests verify observable behavior shared by multiple implementations or
boundaries. Examples include CLI envelope conformance, persistence-adapter
semantics, LocalSession and RemoteSession equivalence, and protocol
negotiation.

Tests in this directory carry the `contract` marker. They may use lightweight
real implementations but must not pass by asserting only against a mock of the
contract being verified. Backend-independent assertions belong here; physical
schema and private route details do not.

The canonical end-to-end acceptance flows remain in the
[golden journey directory](../e2e/golden/README.md).

## Cumulative persistence and Session suites

[`phase_one.py`](phase_one.py) defines the baseline repository and Session
factory protocols. [`phase_two.py`](phase_two.py) extends those protocols with
deterministic clocks and identifiers plus isolated trusted profile registries.
[`phase_three.py`](phase_three.py) completes the identity surface and adds
exact-version repository connections, deterministic lifecycle composition,
and semantic transaction-failure hooks. Each persistence and Session contract
inherits every earlier assertion. Phase 3 adds lifecycle, optimistic race,
dependency graph, readiness, Result review, TaskEvent pagination, attribution,
authorization, restart, idempotency, and rollback behavior without
adapter-specific expected outcomes.

Concrete adapters subclass the relevant contract and provide one factory
fixture. Expected outcomes contain no adapter-specific branches. Factories
must isolate all persistence, configuration, and Workspace state below
pytest-owned paths and must never read an operator's real configuration.
Failure injection is adapter-owned factory plumbing: shared assertions name a
semantic boundary and verify observable rollback, never a table, query, or
private storage function.

Intentional negative samples live under [fixtures](fixtures/README.md). They
prove each dependency rule and the CLI startup detector fail with actionable
importer and imported-module names.
