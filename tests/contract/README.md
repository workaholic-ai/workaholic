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
and semantic transaction-failure hooks. [`phase_four.py`](phase_four.py) adds
the Claim, Attempt, Lease, progress, and Agent Result builders and failure
points required to exercise execution backends. [`phase_five.py`](phase_five.py)
adds deterministic Subject, Token, ProjectGrant, and AuditEvent fixtures,
explicit credential-backed Sessions, and semantic identity transaction failure
points. Each persistence and Session contract inherits every earlier
assertion. Phase 3 adds lifecycle, optimistic race, dependency graph,
readiness, Result review, TaskEvent pagination, attribution, authorization,
restart, idempotency, and rollback behavior. Phase 4 adds Human-versus-Agent
ownership, exact Lease expiry and reclaim, exclusive mutation locks,
structured progress, terminal Attempt behavior, Agent submission and review
races, and independent-connection restart continuity without adapter-specific
expected outcomes. Phase 5 adds non-secret Token lifecycle, distinct Human and
Agent identities, cumulative role enforcement, Project isolation,
last-administrator and last-Owner safeguards, administrative audit attribution,
credential failure, restart, idempotency, and rollback behavior.

Concrete adapters subclass the relevant contract and provide one factory
fixture. Expected outcomes contain no adapter-specific branches. Factories
must isolate all persistence, configuration, and Workspace state below
pytest-owned paths and must never read an operator's real configuration.
Failure injection is adapter-owned factory plumbing: shared assertions name a
semantic boundary and verify observable rollback, never a table, query, or
private storage function.

SQLite additionally runs contention tests with independently spawned Python
processes against one database file. The process tests are intentionally not
replaced by threads: they prove double-Claim prevention, stale Agent-writer
rejection, authentication-versus-revocation and disablement ordering, and
last-Owner preservation across real process and connection boundaries. A race
may authenticate immediately before its competing state change commits; once
revocation or disablement commits, every later authentication fails. Competing
Owner mutations produce exactly one committed change and one documented
`last_project_owner` rejection, with no partial grant or Subject state.

Intentional negative samples live under [fixtures](fixtures/README.md). They
prove each dependency rule and the CLI startup detector fail with actionable
importer and imported-module names.
