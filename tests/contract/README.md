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
The Phase 2 persistence and Session contracts inherit every Phase 1 assertion
and add only multi-Project, profile, binding, authority, pagination, race, and
rollback behavior.

Concrete adapters subclass the relevant contract and provide one factory
fixture. Expected outcomes contain no adapter-specific branches. Factories
must isolate all persistence, configuration, and Workspace state below
pytest-owned paths and must never read an operator's real configuration.

Intentional negative samples live under [fixtures](fixtures/README.md). They
prove each dependency rule and the CLI startup detector fail with actionable
importer and imported-module names.
